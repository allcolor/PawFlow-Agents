"""Semantic browser bridge supplied by the PawFlow Avatar Helper package."""

from pawflow import pfp


arguments = pfp.payload.get("arguments", {})
if not isinstance(arguments, dict):
    raise ValueError("arguments must be an object")

operation = str(arguments.get("operation") or "")
package = "pawflow.avatar-helper"
node = f"{package}:ui.guide"

if operation == "list":
    result = pfp.browser.semantic.list(package)
elif operation == "get":
    result = pfp.browser.semantic.get(package, node)
elif operation == "invoke":
    action = str(arguments.get("action") or "")
    if not action:
        raise ValueError("action is required")
    action_arguments = arguments.get("arguments") or {}
    if not isinstance(action_arguments, dict):
        raise ValueError("arguments.arguments must be an object")
    result = pfp.browser.semantic.invoke(
        package, node, action, action_arguments)
else:
    raise ValueError(f"unsupported semantic operation: {operation}")

pfp.result(result)
