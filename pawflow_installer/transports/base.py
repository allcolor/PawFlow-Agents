"""Transport protocol shared by local and SSH installation targets."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pawflow_installer.commands import CommandSpec

OutputCallback = Callable[[str, str], None]


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class InstallTransport(Protocol):
    def platform(self) -> tuple[str, str]:
        ...

    def command_exists(self, command: str) -> bool:
        ...

    def run(
        self, command: CommandSpec, on_output: OutputCallback | None = None
    ) -> CommandResult:
        ...

    def upload(self, source: Path, destination: str) -> None:
        ...

    def start(
        self, command: CommandSpec, on_output: OutputCallback | None = None
    ) -> int:
        ...

    def cancel(self) -> None:
        ...
