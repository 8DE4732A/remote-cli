"""Session management, PTY allocation, and I/O multiplexing."""

import asyncio
import datetime
import fcntl
import os
import pty
import re
import struct
import termios
import uuid

from .protocol import ExecResult, SessionInfo
from .screen import RingBuffer, VirtualScreen
from .utils import generate_session_id, strip_ansi


class ExecWatcher:
    """Tracks command execution via unique sentinels in output stream."""

    def __init__(self, sentinel_id: str, raw_command: str):
        self.sentinel_id = sentinel_id
        self.raw_command = raw_command
        self.start_marker = f"__REMOTE_CLI_START_{sentinel_id}__"
        self.end_marker_prefix = f"__REMOTE_CLI_END_{sentinel_id}_"
        self.end_marker_regex = re.compile(rf"{re.escape(self.end_marker_prefix)}(\d+)__")
        self.future: asyncio.Future[ExecResult] = asyncio.get_event_loop().create_future()
        self.buffer = ""
        self.start_seen = False
        self.start_time = asyncio.get_event_loop().time()

    def feed(self, text: str) -> None:
        if self.future.done():
            return
        self.buffer += text

        # Check for end marker
        match = self.end_marker_regex.search(self.buffer)
        if match:
            exit_code = int(match.group(1))
            end_pos = match.start()

            # Extract output between the executed start marker and end marker
            content = self.buffer[:end_pos]
            if self.start_marker in content:
                _, _, after_start = content.rpartition(self.start_marker)
                output = after_start
            else:
                output = content

            # Clean output: strip ANSI codes, normalize CRLF to LF, and strip leading/trailing newlines
            clean_output = strip_ansi(output).replace("\r\n", "\n").replace("\r", "\n").strip("\n")
            duration = asyncio.get_event_loop().time() - self.start_time
            result = ExecResult(
                session_id="",
                command=self.raw_command,
                exit_code=exit_code,
                output=clean_output,
                duration=round(duration, 3),
                timed_out=False,
            )
            self.future.set_result(result)


