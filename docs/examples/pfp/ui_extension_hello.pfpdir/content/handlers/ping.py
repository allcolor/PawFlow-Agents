from pawflow import pfp

# UI handler triggered by pfp.call('hello.ping', { message: ... }) in the
# browser. Runs in the relay subprocess sandbox — same isolation as a PFP
# tool: no relay token, no PawFlow tool/service surface unless declared in
# `allowed_tools` / `allowed_services` and re-authorized at call time.

payload = pfp.payload or {}
args = payload.get("arguments", {}) if isinstance(payload, dict) else {}
message = str(args.get("message") or "")

pfp.result({
    "echo": message,
    "action": payload.get("action", ""),
})
