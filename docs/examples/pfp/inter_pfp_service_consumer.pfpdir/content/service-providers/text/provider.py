from pawflow import pfp

payload = pfp.payload
operation = payload.get("operation", "")
arguments = payload.get("arguments", {})
if operation != "clean":
    pfp.error(f"unsupported operation: {operation}")
else:
    normalized = pfp.call_tool(
        "examples.text-core/tool:normalize_text",
        text=arguments.get("text", ""),
        uppercase=bool(arguments.get("uppercase", False)),
    )
    pfp.result({"text": normalized})
