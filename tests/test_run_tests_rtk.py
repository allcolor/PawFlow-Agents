from core.handlers.devops import RunTestsHandler


class RunTestsRelay:
    def __init__(self):
        self.calls = []

    def exec(self, path, command, *args, **kwargs):
        self.calls.append((path, command, args, kwargs))
        if command.startswith("rtk rewrite "):
            return {"stdout": "rtk pytest tests/test_example.py -x -q\n", "stderr": "", "returncode": 0}
        return {"stdout": "1 passed\n", "stderr": "", "returncode": 0}


def _handler():
    handler = RunTestsHandler()
    handler.set_user_id("user-1")
    handler.set_conversation_id("conv-1")
    return handler


def test_run_tests_uses_rtk_rewrite_when_enabled(monkeypatch):
    relay = RunTestsRelay()
    monkeypatch.setenv("PAWFLOW_USE_RTK", "true")
    monkeypatch.setattr("core.handlers._fs_base.find_fs_service", lambda user_id, service_name="": relay)

    result = _handler().execute({"test_files": ["tests/test_example.py"]})

    assert "Tests PASSED" in result
    assert relay.calls[0][1].startswith("rtk rewrite ")
    assert relay.calls[1][1] == "rtk pytest tests/test_example.py -x -q"
    assert relay.calls[0][2] == ()
    assert relay.calls[1][2] == ()


def test_run_tests_does_not_use_rtk_without_env(monkeypatch):
    relay = RunTestsRelay()
    monkeypatch.delenv("PAWFLOW_USE_RTK", raising=False)
    monkeypatch.setattr("core.handlers._fs_base.find_fs_service", lambda user_id, service_name="": relay)

    result = _handler().execute({"test_files": ["tests/test_example.py"]})

    assert "Tests PASSED" in result
    assert len(relay.calls) == 1
    assert relay.calls[0][1] == 'python -m pytest "tests/test_example.py" -x -q --tb=short --no-header'
    assert relay.calls[0][2] == ()


def test_run_tests_accepts_max_output_schema_and_truncates(monkeypatch):
    class LongRelay(RunTestsRelay):
        def exec(self, path, command, *args, **kwargs):
            self.calls.append((path, command, args, kwargs))
            return {"stdout": "x" * 50, "stderr": "", "returncode": 0}

    relay = LongRelay()
    monkeypatch.delenv("PAWFLOW_USE_RTK", raising=False)
    monkeypatch.setattr("core.handlers._fs_base.find_fs_service", lambda user_id, service_name="": relay)

    handler = _handler()
    result = handler.execute({"test_files": ["tests/test_example.py"], "max_output": 10})

    assert "max_output" in handler.parameters_schema["properties"]
    assert "x" * 10 in result
    assert "x" * 11 not in result
    assert "... (truncated)" in result
