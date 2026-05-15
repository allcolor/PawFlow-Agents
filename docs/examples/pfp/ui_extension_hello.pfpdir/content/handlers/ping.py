from pawflow import pfp

# UI handler triggered by `pfp.call('hello.ping', { message: ... })` in the
# browser. Same isolation as a PFP tool: scrubbed env, no PawFlow tool /
# service surface unless declared in allowed_tools / allowed_services and
# re-authorized by the capability broker at call time.

payload = pfp.payload or {}
args = payload.get("arguments", {}) if isinstance(payload, dict) else {}
message = str(args.get("message") or "")

pfp.result({
    "echo": message,
    "action": payload.get("action", ""),
})
