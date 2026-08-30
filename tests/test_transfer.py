import asyncio
import os
import time
import uuid
from pathlib import Path

import pytest

from remote_cli.client import Client
from remote_cli.daemon import DaemonServer
from remote_cli.transfer import (
    create_tar_gz_bytes,
    extract_tar_gz_bytes,
)


@pytest.fixture
def transfer_server():
    sock_path = Path(f"/tmp/rcli_tf_{uuid.uuid4().hex[:8]}.sock")
    server = DaemonServer(socket_path=sock_path)
    server_loop = asyncio.new_event_loop()

    import threading

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

    yield client, server

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


def test_tar_gz_bytes_roundtrip(tmp_path: Path):
    # Test directory archive & extract
    src_dir = tmp_path / "src_dir"
    src_dir.mkdir()
    (src_dir / "file1.txt").write_text("Hello file 1")
    sub = src_dir / "sub"
    sub.mkdir()
    (sub / "file2.bin").write_bytes(b"\x00\x01\x02\x03\x04\xff")

    tar_bytes, is_dir = create_tar_gz_bytes(src_dir)
    assert is_dir is True
    assert len(tar_bytes) > 0

    dest_dir = tmp_path / "dest_dir"
    extract_tar_gz_bytes(tar_bytes, dest_dir)
    assert (dest_dir / "file1.txt").read_text() == "Hello file 1"
    assert (dest_dir / "sub" / "file2.bin").read_bytes() == b"\x00\x01\x02\x03\x04\xff"


def test_session_upload_and_download(transfer_server, tmp_path: Path):
    client, _ = transfer_server

    # Create local shell session
    session_info = client.create_session(command=["/bin/sh"], name="transfer-test")
    session_id = session_info.session_id
    time.sleep(0.2)

    # 1. Upload a single file
    local_file = tmp_path / "upload_test.txt"
    local_file.write_text("Unique Upload Content 12345\nSecond line")

    remote_dest_file = f"/tmp/rcli_dest_{uuid.uuid4().hex[:6]}.txt"
    client.upload_file(session_id, local_file, remote_dest_file)

    # Verify content remotely
    cat_res = client.exec_command(session_id, f"cat {remote_dest_file}", timeout=5.0)
    assert cat_res.exit_code == 0
    assert "Unique Upload Content 12345" in cat_res.output

    # 2. Download the file back to a new local path
    local_download_dest = tmp_path / "downloaded_test.txt"
    client.download_file(session_id, remote_dest_file, local_download_dest)
    assert local_download_dest.exists()
    assert (
        local_download_dest.read_text().strip()
        == "Unique Upload Content 12345\nSecond line".strip()
    )

    # 3. Upload a full directory
    local_pkg = tmp_path / "my_pkg"
    local_pkg.mkdir()
    (local_pkg / "config.yaml").write_text("version: 1.0\napp: test")
    (local_pkg / "data.bin").write_bytes(os.urandom(1024 * 10))  # 10KB binary

    remote_pkg_dir = f"/tmp/rcli_pkg_{uuid.uuid4().hex[:6]}"
    client.upload_file(session_id, local_pkg, remote_pkg_dir)

    # Verify directory contents remotely
    ls_res = client.exec_command(session_id, f"ls {remote_pkg_dir}", timeout=5.0)
    assert ls_res.exit_code == 0
    assert "config.yaml" in ls_res.output
    assert "data.bin" in ls_res.output

    # 4. Download directory back locally
    local_pkg_download = tmp_path / "downloaded_pkg"
    client.download_file(session_id, remote_pkg_dir, local_pkg_download)
    assert (local_pkg_download / "config.yaml").read_text() == "version: 1.0\napp: test"
    assert (local_pkg_download / "data.bin").read_bytes() == (local_pkg / "data.bin").read_bytes()

    # Cleanup remote files
    client.exec_command(session_id, f"rm -rf {remote_dest_file} {remote_pkg_dir}", timeout=5.0)
    client.close_session(session_id)


