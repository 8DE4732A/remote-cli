"""IPC protocol definitions and models for remote-cli."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ActionType(StrEnum):
    PING = "ping"
    CREATE_SESSION = "create_session"
    LIST_SESSIONS = "list_sessions"
    GET_SESSION = "get_session"
    CLOSE_SESSION = "close_session"
    EXEC_COMMAND = "exec_command"
    SEND_INPUT = "send_input"
    SNAPSHOT = "snapshot"
    LOGS = "logs"
    RESIZE = "resize"
    ATTACH = "attach"


class SessionInfo(BaseModel):
    session_id: str
    name: str
    command: list[str]
    created_at: str
    status: str  # "active", "exited"
    exit_code: int | None = None
    attached_clients: int = 0
    rows: int = 24
    cols: int = 80


class Request(BaseModel):
    action: ActionType
    session_id: str | None = None
    command: list[str] | None = None
    command_str: str | None = None
    name: str | None = None
    rows: int | None = None
    cols: int | None = None
    text: str | None = None
    no_newline: bool = False
    ctrl_c: bool = False
    ctrl_d: bool = False
    timeout: float | None = 30.0
    lines: int | None = 100
    clean: bool = True
    extra: dict[str, Any] = Field(default_factory=dict)


class Response(BaseModel):
    success: bool
    message: str = ""
    data: Any = None
    error: str | None = None


class ExecResult(BaseModel):
    session_id: str
    command: str
    exit_code: int | None = None
    output: str
    duration: float
    timed_out: bool = False
