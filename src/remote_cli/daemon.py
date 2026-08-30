"""Background daemon server for remote-cli."""

import asyncio
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

from .protocol import ActionType, Request, Response
from .session import SessionManager
from .utils import get_log_path, get_pid_path, get_socket_path

logger = logging.getLogger("remote_cli.daemon")


class DaemonServer:
    """Unix Domain Socket server managing sessions."""

    def __init__(self, socket_path: Path | None = None):
        self.socket_path = socket_path or get_socket_path()
        self.session_manager = SessionManager()
        self.server: asyncio.Server | None = None
        self.running = False

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            line = await reader.readline()
            if not line:
                return

            req_data = json.loads(line.decode("utf-8"))
            req = Request.model_validate(req_data)

            if req.action == ActionType.ATTACH:
                await self._handle_attach(req, reader, writer)
                return

            response = await self._dispatch_request(req)
            resp_bytes = (response.model_dump_json() + "\n").encode("utf-8")
            writer.write(resp_bytes)
            await writer.drain()
        except Exception as e:
            logger.exception("Error handling client request")
            err_resp = Response(success=False, error=str(e))
            try:
                writer.write((err_resp.model_dump_json() + "\n").encode("utf-8"))
                await writer.drain()
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_attach(
        self, req: Request, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        session = self.session_manager.get_session(req.session_id or "")
        if not session or session.status != "active":
            resp = Response(
                success=False,
                error=f"Session {req.session_id} not found or not active",
            )
            writer.write((resp.model_dump_json() + "\n").encode("utf-8"))
            await writer.drain()
            return

        # Send OK acknowledgment
        resp = Response(success=True, message="ATTACH_STREAM_START")
        writer.write((resp.model_dump_json() + "\n").encode("utf-8"))
        await writer.drain()

        # If client passed initial window size, apply it
        if req.rows and req.cols:
            session.resize(req.rows, req.cols)

        # Attach writer to session
        session.attach_client(writer)
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                session.write_input(data)
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            logger.debug(f"Attach stream read exception: {e}")
        finally:
            session.detach_client(writer)

    async def _dispatch_request(self, req: Request) -> Response:
        if req.action == ActionType.PING:
            return Response(success=True, message="pong")

        elif req.action == ActionType.CREATE_SESSION:
            command = req.command or ["/bin/bash"]
            rows = req.rows or 24
            cols = req.cols or 80
            session = self.session_manager.create_session(
                command=command,
                name=req.name,
                rows=rows,
                cols=cols,
            )
            return Response(
                success=True,
                message=f"Session {session.session_id} created",
                data=session.get_info().model_dump(),
            )

        elif req.action == ActionType.LIST_SESSIONS:
            sessions = [s.model_dump() for s in self.session_manager.list_sessions()]
            return Response(success=True, data=sessions)

        elif req.action == ActionType.GET_SESSION:
            session = self.session_manager.get_session(req.session_id or "")
            if not session:
                return Response(success=False, error="Session not found")
            return Response(success=True, data=session.get_info().model_dump())

        elif req.action == ActionType.CLOSE_SESSION:
            closed = self.session_manager.close_session(req.session_id or "")
            if not closed:
                return Response(success=False, error="Session not found")
            return Response(success=True, message=f"Session {req.session_id} closed")

        elif req.action == ActionType.EXEC_COMMAND:
            session = self.session_manager.get_session(req.session_id or "")
            if not session:
                return Response(success=False, error="Session not found")
            if not req.command_str:
                return Response(success=False, error="No command provided to exec")
            result = await session.execute_command(req.command_str, timeout=req.timeout or 30.0)
            return Response(success=True, data=result.model_dump())

        elif req.action == ActionType.SEND_INPUT:
            session = self.session_manager.get_session(req.session_id or "")
            if not session:
                return Response(success=False, error="Session not found")

            payload = b""
            if req.ctrl_c:
                payload = b"\x03"
            elif req.ctrl_d:
                payload = b"\x04"
            elif req.text is not None:
                text = req.text
                if not req.no_newline:
                    text += "\n"
                payload = text.encode("utf-8")

            session.write_input(payload)
            return Response(success=True, message="Input sent")

        elif req.action == ActionType.SNAPSHOT:
            session = self.session_manager.get_session(req.session_id or "")
            if not session:
                return Response(success=False, error="Session not found")
            text = session.screen.snapshot(clean=req.clean)
            return Response(success=True, data={"snapshot": text})

        elif req.action == ActionType.LOGS:
            session = self.session_manager.get_session(req.session_id or "")
            if not session:
                return Response(success=False, error="Session not found")
            lines = session.buffer.get_lines(req.lines or 100)
            return Response(success=True, data={"lines": lines})

        elif req.action == ActionType.RESIZE:
            session = self.session_manager.get_session(req.session_id or "")
            if not session:
                return Response(success=False, error="Session not found")
            if req.rows and req.cols:
                session.resize(req.rows, req.cols)
            return Response(success=True, message="Resized")

        return Response(success=False, error=f"Unknown action: {req.action}")

    async def start(self) -> None:
        """Starts the Unix Domain Socket server."""
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except Exception:
                pass

        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self.server = await asyncio.start_unix_server(
            self.handle_client, path=str(self.socket_path)
        )
        self.running = True

        # Write PID file
        pid_path = get_pid_path()
        pid_path.write_text(str(os.getpid()))

        logger.info(f"remote-cli daemon started at {self.socket_path} (pid: {os.getpid()})")
        async with self.server:
            await self.server.serve_forever()


def run_daemon() -> None:
    """Entry point for daemon process."""
    log_file = get_log_path()
    logging.basicConfig(
        filename=str(log_file),
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    server = DaemonServer()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def handle_signal():
        for task in asyncio.all_tasks(loop):
            task.cancel()
        loop.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_signal)

    try:
        loop.run_until_complete(server.start())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        pid_path = get_pid_path()
        if pid_path.exists():
            pid_path.unlink()
        sock_path = get_socket_path()
        if sock_path.exists():
            sock_path.unlink()


def ensure_daemon_running() -> None:
    """Checks if daemon is running; if not, spawns it in the background."""
    from .client import Client

    client = Client()
    if client.ping():
        return

    # Clean up stale socket/pid
    sock_path = get_socket_path()
    if sock_path.exists():
        try:
            sock_path.unlink()
        except Exception:
            pass

    import subprocess

    cmd = [sys.executable, "-m", "remote_cli.daemon"]
    subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )

    # Wait for daemon to become ready
    for _ in range(30):
        time.sleep(0.1)
        if client.ping():
            return

    raise RuntimeError("Failed to start background remote-cli daemon")


def stop_daemon() -> bool:
    """Stops the running daemon process."""
    pid_path = get_pid_path()
    sock_path = get_socket_path()
    if not pid_path.exists():
        if sock_path.exists():
            try:
                sock_path.unlink()
            except Exception:
                pass
        return False
    try:
        pid = int(pid_path.read_text().strip())
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(20):
                time.sleep(0.1)
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
            else:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        except OSError:
            pass

        if pid_path.exists():
            try:
                pid_path.unlink()
            except Exception:
                pass
        if sock_path.exists():
            try:
                sock_path.unlink()
            except Exception:
                pass
        return True
    except Exception:
        return False


if __name__ == "__main__":
    run_daemon()