def test_cli_transfer_commands(transfer_server, tmp_path: Path, monkeypatch):
    client, server = transfer_server
    monkeypatch.setattr("remote_cli.utils.get_socket_path", lambda: server.socket_path)
    monkeypatch.setattr("remote_cli.cli.ensure_daemon_running", lambda: None)
    monkeypatch.setattr("remote_cli.client.get_socket_path", lambda: server.socket_path)
    from typer.testing import CliRunner

    from remote_cli.cli import app

    runner = CliRunner()

    # Create session
    session_info = client.create_session(command=["/bin/sh"], name="cli-transfer")
    sid = session_info.session_id
    time.sleep(0.2)

    # Test upload command
    local_f = tmp_path / "cli_up.txt"
    local_f.write_text("CLI Upload Data")
    remote_f = f"/tmp/rcli_cli_up_{uuid.uuid4().hex[:6]}.txt"

    res_up = runner.invoke(app, ["upload", sid, str(local_f), remote_f])
    assert res_up.exit_code == 0
    assert "Successfully uploaded" in res_up.output

    # Test download command
    local_dl = tmp_path / "cli_dl.txt"
    res_dl = runner.invoke(app, ["download", sid, remote_f, str(local_dl)])
    assert res_dl.exit_code == 0
    assert "Successfully downloaded" in res_dl.output
    assert local_dl.read_text().strip() == "CLI Upload Data"

    # Test cp command (upload syntax)
    local_cp_src = tmp_path / "cli_cp_src.txt"
    local_cp_src.write_text("CP Command Data")
    remote_cp_dest = f"/tmp/rcli_cp_{uuid.uuid4().hex[:6]}.txt"

    res_cp_up = runner.invoke(app, ["cp", str(local_cp_src), f"{sid}:{remote_cp_dest}"])
    assert res_cp_up.exit_code == 0
    assert "Successfully uploaded" in res_cp_up.output

    # Test cp command (download syntax)
    local_cp_dl = tmp_path / "cli_cp_dl.txt"
    res_cp_dl = runner.invoke(app, ["cp", f"{sid}:{remote_cp_dest}", str(local_cp_dl)])
    assert res_cp_dl.exit_code == 0
    assert "Successfully downloaded" in res_cp_dl.output
    assert local_cp_dl.read_text().strip() == "CP Command Data"

    # Cleanup
    client.exec_command(session_id=sid, command=f"rm -f {remote_f} {remote_cp_dest}")
    client.close_session(sid)


def test_ssh_session_injects_control_master(transfer_server):
    client, _ = transfer_server
    # Verify that SessionManager auto-injects ControlMaster for ssh commands
    session_info = client.create_session(
        command=["ssh", "-p", "22", "user@example.com"], name="test-ssh-cm"
    )
    cmd_str = " ".join(session_info.command)
    assert "ControlMaster=auto" in cmd_str
    assert "ControlPath=" in cmd_str
    assert f"{session_info.session_id}.sock" in cmd_str
    client.close_session(session_info.session_id)


def test_control_master_fast_path(tmp_path: Path, monkeypatch):
    import remote_cli.transfer as rt

    # Create dummy socket
    sock_path = tmp_path / "dummy_cm.sock"
    sock_path.touch()

    # Monkeypatch get_cm_socket_path and is_control_master_active
    monkeypatch.setattr("remote_cli.utils.get_cm_socket_path", lambda sid: sock_path)
    monkeypatch.setattr(rt, "is_control_master_active", lambda sid: True)

    assert rt.is_control_master_active("s_test") is True

    # Test TransferManager upload using mock subprocess
    executed_cmds = []

    def mock_run(cmd, *args, **kwargs):
        executed_cmds.append(cmd)
        import subprocess

        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", mock_run)

    dummy_client = None
    tm = rt.TransferManager(dummy_client)

    local_f = tmp_path / "test.txt"
    local_f.write_text("Fast path content")
    tm.upload("s_test", local_f, "/remote/test.txt")

    assert len(executed_cmds) == 1
    assert executed_cmds[0][0] == "scp"
    assert f"ControlPath={sock_path}" in " ".join(executed_cmds[0])
    assert "dummy_host:/remote/test.txt" in " ".join(executed_cmds[0])

    # Test download fast path
    executed_cmds.clear()
    local_dl = tmp_path / "dl.txt"
    tm.download("s_test", "/remote/test.txt", local_dl)

    assert len(executed_cmds) == 1
    assert executed_cmds[0][0] == "scp"
    assert f"ControlPath={sock_path}" in " ".join(executed_cmds[0])
    assert "dummy_host:/remote/test.txt" in " ".join(executed_cmds[0])
