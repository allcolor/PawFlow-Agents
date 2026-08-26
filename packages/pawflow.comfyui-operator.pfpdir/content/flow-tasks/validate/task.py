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
    # Substring, not equality: an exact-match denylist is defeated by any
    # suffix (api_key_ref, my_token, shell_cmd all slipped through), which
    # made this read like a control while being trivial to walk past. The
    # real guarantee is still the action allowlist below; this is defence in
    # depth over it.
    forbidden = (
        "api_key",
        "apikey",
        "password",
        "passwd",
        "secret",
        "credential",
        "shell",
        "script",
        "sudo",
        "token",
        "command",
    )
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key).lower()
            hit = next((word for word in forbidden if word in key_text), "")
            if hit:
                errors.append(f"{path}.{key}: forbidden field (matches '{hit}')")
            walk_forbidden(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            walk_forbidden(child, f"{path}[{index}]")


def require_https(value, label):
    parsed = urlparse(str(value or ""))
    if parsed.scheme != "https" or not parsed.hostname:
        errors.append(f"{label} must be an absolute HTTPS URL")


def require_string_array(value, label):
    if not isinstance(value, list):
        errors.append(f"{label} must be an array")
        return
    if any(not isinstance(item, str) or not item.strip() for item in value):
        errors.append(f"{label} must contain only non-empty strings")
    if len(value) != len(set(value)):
        errors.append(f"{label} must not contain duplicates")


def validate_workflow_metadata(metadata, operation):
    label = "metadata"
    if not isinstance(metadata, dict):
        errors.append("metadata must be an object")
        return ""
    for name in ("preset_id", "revision", "created_at", "media_kind"):
        if not str(metadata.get(name) or "").strip():
            errors.append(f"{label}.{name} is required")
    media_kind = str(metadata.get("media_kind") or "")
    if media_kind not in {"image", "video", "audio"}:
        errors.append("metadata.media_kind must be image, video, or audio")
    created_at = str(metadata.get("created_at") or "")
    if created_at and not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T.+(?:Z|[+-]\d{2}:\d{2})", created_at):
        errors.append("metadata.created_at must be a timezone-aware ISO 8601 timestamp")
    provenance = metadata.get("provenance")
    if not isinstance(provenance, dict):
        errors.append("metadata.provenance must be an object")
    else:
        for name in ("source", "license"):
            if not str(provenance.get(name) or "").strip():
                errors.append(f"metadata.provenance.{name} is required")
    require_string_array(metadata.get("capabilities"), "metadata.capabilities")
    limits = metadata.get("limits")
    allowed_limits = {
        "max_duration_seconds", "max_width", "max_height", "max_count",
    }
    if not isinstance(limits, dict):
        errors.append("metadata.limits must be an object")
    else:
        for name, value in limits.items():
            if name not in allowed_limits:
                errors.append(f"metadata.limits contains unsupported key: {name}")
            if (not isinstance(value, (int, float))
                    or isinstance(value, bool) or value <= 0):
                errors.append(f"metadata.limits.{name} must be positive")
    inventory = metadata.get("required_inventory")
    inventory_names = {"nodes", "models", "loras", "custom_nodes"}
    if not isinstance(inventory, dict):
        errors.append("metadata.required_inventory must be an object")
    else:
        for name in inventory_names:
            require_string_array(
                inventory.get(name), f"metadata.required_inventory.{name}")
        for name in set(inventory) - inventory_names:
            errors.append(
                f"metadata.required_inventory contains unsupported key: {name}")
    if not str(operation or "").strip():
        errors.append("operation is required")
    return media_kind


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
    output_spec = document.get("output") if isinstance(document, dict) else None
    operation = document.get("operation") if isinstance(document, dict) else None
    media_kind = validate_workflow_metadata(
        document.get("metadata") if isinstance(document, dict) else None,
        operation,
    )
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
    if not isinstance(output_spec, dict):
        errors.append("output must be an object")
    else:
        output_node = str(output_spec.get("node") or "")
        if not output_node:
            errors.append("output.node is required")
        elif isinstance(workflow, dict) and output_node not in {
                str(node_id) for node_id in workflow}:
            errors.append("output.node is not in the workflow")
        try:
            output_index = int(output_spec.get("index", 0))
            if output_index < 0:
                errors.append("output.index must be non-negative")
        except (TypeError, ValueError):
            errors.append("output.index must be an integer")
        content_types = output_spec.get("content_types")
        if not isinstance(content_types, list) or not content_types:
            errors.append("output.content_types must be a non-empty array")
        elif media_kind and any(
                not isinstance(item, str) or not item.startswith(media_kind + "/")
                for item in content_types):
            errors.append(
                f"output.content_types must contain only {media_kind} media types")

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
