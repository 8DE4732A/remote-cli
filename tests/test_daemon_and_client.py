import asyncio
import threading
import time
import uuid
from pathlib import Path

import pytest

from remote_cli.client import Client
from remote_cli.daemon import DaemonServer


@pytest.fixture
def test_server():
    sock_path = Path(f"/tmp/rcli_{uuid.uuid4().hex[:8]}.sock")
    server = DaemonServer(socket_path=sock_path)
    server_loop = asyncio.new_event_loop()

    def run_server():
        asyncio.set_event_loop(server_loop)
        try:
            server_loop.run_until_complete(server.start())
        except (asyncio.CancelledError, Exception):
            pass

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()

    # Wait for server socket to be created
    for _ in range(50):
        if sock_path.exists():
            break
        time.sleep(0.05)

    client = Client(socket_path=sock_path)
    # Ensure ping succeeds before test
    assert client.ping() is True

    yield client, server

    # Teardown
    for s in list(server.session_manager.sessions.values()):
        s.close()

    def stop_server():
        if server.server:
            server.server.close()
        server_loop.stop()

    server_loop.call_soon_threadsafe(stop_server)
    thread.join(timeout=1.0)
    if sock_path.exists():
        try:
            sock_path.unlink()
        except Exception:
            pass


def test_daemon_ping(test_server):
    client, _ = test_server
    assert client.ping() is True


def test_daemon_session_crud_and_exec(test_server):
    client, _ = test_server

    # Create session
    session_info = client.create_session(command=["/bin/sh"], name="test-sh")
    assert session_info.session_id.startswith("s_")
    assert session_info.status == "active"

    # List sessions
    sessions = client.list_sessions()
    assert len(sessions) == 1
    assert sessions[0].session_id == session_info.session_id

    # Wait for shell to be ready
    time.sleep(0.2)

    # Exec command
    exec_res = client.exec_command(session_info.session_id, "echo 'Testing 123'", timeout=5.0)
    assert exec_res.exit_code == 0
    assert "Testing 123" in exec_res.output

    # Snapshot
    snap = client.snapshot(session_info.session_id)
    assert "Testing 123" in snap

    # Logs
    log_lines = client.logs(session_info.session_id)
    assert any("Testing 123" in line for line in log_lines)

    # Send raw input
    ok = client.send_input(session_info.session_id, text="echo 'Input Sent'")
    assert ok is True

    # Close session
    closed = client.close_session(session_info.session_id)
    assert closed is True

    # Get session should now return None
    assert client.get_session(session_info.session_id) is None
