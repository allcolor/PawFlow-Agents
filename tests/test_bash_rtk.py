from core.handlers.bash import BashHandler


class RtkRelay:
    TYPE = "relay"
    _service_id = "fs_test"

    def __init__(self, rewrite_stdout="rtk git status --short\n", rewrite_rc=0):
        self.commands = []
        self.rewrite_stdout = rewrite_stdout
        self.rewrite_rc = rewrite_rc

    def exec(self, path, command, **kwargs):
        self.commands.append((path, command, kwargs))
        if command.startswith("rtk rewrite "):
            return {"stdout": self.rewrite_stdout, "stderr": "", "returncode": self.rewrite_rc}
        return {"stdout": f"ran: {command}\n", "stderr": "", "returncode": 0}


def _handler(relay):
    handler = BashHandler()
    handler.set_fs_service(relay)
    handler.set_user_id("user-1")
    handler.set_conversation_id("conv-1")
    return handler


def test_bash_uses_rtk_rewrite_when_enabled(monkeypatch):
    monkeypatch.setenv("PAWFLOW_USE_RTK", "true")
    relay = RtkRelay()
    handler = _handler(relay)

    result = handler.execute({"relay": "fs_test", "command": "git status --short"})

    assert result == "ran: rtk git status --short\n"
    assert relay.commands[0][1] == "rtk rewrite 'git status --short'"
    assert relay.commands[1][1] == "rtk git status --short"


def test_bash_keeps_raw_command_when_rtk_is_disabled(monkeypatch):
    monkeypatch.delenv("PAWFLOW_USE_RTK", raising=False)
    relay = RtkRelay()
    handler = _handler(relay)

    result = handler.execute({"relay": "fs_test", "command": "git status --short"})

    assert result == "ran: git status --short\n"
    assert [command for _path, command, _kwargs in relay.commands] == ["git status --short"]


def test_bash_falls_back_when_rtk_cannot_rewrite(monkeypatch):
    monkeypatch.setenv("PAWFLOW_USE_RTK", "true")
    relay = RtkRelay(rewrite_stdout="", rewrite_rc=1)
    handler = _handler(relay)

    result = handler.execute({"relay": "fs_test", "command": "unknown-tool --flag"})

    assert result == "ran: unknown-tool --flag\n"
    assert relay.commands[0][1] == "rtk rewrite 'unknown-tool --flag'"
    assert relay.commands[1][1] == "unknown-tool --flag"


def test_bash_does_not_rtk_rewrite_non_shell_commands(monkeypatch):
    monkeypatch.setenv("PAWFLOW_USE_RTK", "true")
    relay = RtkRelay()
    handler = _handler(relay)

    result = handler.execute({
        "relay": "fs_test",
        "shell": "python",
        "command": "print('hello')",
    })

    assert result == "ran: print('hello')\n"
    assert [command for _path, command, _kwargs in relay.commands] == ["print('hello')"]
