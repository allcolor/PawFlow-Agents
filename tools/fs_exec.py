"""Execution actions for the filesystem relay.

Split from fs_actions.py — shell and streaming exec.
"""

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict

from fs_actions import (
    MAX_EXEC_OUTPUT,
    _docker_cmd,
    _resolve_shell,
    _translate_path,
    _to_host_path,
    detect_available_shells,
)


def action_exec(root_dir: str, path: str, req: Dict[str, Any], *,
                allow_exec: bool = False) -> Any:
    """Execute a shell command in the sandbox directory."""
    if not allow_exec:
        raise PermissionError("Shell execution disabled. Start relay with --allow-exec")
    command = req.get("command", "")
    timeout = req.get("timeout")  # None = no limit
    shell_name = req.get("shell", "")  # optional: powershell, bash, python, node, cmd
    if not command:
        raise ValueError("Missing 'command' parameter")
    # Resolve fs:// URLs in the command to real local paths
    root_abs = str(Path(root_dir).resolve())
    _fs_url_pattern = re.compile(r'fs://[^/\s]+/(\S+)')
    command = _fs_url_pattern.sub(
        lambda m: str(Path(root_abs) / m.group(1)).replace("\\", "/"), command)
    # Force UTF-8 output from child process (Windows defaults to cp850/cp1252)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PAWFLOW_FS_ROOT"] = root_abs
    # Inject server-side secrets as environment variables
    _extra_env = req.get("env")
    if isinstance(_extra_env, dict):
        env.update(_extra_env)
    # Relay-level Docker container: exec commands inside the persistent container
    # _DOCKER_CONTAINERS: {root_dir: container_name} — supports multiple relays
    _relay_container = req.get("_docker_container")
    if not _relay_container:
        _containers = globals().get('_DOCKER_CONTAINERS', {})
        _relay_container = _containers.get(root_abs) or globals().get('_DOCKER_EXEC_CONTAINER')
    if _relay_container and not (shell_name and shell_name.startswith("docker-")):
        # Determine shell/interpreter for the container
        if shell_name in ("python", "python3"):
            _container_shell = ["python3", "-c", command]
        elif shell_name == "node":
            _container_shell = ["node", "-e", command]
        else:
            _container_shell = ["bash", "-c", command]
        _docker_env_args = ["-e", "PYTHONIOENCODING=utf-8"]
        if isinstance(_extra_env, dict):
            for _ek, _ev in _extra_env.items():
                _docker_env_args.extend(["-e", f"{_ek}={_ev}"])
        docker_exec_cmd = _docker_cmd() + [
            "exec", "-w", "/workspace",
        ] + _docker_env_args + [
            _relay_container,
        ] + _container_shell
        result = subprocess.run(
            docker_exec_cmd,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout,
        )
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        if len(stdout) > MAX_EXEC_OUTPUT:
            stdout = stdout[:MAX_EXEC_OUTPUT] + f"\n... (truncated)"
        if len(stderr) > MAX_EXEC_OUTPUT:
            stderr = stderr[:MAX_EXEC_OUTPUT] + f"\n... (truncated)"
        return {"stdout": stdout, "stderr": stderr, "returncode": result.returncode}

    # Docker-based execution: docker-python, docker-node, docker-bash
    if shell_name and shell_name.startswith("docker-"):
        _lang = shell_name.split("-", 1)[1]
        _images = {
            "python": "python:3.12-slim",
            "node": "node:22-slim",
            "bash": "ubuntu:24.04",
        }
        _cmds = {
            "python": ["python3", "-c", command],
            "node": ["node", "-e", command],
            "bash": ["bash", "-c", command],
        }
        _image = _images.get(_lang)
        _exec_cmd = _cmds.get(_lang)
        if not _image or not _exec_cmd:
            raise ValueError(f"Unknown docker shell '{shell_name}'. "
                             f"Use docker-python, docker-node, or docker-bash.")
        docker_run_args = [
            "--rm",
            "-v", f"{_translate_path(_to_host_path(root_abs))}:/workspace",
            "-w", "/workspace",
            "-e", "PYTHONIOENCODING=utf-8",
            "--cpus", "2",
            "--memory", "1g",
            "--network", "none",
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m",
            "--security-opt", "no-new-privileges",
            _image,
        ] + _exec_cmd
        result = subprocess.run(
            _docker_cmd() + ["run"] + docker_run_args,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    else:
        # Native execution
        executable = None
        if shell_name:
            executable = _resolve_shell(shell_name)
            if not executable:
                raise ValueError(f"Shell '{shell_name}' not found. "
                                 f"Available: {', '.join(detect_available_shells().keys())}")
        if not executable and os.name == "nt":
            # Default: cmd.exe with UTF-8 codepage
            command = f"chcp 65001 >nul 2>&1 & {command}"
        result = subprocess.run(
            command, shell=True,
            executable=executable,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout,
            cwd=root_dir,
            env=env,
        )
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    if len(stdout) > MAX_EXEC_OUTPUT:
        stdout = stdout[:MAX_EXEC_OUTPUT] + f"\n... (truncated, {len(result.stdout)} bytes total)"
    if len(stderr) > MAX_EXEC_OUTPUT:
        stderr = stderr[:MAX_EXEC_OUTPUT] + f"\n... (truncated, {len(result.stderr)} bytes total)"
    return {
        "stdout": stdout,
        "stderr": stderr,
        "returncode": result.returncode,
    }



def action_exec_stream(root_dir: str, path: str, req: Dict[str, Any], *,
                       allow_exec: bool = False,
                       on_output=None) -> Any:
    """Execute a shell command with streaming output.

    on_output(stream: str, data: str) is called for each line of output.
    stream is "stdout" or "stderr".
    Returns the same dict as action_exec (stdout, stderr, returncode).
    If on_output is None, behaves exactly like action_exec.
    """
    if not allow_exec:
        raise PermissionError("Shell execution disabled. Start relay with --allow-exec")
    command = req.get("command", "")
    timeout = req.get("timeout")  # None = no limit
    shell_name = req.get("shell", "")
    if not command:
        raise ValueError("Missing 'command' parameter")

    root_abs = str(Path(root_dir).resolve())
    _fs_url_pattern = re.compile(r'fs://[^/\s]+/(\S+)')
    command = _fs_url_pattern.sub(
        lambda m: str(Path(root_abs) / m.group(1)).replace("\\", "/"), command)

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PAWFLOW_FS_ROOT"] = root_abs

    # Build Popen args (same logic as action_exec for shell resolution)
    popen_kwargs = dict(
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )

    _relay_container = req.get("_docker_container")
    if not _relay_container:
        _containers = globals().get('_DOCKER_CONTAINERS', {})
        _relay_container = _containers.get(root_abs) or globals().get('_DOCKER_EXEC_CONTAINER')

    if _relay_container and not (shell_name and shell_name.startswith("docker-")):
        if shell_name in ("python", "python3"):
            _container_shell = ["python3", "-c", command]
        elif shell_name == "node":
            _container_shell = ["node", "-e", command]
        else:
            _container_shell = ["bash", "-c", command]
        cmd = _docker_cmd() + [
            "exec", "-w", "/workspace",
            "-e", "PYTHONIOENCODING=utf-8",
            _relay_container,
        ] + _container_shell
        popen_kwargs["shell"] = False
    elif shell_name and shell_name.startswith("docker-"):
        # Docker-based shells — not streamable (run, not exec), fallback to action_exec
        return action_exec(root_dir, path, req, allow_exec=allow_exec)
    else:
        executable = None
        if shell_name:
            executable = _resolve_shell(shell_name)
            if not executable:
                raise ValueError(f"Shell '{shell_name}' not found.")
        if not executable and os.name == "nt":
            command = f"chcp 65001 >nul 2>&1 & {command}"
        cmd = command
        popen_kwargs["shell"] = True
        popen_kwargs["cwd"] = root_dir
        popen_kwargs["env"] = env
        if executable:
            popen_kwargs["executable"] = executable

    proc = subprocess.Popen(cmd if not popen_kwargs.get("shell") else cmd, **popen_kwargs)

    stdout_lines = []
    stderr_lines = []
    total_stdout = 0
    total_stderr = 0
    truncated_out = False
    truncated_err = False

    import selectors
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ, "stdout")
    sel.register(proc.stderr, selectors.EVENT_READ, "stderr")

    import time as _time
    deadline = _time.monotonic() + timeout
    open_streams = 2

    while open_streams > 0:
        remaining = deadline - _time.monotonic()
        if remaining <= 0:
            proc.kill()
            proc.wait()
            raise TimeoutError(f"Command timed out after {timeout}s")
        events = sel.select(timeout=min(remaining, 1.0))
        for key, _ in events:
            stream_name = key.data
            line = key.fileobj.readline()
            if not line:
                sel.unregister(key.fileobj)
                open_streams -= 1
                continue
            if stream_name == "stdout":
                total_stdout += len(line)
                if total_stdout <= MAX_EXEC_OUTPUT:
                    stdout_lines.append(line)
                    if on_output:
                        on_output("stdout", line)
                elif not truncated_out:
                    truncated_out = True
                    if on_output:
                        on_output("stdout", f"\n... (truncating, >{MAX_EXEC_OUTPUT} bytes)\n")
            else:
                total_stderr += len(line)
                if total_stderr <= MAX_EXEC_OUTPUT:
                    stderr_lines.append(line)
                    if on_output:
                        on_output("stderr", line)
                elif not truncated_err:
                    truncated_err = True
                    if on_output:
                        on_output("stderr", f"\n... (truncating, >{MAX_EXEC_OUTPUT} bytes)\n")

    sel.close()
    proc.wait()

    stdout = "".join(stdout_lines)
    stderr = "".join(stderr_lines)
    if truncated_out:
        stdout += f"\n... (truncated, {total_stdout} bytes total)"
    if truncated_err:
        stderr += f"\n... (truncated, {total_stderr} bytes total)"
    return {"stdout": stdout, "stderr": stderr, "returncode": proc.returncode}
