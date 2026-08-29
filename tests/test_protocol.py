from remote_cli.protocol import ActionType, ExecResult, Request, Response


def test_request_serialization():
    req = Request(
        action=ActionType.EXEC_COMMAND,
        session_id="s_12345678",
        command_str="echo hello",
        timeout=15.0,
    )
    json_str = req.model_dump_json()
    parsed = Request.model_validate_json(json_str)
    assert parsed.action == ActionType.EXEC_COMMAND
    assert parsed.session_id == "s_12345678"
    assert parsed.command_str == "echo hello"
    assert parsed.timeout == 15.0


def test_response_serialization():
    resp = Response(
        success=True,
        message="Session created",
        data={"session_id": "s_abc"},
    )
    json_str = resp.model_dump_json()
    parsed = Response.model_validate_json(json_str)
    assert parsed.success is True
    assert parsed.data["session_id"] == "s_abc"


def test_exec_result():
    res = ExecResult(
        session_id="s_123",
        command="uname",
        exit_code=0,
        output="Darwin",
        duration=0.05,
        timed_out=False,
    )
    assert res.exit_code == 0
    assert res.output == "Darwin"
    assert not res.timed_out
