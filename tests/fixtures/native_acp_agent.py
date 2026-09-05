"""Raw JSON-RPC native extension fixture; intentionally independent of SDK routing."""

import json
import sys


def send(value):
    print(json.dumps({"jsonrpc": "2.0", **value}), flush=True)


def result(identifier, value):
    send({"id": identifier, "result": value})


pending = {}
counter = 0
provider = sys.argv[1]
prefix = "" if "plain" in sys.argv else "_"
for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    identifier = message.get("id")
    params = message.get("params", {})
    if method == "initialize":
        result(
            identifier,
            {
                "protocolVersion": 1,
                "agentCapabilities": {"loadSession": True},
                "authMethods": [
                    {
                        "id": "cursor_login" if provider == "cursor" else "cached_token",
                        "name": "Login",
                    }
                ],
            },
        )
    elif method == "authenticate":
        result(identifier, {})
    elif method == "session/new":
        result(identifier, {"sessionId": "native-session"})
    elif method == "session/load":
        result(identifier, {})
    elif method == "session/prompt":
        counter += 1
        reverse_id = f"native-{counter}"
        pending[reverse_id] = (identifier, params)
        if provider == "cursor":
            send(
                {
                    "id": reverse_id,
                    "method": prefix + "cursor/ask_question",
                    "params": {
                        "toolCallId": reverse_id,
                        "questions": [
                            {
                                "id": "q",
                                "prompt": "Choose",
                                "options": [
                                    {"id": "first", "label": "First"},
                                    {"id": "second", "label": "Second"},
                                ],
                            }
                        ],
                    },
                }
            )
        else:
            send(
                {
                    "id": reverse_id,
                    "method": prefix + "x.ai/ask_user_question",
                    "params": {
                        "sessionId": params["sessionId"],
                        "toolCallId": reverse_id,
                        "mode": "default",
                        "questions": [
                            {
                                "id": "q",
                                "question": "Choose",
                                "options": [
                                    {"label": "First"},
                                    {"label": "Second"},
                                ],
                            }
                        ],
                    },
                }
            )
    elif method == "session/cancel":
        for prompt_identifier, _ in pending.values():
            result(prompt_identifier, {"stopReason": "cancelled"})
        pending.clear()
    elif identifier in pending:
        prompt_identifier, request = pending.pop(identifier)
        send(
            {
                "method": "session/update",
                "params": {
                    "sessionId": request["sessionId"],
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {
                            "type": "text",
                            "text": json.dumps(message.get("result", message.get("error"))),
                        },
                    },
                },
            }
        )
        if provider == "cursor":
            result(prompt_identifier, {"stopReason": "end_turn"})
        else:
            send(
                {
                    "method": prefix + "x.ai/session/prompt_complete",
                    "params": {
                        "sessionId": request["sessionId"],
                        "promptId": request["_meta"]["promptId"],
                        "stopReason": "end_turn",
                    },
                }
            )
