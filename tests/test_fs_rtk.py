from core.handlers.glob_handler import GlobHandler
from core.handlers.grep_handler import GrepHandler
from core.handlers.read import ReadHandler


class RtkFsRelay:
    TYPE = "relay"
    _service_id = "fs_test"

    def __init__(self, rtk_stdout="rtk output\n", rtk_rc=0):
        self.commands = []
        self.native_calls = []
        self.rtk_stdout = rtk_stdout
        self.rtk_rc = rtk_rc

    def exec(self, path, command, **kwargs):
        self.commands.append((path, command, kwargs))
        return {"stdout": self.rtk_stdout, "stderr": "", "returncode": self.rtk_rc}

    def read_file(self, path, **kwargs):
        self.native_calls.append(("read_file", path, kwargs))
        return b"line one\nline two\n"

    def grep(self, path, pattern, recursive, **kwargs):
        self.native_calls.append(("grep", path, pattern, recursive, kwargs))
        return "native grep\n"

    def search(self, path, pattern, recursive, **kwargs):
        self.native_calls.append(("search", path, pattern, recursive, kwargs))
        return ["native.py"]


def _handler(cls, relay):
    handler = cls()
    handler.set_fs_service(relay)
    handler.set_user_id("user-1")
    handler.set_conversation_id("conv-1")
    handler.set_agent_name("assistant")
    return handler


def test_read_uses_rtk_for_relay_text_output(monkeypatch):
    monkeypatch.setenv("PAWFLOW_USE_RTK", "true")
    relay = RtkFsRelay("compact read\n")
    handler = _handler(ReadHandler, relay)

    result = handler.execute({"source": "fs_test", "path": "README.md", "limit": 20})

    assert result == "compact read\n"
    assert relay.native_calls[0][0] == "read_file"
    assert relay.commands[0][1] == "rtk read README.md --line-numbers --max-lines 20"


def test_grep_stays_native_even_when_rtk_is_enabled(monkeypatch):
    """RTK grep does not preserve PawFlow grep output semantics reliably."""
    monkeypatch.setenv("PAWFLOW_USE_RTK", "true")
    relay = RtkFsRelay("compact grep\n")
    handler = _handler(GrepHandler, relay)

    result = handler.execute({
        "source": "fs_test",
        "path": "core",
        "pattern": "PAWFLOW_USE_RTK",
        "output_mode": "content",
        "head_limit": 10,
    })

    assert result == "native grep\n"
    assert relay.commands == []
    assert relay.native_calls[0][0] == "grep"


def test_glob_stays_native_even_when_rtk_is_enabled(monkeypatch):
    """RTK find does not preserve PawFlow glob semantics for ** patterns."""
    monkeypatch.setenv("PAWFLOW_USE_RTK", "true")
    relay = RtkFsRelay("a.py\nb.py\nc.py\n")
    handler = _handler(GlobHandler, relay)

    result = handler.execute({
        "source": "fs_test",
        "path": ".",
        "pattern": "**/*relay*Dockerfile",
        "limit": 2,
    })

    assert result == "native.py"
    assert relay.commands == []
    assert relay.native_calls[0][0] == "search"


def test_grep_falls_back_when_rtk_fails(monkeypatch):
    monkeypatch.setenv("PAWFLOW_USE_RTK", "true")
    relay = RtkFsRelay("", rtk_rc=127)
    handler = _handler(GrepHandler, relay)

    result = handler.execute({"source": "fs_test", "path": "core", "pattern": "x"})

    assert result == "native grep\n"
    assert relay.native_calls[0][0] == "grep"


def test_grep_does_not_use_rtk_without_env(monkeypatch):
    monkeypatch.delenv("PAWFLOW_USE_RTK", raising=False)
    relay = RtkFsRelay("compact grep\n")
    handler = _handler(GrepHandler, relay)

    result = handler.execute({"source": "fs_test", "path": "core", "pattern": "x"})

    assert result == "native grep\n"
    assert relay.commands == []
    assert relay.native_calls[0][0] == "grep"
