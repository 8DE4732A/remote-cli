"""Utility functions for remote-cli."""

import os
import re
import uuid
from pathlib import Path

ANSI_ESCAPE_REGEX = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def get_base_dir() -> Path:
    """Returns the base directory for remote-cli configuration and sockets."""
    base = Path(os.environ.get("REMOTE_CLI_DIR", Path.home() / ".remote-cli"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_socket_path() -> Path:
    """Returns the Unix domain socket path."""
    return get_base_dir() / "remote-cli.sock"


def get_pid_path() -> Path:
    """Returns the daemon PID file path."""
    return get_base_dir() / "remote-cli.pid"


def get_log_path() -> Path:
    """Returns the daemon log file path."""
    return get_base_dir() / "daemon.log"


def generate_session_id() -> str:
    """Generates a short, user-friendly session ID."""
    return f"s_{uuid.uuid4().hex[:8]}"


def strip_ansi(text: str) -> str:
    """Strips ANSI escape codes from string."""
    return ANSI_ESCAPE_REGEX.sub("", text)
