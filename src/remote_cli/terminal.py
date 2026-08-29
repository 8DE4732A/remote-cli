"""Terminal raw mode handling, window resize, and interactive attach client."""

import json
import os
import select
import signal
import socket
import sys
import termios
import tty
from pathlib import Path

from .client import Client
from .protocol import ActionType, Request, Response
from .utils import get_socket_path

ESCAPE_KEY = b"\x1d"  # Ctrl+]


def get_terminal_size() -> tuple[int, int]:
    """Returns (rows, cols) for the current terminal."""
    try:
        sz = os.get_terminal_size()
        return sz.lines, sz.columns
    except Exception:
        return 24, 80


def attach_session(session_id: str, socket_path: Path | None = None) -> None:
    """Attaches current terminal in raw mode to a remote-cli session."""
    sock_path = socket_path or get_socket_path()
    if not sock_path.exists():
        print(f"Error: Daemon socket {sock_path} not found.", file=sys.stderr)
        return

    rows, cols = get_terminal_size()

    # Connect to daemon
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(str(sock_path))
    except Exception as e:
        print(f"Error connecting to daemon: {e}", file=sys.stderr)
        return

    # Send ATTACH request
    req = Request(
        action=ActionType.ATTACH,
        session_id=session_id,
        rows=rows,
        cols=cols,
    )
    s.sendall((req.model_dump_json() + "\n").encode("utf-8"))

    # Read acknowledgment
    resp_line = b""
    while b"\n" not in resp_line:
        chunk = s.recv(1024)
        if not chunk:
            break
        resp_line += chunk

    try:
        resp = Response.model_validate(json.loads(resp_line.decode("utf-8").strip()))
        if not resp.success:
            print(f"Failed to attach: {resp.error or resp.message}", file=sys.stderr)
            s.close()
            return
    except Exception as e:
        print(f"Invalid attach response from server: {e}", file=sys.stderr)
        s.close()
        return

    # Save original terminal attributes
    stdin_fd = sys.stdin.fileno()
    is_tty = os.isatty(stdin_fd)
    old_attrs = None
    if is_tty:
        old_attrs = termios.tcgetattr(stdin_fd)

    client_helper = Client(socket_path=sock_path)

    # SIGWINCH handler
    def handle_sigwinch(signum, frame):
        try:
            r, c = get_terminal_size()
            client_helper.resize(session_id, rows=r, cols=c)
        except Exception:
            pass

    old_sigwinch = None
    try:
        old_sigwinch = signal.signal(signal.SIGWINCH, handle_sigwinch)
    except Exception:
        pass

    try:
        if is_tty:
            tty.setraw(stdin_fd)

        # Main select loop
        running = True
        sock_fd = s.fileno()

        while running:
            rlist, _, _ = select.select([stdin_fd, sock_fd], [], [])

            if sock_fd in rlist:
                data = s.recv(4096)
                if not data:
                    # Session exited or socket closed
                    break
                os.write(sys.stdout.fileno(), data)

            if stdin_fd in rlist:
                data = os.read(stdin_fd, 1024)
                if not data:
                    break
                # Check for detach escape key (Ctrl+])
                if ESCAPE_KEY in data:
                    # Print detach notice after restoring terminal
                    break
                s.sendall(data)

    except (KeyboardInterrupt, BrokenPipeError, ConnectionResetError):
        pass
    finally:
        # Restore terminal settings
        if is_tty and old_attrs is not None:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_attrs)
        if old_sigwinch is not None:
            try:
                signal.signal(signal.SIGWINCH, old_sigwinch)
            except Exception:
                pass
        s.close()
        print("\n[Detached from session]")
