import asyncio

import pytest

from remote_cli.session import Session, SessionManager


@pytest.mark.asyncio
async def test_session_lifecycle_and_exec():
    session = Session(
        session_id="s_test1",
        name="test-session",
        command=["/bin/sh"],
        rows=24,
        cols=80,
    )
    session.start()
    assert session.status == "active"
    assert session.master_fd is not None

    # Wait briefly for shell prompt
    await asyncio.sleep(0.2)

    # Execute a simple echo command
    res = await session.execute_command("echo 'Hello Remote CLI'", timeout=5.0)
    assert not res.timed_out
    assert res.exit_code == 0
    assert "Hello Remote CLI" in res.output

    # Execute a command with non-zero exit code
    res_err = await session.execute_command("false", timeout=5.0)
    assert not res_err.timed_out
    assert res_err.exit_code == 1

    # Check screen snapshot
    snapshot = session.screen.snapshot()
    assert "Hello Remote CLI" in snapshot

    # Close session
    session.close()
    assert session.status == "exited"


@pytest.mark.asyncio
async def test_session_manager():
    sm = SessionManager()
    session = sm.create_session(command=["/bin/sh"], name="managed-test")
    assert session.session_id in sm.sessions

    info_list = sm.list_sessions()
    assert len(info_list) == 1
    assert info_list[0].session_id == session.session_id

    assert sm.get_session(session.session_id) is session

    closed = sm.close_session(session.session_id)
    assert closed is True
    assert session.session_id not in sm.sessions
