"""Relay package-runtime bridge + the relay runner source template.

Split out of core/pfp_runtime.py for the <=800-line rule; re-exported from
core.pfp_runtime (invariant 1: import-path stability).
"""

from __future__ import annotations
import logging
import json
import base64
import shlex
import uuid
import copy
import tempfile
from pathlib import Path
from typing import Any, Dict

from core.pfp_runtime._base import PackageRuntimeError, RUNTIME_INVOKE_FORMAT, RUNTIME_RESULT_FORMAT, _safe_cache_name
from core.pfp_runtime._helpers import _artifact_from_result, _safe_artifact_relpath, _subprocess_env

logger = logging.getLogger(__name__)


class RelayPackageRuntimeBridge:
    """Run package entrypoints inside the selected relay."""

    def invoke(self, request: Dict[str, Any]) -> Any:
        if not isinstance(request, dict) or request.get("format") != RUNTIME_INVOKE_FORMAT:
            raise PackageRuntimeError("invalid PFP runtime invocation envelope")
        package = request.get("package") or {}
        runtime = str(package.get("runtime") or "python")
        if runtime != "python":
            raise PackageRuntimeError(f"unsupported PFP runtime: {runtime}")
        runner = str(package.get("runner") or "")
        if runner != "python":
            raise PackageRuntimeError(f"unsupported PFP runtime runner: {runner}")

        relay = self._resolve_relay(request)
        relay_root = self._relay_package_root(package)
        self._deploy_package(relay, package, relay_root)
        relay_request = self._relay_request(request, relay, relay_root)
        output_dir = str((relay_request.get("context") or {}).get("output_dir") or "")
        if output_dir:
            relay.mkdir(output_dir)
        request_file = f".pawflow/request-{uuid.uuid4().hex}.json"
        relay.write_file(
            f"{relay_root}/{request_file}",
            (json.dumps(relay_request, ensure_ascii=False) + "\n").encode("utf-8"),
        )
        controller = ".pawflow/pfp_relay_runner.py"
        entrypoint = package["entrypoint"]
        command = " ".join([
            "python3",
            shlex.quote(controller),
            shlex.quote(request_file),
            shlex.quote(entrypoint),
        ])
        result = relay.exec(relay_root, command, env=self._controller_env(request))
        stdout = str((result or {}).get("stdout") or "")
        stderr = str((result or {}).get("stderr") or "")
        code = int((result or {}).get("returncode") or 0)
        if code != 0:
            detail = f": {stderr.strip()}" if stderr.strip() else ""
            raise PackageRuntimeError(f"PFP relay runner exited with code {code}{detail}")
        lines = [line for line in stdout.splitlines() if line.strip()]
        if len(lines) != 1:
            raise PackageRuntimeError("PFP relay runner must emit exactly one JSON result line")
        try:
            result = json.loads(lines[0])
        except Exception as exc:
            raise PackageRuntimeError("PFP relay runner did not return JSON") from exc
        if not isinstance(result, dict) or result.get("format") != RUNTIME_RESULT_FORMAT:
            raise PackageRuntimeError("PFP relay runner returned an invalid result envelope")
        self._copy_result_artifacts(relay, relay_request, result, relay_root)
        return result

    def _resolve_relay(self, request: Dict[str, Any]) -> Any:
        context = request.get("context") or {}
        user_id = str(context.get("user_id") or "")
        conversation_id = str(context.get("conversation_id") or "")
        agent_name = str(context.get("agent_name") or "")
        relay_id = str(context.get("relay_id") or context.get("relay") or "").strip()
        if relay_id:
            from core.service_registry import ServiceRegistry
            relay = ServiceRegistry.get_instance().resolve(
                relay_id, user_id=user_id, conv_id=conversation_id)
            if relay is None:
                raise PackageRuntimeError(f"PFP relay is not available: {relay_id}")
            if not hasattr(relay, "exec") or not hasattr(relay, "write_file"):
                raise PackageRuntimeError(f"PFP relay does not support runtime execution: {relay_id}")
            return relay
        if request.get("kind") == "flow_task":
            raise PackageRuntimeError("PFP flow task requires relay parameter")
        if not user_id or not conversation_id:
            raise PackageRuntimeError("PFP runtime requires user_id and conversation_id to resolve the default relay")
        from core.relay_bindings import get_default
        relay_id = get_default(conversation_id, agent=agent_name) or ""
        if not relay_id:
            raise PackageRuntimeError("PFP runtime requires a default relay for this conversation")
        from core.service_registry import ServiceRegistry
        relay = ServiceRegistry.get_instance().resolve(relay_id, user_id=user_id, conv_id=conversation_id)
        if relay is None:
            raise PackageRuntimeError(f"PFP default relay is not available: {relay_id}")
        if not hasattr(relay, "exec") or not hasattr(relay, "write_file"):
            raise PackageRuntimeError(f"PFP default relay does not support runtime execution: {relay_id}")
        return relay

    def _relay_package_root(self, package: Dict[str, Any]) -> str:
        package_id = _safe_cache_name(str(package.get("package") or "package"))
        version = _safe_cache_name(str(package.get("version") or "0"))
        digest = str(package.get("hash") or "").replace("sha256:", "")[:16] or "dev"
        return f".pawflow/pfp/packages/{package_id}@{version}-{digest}"

    def _deploy_package(self, relay: Any, package: Dict[str, Any], relay_root: str) -> None:
        content_dir = Path(str(package.get("content_dir") or "")).resolve()
        if not content_dir.is_dir():
            raise PackageRuntimeError("PFP package content directory is missing")
        relay.mkdir(f"{relay_root}/.pawflow")
        relay.write_file(f"{relay_root}/.pawflow/pfp_relay_runner.py", _RELAY_RUNNER.encode("utf-8"))
        sdk_source = Path(__file__).resolve().parents[1] / "docker" / "pawflow_sdk" / "pawflow.py"
        relay.mkdir(f"{relay_root}/.pawflow/sdk")
        relay.write_file(f"{relay_root}/.pawflow/sdk/pawflow.py", sdk_source.read_bytes())
        for path in sorted(content_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(content_dir).as_posix()
            relay.write_file(f"{relay_root}/{rel}", path.read_bytes())

    def _relay_request(self, request: Dict[str, Any], relay: Any, relay_root: str) -> Dict[str, Any]:
        copied = copy.deepcopy(request)
        context = copied.setdefault("context", {})
        if context.get("output_dir"):
            context["server_output_dir"] = context["output_dir"]
            context["output_dir"] = f"{relay_root}/.pawflow/artifacts/{uuid.uuid4().hex}"
        self._stage_flowfile_payload(copied, relay, relay_root)
        return copied

    def _stage_flowfile_payload(self, request: Dict[str, Any], relay: Any,
                                relay_root: str) -> None:
        if request.get("kind") != "flow_task":
            return
        payload = request.get("payload") or {}
        flowfile = payload.get("flowfile") if isinstance(payload, dict) else None
        if not isinstance(flowfile, dict):
            return
        content = flowfile.pop("_content_bytes", None)
        local_content_path = flowfile.pop("_content_path", "")
        if content is None and not local_content_path:
            return
        content_dir = f"{relay_root}/.pawflow/flowfiles"
        relay.mkdir(content_dir)
        rel_path = f".pawflow/flowfiles/input-{uuid.uuid4().hex}.bin"
        target_path = f"{relay_root}/{rel_path}"
        if local_content_path:
            self._write_relay_file_from_path(relay, target_path, Path(str(local_content_path)))
        else:
            if not isinstance(content, (bytes, bytearray)):
                raise PackageRuntimeError("PFP task flowfile content must be bytes")
            relay.write_file(target_path, bytes(content))
        flowfile["content_path"] = rel_path

    def _write_relay_file_from_path(self, relay: Any, target_path: str,
                                    source_path: Path) -> None:
        source = source_path.expanduser().resolve()
        if not source.is_file():
            raise PackageRuntimeError("PFP task flowfile content_path is missing")
        requester = getattr(relay, "_request", None)
        if callable(requester):
            chunk_size = 1024 * 1024
            total = source.stat().st_size
            if total == 0:
                requester("write_file", target_path, content="", base64=True)
                return
            written = 0
            index = 0
            with source.open("rb") as handle:
                while True:
                    chunk = handle.read(chunk_size)
                    if not chunk:
                        break
                    written += len(chunk)
                    requester(
                        "write_file_chunked", target_path,
                        index=index,
                        data=base64.b64encode(chunk).decode("ascii"),
                        done=written >= total,
                    )
                    index += 1
            return
        relay.write_file(target_path, source.read_bytes())

    def _controller_env(self, request: Dict[str, Any]) -> Dict[str, str]:
        env = _subprocess_env(request)
        from core.handlers._fs_base import get_tool_relay_env
        env.update(get_tool_relay_env())
        env["PAWFLOW_PFP_RELAY_RUNNER"] = "1"
        env["PYTHONPATH"] = ".pawflow/sdk"
        env["PAWFLOW_PFP_SDK_PATH"] = ".pawflow/sdk"
        context = request.get("context") or {}
        env["PAWFLOW_USER_ID"] = str(context.get("user_id") or "")
        env["PAWFLOW_CONVERSATION_ID"] = str(context.get("conversation_id") or "")
        env["PAWFLOW_AGENT_NAME"] = str(context.get("agent_name") or "")
        return env

    def _copy_result_artifacts(self, relay: Any, request: Dict[str, Any],
                               result: Dict[str, Any], relay_root: str) -> None:
        self._copy_result_flowfiles(relay, result, relay_root)
        context = request.get("context") or {}
        relay_output_dir = str(context.get("output_dir") or "")
        server_output_dir = str(context.get("server_output_dir") or "")
        if not relay_output_dir or not server_output_dir:
            return
        artifact = _artifact_from_result(result)
        if not artifact:
            return
        rel = _safe_artifact_relpath(str(artifact.get("path") or ""))
        target = (Path(server_output_dir).resolve() / rel).resolve()
        try:
            target.relative_to(Path(server_output_dir).resolve())
        except ValueError as exc:
            raise PackageRuntimeError("PFP media artifact escapes server output_dir") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        copier = getattr(relay, "copy_file_to_local", None)
        if not callable(copier):
            raise PackageRuntimeError(
                "PFP media artifact relay must support chunked copy_file_to_local")
        copier(f"{relay_output_dir}/{rel}", str(target))

    def _copy_result_flowfiles(self, relay: Any, result: Dict[str, Any],
                               relay_root: str) -> None:
        flowfiles = result.get("flowfiles") if isinstance(result, dict) else None
        if not isinstance(flowfiles, list):
            return
        copier = getattr(relay, "copy_file_to_local", None)
        for item in flowfiles:
            if not isinstance(item, dict) or not item.get("content_path"):
                continue
            if not callable(copier):
                raise PackageRuntimeError(
                    "PFP task flowfile relay must support chunked copy_file_to_local")
            rel = _safe_artifact_relpath(str(item.get("content_path") or ""))
            copied = tempfile.NamedTemporaryFile(prefix="pawflow-pfp-flowfile-", delete=False)
            copied_path = Path(copied.name)
            copied.close()
            copier(f"{relay_root}/{rel}", str(copied_path))
            item["content_path"] = str(copied_path)
            item["content_root"] = str(copied_path.parent)
            item["_delete_content_path"] = True

_RELAY_RUNNER = r'''
import json
import os
import subprocess
import sys
import threading

RUNTIME_INVOKE_FORMAT = "pawflow.package.runtime.invoke.v1"
RUNTIME_RESULT_FORMAT = "pawflow.package.runtime.result.v1"
HOST_CALL_FORMAT = "pawflow.package.runtime.host_call.v1"


def _load_request(path):
    with open(path, "r", encoding="utf-8") as handle:
        request = json.load(handle)
    if not isinstance(request, dict) or request.get("format") != RUNTIME_INVOKE_FORMAT:
        raise RuntimeError("invalid PFP invocation envelope")
    return request


def _child_env():
    blocked = {
        "PAWFLOW_TOOL_RELAY_URL",
        "PAWFLOW_TOOL_RELAY_TOKEN",
        "PAWFLOW_PFP_RELAY_RUNNER",
    }
    env = {k: v for k, v in os.environ.items() if k not in blocked}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = os.environ.get("PAWFLOW_PFP_SDK_PATH", ".pawflow/sdk")
    return env


def _host_response(invocation, host_call):
    try:
        import pawflow
        response = pawflow._request(
            "execute_pfp_host_call",
            invocation=invocation,
            host_call=host_call,
        )
        if isinstance(response, dict) and response.get("format") == RUNTIME_RESULT_FORMAT:
            return response
        return {"format": RUNTIME_RESULT_FORMAT, "ok": True, "result": response}
    except Exception as exc:
        return {"format": RUNTIME_RESULT_FORMAT, "ok": False, "error": str(exc)}


def _emit_result(envelope):
    print(json.dumps(envelope, ensure_ascii=False), flush=True)


def main():
    if len(sys.argv) != 3:
        raise RuntimeError("usage: pfp_relay_runner.py <request.json> <entrypoint.py>")
    request = _load_request(sys.argv[1])
    entrypoint = sys.argv[2]
    proc = subprocess.Popen(
        [sys.executable, entrypoint],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=_child_env(),
    )
    stderr_chunks = []

    def _read_stderr():
        if proc.stderr is None:
            return
        for chunk in proc.stderr:
            stderr_chunks.append(chunk)

    threading.Thread(target=_read_stderr, daemon=True).start()
    assert proc.stdin is not None
    assert proc.stdout is not None
    proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
    proc.stdin.flush()
    results = []
    invalid_output = []
    for line in proc.stdout:
        text = line.strip()
        if not text:
            continue
        try:
            envelope = json.loads(text)
        except Exception:
            invalid_output.append(text)
            continue
        if not isinstance(envelope, dict):
            invalid_output.append(text)
            continue
        fmt = envelope.get("format")
        if fmt == HOST_CALL_FORMAT:
            proc.stdin.write(json.dumps(_host_response(request, envelope), ensure_ascii=False) + "\n")
            proc.stdin.flush()
            continue
        if fmt == RUNTIME_RESULT_FORMAT:
            results.append(envelope)
            continue
        invalid_output.append(text)
    code = proc.wait()
    if code != 0:
        detail = "".join(stderr_chunks).strip()
        _emit_result({
            "format": RUNTIME_RESULT_FORMAT,
            "ok": False,
            "error": f"PFP entrypoint exited with code {code}" + (f": {detail}" if detail else ""),
        })
        return
    if invalid_output:
        _emit_result({
            "format": RUNTIME_RESULT_FORMAT,
            "ok": False,
            "error": "PFP entrypoint emitted non-runtime stdout",
        })
        return
    if len(results) != 1:
        _emit_result({
            "format": RUNTIME_RESULT_FORMAT,
            "ok": False,
            "error": "PFP entrypoint must emit exactly one result envelope",
        })
        return
    _emit_result(results[0])


if __name__ == "__main__":
    main()
'''
