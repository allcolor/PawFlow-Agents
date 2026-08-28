"""Local process transport with streamed output and immediate cancellation."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess  # nosec B404
import threading
from pathlib import Path

from pawflow_installer.commands import CommandSpec
from pawflow_installer.transports.base import CommandResult, OutputCallback


class LocalTransport:
    def __init__(self):
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._background: dict[int, subprocess.Popen[str]] = {}

    def platform(self) -> tuple[str, str]:
        system = platform.system().lower()
        if system == "darwin":
            system = "macos"
        return system, platform.machine().lower()

    def command_exists(self, command: str) -> bool:
        return shutil.which(command) is not None

    def run(
        self, command: CommandSpec, on_output: OutputCallback | None = None
    ) -> CommandResult:
        environment = os.environ.copy()
        environment.update(command.env)
        process = subprocess.Popen(  # nosec B603
            list(command.argv),
            cwd=command.cwd,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
        )
        with self._lock:
            self._process = process
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        def drain(stream, channel: str, sink: list[str]) -> None:
            if stream is None:
                return
            for line in iter(stream.readline, ""):
                sink.append(line)
                if on_output is not None:
                    on_output(channel, line)
            stream.close()

        readers = [
            threading.Thread(
                target=drain, args=(process.stdout, "stdout", stdout_lines), daemon=True
            ),
            threading.Thread(
                target=drain, args=(process.stderr, "stderr", stderr_lines), daemon=True
            ),
        ]
        for reader in readers:
            reader.start()
        returncode = process.wait()
        for reader in readers:
            reader.join()
        with self._lock:
            if self._process is process:
                self._process = None
        return CommandResult(returncode, "".join(stdout_lines), "".join(stderr_lines))

    def upload(self, source: Path, destination: str) -> None:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    def start(
        self, command: CommandSpec, on_output: OutputCallback | None = None
    ) -> int:
        environment = os.environ.copy()
        environment.update(command.env)
        process = subprocess.Popen(  # nosec B603
            list(command.argv),
            cwd=command.cwd,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
        )
        with self._lock:
            self._background[process.pid] = process

        def drain(stream, channel: str) -> None:
            if stream is None:
                return
            for line in iter(stream.readline, ""):
                if on_output is not None:
                    on_output(channel, line)
            stream.close()

        for stream, channel in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            threading.Thread(target=drain, args=(stream, channel), daemon=True).start()

        def reap() -> None:
            process.wait()
            with self._lock:
                self._background.pop(process.pid, None)

        threading.Thread(target=reap, daemon=True).start()
        return process.pid

    def cancel(self) -> None:
        with self._lock:
            process = self._process
            background = list(self._background.values())
        if process is not None and process.poll() is None:
            process.kill()
        for child in background:
            if child.poll() is None:
                child.kill()
