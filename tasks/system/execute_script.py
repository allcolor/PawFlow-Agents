# Execute Script Task

"""
Task ExecuteScript - Execute a Python script on a FlowFile's content.

In-process (default) uses the unified sandbox from core.sandbox:
- Safe builtins whitelist
- Module whitelist (json, re, csv, datetime, io, requests, etc.)
- Sandboxed open() backed by FileStore (virtual filesystem)
- Print capture

Containerized (containerize=true) runs the script in a Docker container with
FULL PARITY: the same names are injected (content, attributes, flowfile,
fs, tools, get_service, pawflow). get_service/pawflow/flowfile are proxies that
route each call back to the host over the pfp host-call protocol (stdin/stdout)
and are resolved on the host against THIS flow's declared services, its
scope-bounded pawflow API, and the live FlowFile (Option A: the service stays
on the host, the container holds no secrets). It is a drop-in replacement for
the in-process path: bytes round-trip losslessly (base64) and any non-dunder
operation the in-process script could call stays callable.

Concurrency note for flow authors: get_service() reaches the SAME shared
service instance the in-process path uses, and up to max_instances script
instances run concurrently. A service used this way must tolerate concurrent
calls (e.g. SQLite with check_same_thread is not safe across threads; prefer
Postgres / a connection pool for concurrent moderation workloads).
"""
import logging

from typing import Dict, Any, List
from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask
from core.docker_utils import (
    docker_popen, docker_rm, make_container_name,
    to_host_path as _to_host_path,
)


# Wrapper that runs inside the container. It injects the SAME names as the
# in-process path (content/attributes/fs/tools/get_service/pawflow/flowfile),
# captures user stdout so print() never corrupts the host-call protocol stream,
# and reports the final result (or error) back over the pfp protocol on stdout.
_DOCKER_WRAPPER = '''
import json, sys, io

with open("/data/input.json") as _f:
    _data = json.load(_f)
content = _data["content"]
attributes = _data["attributes"]

from pawflow import (
    fs, tools, get_service,
    script_pawflow as pawflow,
    script_flowfile as flowfile,
    pfp as _pfp,
)
flow_file = flowfile  # alias for parity with the in-process path

# Capture user stdout/stderr; the pfp runtime emits protocol JSON on the real
# stdout it bound at import, so the host-call channel stays clean.
_buf = io.StringIO()
sys.stdout = _buf
sys.stderr = _buf

_ns = {
    "content": content,
    "attributes": attributes,
    "fs": fs,
    "tools": tools,
    "get_service": get_service,
    "pawflow": pawflow,
    "flowfile": flowfile,
    "flow_file": flow_file,
}

_error = None
try:
    with open("/data/user_script.py") as _sf:
        exec(_sf.read(), _ns)
except BaseException as _e:  # report any failure back to the host
    _error = repr(_e)

sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__

if _error is not None:
    _pfp.error(_error)
else:
    if "result" in _ns and _ns["result"] is not None:
        _content_out = str(_ns["result"])
    else:
        _printed = _buf.getvalue().rstrip()
        _content_out = _printed if _printed else None
    _pfp.result({"content": _content_out})
'''

