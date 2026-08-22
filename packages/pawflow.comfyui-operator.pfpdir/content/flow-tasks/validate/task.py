import json
import re
from pathlib import Path
from urllib.parse import urlparse

from pawflow import pfp

payload = pfp.payload or {}
task_config = dict(payload.get("task_config") or {})
flowfile = dict(payload.get("flowfile") or {})
attributes = dict(flowfile.get("attributes") or {})
raw = Path(flowfile["content_path"]).read_text(encoding="utf-8").strip()
mode = str(task_config.get("mode") or "video_request").strip().lower()

try:
    document = json.loads(raw) if raw else {}
except json.JSONDecodeError:
    document = raw if mode == "result" else None

errors = []
warnings = []
needs_confirmation = False


def as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def walk_forbidden(value, path="$"):
    forbidden = {
        "api_key",
        "password",
        "secret",
        "shell",
        "script",
        "sudo",
        "token",
        "command",
    }
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key).lower()
            if key_text in forbidden:
                errors.append(f"{path}.{key}: forbidden field")
            walk_forbidden(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            walk_forbidden(child, f"{path}[{index}]")


def require_https(value, label):
    parsed = urlparse(str(value or ""))
    if parsed.scheme != "https" or not parsed.hostname:
        errors.append(f"{label} must be an absolute HTTPS URL")


if mode == "plan":
    if not isinstance(document, dict):
        errors.append("plan input must be a JSON object")
    else:
        plan = document.get("plan")
        if not isinstance(plan, list) or not plan:
            errors.append("plan must be a non-empty array")
        else:
            allowed_actions = {
                "inspect",
                "install_comfyui",
                "configure_route",
                "install_model",
                "install_lora",
                "install_custom_node",
                "restart_comfyui",
                "validate",
                "run_workflow",
                "fetch_outputs",
            }
            for index, step in enumerate(plan):
                if not isinstance(step, dict):
                    errors.append(f"plan[{index}] must be an object")
                    continue
                action = str(step.get("action") or "")
                if action not in allowed_actions:
                    errors.append(f"plan[{index}].action is not allowed: {action}")
                if action in {"install_model", "install_lora"}:
                    require_https(step.get("source"), f"plan[{index}].source")
                    digest = str(step.get("sha256") or "")
                    if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                        errors.append(f"plan[{index}].sha256 must contain 64 hex characters")
                    if not str(step.get("license") or "").strip():
                        errors.append(f"plan[{index}].license is required")
                if action == "install_custom_node":
                    require_https(step.get("source"), f"plan[{index}].source")
                    if not str(step.get("revision") or "").strip():
                        errors.append(f"plan[{index}].revision is required")
        walk_forbidden(document)

elif mode == "workflow":
    workflow = document.get("workflow") if isinstance(document, dict) else None
    bindings = document.get("bindings", {}) if isinstance(document, dict) else {}
    if not isinstance(workflow, dict) or not workflow:
        errors.append("workflow must be a non-empty API-format object")
    else:
        if "nodes" in workflow or "links" in workflow:
            errors.append("UI workflow detected; export Workflow (API) before submission")
        for node_id, node in workflow.items():
            if not isinstance(node, dict):
                errors.append(f"workflow node {node_id} must be an object")
                continue
            if not str(node.get("class_type") or ""):
                errors.append(f"workflow node {node_id} is missing class_type")
            if not isinstance(node.get("inputs"), dict):
                errors.append(f"workflow node {node_id} is missing inputs")
    if bindings and not isinstance(bindings, dict):
        errors.append("bindings must be an object")
    elif isinstance(workflow, dict):
        for binding, target in bindings.items():
            targets = target if isinstance(target, list) else [target]
            for item in targets:
                if not isinstance(item, dict):
                    errors.append(f"binding {binding} target must be an object")
                    continue
                node_id = str(item.get("node") or "")
                input_name = str(item.get("input") or "")
                node = workflow.get(node_id)
                if not isinstance(node, dict):
                    errors.append(f"binding {binding} references unknown node {node_id}")
                elif input_name not in (node.get("inputs") or {}):
                    errors.append(
                        f"binding {binding} references unknown input {node_id}.{input_name}"
                    )
        walk_forbidden({"bindings": bindings})

elif mode == "video_request":
    if not isinstance(document, dict):
        errors.append("video request must be a JSON object")
    else:
        prompt = str(document.get("prompt") or "").strip()
        if not prompt:
            errors.append("prompt is required")
        duration = document.get("duration", 5)
        try:
            duration = float(duration)
            if duration <= 0 or duration > 60:
                errors.append("duration must be greater than 0 and at most 60 seconds")
        except (TypeError, ValueError):
            errors.append("duration must be numeric")
        for key in ("width", "height"):
            try:
                value = int(document.get(key))
                if value < 256 or value > 4096 or value % 8:
                    errors.append(f"{key} must be 256..4096 and divisible by 8")
            except (TypeError, ValueError):
                errors.append(f"{key} must be an integer")
        if document.get("video_url") and document.get("end_image_url"):
            errors.append("end_image_url cannot be combined with video_url")
        try:
            cost = float(document.get("partner_cost_usd") or 0)
            cap = float(
                document.get(
                    "max_partner_cost_usd",
                    task_config.get("max_partner_cost_usd", 0),
                )
                or 0
            )
            if cost > 0:
                if not as_bool(document.get(
                    "allow_partner_api",
                    task_config.get("allow_partner_api", False),
                )):
                    errors.append("partner API use is disabled")
                else:
                    needs_confirmation = True
            if cost > cap:
                errors.append(
                    f"partner cost estimate {cost:.2f} USD exceeds cap {cap:.2f} USD"
                )
        except (TypeError, ValueError):
            errors.append("partner cost values must be numeric")

elif mode == "result":
    if isinstance(document, dict):
        output = (
            document.get("url")
            or document.get("file_url")
            or document.get("output")
            or document.get("result")
        )
    else:
        output = document
    if not str(output or "").strip():
        errors.append("generation result does not contain an output reference")
    warnings.append(
        "Structural validation only; the operate-comfyui skill must run media QA before delivery"
    )

else:
    errors.append(f"unsupported validation mode: {mode}")

valid = not errors
attributes["comfyui.validation.mode"] = mode
attributes["comfyui.valid"] = "true" if valid else "false"
attributes["comfyui.needs_confirmation"] = (
    "true" if needs_confirmation else "false"
)
attributes["comfyui.validation.errors"] = json.dumps(errors, ensure_ascii=False)
result = {
    "mode": mode,
    "valid": valid,
    "needs_confirmation": needs_confirmation,
    "errors": errors,
    "warnings": warnings,
    "document": document,
}
output = document if valid else result
pfp.result(flowfiles=[
    pfp.flowfile(
        json.dumps(output, ensure_ascii=False, sort_keys=True),
        attributes,
    )
])
