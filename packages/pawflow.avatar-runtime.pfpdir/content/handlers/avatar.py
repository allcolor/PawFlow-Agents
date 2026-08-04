"""Repository handler for the installable PawFlow avatar UI."""

from pawflow import pfp

_RESOURCE_TYPE = "pawflow.avatar"

payload = pfp.payload or {}
arguments = payload.get("arguments", {}) if isinstance(payload, dict) else {}
if not isinstance(arguments, dict):
    raise ValueError("arguments must be an object")
action = str(payload.get("action") or "")

if action == "avatar.list":
    result = pfp.repository.list(_RESOURCE_TYPE)
elif action == "avatar.get":
    name = str(arguments.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")
    result = pfp.repository.get(_RESOURCE_TYPE, name)
elif action == "avatar.create":
    name = str(arguments.get("name") or "").strip()
    document = arguments.get("document")
    if not name:
        raise ValueError("name is required")
    if not isinstance(document, dict):
        raise ValueError("document must be an object")
    result = pfp.repository.create(_RESOURCE_TYPE, name, document)
elif action == "avatar.update":
    name = str(arguments.get("name") or "").strip()
    document = arguments.get("document")
    if not name:
        raise ValueError("name is required")
    if not isinstance(document, dict):
        raise ValueError("document must be an object")
    result = pfp.repository.update(_RESOURCE_TYPE, name, document)
elif action == "avatar.delete":
    name = str(arguments.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")
    result = pfp.repository.delete(_RESOURCE_TYPE, name)
else:
    raise ValueError(f"unsupported avatar action: {action}")

pfp.result(result)