# Caps that keep a malicious/buggy container from exhausting HOST memory: the
# container is memory-limited (--memory) but the host reads its stdout/stderr,
# and up to max_instances scripts run concurrently. Bound both streams.
_MAX_PROTOCOL_LINE = 64 * 1024 * 1024   # 64 MiB per host-call / result line
_MAX_STDERR_CAPTURE = 64 * 1024         # 64 KiB of stderr kept for diagnostics


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

    def _build_pawflow_api(self, flowfile: FlowFile):
        """Build the scope-bounded `pawflow` API facade for this task (or None).

        Shared by the in-process and containerized paths so both inject the
        exact same facade, authorized against this flow's deployment scope.
        """
        try:
            from core.flow_runtime_access import (
                runtime_context_from_task, trusted_requester_user_id)
            from core.flow_pawflow_api import FlowPawflowApi
            return FlowPawflowApi(
                runtime_context_from_task(self),
                requester_user_id=trusted_requester_user_id(flowfile),
                default_runtime_port=str(self.config.get('agent_runtime_port') or ''),
            )
        except Exception:
            logging.getLogger(__name__).debug(
                "pawflow facade injection failed", exc_info=True)
            return None

    def _execute_docker(self, flowfile: FlowFile) -> List[FlowFile]:
        """Execute script in a Docker container with full host-call parity.

        The container injects the same names as `_execute_local`
        (get_service/pawflow/flowfile) as proxies that route every call back to
        the host over the pfp host-call protocol (stdin/stdout). The host
        resolves each call against THIS flow's declared services / scope-bounded
        API / live FlowFile (Option A: the service stays on the host, the
        container holds no secrets).
        """
        import subprocess, json, tempfile, os, threading  # nosec B404

        content = flowfile.get_content().decode('utf-8', errors='replace')
        attributes = dict(flowfile.get_attributes())
        image = self.config.get('docker_image', 'pawflow-relay-dev:latest')
        timeout = int(self.config.get('docker_timeout', 120))

        # Tool relay info for the websocket-based fs/tools SDK (separate channel
        # from the stdin/stdout host-call protocol).
        from core.handlers._fs_base import get_tool_relay_env
        _sdk_env = get_tool_relay_env()
        relay_url = _sdk_env.get("PAWFLOW_TOOL_RELAY_URL", "")
        relay_token = _sdk_env.get("PAWFLOW_TOOL_RELAY_TOKEN", "")
        fs_service = self.config.get('filesystem_service_id', '')

        from core.flow_script_host import (
            FlowScriptHostDispatcher, HOST_CALL_FORMAT, RESULT_FORMAT)
        dispatcher = FlowScriptHostDispatcher(
            services=self._services,
            pawflow_api=self._build_pawflow_api(flowfile),
            flowfile=flowfile,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = os.path.join(tmpdir, "user_script.py")
            wrapper_path = os.path.join(tmpdir, "wrapper.py")
            data_path = os.path.join(tmpdir, "input.json")

            with open(script_path, "w", encoding="utf-8") as f:
                f.write(self.script)
            with open(data_path, "w", encoding="utf-8") as f:
                json.dump({"content": content, "attributes": attributes}, f)
            with open(wrapper_path, "w", encoding="utf-8") as f:
                f.write(_DOCKER_WRAPPER)

            # Bundle the PawFlow SDK next to the wrapper so `import pawflow`
            # resolves to /data/pawflow.py (CPython puts the script dir on
            # sys.path[0]). This makes the host-call proxies available
            # regardless of whether the image bakes/mounts the SDK, and pins
            # the exact SDK version the host speaks to.
            import shutil  # nosec B404
            from pathlib import Path as _Path
            _sdk_src = (_Path(__file__).resolve().parents[2]
                        / "docker" / "pawflow_sdk" / "pawflow.py")
            if _sdk_src.exists():
                shutil.copy2(_sdk_src, os.path.join(tmpdir, "pawflow.py"))

            container_name = make_container_name(
                attributes.get('http.auth.principal', '') or 'flow', 'escript')
            docker_run_args = [
                "--rm", "-i",
                "--name", container_name,
                "-v", f"{_to_host_path(tmpdir)}:/data",
                "--add-host", "host.docker.internal:host-gateway",
                "-e", f"PAWFLOW_TOOL_RELAY_URL={relay_url}",
                "-e", f"PAWFLOW_TOOL_RELAY_TOKEN={relay_token}",
                "-e", f"PAWFLOW_FS_SERVICE={fs_service}",
                "-e", f"PAWFLOW_USER_ID={attributes.get('http.auth.principal', '')}",
                "-e", "PYTHONIOENCODING=utf-8",
                "-e", "PYTHONUNBUFFERED=1",
                "--cpus", "2",
                "--memory", "1g",
                "--security-opt", "no-new-privileges",
                image,
                "python3", "/data/wrapper.py",
            ]

            proc = docker_popen(
                docker_run_args,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace", bufsize=1,
            )

            state = {"timed_out": False}

            def _kill_container():
                state["timed_out"] = True
                # Cancel a host-side pawflow.run_agent the script may be blocked
                # in, so this EXPLICIT timeout actually frees the worker thread
                # (no implicit per-call timeout is added anywhere).
                try:
                    dispatcher.abort()
                except Exception:
                    logging.getLogger(__name__).debug(
                        "timeout dispatcher abort failed", exc_info=True)
                try:
                    docker_rm(container_name)
                except Exception:
                    logging.getLogger(__name__).debug(
                        "timeout container rm failed", exc_info=True)
                try:
                    proc.kill()
                except Exception:
                    logging.getLogger(__name__).debug(
                        "timeout proc kill failed", exc_info=True)

            watchdog = threading.Timer(timeout, _kill_container)
            watchdog.daemon = True
            watchdog.start()

            stderr_chunks: list = []
            stderr_size = {"n": 0}

            def _drain_stderr():
                # Keep draining to avoid a pipe-buffer deadlock, but stop
                # ACCUMULATING past the cap so a noisy container can't grow
                # host memory without bound.
                try:
                    for line in proc.stderr:
                        if stderr_size["n"] < _MAX_STDERR_CAPTURE:
                            stderr_chunks.append(line)
                            stderr_size["n"] += len(line)
                except Exception:
                    logging.getLogger(__name__).debug(
                        "stderr drain failed", exc_info=True)

            stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
            stderr_thread.start()

            final = None
            final_error = None
            try:
                while True:
                    # Bounded read: a single oversized line (no newline) must not
                    # let the container balloon host memory via readline().
                    line = proc.stdout.readline(_MAX_PROTOCOL_LINE + 1)
                    if not line:
                        break  # EOF: container exited
                    if len(line) > _MAX_PROTOCOL_LINE:
                        raise TaskError(
                            "Container emitted an oversized protocol line "
                            f"(> {_MAX_PROTOCOL_LINE} bytes)")
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        env = json.loads(line)
                    except (ValueError, TypeError):
                        continue  # stray output (prints are captured in-container)
                    if not isinstance(env, dict):
                        continue
                    fmt = env.get("format")
                    if fmt == HOST_CALL_FORMAT:
                        response = dispatcher.handle(env)
                        try:
                            proc.stdin.write(
                                json.dumps(response, ensure_ascii=False) + "\n")
                            proc.stdin.flush()
                        except (BrokenPipeError, ValueError, OSError):
                            break
                    elif fmt == RESULT_FORMAT:
                        if env.get("ok", True):
                            final = env.get("result")
                        else:
                            final_error = str(
                                env.get("error") or "container script failed")
                        break
            finally:
                watchdog.cancel()
                try:
                    proc.stdin.close()
                except Exception:
                    logging.getLogger(__name__).debug(
                        "stdin close failed", exc_info=True)
                try:
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        logging.getLogger(__name__).debug(
                            "final proc kill failed", exc_info=True)
                stderr_thread.join(timeout=2)

            stderr_text = "".join(stderr_chunks)[-500:]
            if state["timed_out"]:
                raise TaskError(f"Container script timed out ({timeout}s)")
            if final_error is not None:
                raise TaskError(f"Container script error: {final_error}")
            if final is None:
                raise TaskError(
                    "Container script produced no result "
                    f"(exit {proc.returncode}): {stderr_text}")
            if isinstance(final, dict):
                out_content = final.get("content")
                if out_content is not None:
                    flowfile.set_content(str(out_content).encode("utf-8"))

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
            _pawflow_api = self._build_pawflow_api(flowfile)
            if _pawflow_api is not None:
                local_ns['pawflow'] = _pawflow_api

            # Inject the embedding helper as a host-built callable (bypasses the
            # module whitelist like pawflow/fs). In-process only: a containerized
            # script can't import core.embeddings, so embeddings would need a
            # host-call facade there — out of scope here.
            try:
                from core.embeddings import build_memory_embed_fn as _bmef
                local_ns['build_memory_embed_fn'] = _bmef
            except Exception:
                logging.getLogger(__name__).debug(
                    "embed helper injection failed", exc_info=True)

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
                    "fs (filesystem service), get_service(id) (a service "
                    "declared in this flow, e.g. dbConnectionPool/telegramBot), "
                    "pawflow (scope-bounded PawFlow API: create_conversation/"
                    "run_agent/submit_agent/set_tool_filters/get_extra/set_extra/"
                    "list_conversations/find_conversations/delete_conversation/"
                    "cancel_agent), build_memory_embed_fn() (in-process embedding helper). "
                    "These work identically in-process and when "
                    "containerize=true (then they proxy to the host; only "
                    "JSON-serializable args/results cross, and get_service is "
                    "bounded to services declared in this flow). "
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
