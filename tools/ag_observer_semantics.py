"""Antigravity observer semantic extractors (split from ag_observer_proxy.py
for <=800 lines). Pure helpers over decoded request/response JSON; no network
or proxy state. Imported back into ag_observer_proxy via a dual (standalone /
package) import."""
from __future__ import annotations

import hashlib
import json


def _redact_header(name: str, value: str) -> str:
    if name.lower() in {"authorization", "cookie", "x-goog-api-key"}:
        return "<redacted>"
    return value


def _extract_text_values(value) -> list[str]:
    out: list[str] = []
    if isinstance(value, dict):
        if value.get("thought") is True:
            return out
        if any(k in value for k in ("functionCall", "function_call", "toolCall", "tool_call",
                                    "functionResponse", "function_response", "toolResult", "tool_result")):
            return out
        for key, item in value.items():
            lkey = str(key).lower()
            if lkey in {"token", "authorization", "credential", "secret", "key", "usage", "usagemetadata"}:
                continue
            if lkey == "text" and isinstance(item, str):
                out.append(item)
                continue
            if lkey in {"content", "message"} and isinstance(item, str):
                out.append(item)
                continue
            out.extend(_extract_text_values(item))
        return out
    if isinstance(value, list):
        for item in value:
            out.extend(_extract_text_values(item))
    return out


def _semantic_model_delta(value) -> dict:
    texts = _extract_text_values(value)
    thinking = _extract_thinking_values(value)
    tool_calls = _extract_tool_calls(value)
    tool_results = _extract_tool_results(value)
    finish_reason = _extract_finish_reason(value)
    usage = _extract_usage(value)
    out = {}
    if texts:
        out["texts"] = texts
        out["text"] = "".join(texts)
    if thinking:
        out["thinking_texts"] = thinking
        out["thinking"] = "".join(thinking)
    if tool_calls:
        out["tool_calls"] = tool_calls
    if tool_results:
        out["tool_results"] = tool_results
    if finish_reason:
        out["finish_reason"] = finish_reason
    if usage:
        out["usage"] = usage
    return out


def _latest_request_tool_results(value) -> list[dict]:
    """Extract tool results from the latest Antigravity request input only."""
    payload = value.get("request") if isinstance(value, dict) else None
    root = payload if isinstance(payload, dict) else value
    contents = root.get("contents") if isinstance(root, dict) else None
    if not isinstance(contents, list):
        contents = root.get("messages") if isinstance(root, dict) else None
    if not isinstance(contents, list):
        return _standard_tool_results(_extract_tool_results(root))
    for message in reversed(contents):
        if not isinstance(message, dict):
            continue
        results = _extract_tool_results(message)
        if results:
            return _standard_tool_results(results)
        for content in _extract_tool_output_texts(message):
            return [{
                "tool_use_id": "",
                "name": "pawflow/use_tool",
                "content": content,
                "tool_origin": "mcp",
            }]
    return []


def _extract_tool_output_texts(value) -> list[str]:
    """Find Antigravity text-encoded tool outputs in request contents."""
    out: list[str] = []

    def visit(item) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if str(key).lower() == "text" and isinstance(child, str):
                    if _looks_like_tool_output(child):
                        out.append(child)
                    continue
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return out


def _looks_like_tool_output(text: str) -> bool:
    if not text:
        return False
    markers = (
        "Created At:",
        "Completed At:",
        "[Result cleared",
        "tool_result_",
    )
    if any(marker in text for marker in markers):
        return True
    return False


def _standard_tool_results(results: list[dict]) -> list[dict]:
    out = []
    for result in results:
        if not isinstance(result, dict):
            continue
        out.append({
            "tool_use_id": result.get("tool_use_id", ""),
            "name": result.get("name", ""),
            "content": result.get("content", ""),
            "tool_origin": result.get("tool_origin", ""),
        })
    return out


def _semantic_user_prompt(value) -> str:
    """Extract the latest user-authored text from an Antigravity request body."""
    prompts: list[str] = []

    def visit(item) -> None:
        if isinstance(item, dict):
            role = str(item.get("role") or item.get("author") or "").lower()
            if role == "user":
                text = "".join(_extract_text_values(item)).strip()
                if text:
                    prompts.append(text)
                return
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return prompts[-1] if prompts else ""


