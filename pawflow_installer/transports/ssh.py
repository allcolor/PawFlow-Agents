"""OpenSSH transport with explicit host-key policy and argv-safe commands."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from pawflow_installer.commands import CommandSpec
from pawflow_installer.models import TargetConfig
from pawflow_installer.transports.base import CommandResult, OutputCallback
from pawflow_installer.transports.local import LocalTransport

_COMMAND_RE = re.compile(r"[A-Za-z0-9_.+-]+")


class SshTransport:
    def __init__(self, target: TargetConfig, runner: LocalTransport | None = None):
        if target.kind != "ssh":
            raise ValueError("SshTransport requires an SSH target")
        self.target = target
        self.runner = runner or LocalTransport()

    def _base(self, executable: str) -> list[str]:
        policy = self.target.host_key_policy
        if policy not in {"strict", "accept-new"}:
            raise ValueError("explicit SSH host-key policy is required")
        argv = [
            executable,
            "-p" if executable == "ssh" else "-P",
            str(self.target.port),
            "-o",
            f"StrictHostKeyChecking={'yes' if policy == 'strict' else 'accept-new'}",
        ]
        if self.target.identity_file:
            argv.extend(["-i", self.target.identity_file])
        return argv

    @property
    def destination(self) -> str:
        return f"{self.target.user}@{self.target.host}"

    def _wrap(self, command: CommandSpec) -> CommandSpec:
        remote = shlex.join(command.argv)
        argv = [
            *self._base("ssh"),
            self.destination,
            "--",
            "sh",
            "-lc",
            remote,
        ]
        return CommandSpec(tuple(argv), mutating=command.mutating)

    def platform(self) -> tuple[str, str]:
        result = self.run(
            CommandSpec(("uname", "-s", "-m"), mutating=False)
        )
        if not result.ok:
            raise RuntimeError(result.stderr.strip() or "could not discover SSH target")
        parts = result.stdout.strip().split()
        if len(parts) < 2:
            raise RuntimeError("SSH target returned an invalid platform response")
        system = parts[0].lower()
        if system == "darwin":
            system = "macos"
        return system, parts[1].lower()

    def command_exists(self, command: str) -> bool:
        if not _COMMAND_RE.fullmatch(command):
            raise ValueError("invalid command name")
        result = self.run(
            CommandSpec(("command", "-v", "--", command), mutating=False)
        )
        return result.ok

    def run(
        self, command: CommandSpec, on_output: OutputCallback | None = None
    ) -> CommandResult:
        return self.runner.run(self._wrap(command), on_output)

    def upload(self, source: Path, destination: str) -> None:
        argv = [
            *self._base("scp"),
            str(source),
            f"{self.destination}:{destination}",
        ]
        result = self.runner.run(CommandSpec(tuple(argv), mutating=True))
        if not result.ok:
            raise RuntimeError(result.stderr.strip() or "SCP upload failed")

    def start(
        self, command: CommandSpec, on_output: OutputCallback | None = None
    ) -> int:
        return self.runner.start(self._wrap(command), on_output)

    def cancel(self) -> None:
        self.runner.cancel()
