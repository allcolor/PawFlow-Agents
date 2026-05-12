from pawflow import pfp

payload = pfp.payload
operation = payload.get("operation", "")
arguments = payload.get("arguments", {})
if operation != "generate":
    pfp.error(f"unsupported operation: {operation}")
else:
    pfp.result({
        "url": "https://example.invalid/generated.png",
        "prompt": arguments.get("prompt", ""),
    })