def _stable_tool_id(prefix: str, value) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _json_dict(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {"value": value}
    return value if isinstance(value, dict) else {}


def _clean_name_part(value) -> str:
    text = str(value or "")
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            decoded = json.loads(text)
            if isinstance(decoded, str):
                return decoded
        except (TypeError, ValueError):
            pass
    return text


def _normalize_tool_call(name: str, args: dict) -> tuple[str, dict, str]:
    raw_name = str(name or "")
    payload = args if isinstance(args, dict) else {}
    if raw_name.startswith("pawflow/"):
        return raw_name, payload, "mcp"
    if raw_name != "call_mcp_tool":
        return raw_name, payload, "native"
    server_name = _clean_name_part(
        payload.get("ServerName") or payload.get("serverName")
        or payload.get("server_name") or "")
    tool_name = _clean_name_part(
        payload.get("ToolName") or payload.get("toolName")
        or payload.get("tool_name") or "")
    inner = (
        payload.get("Arguments") if "Arguments" in payload
        else payload.get("arguments", payload.get("Parameters", payload.get("parameters", {})))
    )
    display_name = f"{server_name}/{tool_name}" if server_name and tool_name else tool_name
    return display_name, _json_dict(inner), "mcp"


def _tool_result_content(response) -> str:
    if isinstance(response, dict):
        for key in ("output", "content", "result"):
            if key in response:
                value = response.get(key)
                return value if isinstance(value, str) else json.dumps(
                    value, ensure_ascii=False, default=str)
    return response if isinstance(response, str) else json.dumps(
        response, ensure_ascii=False, default=str)


def _extract_tool_calls(value) -> list[dict]:
    out = []
    if isinstance(value, dict):
        for key in ("functionCall", "function_call", "toolCall", "tool_call"):
            call = value.get(key)
            if isinstance(call, dict):
                name = call.get("name") or call.get("tool") or ""
                args = call.get("args") or call.get("arguments") or call.get("input") or {}
                args = _json_dict(args)
                name, args, origin = _normalize_tool_call(str(name or ""), args)
                if origin == "mcp" and not name:
                    continue
                out.append({
                    "id": str(call.get("id") or call.get("tool_call_id") or _stable_tool_id("ag_tool", call)),
                    "name": str(name or ""),
                    "arguments": args,
                    "tool_origin": origin,
                })
        for item in value.values():
            out.extend(_extract_tool_calls(item))
    elif isinstance(value, list):
        for item in value:
            out.extend(_extract_tool_calls(item))
    return out


def _extract_tool_results(value) -> list[dict]:
    out = []
    if isinstance(value, dict):
        for key in ("functionResponse", "function_response", "toolResult", "tool_result"):
            result = value.get(key)
            if isinstance(result, dict):
                response = result.get("response") or result.get("result") or result.get("content") or ""
                raw_name = str(result.get("name") or result.get("tool") or "")
                name, _args, origin = _normalize_tool_call(
                    raw_name, result)
                if raw_name == "call_mcp_tool" and origin == "mcp" and not name:
                    name = "pawflow/use_tool"
                if origin == "mcp" and not name:
                    continue
                out.append({
                    "tool_use_id": str(result.get("id") or result.get("tool_call_id") or _stable_tool_id("ag_tool", result)),
                    "name": str(name or ""),
                    "content": _tool_result_content(response),
                    "tool_origin": origin,
                })
        for item in value.values():
            out.extend(_extract_tool_results(item))
    elif isinstance(value, list):
        for item in value:
            out.extend(_extract_tool_results(item))
    return out


def _extract_thinking_values(value) -> list[str]:
    out = []
    if isinstance(value, dict):
        if value.get("thought") is True and isinstance(value.get("text"), str):
            out.append(value.get("text") or "")
            return out
        for key, item in value.items():
            lkey = str(key).lower()
            if lkey in {"thinking", "reasoning", "thought"} and isinstance(item, str):
                out.append(item)
                continue
            out.extend(_extract_thinking_values(item))
    elif isinstance(value, list):
        for item in value:
            out.extend(_extract_thinking_values(item))
    return out


def _extract_usage(value) -> dict:
    if isinstance(value, dict):
        usage = value.get("usage") or value.get("usageMetadata") or value.get("usage_metadata")
        if isinstance(usage, dict):
            out = {}
            in_tokens = usage.get("input_tokens") or usage.get("promptTokenCount") or usage.get("prompt_tokens")
            out_tokens = usage.get("output_tokens") or usage.get("candidatesTokenCount") or usage.get("completion_tokens")
            if in_tokens is not None:
                out["input_tokens"] = in_tokens
            if out_tokens is not None:
                out["output_tokens"] = out_tokens
            return out
        for item in value.values():
            found = _extract_usage(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _extract_usage(item)
            if found:
                return found
    return {}


def _extract_finish_reason(value) -> str:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in {"finishreason", "finish_reason", "stopreason", "stop_reason"}:
                return str(item or "")
            nested = _extract_finish_reason(item)
            if nested:
                return nested
    if isinstance(value, list):
        for item in value:
            nested = _extract_finish_reason(item)
            if nested:
                return nested
    return ""


def _json_shape(value, depth: int = 0):
    if depth >= 5:
        return {"type": type(value).__name__}
    if isinstance(value, dict):
        out = {"type": "object", "keys": sorted(str(k) for k in value.keys())}
        fields = {}
        for key, item in value.items():
            skey = str(key)
            lkey = skey.lower()
            if any(secret in lkey for secret in ("token", "authorization", "credential", "secret", "key")):
                fields[skey] = {"type": "redacted"}
            else:
                fields[skey] = _json_shape(item, depth + 1)
        out["fields"] = fields
        return out
    if isinstance(value, list):
        return {
            "type": "array",
            "length": len(value),
            "items": _json_shape(value[0], depth + 1) if value else {"type": "empty"},
        }
    if isinstance(value, str):
        return {"type": "string", "length": len(value)}
    if isinstance(value, bool):
        return {"type": "bool"}
    if isinstance(value, (int, float)):
        return {"type": "number"}
    if value is None:
        return {"type": "null"}
    return {"type": type(value).__name__}
