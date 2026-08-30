"""Client communication layer for remote-cli."""

import json
import socket
from pathlib import Path

from .protocol import (
    ActionType,
    ExecResult,
    Request,
    Response,
    SessionInfo,
)
from .utils import get_socket_path


class Client:
    """Synchronous socket client for sending requests to remote-cli daemon."""

    def __init__(self, socket_path: Path | None = None):
        self.socket_path = socket_path or get_socket_path()

    def _send_request(self, req: Request, timeout: float | None = 35.0) -> Response:
        if not self.socket_path.exists():
            raise ConnectionError(
                f"Daemon socket {self.socket_path} does not exist. Is daemon running?"
            )

        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if timeout:
            s.settimeout(timeout)

        try:
            s.connect(str(self.socket_path))
            payload = (req.model_dump_json() + "\n").encode("utf-8")
            s.sendall(payload)

            # Read response until newline
            chunks = []
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break

            raw = b"".join(chunks).decode("utf-8").strip()
            if not raw:
                raise RuntimeError("Empty response received from daemon")

            resp_data = json.loads(raw)
            return Response.model_validate(resp_data)
        except Exception as e:
            raise RuntimeError(f"IPC communication error: {e}") from e
        finally:
            s.close()

    def ping(self) -> bool:
        """Checks if daemon is reachable and responding."""
        try:
            resp = self._send_request(Request(action=ActionType.PING), timeout=1.0)
            return resp.success and resp.message == "pong"
        except Exception:
            return False

    def create_session(
        self,
        command: list[str],
        name: str | None = None,
        rows: int = 24,
        cols: int = 80,
    ) -> SessionInfo:
        req = Request(
            action=ActionType.CREATE_SESSION,
            command=command,
            name=name,
            rows=rows,
            cols=cols,
        )
        resp = self._send_request(req)
        if not resp.success:
            raise RuntimeError(resp.error or resp.message)
        return SessionInfo.model_validate(resp.data)

    def list_sessions(self) -> list[SessionInfo]:
        req = Request(action=ActionType.LIST_SESSIONS)
        resp = self._send_request(req)
        if not resp.success:
            raise RuntimeError(resp.error or resp.message)
        return [SessionInfo.model_validate(item) for item in (resp.data or [])]

    def get_session(self, session_id: str) -> SessionInfo | None:
        req = Request(action=ActionType.GET_SESSION, session_id=session_id)
        resp = self._send_request(req)
        if not resp.success:
            return None
        return SessionInfo.model_validate(resp.data)

    def close_session(self, session_id: str) -> bool:
        req = Request(action=ActionType.CLOSE_SESSION, session_id=session_id)
        resp = self._send_request(req)
        return resp.success

    def exec_command(
        self,
        session_id: str,
        command: str,
        timeout: float = 30.0,
    ) -> ExecResult:
        req = Request(
            action=ActionType.EXEC_COMMAND,
            session_id=session_id,
            command_str=command,
            timeout=timeout,
        )
        # Give extra buffer to IPC socket timeout
        resp = self._send_request(req, timeout=timeout + 5.0)
        if not resp.success:
            raise RuntimeError(resp.error or resp.message)
        return ExecResult.model_validate(resp.data)

    def send_input(
        self,
        session_id: str,
        text: str | None = None,
        no_newline: bool = False,
        ctrl_c: bool = False,
        ctrl_d: bool = False,
    ) -> bool:
        req = Request(
            action=ActionType.SEND_INPUT,
            session_id=session_id,
            text=text,
            no_newline=no_newline,
            ctrl_c=ctrl_c,
            ctrl_d=ctrl_d,
        )
        resp = self._send_request(req)
        return resp.success

    def snapshot(self, session_id: str, clean: bool = True) -> str:
        req = Request(action=ActionType.SNAPSHOT, session_id=session_id, clean=clean)
        resp = self._send_request(req)
        if not resp.success:
            raise RuntimeError(resp.error or resp.message)
        return resp.data.get("snapshot", "")

    def logs(self, session_id: str, lines: int = 100) -> list[str]:
        req = Request(action=ActionType.LOGS, session_id=session_id, lines=lines)
        resp = self._send_request(req)
        if not resp.success:
            raise RuntimeError(resp.error or resp.message)
        return resp.data.get("lines", [])

    def resize(self, session_id: str, rows: int, cols: int) -> bool:
        req = Request(
            action=ActionType.RESIZE,
            session_id=session_id,
            rows=rows,
            cols=cols,
        )
        resp = self._send_request(req)
        return resp.success

    def upload_file(
        self,
        session_id: str,
        local_path: str | Path,
        remote_path: str,
        progress_callback=None,
    ) -> None:
        """Uploads a local file or directory to remote_path in the session."""
        from .transfer import TransferManager

        tm = TransferManager(self)
        tm.upload(
            session_id=session_id,
            local_path=local_path,
            remote_path=remote_path,
            progress_callback=progress_callback,
        )

    def download_file(
        self,
        session_id: str,
        remote_path: str,
        local_path: str | Path,
        progress_callback=None,
    ) -> None:
        """Downloads a remote file or directory from the session to local_path."""
        from .transfer import TransferManager

        tm = TransferManager(self)
        tm.download(
            session_id=session_id,
            remote_path=remote_path,
            local_path=local_path,
            progress_callback=progress_callback,
        )
