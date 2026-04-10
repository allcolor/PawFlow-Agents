# Execute Script Task

"""
Tâche ExecuteScript - Exécuter un script Python sur le contenu d'un FlowFile.

Uses the unified sandbox from core.sandbox:
- Safe builtins whitelist
- Module whitelist (json, re, csv, datetime, io, requests, etc.)
- Sandboxed open() backed by FileStore (virtual filesystem)
- Print capture
"""

from typing import Dict, Any, List
from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask
from core.docker_utils import docker_run, to_host_path as _to_host_path


class ExecuteScriptTask(BaseTask):
    """Exécuter un script Python sur le contenu d'un FlowFile."""

    TYPE = "executeScript"
    VERSION = "2.0.0"
    NAME = "Execute Script"
    DESCRIPTION = "Exécuter un script Python sur le contenu d'un FlowFile"
    ICON = "terminal"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.script = self.config.get('script', '')
        self.script_engine = self.config.get('script_engine', 'python')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Exécuter le script sur le contenu du FlowFile."""
        if self.config.get('containerize'):
            return self._execute_docker(flowfile)
        return self._execute_local(flowfile)

    def _execute_docker(self, flowfile: FlowFile) -> List[FlowFile]:
        """Execute script inside a Docker container with PawFlow SDK access."""
        import subprocess, json, tempfile, os

        content = flowfile.get_content().decode('utf-8', errors='replace')
        attributes = dict(flowfile.get_attributes())
        image = self.config.get('docker_image', 'pawflow-relay-dev:latest')
        timeout = int(self.config.get('docker_timeout', 120))

        # Get tool relay info for SDK access
        from core.handlers._fs_base import get_tool_relay_env
        _sdk_env = get_tool_relay_env()
        relay_url = _sdk_env.get("PAWFLOW_TOOL_RELAY_URL", "")
        relay_token = _sdk_env.get("PAWFLOW_TOOL_RELAY_TOKEN", "")

        # Detect filesystem service for SDK
        fs_service = self.config.get('filesystem_service_id', '')

        # Write wrapper script to temp dir
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write the user script
            script_path = os.path.join(tmpdir, "user_script.py")
            wrapper_path = os.path.join(tmpdir, "wrapper.py")
            data_path = os.path.join(tmpdir, "input.json")
            result_path = os.path.join(tmpdir, "output.json")

            with open(script_path, "w", encoding="utf-8") as f:
                f.write(self.script)

            with open(data_path, "w", encoding="utf-8") as f:
                json.dump({"content": content, "attributes": attributes}, f)

            # Wrapper that sets up context and runs user script
            wrapper = '''
import json, sys, os
with open("/data/input.json") as f:
    _data = json.load(f)
content = _data["content"]
attributes = _data["attributes"]

# PawFlow SDK available via PYTHONPATH
try:
    from pawflow import fs, tools
except ImportError:
    fs = None
    tools = None

# Execute user script
_ns = {"content": content, "attributes": attributes, "fs": fs, "tools": tools}
with open("/data/user_script.py") as f:
    exec(f.read(), _ns)

# Collect result
_result = {}
if "result" in _ns:
    _result["result"] = str(_ns["result"])
with open("/data/output.json", "w") as f:
    json.dump(_result, f)
'''
            with open(wrapper_path, "w", encoding="utf-8") as f:
                f.write(wrapper)

            # Run in Docker
            docker_run_args = [
                "--rm",
                "-v", f"{_to_host_path(tmpdir)}:/data",
                "--add-host", "host.docker.internal:host-gateway",
                "-e", f"PAWFLOW_TOOL_RELAY_URL={relay_url}",
                "-e", f"PAWFLOW_TOOL_RELAY_TOKEN={relay_token}",
                "-e", f"PAWFLOW_FS_SERVICE={fs_service}",
                "-e", f"PAWFLOW_USER_ID={attributes.get('http.auth.principal', '')}",
                "-e", "PYTHONIOENCODING=utf-8",
                "--cpus", "2",
                "--memory", "1g",
                "--security-opt", "no-new-privileges",
                image,
                "python3", "/data/wrapper.py",
            ]

            try:
                proc = docker_run(
                    docker_run_args, capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                    timeout=timeout,
                )
                if proc.returncode != 0:
                    raise TaskError(
                        f"Container script failed (exit {proc.returncode}): "
                        f"{proc.stderr[:500]}")

                # Read result
                if os.path.exists(result_path):
                    with open(result_path, encoding="utf-8") as f:
                        result_data = json.load(f)
                    if "result" in result_data:
                        flowfile.set_content(
                            result_data["result"].encode("utf-8"))

            except subprocess.TimeoutExpired:
                raise TaskError(f"Container script timed out ({timeout}s)")

        return [flowfile]

    def _execute_local(self, flowfile: FlowFile) -> List[FlowFile]:
        """Execute script in-process with Python sandbox."""
        from core.sandbox import build_sandbox_globals, make_sandbox_open

        try:
            created_files: list = []
            sandbox_open = make_sandbox_open(created_files=created_files)
            globals_dict, print_buf = build_sandbox_globals(
                sandbox_open=sandbox_open,
            )

            # Inject FlowFile context into namespace
            content = flowfile.get_content().decode('utf-8', errors='replace')
            attributes = dict(flowfile.get_attributes())
            local_ns = {
                'content': content,
                'attributes': attributes,
                'flowfile': flowfile,
                'flow_file': flowfile,  # alias for compat
            }

            # Inject filesystem service — explicit config or auto-detect first relay
            fs_service_id = self.config.get('filesystem_service_id')
            fs_svc = None
            if fs_service_id:
                fs_svc = self.get_service(fs_service_id)
            if not fs_svc:
                # Auto-detect: prefer connected relay, then any filesystem
                try:
                    from core.service_registry import ServiceRegistry
                    for _sid, _sdef in ServiceRegistry.get_instance().get_all("global", "").items():
                        if getattr(_sdef, "service_type", "") in ("relay", "filesystem"):
                            _s = ServiceRegistry.get_instance().get_live_instance("global", "", _sid)
                            if _s:
                                fs_svc = _s
                                break
                except Exception:
                    pass
            if fs_svc:
                local_ns['fs'] = fs_svc

            exec(self.script, globals_dict, local_ns)

            if 'result' in local_ns:
                flowfile.set_content(str(local_ns['result']).encode('utf-8'))
            elif print_buf:
                flowfile.set_content(
                    "".join(print_buf).rstrip().encode('utf-8'))

            # Record created files as attributes
            if created_files:
                flowfile.set_attribute(
                    'script.created_files',
                    ', '.join(created_files),
                )

            return [flowfile]

        except ImportError as e:
            raise TaskError(f"Blocked by sandbox: {e}")
        except Exception as e:
            raise TaskError(f"Erreur lors de l'exécution du script: {str(e)}")

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'script': {
                'type': 'textarea',
                'required': True,
                'description': (
                    "Script Python à exécuter. Variables disponibles: "
                    "content (str), attributes (dict), flowfile (FlowFile). "
                    "Définir 'result' pour modifier le contenu du FlowFile. "
                    "open() écrit dans un sandbox FileStore. "
                    "Modules autorisés: json, re, csv, datetime, math, io, "
                    "requests, collections, itertools, hashlib, base64, etc."
                ),
            },
            'script_engine': {
                'type': 'select',
                'required': False,
                'description': 'Moteur de script',
                'options': ['python'],
                'default': 'python',
            },
            'filesystem_service_id': {
                'type': 'string', 'required': False,
                'description': 'Filesystem service ID for file access (fs.read_file(), fs.write_file(), etc.)',
            },
            'containerize': {
                'type': 'boolean', 'required': False, 'default': False,
                'description': 'Run script in Docker container for isolation (requires Docker)',
            },
            'docker_image': {
                'type': 'string', 'required': False,
                'default': 'pawflow-relay-dev:latest',
                'description': 'Docker image for containerized execution',
            },
            'docker_timeout': {
                'type': 'integer', 'required': False, 'default': 120,
                'description': 'Timeout in seconds for containerized execution',
            },
        }


TaskFactory.register(ExecuteScriptTask)
