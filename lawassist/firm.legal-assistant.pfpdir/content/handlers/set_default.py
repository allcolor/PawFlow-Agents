import json
import os
from pawflow import pfp

ctx = pfp.context or {}
user_id = str(ctx.get("user_id") or "")
args = (pfp.payload or {}).get("arguments", {})
prefs_path = str(args.get("prefs_path") or "/workspace/.legal_prefs.json")
conversation_id = str(args.get("conversation_id") or "").strip()

if not user_id:
    pfp.result({"error": "authentication required"})
elif not conversation_id:
    pfp.result({"error": "conversation_id is required"})
else:
    prefs = {}
    if os.path.exists(prefs_path):
        try:
            with open(prefs_path, "r", encoding="utf-8") as f:
                prefs = json.load(f) or {}
        except (json.JSONDecodeError, OSError):
            prefs = {}
    prefs[user_id] = {"default_conversation_id": conversation_id}
    tmp_path = prefs_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(prefs, f)
    os.replace(tmp_path, prefs_path)
    pfp.result({"default_conversation_id": conversation_id})
