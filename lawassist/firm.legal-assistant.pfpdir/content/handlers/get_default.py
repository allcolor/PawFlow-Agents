import json
import os
from pawflow import pfp

# Per-user "default endpoint" preference (README S9.3 of the plan): which
# conversation the dashboard/web_app should treat as the primary one. Stored
# relay-local, keyed by user_id -- server-side so it survives switching
# between the main PawFlow UI and this dedicated web_app.

ctx = pfp.context or {}
user_id = str(ctx.get("user_id") or "")
args = (pfp.payload or {}).get("arguments", {})
prefs_path = str(args.get("prefs_path") or "/workspace/.legal_prefs.json")

prefs = {}
if os.path.exists(prefs_path):
    try:
        with open(prefs_path, "r", encoding="utf-8") as f:
            prefs = json.load(f) or {}
    except (json.JSONDecodeError, OSError):
        prefs = {}

entry = prefs.get(user_id) or {}
pfp.result({
    "default_conversation_id": entry.get("default_conversation_id") or "",
})
