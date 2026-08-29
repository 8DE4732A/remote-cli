import json
import os
import re
import subprocess
import sys
import time

import pytest


@pytest.fixture(autouse=True)
def clean_daemon():
    # Use a custom test directory for sockets
    test_dir = f"/tmp/rcli_e2e_{int(time.time())}"
    os.environ["REMOTE_CLI_DIR"] = test_dir
    yield
    # Stop daemon if running
    try:
        subprocess.run(
            [sys.executable, "-m", "remote_cli.cli", "daemon", "stop"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        pass


def run_cli(*args):
    cmd = [sys.executable, "-m", "remote_cli.cli"] + list(args)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_cli_end_to_end():
    # 1. Create detached session
    res = run_cli("session", "create", "-d", "--", "/bin/sh")
    assert res.returncode == 0
    match = re.search(r"(s_[a-f0-9]+)", res.stdout)
    assert match is not None
    session_id = match.group(1)

    time.sleep(0.3)

    # 2. List sessions
    res_list = run_cli("ls")
    assert res_list.returncode == 0
    assert session_id in res_list.stdout

    # 3. Exec command (stdout format)
    res_exec = run_cli("exec", session_id, "echo 'E2E_HELLO_WORLD'")
    assert res_exec.returncode == 0
    assert "E2E_HELLO_WORLD" in res_exec.stdout

    # 4. Exec command (--json format)
    res_json = run_cli("exec", session_id, "expr 21 + 21", "--json")
    assert res_json.returncode == 0
    data = json.loads(res_json.stdout)
    assert data["exit_code"] == 0
    assert "42" in data["output"]

    # 5. Snapshot
    res_snap = run_cli("snapshot", session_id)
    assert res_snap.returncode == 0
    assert "E2E_HELLO_WORLD" in res_snap.stdout or "42" in res_snap.stdout

    # 6. Logs
    res_logs = run_cli("logs", session_id)
    assert res_logs.returncode == 0
    assert "42" in res_logs.stdout

    # 7. Send input
    res_send = run_cli("send", session_id, "echo 'KEYSTROKE'")
    assert res_send.returncode == 0

    # 8. Close session
    res_close = run_cli("session", "close", session_id)
    assert res_close.returncode == 0
    assert f"Session {session_id} closed" in res_close.stdout

    # 9. Stop daemon
    res_stop = run_cli("daemon", "stop")
    assert res_stop.returncode == 0
