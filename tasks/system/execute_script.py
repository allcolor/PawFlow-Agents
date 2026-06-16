# Execute Script Task

"""
Task ExecuteScript - Execute a Python script on a FlowFile's content.

Uses the unified sandbox from core.sandbox:
- Safe builtins whitelist
- Module whitelist (json, re, csv, datetime, io, requests, etc.)
- Sandboxed open() backed by FileStore (virtual filesystem)
- Print capture
"""
import logging

from typing import Dict, Any, List
from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask
from core.docker_utils import docker_run, to_host_path as _to_host_path


class ExecuteScriptTask(BaseTask):
    """Execute a Python script on a FlowFile's content."""

    TYPE = "executeScript"
    VERSION = "2.0.0"
    NAME = "Execute Script"
    DESCRIPTION = "Execute a Python script on a FlowFile's content"
    ICON = "terminal"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.script = self.config.get('script', '')
        self.script_engine = self.config.get('script_engine', 'python')

    def set_runtime_context(self, *, user_id: str = "", conversation_id: str = "",
                            scope: str = "", agent_name: str = ""):
        # Deployment scope is the authorization boundary for the injected
        # `pawflow` API facade (see _execute_local).
        from core.flow_runtime_access import set_runtime_context
        set_runtime_context(
            self, user_id=user_id, conversation_id=conversation_id,
            scope=scope, agent_name=agent_name)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Execute the script on the FlowFile content."""
        if self.config.get('containerize'):
            return self._execute_docker(flowfile)
        return self._execute_local(flowfile)

    def _execute_docker(self, flowfile: FlowFile) -> List[FlowFile]:
        """Execute script inside a Docker container with PawFlow SDK access."""
        import subprocess, json, tempfile, os  # nosec B404

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
            _uid = flowfile.get_attribute("http.auth.principal") or ""
            _cid = flowfile.get_attribute("conversation_id") or ""
            sandbox_open = make_sandbox_open(
                created_files=created_files,
                user_id=_uid, conversation_id=_cid)
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

            # Inject the scope-bounded PawFlow API facade, the same way `fs`
            # and `tools` are injected. It is a host-built object (not an
            # import), so it legitimately bypasses the module whitelist; every
            # operation is authorized against this flow's deployment scope.
            try:
                from core.flow_runtime_access import (
                    runtime_context_from_task, trusted_requester_user_id)
                from core.flow_pawflow_api import FlowPawflowApi
                local_ns['pawflow'] = FlowPawflowApi(
                    runtime_context_from_task(self),
                    requester_user_id=trusted_requester_user_id(flowfile),
                    default_runtime_port=str(self.config.get('agent_runtime_port') or ''),
                )
            except Exception:
                logging.getLogger(__name__).debug(
                    "pawflow facade injection failed", exc_info=True)

            # Inject filesystem service — explicit config or auto-detect first relay
            fs_service_id = self.config.get('filesystem_service_id')
            fs_svc = None
            if fs_service_id:
                fs_svc = self.get_service(fs_service_id)
            if not fs_svc:
                # Auto-detect: prefer connected relay, then any filesystem
                # (conv > user > global so scoped relays are found too)
                try:
                    from core.service_registry import ServiceRegistry
                    _reg = ServiceRegistry.get_instance()
                    for _stype in ("relay", "filesystem"):
                        for _sdef in _reg.resolve_by_type(
                                _stype, user_id=_uid, conv_id=_cid):
                            _s = _reg.get_live_instance(
                                _sdef.scope, _sdef.scope_id, _sdef.service_id)
                            if _s:
                                fs_svc = _s
                                break
                        if fs_svc:
                            break
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            if fs_svc:
                local_ns['fs'] = fs_svc

            # Inject get_service(sid), bounded to THIS flow's declared services
            # (self._services), mirroring how `fs` is injected. Scripts can then
            # reach declared controller services (e.g. a telegramBot or
            # dbConnectionPool) without importing anything. Anything not wired
            # into the flow raises a clear error instead of leaking the registry.
            _declared_services = self._services

            def _sandbox_get_service(service_id):
                svc = _declared_services.get(service_id)
                if svc is None:
                    raise KeyError(
                        "Service '%s' is not declared in this flow's services"
                        % service_id)
                return svc

            local_ns['get_service'] = _sandbox_get_service

            # Execute with a SINGLE namespace (globals is also locals).
            # Passing two separate dicts breaks name resolution inside any
            # function or comprehension the script defines at top level: such
            # callables capture `globals_dict` as their __globals__, so the
            # injected names assigned at the script's top level (flowfile,
            # content, attributes, pawflow, fs, ...) — which would live in the
            # locals dict — are invisible to them and raise NameError
            # (e.g. "name 'flowfile' is not defined" from a helper like
            # _respond()). Merging into globals_dict keeps them visible both
            # at top level and inside script-defined callables.
            globals_dict.update(local_ns)
            exec(self.script, globals_dict)  # nosec B102 - executeScript task is an explicit scripting primitive.

            if 'result' in globals_dict:
                flowfile.set_content(str(globals_dict['result']).encode('utf-8'))
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
                    "Python script to execute. Available variables: "
                    "content (str), attributes (dict), flowfile (FlowFile), "
                    "fs (filesystem service), pawflow (scope-bounded PawFlow "
                    "API: create_conversation/run_agent/submit_agent/"
                    "set_tool_filters/get_extra/set_extra/list_conversations/"
                    "find_conversations/delete_conversation/cancel_agent). "
                    "Set 'result' to modify the FlowFile content. "
                    "open() writes inside a FileStore sandbox. "
                    "Allowed modules: json, re, csv, datetime, math, io, "
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
            'agent_runtime_port': {
                'type': 'string', 'required': False, 'default': '',
                'description': 'Default runtime port for pawflow.run_agent()/submit_agent() (e.g. pawflow_agent.agent_runtime_in)',
            },
        }


TaskFactory.register(ExecuteScriptTask)
