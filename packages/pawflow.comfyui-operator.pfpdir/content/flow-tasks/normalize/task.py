import json
from pathlib import Path

from pawflow import pfp

payload = pfp.payload or {}
task_config = dict(payload.get("task_config") or {})
flowfile = dict(payload.get("flowfile") or {})
attributes = dict(flowfile.get("attributes") or {})
raw = Path(flowfile["content_path"]).read_text(encoding="utf-8").strip()

try:
    request = json.loads(raw) if raw else {}
except json.JSONDecodeError as exc:
    pfp.error(f"ComfyUI request must be valid JSON: {exc}")
    raise SystemExit(1)

if not isinstance(request, dict):
    pfp.error("ComfyUI request must be a JSON object")
    raise SystemExit(1)

operation = str(
    task_config.get("operation")
    or request.get("operation")
    or "ensure_ready"
).strip().lower().replace("-", "_")
allowed_operations = {
    "ensure_ready",
    "provision_assets",
    "generate_video",
    "validate_video",
}
if operation not in allowed_operations:
    pfp.error(f"Unsupported ComfyUI operation: {operation}")
    raise SystemExit(1)

choice_raw = attributes.get("durable.wait.value", "")
choice = ""
if choice_raw:
    try:
        parsed_choice = json.loads(choice_raw)
        choice = str(parsed_choice if parsed_choice is not None else "")
    except (TypeError, json.JSONDecodeError):
        choice = str(choice_raw)

for key in (
    "base_url",
    "video_service",
    "allow_partner_api",
    "max_partner_cost_usd",
    "qa_enabled",
):
    value = task_config.get(key)
    if value not in (None, "") and key not in request:
        request[key] = value

request["operation"] = operation
request.setdefault("base_url", "http://127.0.0.1:8188")
if operation == "generate_video":
    request.setdefault("negative_prompt", "")
    request.setdefault("duration", 5)
    request.setdefault("width", 1024)
    request.setdefault("height", 576)
    request.setdefault("destination", "filestore")
    request.setdefault("qa_enabled", True)

attributes["comfyui.operation"] = operation
attributes["comfyui.choice"] = choice
attributes["comfyui.request.normalized"] = "true"
pfp.result(flowfiles=[
    pfp.flowfile(
        json.dumps(request, ensure_ascii=False, sort_keys=True),
        attributes,
    )
])