class Session:
    """Represents a single interactive PTY session."""

    def __init__(
        self,
        session_id: str,
        name: str,
        command: list[str],
        rows: int = 24,
        cols: int = 80,
    ):
        self.session_id = session_id
        self.name = name or session_id
        self.command = command
        self.rows = rows
        self.cols = cols
        self.created_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.status = "active"
        self.exit_code: int | None = None

        self.master_fd: int | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.screen = VirtualScreen(cols=cols, rows=rows)
        self.buffer = RingBuffer()

        self.attached_writers: set[asyncio.StreamWriter] = set()
        self.active_watcher: ExecWatcher | None = None
        self.exec_lock = asyncio.Lock()
        self.loop = asyncio.get_event_loop()

    def start(self) -> None:
        """Allocates PTY and spawns subprocess."""
        master_fd, slave_fd = pty.openpty()
        self.master_fd = master_fd

        # Set window size on PTY
        self._set_pty_size(master_fd, self.rows, self.cols)

        # Set non-blocking master
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        # Spawn subprocess
        try:
            self.process = asyncio.subprocess.Process(
                transport=None,  # Handled directly via PTY fd
                protocol=None,
                loop=self.loop,
            )
        except Exception:
            pass

        # Use os.fork or subprocess with slave_fd
        import subprocess

        # Set slave fd inheritable
        os.set_inheritable(slave_fd, True)

        proc = subprocess.Popen(
            self.command,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            preexec_fn=os.setsid,
            close_fds=True,
        )
        os.close(slave_fd)
        self._proc = proc

        # Add reader to event loop
        self.loop.add_reader(self.master_fd, self._on_pty_readable)

    def _set_pty_size(self, fd: int, rows: int, cols: int) -> None:
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
        except Exception:
            pass

    def resize(self, rows: int, cols: int) -> None:
        """Resizes the PTY and virtual screen."""
        self.rows = rows
        self.cols = cols
        self.screen.resize(rows, cols)
        if self.master_fd is not None:
            self._set_pty_size(self.master_fd, rows, cols)

    def _handle_output_data(self, data: bytes) -> None:
        """Processes output bytes received from PTY."""
        self.screen.feed(data)
        self.buffer.append(data)

        if self.active_watcher:
            text = data.decode("utf-8", errors="replace")
            self.active_watcher.feed(text)

        dead_writers = set()
        for writer in self.attached_writers:
            try:
                writer.write(data)
            except Exception:
                dead_writers.add(writer)

        for writer in dead_writers:
            self.attached_writers.discard(writer)

    def _on_pty_readable(self) -> None:
        """Callback invoked when PTY master has output to read."""
        if self.master_fd is None:
            return

        try:
            data = os.read(self.master_fd, 4096)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            data = b""

        if not data:
            self._on_process_exit()
            return

        self._handle_output_data(data)

    def _on_process_exit(self) -> None:
        """Handles PTY close and subprocess termination."""
        if self.master_fd is not None:
            try:
                self.loop.remove_reader(self.master_fd)
                os.close(self.master_fd)
            except Exception:
                pass
            self.master_fd = None

        self.status = "exited"
        if hasattr(self, "_proc") and self._proc:
            try:
                self._proc.poll()
                self.exit_code = self._proc.returncode
            except Exception:
                pass

        # Clean up ControlMaster socket if exists
        try:
            from .utils import get_cm_socket_path

            cm_sock = get_cm_socket_path(self.session_id)
            if cm_sock.exists():
                cm_sock.unlink()
        except Exception:
            pass

        # Notify attached writers
        for writer in list(self.attached_writers):
            try:
                writer.close()
            except Exception:
                pass
        self.attached_writers.clear()

        # Fail any waiting exec
        if self.active_watcher and not self.active_watcher.future.done():
            self.active_watcher.future.set_exception(
                RuntimeError(f"Session {self.session_id} exited with code {self.exit_code}")
            )

    def write_input(self, data: bytes) -> None:
        """Writes raw input bytes to the PTY with non-blocking flow control and echo draining."""
        if self.master_fd is None or self.status != "active":
            raise RuntimeError(f"Session {self.session_id} is not active")
        try:
            import select
            import time

            total_written = 0
            total_len = len(data)

            while total_written < total_len:
                if self.master_fd is None or self.status != "active":
                    break

                # 1. Drain pending output from PTY to prevent slave echo buffer deadlock
                try:
                    rlist, _, _ = select.select([self.master_fd], [], [], 0)
                    if self.master_fd in rlist:
                        chunk_out = os.read(self.master_fd, 4096)
                        if chunk_out:
                            self._handle_output_data(chunk_out)
                except Exception:
                    pass

                # 2. Write chunk
                chunk = data[total_written : total_written + 512]
                try:
                    n = os.write(self.master_fd, chunk)
                    total_written += n
                    if total_len > 512:
                        time.sleep(0.0005)
                except (BlockingIOError, InterruptedError):
                    # PTY buffer full: wait briefly and drain
                    try:
                        select.select([self.master_fd], [self.master_fd], [], 0.02)
                    except Exception:
                        pass
        except Exception as e:
            raise RuntimeError(f"Failed to write to session {self.session_id}: {e}") from e

    def attach_client(self, writer: asyncio.StreamWriter) -> None:
        """Registers a client stream for live output broadcast."""
        self.attached_writers.add(writer)

    def detach_client(self, writer: asyncio.StreamWriter) -> None:
        """Unregisters a client stream."""
        self.attached_writers.discard(writer)

    async def execute_command(self, command: str, timeout: float = 30.0) -> ExecResult:
        """Executes a command and waits for completion sentinel."""
        async with self.exec_lock:
            if self.master_fd is None or self.status != "active":
                raise RuntimeError(f"Session {self.session_id} is not active")

            sentinel_id = uuid.uuid4().hex[:12]
            watcher = ExecWatcher(sentinel_id, command)
            self.active_watcher = watcher

            # Format injection with POSIX printf for clean delimiter detection
            # Command: printf "\n%s\n" "<START>"; <CMD>; printf "\n%s%d__\n" "<END_PREFIX>" "$?"
            cmd_payload = (
                f'printf "\\n%s\\n" "{watcher.start_marker}"; '
                f"{command}; "
                f'printf "\\n%s%d__\\n" "{watcher.end_marker_prefix}" "$?"\n'
            )

            self.write_input(cmd_payload.encode("utf-8"))

            try:
                result = await asyncio.wait_for(watcher.future, timeout=timeout)
                result.session_id = self.session_id
                return result
            except TimeoutError:
                # Capture partial output
                partial_output = strip_ansi(watcher.buffer).strip("\r\n")
                duration = self.loop.time() - watcher.start_time
                return ExecResult(
                    session_id=self.session_id,
                    command=command,
                    exit_code=None,
                    output=partial_output,
                    duration=round(duration, 3),
                    timed_out=True,
                )
            finally:
                self.active_watcher = None

    def close(self) -> None:
        """Terminates the session and kills process."""
        if hasattr(self, "_proc") and self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass
        self._on_process_exit()

    def get_info(self) -> SessionInfo:
        return SessionInfo(
            session_id=self.session_id,
            name=self.name,
            command=self.command,
            created_at=self.created_at,
            status=self.status,
            exit_code=self.exit_code,
            attached_clients=len(self.attached_writers),
            rows=self.rows,
            cols=self.cols,
        )


class SessionManager:
    """Manages all active sessions."""

    def __init__(self):
        self.sessions: dict[str, Session] = {}

    def create_session(
        self,
        command: list[str],
        name: str | None = None,
        rows: int = 24,
        cols: int = 80,
    ) -> Session:
        session_id = generate_session_id()
        session_name = name or f"session-{session_id}"

        # If command starts with ssh, automatically configure ControlMaster
        final_command = list(command)
        if final_command and final_command[0] == "ssh":
            from .utils import get_cm_socket_path

            cm_sock = get_cm_socket_path(session_id)
            has_cm = any("ControlPath" in str(arg) for arg in final_command)
            if not has_cm:
                cm_options = [
                    "-o",
                    "ControlMaster=auto",
                    "-o",
                    f"ControlPath={cm_sock}",
                    "-o",
                    "ControlPersist=600s",
                ]
                final_command = [final_command[0]] + cm_options + final_command[1:]

        session = Session(
            session_id=session_id,
            name=session_name,
            command=final_command,
            rows=rows,
            cols=cols,
        )
        session.start()
        self.sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    def list_sessions(self) -> list[SessionInfo]:
        # Cleanup exited sessions older than threshold if needed or return all
        return [session.get_info() for session in self.sessions.values()]

    def close_session(self, session_id: str) -> bool:
        session = self.sessions.get(session_id)
        if session:
            session.close()
            del self.sessions[session_id]
            return True
        return False
