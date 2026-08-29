import asyncio
import socket
import threading
import time
import uuid
from pathlib import Path

import pytest

from remote_cli.client import Client
from remote_cli.daemon import DaemonServer
from remote_cli.protocol import ActionType, Request


@pytest.fixture
def multiplex_server():
    sock_path = Path(f"/tmp/rcli_mp_{uuid.uuid4().hex[:8]}.sock")
    server = DaemonServer(socket_path=sock_path)
    server_loop = asyncio.new_event_loop()

    def run_server():
        asyncio.set_event_loop(server_loop)
        try:
            server_loop.run_until_complete(server.start())
        except Exception:
            pass

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()

    for _ in range(50):
        if sock_path.exists():
            break
        time.sleep(0.05)

    client = Client(socket_path=sock_path)
    assert client.ping() is True

    yield client, sock_path, server

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


def test_realtime_shared_stream(multiplex_server):
    client, sock_path, server = multiplex_server

    # 1. Human creates session
    session_info = client.create_session(command=["/bin/sh"], name="shared-test")
    session_id = session_info.session_id

    # 2. Human terminal attaches via raw streaming socket
    human_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    human_sock.connect(str(sock_path))
    attach_req = Request(action=ActionType.ATTACH, session_id=session_id, rows=24, cols=80)
    human_sock.sendall((attach_req.model_dump_json() + "\n").encode("utf-8"))

    # Read attach acknowledgment
    resp_line = human_sock.recv(1024)
    assert b"ATTACH_STREAM_START" in resp_line

    # 3. Start background reader for human terminal
    received_human_output = bytearray()
    stop_reader = threading.Event()

    def human_terminal_reader():
        human_sock.settimeout(0.5)
        while not stop_reader.is_set():
            try:
                data = human_sock.recv(4096)
                if not data:
                    break
                received_human_output.extend(data)
            except TimeoutError:
                continue
            except Exception:
                break

    reader_thread = threading.Thread(target=human_terminal_reader, daemon=True)
    reader_thread.start()

    time.sleep(0.2)

    # 4. Agent executes a command in the shared session
    agent_result = client.exec_command(session_id, "echo 'AGENT_ACTION_12345'", timeout=5.0)
    assert agent_result.exit_code == 0
    assert "AGENT_ACTION_12345" in agent_result.output

    # 5. Verify human terminal saw the exact same output in real time
    time.sleep(0.3)
    human_output_str = received_human_output.decode("utf-8", errors="replace")
    assert "AGENT_ACTION_12345" in human_output_str

    # 6. Human types in terminal: write directly into human_sock
    human_sock.sendall(b"echo 'HUMAN_INPUT_67890'\n")
    time.sleep(0.3)

    # 7. Agent checks snapshot: both Agent action and Human input are present on screen!
    screen_snapshot = client.snapshot(session_id)
    assert "AGENT_ACTION_12345" in screen_snapshot
    assert "HUMAN_INPUT_67890" in screen_snapshot

    # Teardown
    stop_reader.set()
    reader_thread.join(timeout=1.0)
    human_sock.close()
    client.close_session(session_id)
