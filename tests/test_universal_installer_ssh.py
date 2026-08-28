from pawflow_installer.commands import CommandSpec
from pawflow_installer.models import TargetConfig
from pawflow_installer.transports.base import CommandResult
from pawflow_installer.transports.ssh import SshTransport


class Runner:
    def __init__(self):
        self.commands = []

    def run(self, command, on_output=None):
        self.commands.append(command)
        if any("uname" in argument for argument in command.argv):
            return CommandResult(0, "Linux x86_64\n", "")
        return CommandResult(0, "/usr/bin/docker\n", "")

    def start(self, command, on_output=None):
        self.commands.append(command)
        return 123

    def cancel(self):
        pass


def target(policy="strict"):
    return TargetConfig.model_validate({
        "kind": "ssh",
        "host": "pawflow.example",
        "port": 2222,
        "user": "operator",
        "identity_file": "/keys/pawflow",
        "host_key_policy": policy,
    })


def test_ssh_transport_uses_strict_host_verification_and_separate_argv():
    runner = Runner()
    transport = SshTransport(target(), runner)
    result = transport.run(CommandSpec(("docker", "info"), mutating=False))
    assert result.ok
    argv = runner.commands[0].argv
    assert "StrictHostKeyChecking=yes" in argv
    assert "operator@pawflow.example" in argv
    assert argv[-3:] == ("sh", "-lc", "docker info")


def test_accept_new_host_key_is_only_enabled_by_explicit_policy():
    runner = Runner()
    SshTransport(target("accept-new"), runner).platform()
    assert "StrictHostKeyChecking=accept-new" in runner.commands[0].argv


def test_command_exists_rejects_shell_metacharacters():
    transport = SshTransport(target(), Runner())
    try:
        transport.command_exists("docker;touch")
    except ValueError as exc:
        assert "invalid command" in str(exc)
    else:
        raise AssertionError("unsafe command name was accepted")
