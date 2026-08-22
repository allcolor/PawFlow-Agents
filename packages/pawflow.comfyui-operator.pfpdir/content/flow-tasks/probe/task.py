import json
from pathlib import Path
from urllib.parse import urlparse

from pawflow import pfp

payload = pfp.payload or {}
task_config = dict(payload.get("task_config") or {})
flowfile = dict(payload.get("flowfile") or {})
attributes = dict(flowfile.get("attributes") or {})
raw = Path(flowfile["content_path"]).read_text(encoding="utf-8").strip()
request = json.loads(raw) if raw else {}
if not isinstance(request, dict):
    pfp.error("ComfyUI probe input must be a JSON object")
    raise SystemExit(1)


def as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


base_url = str(
    task_config.get("base_url")
    or request.get("base_url")
    or "http://127.0.0.1:8188"
).rstrip("/")
parsed = urlparse(base_url)
allow_remote = as_bool(task_config.get("allow_remote", False))
loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"} or (
    parsed.hostname or ""
).startswith("127.")
if parsed.scheme not in {"http", "https"} or not parsed.hostname:
    pfp.error("ComfyUI base_url must be an absolute HTTP(S) URL")
    raise SystemExit(1)
if not loopback and not allow_remote:
    pfp.error("Remote ComfyUI probing requires allow_remote=true")
    raise SystemExit(1)

timeout_seconds = max(1.0, min(float(task_config.get("timeout_seconds", 8)), 30.0))
include_object_info = as_bool(task_config.get("include_object_info", True))
host_script = f"""
import json
import urllib.request

base_url = {base_url!r}
timeout = {timeout_seconds!r}
endpoints = ["/system_stats", "/queue"]
if {include_object_info!r}:
    endpoints.append("/object_info")
result = {{"base_url": base_url, "endpoints": {{}}, "ready": False}}
for endpoint in endpoints:
    try:
        with urllib.request.urlopen(base_url + endpoint, timeout=timeout) as response:
            body = response.read(4 * 1024 * 1024)
            decoded = json.loads(body.decode("utf-8"))
            if endpoint == "/object_info" and isinstance(decoded, dict):
                value = {{"ok": True, "node_count": len(decoded)}}
            else:
                value = {{"ok": True, "data": decoded}}
    except Exception as exc:
        value = {{"ok": False, "error": str(exc)}}
    result["endpoints"][endpoint] = value
result["ready"] = all(
    result["endpoints"].get(name, {{}}).get("ok") is True
    for name in ("/system_stats", "/queue")
)
print(json.dumps(result, ensure_ascii=False))
"""

relay_id = str((pfp.context or {}).get("relay_id") or "")
tool_result = pfp.call_tool(  # nosec B604 - fixed Python probe, no system shell
    "bash",
    command=host_script,
    shell="python",
    local=True,
    relay=relay_id,
    timeout=int((timeout_seconds * 3 + 5) * 1000),
)
if isinstance(tool_result, dict):
    candidate = tool_result.get("result", tool_result.get("content", tool_result))
else:
    candidate = tool_result
if not isinstance(candidate, str):
    candidate = json.dumps(candidate)
try:
    probe = json.loads(candidate)
except (TypeError, json.JSONDecodeError):
    probe = {
        "base_url": base_url,
        "ready": False,
        "error": str(candidate)[:2000],
    }

ready = bool(probe.get("ready"))
attributes["comfyui.ready"] = "true" if ready else "false"
attributes["comfyui.base_url"] = base_url
attributes["comfyui.probe.error"] = str(probe.get("error") or "")
request["probe"] = probe
request["base_url"] = base_url
pfp.result(flowfiles=[
    pfp.flowfile(
        json.dumps(request, ensure_ascii=False, sort_keys=True),
        attributes,
    )
])
