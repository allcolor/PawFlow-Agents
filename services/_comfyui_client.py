"""Strict legacy-API client shared by PawFlow ComfyUI media services."""

from __future__ import annotations

import copy
import json
import logging
import mimetypes
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Dict, Iterable

from core import ServiceError
from core.comfyui_workflow import ComfyWorkflowRevision
from core.relay_proxy_url import (
    CONV_RELAY_EXPR,
    parse_relay_proxy_url,
    relay_proxy_ssl_context,
    resolve_relay_aware_url,
)


logger = logging.getLogger(__name__)


class _ValidatedInputRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Revalidate every media redirect before urllib follows it."""

    def __init__(self, validate):
        super().__init__()
        self._validate = validate

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self._validate(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def raw_config(config: dict, key: str, default=None):
    """Read a LazyResolveDict value without resolving its template early."""
    try:
        return dict.__getitem__(config, key)
    except KeyError:
        return default


def truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def json_object(value, *, field_name: str) -> Dict[str, Any]:
    if isinstance(value, dict):
        return copy.deepcopy(dict(value))
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field_name} must be valid JSON") from exc
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"{field_name} must be a JSON object")


class ComfyUIClient:
    """Submit configured workflows and download their selected artifact."""

    def __init__(self, config: dict, *, media_kind: str):
        if media_kind not in {"image", "video", "audio"}:
            raise ValueError("ComfyUI media_kind must be image, video, or audio")
        self.media_kind = media_kind
        defaults = ({
            "timeout": 0,
            "request_timeout": 60,
            "poll_interval": 1.0,
            "max_input_bytes": 100 * 1024 * 1024,
            "max_output_bytes": 4 * 1024 * 1024 * 1024,
        } if media_kind == "image" else {
            "timeout": 0,
            "request_timeout": 60,
            "poll_interval": 2.0,
            "max_input_bytes": 512 * 1024 * 1024,
            "max_output_bytes": (
                8 * 1024 * 1024 * 1024
                if media_kind == "video" else 4 * 1024 * 1024 * 1024),
        })
        self._raw_base_url = str(raw_config(
            config, "base_url", "relay://MyWorkspace/localhost:8188")
            or "relay://MyWorkspace/localhost:8188").rstrip("/")
        self.allow_private_base_url = truthy(
            config.get("allow_private_base_url", False))
        self.relay_local = truthy(config.get("relay_local", True))
        self.api_key = str(config.get("api_key", "") or "")
        self.api_key_header = str(
            config.get("api_key_header", "Authorization")
            or "Authorization").strip()
        self.api_key_prefix = str(
            config.get("api_key_prefix", "Bearer") or "").strip()
        def configured(key: str):
            value = config.get(key, defaults[key])
            return defaults[key] if value is None or value == "" else value

        self.timeout = int(configured("timeout"))
        self.request_timeout = int(configured("request_timeout"))
        self.poll_interval = float(configured("poll_interval"))
        self.max_input_bytes = int(configured("max_input_bytes"))
        self.max_output_bytes = int(configured("max_output_bytes"))
        if self.timeout < 0:
            raise ValueError("ComfyUI timeout must be zero or positive")
        if self.request_timeout <= 0:
            raise ValueError("ComfyUI request_timeout must be positive")
        if self.poll_interval <= 0:
            raise ValueError("ComfyUI poll_interval must be positive")
        if self.max_input_bytes <= 0 or self.max_output_bytes <= 0:
            raise ValueError("ComfyUI media size limits must be positive")
        self.workflows = json_object(
            raw_config(config, "workflows", None), field_name="workflows")
        if not self.workflows:
            raise ValueError("workflows must define at least one ComfyUI operation")
        self._validate_workflows()
        self._runtime_user_id = ""
        self._runtime_conversation_id = ""
        self._runtime_agent_name = ""
        self._runtime_relay_id = ""
        self._runtime_relay_local = None

    def set_runtime_context(self, *, user_id: str = "",
                            conversation_id: str = "",
                            agent_name: str = "",
                            relay_id: str = "", relay_local=None) -> None:
        self._runtime_user_id = user_id or ""
        self._runtime_conversation_id = conversation_id or ""
        self._runtime_agent_name = agent_name or ""
        self._runtime_relay_id = str(relay_id or "").strip()
        self._runtime_relay_local = relay_local

    def _base_url(self) -> str:
        raw_base_url = self._raw_base_url
        if self._runtime_relay_id:
            raw_base_url = raw_base_url.replace(
                CONV_RELAY_EXPR, self._runtime_relay_id)
            if parse_relay_proxy_url(raw_base_url) is not None:
                parsed = urllib.parse.urlsplit(raw_base_url)
                raw_base_url = urllib.parse.urlunsplit(parsed._replace(
                    netloc=self._runtime_relay_id))
        return resolve_relay_aware_url(
            raw_base_url,
            user_id=self._runtime_user_id,
            conversation_id=self._runtime_conversation_id,
            agent_name=self._runtime_agent_name,
            allow_private=self.allow_private_base_url,
            service_name="ComfyUI",
            transform_relay=True,
            relay_local=(
                self.relay_local if self._runtime_relay_local is None
                else bool(self._runtime_relay_local)),
        )

    def _headers(self, *, json_body: bool = False) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "PawFlow-Agent/1.0",
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        if self.api_key:
            value = self.api_key
            if self.api_key_prefix:
                value = f"{self.api_key_prefix} {value}"
            headers[self.api_key_header] = value
        return headers

    def _validate_workflows(self) -> None:
        revisions = {}
        for operation, preset in self.workflows.items():
            if not isinstance(operation, str) or not operation.strip():
                raise ValueError("ComfyUI workflow operation names must be strings")
            if not isinstance(preset, dict):
                raise ValueError(f"workflows.{operation} must be an object")
            revisions[operation] = ComfyWorkflowRevision.from_preset(
                operation, preset, media_kind=self.media_kind)
            workflow = preset.get("workflow")
            if not isinstance(workflow, dict) or not workflow:
                raise ValueError(
                    f"workflows.{operation}.workflow must be a non-empty API workflow")
            if "nodes" in workflow or "links" in workflow:
                raise ValueError(
                    f"workflows.{operation}.workflow is UI format; export API format")
            for node_id, node in workflow.items():
                if not isinstance(node, dict):
                    raise ValueError(
                        f"workflows.{operation}.workflow.{node_id} must be an object")
                if not isinstance(node.get("class_type"), str):
                    raise ValueError(
                        f"workflows.{operation}.workflow.{node_id}.class_type is required")
                if not isinstance(node.get("inputs"), dict):
                    raise ValueError(
                        f"workflows.{operation}.workflow.{node_id}.inputs must be an object")
            bindings = preset.get("bindings", {})
            if not isinstance(bindings, dict):
                raise ValueError(f"workflows.{operation}.bindings must be an object")
            for source, targets in bindings.items():
                if not isinstance(source, str) or not source:
                    raise ValueError(
                        f"workflows.{operation}.bindings keys must be strings")
                if isinstance(targets, dict):
                    targets = [targets]
                if not isinstance(targets, list) or not targets:
                    raise ValueError(
                        f"workflows.{operation}.bindings.{source} must be a target or list")
                for target in targets:
                    self._validate_binding(operation, workflow, source, target)
            output = preset.get("output")
            if not isinstance(output, dict) or not str(output.get("node", "")):
                raise ValueError(
                    f"workflows.{operation}.output.node is required")
            if str(output["node"]) not in {str(key) for key in workflow}:
                raise ValueError(
                    f"workflows.{operation}.output.node is not in the workflow")
            try:
                index = int(output.get("index", 0))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"workflows.{operation}.output.index must be an integer") from exc
            if index < 0:
                raise ValueError(
                    f"workflows.{operation}.output.index must be non-negative")
            content_types = output.get("content_types")
            if not isinstance(content_types, list) or not content_types:
                raise ValueError(
                    f"workflows.{operation}.output.content_types must be a non-empty array")
            prefix = self.media_kind + "/"
            if any(not isinstance(value, str) or not value.startswith(prefix)
                   for value in content_types):
                raise ValueError(
                    f"workflows.{operation}.output.content_types must contain only "
                    f"{self.media_kind} media types")
        self.workflow_revisions = revisions

    @staticmethod
    def _validate_binding(operation: str, workflow: dict,
                          source: str, target: Any) -> None:
        if not isinstance(target, dict):
            raise ValueError(
                f"workflows.{operation}.bindings.{source} targets must be objects")
        node_id = str(target.get("node", ""))
        input_name = target.get("input")
        node_ids = {str(key): key for key in workflow}
        if node_id not in node_ids or not isinstance(input_name, str) or not input_name:
            raise ValueError(
                f"workflows.{operation}.bindings.{source} requires a valid node and input")
        node = workflow[node_ids[node_id]]
        if input_name not in node["inputs"]:
            raise ValueError(
                f"workflows.{operation}.bindings.{source} targets missing input "
                f"{node_id}.{input_name}")
        if "index" in target:
            try:
                index = int(target["index"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"workflows.{operation}.bindings.{source}.index must be an integer") from exc
            if index < 0:
                raise ValueError(
                    f"workflows.{operation}.bindings.{source}.index must be non-negative")
        if "multiply" in target:
            try:
                float(target["multiply"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"workflows.{operation}.bindings.{source}.multiply must be numeric") from exc
        if target.get("coerce", "") not in {
                "", "int", "float", "string", "bool"}:
            raise ValueError(
                f"workflows.{operation}.bindings.{source}.coerce is invalid")

    def ping(self) -> dict:
        """Validate reachability without mutating the ComfyUI queue."""
        return self.request_json("GET", "/system_stats")

    def has_operation(self, operation: str) -> bool:
        return operation in self.workflows

    def require_operation(self, operation: str) -> dict:
        preset = self.workflows.get(operation)
        if not isinstance(preset, dict):
            configured = ", ".join(sorted(self.workflows))
            raise ServiceError(
                f"ComfyUI operation '{operation}' is not configured; "
                f"configured operations: {configured}")
        return preset

    def request_json(self, method: str, path: str,
                     body: dict | None = None) -> dict:
        base_url = self._base_url()
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            base_url + path, data=data,
            headers=self._headers(json_body=body is not None), method=method)
        try:
            with urllib.request.urlopen(  # nosec B310 - validated configured ComfyUI endpoint.
                    req, timeout=self.request_timeout,
                    context=relay_proxy_ssl_context(base_url)) as resp:
                raw = self._read_control_response(resp)
        except urllib.error.HTTPError as exc:
            detail = exc.read(4096).decode("utf-8", errors="replace")
            raise ServiceError(
                f"ComfyUI API error {method} {path} ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise ServiceError(
                f"ComfyUI API unavailable at {self._raw_base_url}: {exc}") from exc
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ServiceError(
                f"ComfyUI returned non-JSON for {method} {path}: "
                f"{raw[:300].decode('utf-8', errors='replace')}") from exc
        if not isinstance(parsed, dict):
            raise ServiceError(f"ComfyUI returned non-object JSON for {method} {path}")
        return parsed

    @staticmethod
    def _read_control_response(resp, limit: int = 16 * 1024 * 1024) -> bytes:
        chunks = []
        total = 0
        while True:
            chunk = resp.read(min(64 * 1024, limit - total + 1))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise ServiceError("ComfyUI control response exceeded 16 MiB")
        return b"".join(chunks)

    def upload_source(self, source: str, *, index: int = 0) -> str:
        filename, payload, content_type = self._load_source(source, index=index)
        return self._upload_file(filename, payload, content_type)

    def _load_source(self, source: str, *, index: int) -> tuple[str, bytes, str]:
        ref = str(source or "")
        if not ref:
            raise ServiceError("ComfyUI input source is empty")
        if ref.startswith("fs://filestore/") or ref.startswith("/files/"):
            from core.file_store import FileStore
            remainder = (ref[len("fs://filestore/"):]
                         if ref.startswith("fs://filestore/")
                         else ref[len("/files/"):])
            file_id = remainder.split("/", 1)[0]
            filename, payload, content_type = FileStore.instance().get_required(
                file_id, user_id=self._runtime_user_id,
                conversation_id=self._runtime_conversation_id)
            self._check_input_size(len(payload))
            return (filename or f"input-{index}.bin", payload,
                    content_type or "application/octet-stream")
        parsed = urllib.parse.urlparse(ref)
        if parsed.scheme not in {"http", "https"}:
            raise ServiceError("ComfyUI inputs must use HTTP(S) or fs://filestore")
        ref = self._validate_input_url(ref)
        parsed = urllib.parse.urlparse(ref)
        req = urllib.request.Request(
            ref, headers={"User-Agent": "PawFlow-Agent/1.0"})
        opener = urllib.request.build_opener(
            _ValidatedInputRedirectHandler(self._validate_input_url),
            urllib.request.HTTPSHandler(context=relay_proxy_ssl_context(ref)),
        )
        try:
            with opener.open(req, timeout=self.request_timeout) as resp:
                length = int(resp.headers.get("Content-Length", "0") or 0)
                if length:
                    self._check_input_size(length)
                chunks = []
                total = 0
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    self._check_input_size(total)
                    chunks.append(chunk)
                payload = b"".join(chunks)
                content_type = (resp.headers.get("Content-Type", "")
                                or "application/octet-stream")
        except urllib.error.URLError as exc:
            raise ServiceError(f"Could not load ComfyUI input {ref}: {exc}") from exc
        filename = os.path.basename(parsed.path) or f"input-{index}.bin"
        return filename, payload, content_type

    def _validate_input_url(self, url: str) -> str:
        """Reject direct or redirected media URLs that cross the SSRF boundary."""
        return resolve_relay_aware_url(
            url,
            user_id=self._runtime_user_id,
            conversation_id=self._runtime_conversation_id,
            agent_name=self._runtime_agent_name,
            allow_private=False,
            service_name="ComfyUI input",
            transform_relay=False,
        )

    def _check_input_size(self, size: int) -> None:
        if size > self.max_input_bytes:
            raise ServiceError(
                f"ComfyUI input exceeds max_input_bytes ({self.max_input_bytes})")

    def _upload_file(self, filename: str, payload: bytes,
                     content_type: str) -> str:
        safe_name = os.path.basename(filename).replace('"', "") or "input.bin"
        remote_name = f"pawflow-{uuid.uuid4().hex[:12]}-{safe_name}"
        boundary = "----PawFlowComfyUI" + uuid.uuid4().hex
        body = b"".join([
            f"--{boundary}\r\n".encode(),
            (f'Content-Disposition: form-data; name="image"; '
             f'filename="{remote_name}"\r\n').encode(),
            f"Content-Type: {content_type or 'application/octet-stream'}\r\n\r\n".encode(),
            payload,
            b"\r\n",
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n',
            f"--{boundary}--\r\n".encode(),
        ])
        base_url = self._base_url()
        headers = self._headers()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        req = urllib.request.Request(
            base_url + "/upload/image", data=body, headers=headers,
            method="POST")
        try:
            with urllib.request.urlopen(  # nosec B310 - validated configured ComfyUI endpoint.
                    req, timeout=self.request_timeout,
                    context=relay_proxy_ssl_context(base_url)) as resp:
                raw = self._read_control_response(resp)
        except urllib.error.HTTPError as exc:
            detail = exc.read(4096).decode("utf-8", errors="replace")
            raise ServiceError(
                f"ComfyUI upload failed ({exc.code}): {detail}") from exc
        try:
            result = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ServiceError("ComfyUI upload returned non-JSON") from exc
        name = result.get("name") if isinstance(result, dict) else ""
        if not name:
            raise ServiceError(f"ComfyUI upload returned no filename: {str(result)[:300]}")
        subfolder = str(result.get("subfolder") or "")
        return f"{subfolder}/{name}".lstrip("/") if subfolder else str(name)

    def run(self, operation: str, values: Dict[str, Any]) -> dict:
        preset = self.require_operation(operation)
        workflow = copy.deepcopy(preset["workflow"])
        self._apply_bindings(workflow, preset.get("bindings", {}), values)
        client_id = uuid.uuid4().hex
        prompt_id = uuid.uuid4().hex
        submitted = self.request_json("POST", "/prompt", {
            "prompt": workflow,
            "client_id": client_id,
            "prompt_id": prompt_id,
        })
        if submitted.get("error"):
            raise ServiceError(
                f"ComfyUI rejected workflow '{operation}': {submitted['error']}")
        node_errors = submitted.get("node_errors")
        if node_errors:
            raise ServiceError(
                f"ComfyUI workflow '{operation}' has node errors: "
                f"{json.dumps(node_errors)[:1200]}")
        prompt_id = str(submitted.get("prompt_id") or prompt_id)
        history = self._wait_history(prompt_id)
        artifact = self._select_artifact(
            history, preset["output"], prompt_id=prompt_id)
        path, content_type = self._download_artifact(artifact)
        normalized_type = str(content_type or "").split(";", 1)[0].strip().lower()
        declared_types = {
            str(value).split(";", 1)[0].strip().lower()
            for value in preset["output"]["content_types"]
        }
        if normalized_type not in declared_types:
            try:
                os.unlink(path)
            except OSError:
                pass
            raise ServiceError(
                f"ComfyUI workflow '{operation}' returned undeclared content type "
                f"{content_type or '<empty>'}")
        return {
            "path": path,
            "content_type": content_type,
            "prompt_id": prompt_id,
            "artifact": artifact,
        }

    def _apply_bindings(self, workflow: dict, bindings: dict,
                        values: Dict[str, Any]) -> None:
        node_ids = {str(key): key for key in workflow}
        for source, targets in bindings.items():
            if source not in values or values[source] is None:
                continue
            if isinstance(targets, dict):
                targets = [targets]
            for target in targets:
                value = values[source]
                if "index" in target:
                    if not isinstance(value, (list, tuple)):
                        raise ServiceError(
                            f"ComfyUI binding '{source}' requires a list value")
                    index = int(target["index"])
                    if index >= len(value):
                        raise ServiceError(
                            f"ComfyUI binding '{source}' index {index} is missing")
                    value = value[index]
                if "multiply" in target:
                    value = float(value) * float(target["multiply"])
                value = self._coerce(value, target.get("coerce", ""))
                node_key = node_ids[str(target["node"])]
                workflow[node_key]["inputs"][target["input"]] = value

    @staticmethod
    def _coerce(value, coerce: str):
        if coerce == "int":
            return int(round(float(value)))
        if coerce == "float":
            return float(value)
        if coerce == "string":
            return str(value)
        if coerce == "bool":
            return truthy(value)
        return value

    def _wait_history(self, prompt_id: str) -> dict:
        deadline = (
            time.monotonic() + self.timeout if self.timeout > 0 else None)
        while deadline is None or time.monotonic() < deadline:
            history = self.request_json(
                "GET", f"/history/{urllib.parse.quote(prompt_id, safe='')}")
            entry = history.get(prompt_id)
            if entry is None and "outputs" in history:
                entry = history
            if isinstance(entry, dict):
                status = entry.get("status") or {}
                status_text = str(
                    status.get("status_str") or status.get("status") or "").lower()
                if status_text in {"error", "failed", "failure"}:
                    messages = status.get("messages") or entry.get("messages") or []
                    raise ServiceError(
                        f"ComfyUI workflow failed: {json.dumps(messages)[:1200]}")
                if isinstance(entry.get("outputs"), dict):
                    return entry
            time.sleep(self.poll_interval)
        raise ServiceError(
            f"ComfyUI generation timed out after {self.timeout} seconds")

    def _select_artifact(self, history: dict, output: dict,
                         *, prompt_id: str) -> dict:
        outputs = history.get("outputs") or {}
        node = outputs.get(str(output["node"]))
        if node is None:
            node = outputs.get(output["node"])
        if not isinstance(node, dict):
            raise ServiceError(
                f"ComfyUI prompt {prompt_id} produced no configured output node "
                f"{output['node']}")
        key = str(output.get("key") or "")
        candidates: Iterable[Any]
        if key:
            candidates = node.get(key) or []
        else:
            preferred = {
                "image": ["images"],
                "video": ["gifs", "videos", "images"],
                "audio": ["audio", "audios", "files"],
            }[self.media_kind]
            lists = [node.get(name) for name in preferred]
            lists.extend(value for value in node.values()
                         if isinstance(value, list))
            candidates = next((value for value in lists if value), [])
        if not isinstance(candidates, list):
            raise ServiceError(
                f"ComfyUI output {output['node']}.{key or '*'} is not a list")
        index = int(output.get("index", 0))
        if index >= len(candidates) or not isinstance(candidates[index], dict):
            raise ServiceError(
                f"ComfyUI output {output['node']}.{key or '*'}[{index}] is missing")
        artifact = dict(candidates[index])
        if not artifact.get("filename"):
            raise ServiceError("ComfyUI output artifact has no filename")
        return artifact

    def _download_artifact(self, artifact: dict) -> tuple[str, str]:
        query = urllib.parse.urlencode({
            "filename": str(artifact["filename"]),
            "subfolder": str(artifact.get("subfolder") or ""),
            "type": str(artifact.get("type") or "output"),
        })
        base_url = self._base_url()
        req = urllib.request.Request(
            base_url + "/view?" + query,
            headers=self._headers(), method="GET")
        suffix = os.path.splitext(os.path.basename(str(artifact["filename"])))[1]
        if not suffix:
            suffix = {
                "image": ".png", "video": ".mp4", "audio": ".wav",
            }[self.media_kind]
        fd, path = tempfile.mkstemp(prefix="pawflow-comfyui-", suffix=suffix)
        total = 0
        try:
            with os.fdopen(fd, "wb") as out:
                with urllib.request.urlopen(  # nosec B310 - validated configured ComfyUI endpoint.
                        req, timeout=self.request_timeout,
                        context=relay_proxy_ssl_context(base_url)) as resp:
                    content_type = (resp.headers.get("Content-Type", "")
                                    or mimetypes.guess_type(path)[0]
                                    or "application/octet-stream")
                    length = int(resp.headers.get("Content-Length", "0") or 0)
                    if length > self.max_output_bytes:
                        raise ServiceError(
                            "ComfyUI output exceeds max_output_bytes "
                            f"({self.max_output_bytes})")
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > self.max_output_bytes:
                            raise ServiceError(
                                "ComfyUI output exceeds max_output_bytes "
                                f"({self.max_output_bytes})")
                        out.write(chunk)
            return path, content_type
        except Exception:
            try:
                os.unlink(path)
            except OSError:
                pass
            raise
