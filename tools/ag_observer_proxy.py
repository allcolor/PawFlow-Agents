#!/usr/bin/env python3
"""Transparent observer proxy for Antigravity CLI traffic.

The proxy is intentionally read-only: it terminates local TLS for
``daily-cloudcode-pa.googleapis.com``, opens a second TLS connection to the real upstream,
forwards bytes unchanged, and writes newline-delimited JSON observations to a
log file. It is a protocol discovery tool, not a provider parser.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import socket
import ssl
import sys
import threading
import time
import uuid
import zlib


UPSTREAM_HOST = os.environ.get("PAWFLOW_AG_UPSTREAM_HOST", "daily-cloudcode-pa.googleapis.com")
UPSTREAM_PORT = int(os.environ.get("PAWFLOW_AG_UPSTREAM_PORT", "443"))
LISTEN_HOST = os.environ.get("PAWFLOW_AG_PROXY_HOST", "0.0.0.0")  # nosec B104 - container-local proxy bind.
LISTEN_PORT = int(os.environ.get("PAWFLOW_AG_PROXY_PORT", "443"))
CERT_FILE = os.environ.get("PAWFLOW_AG_LEAF_CERT", "/tmp/aicode-googleapis.crt")  # nosec B108 - ephemeral container fallback; production passes a session path.
KEY_FILE = os.environ.get("PAWFLOW_AG_LEAF_KEY", "/tmp/aicode-googleapis.key")  # nosec B108 - ephemeral container fallback; production passes a session path.
LOG_FILE = os.environ.get("PAWFLOW_AG_OBSERVER_LOG", "/tmp/pawflow-antigravity-observer.jsonl")  # nosec B108 - ephemeral container fallback; production passes a session path.
LOG_B64 = os.environ.get("PAWFLOW_AG_OBSERVER_LOG_B64", "0").lower() in {"1", "true", "yes"}
MAX_B64_BYTES = int(os.environ.get("PAWFLOW_AG_OBSERVER_MAX_B64_BYTES", "4096") or "4096")
MAX_BODY_CAPTURE_BYTES = int(os.environ.get("PAWFLOW_AG_OBSERVER_MAX_BODY_BYTES", str(8 * 1024 * 1024)) or str(8 * 1024 * 1024))

try:  # Optional. The proxy still works without HTTP/2 semantic decoding.
    import h2.config
    import h2.connection
    import h2.events
except Exception:  # pragma: no cover - exercised in deployed image variants.
    h2 = None


_log_lock = threading.Lock()


def _stderr(msg: str) -> None:
    sys.stderr.write(f"[ag-observer-proxy] {msg}\n")
    sys.stderr.flush()


def _event(event: dict) -> None:
    event.setdefault("timestamp", time.time())
    event.setdefault("proxy", "antigravity-observer")
    line = json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n"
    with _log_lock:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()


def _payload_meta(data: bytes) -> dict:
    out = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    if LOG_B64 and data:
        sample = data[:MAX_B64_BYTES]
        out["data_b64"] = base64.b64encode(sample).decode("ascii")
        out["data_b64_truncated"] = len(sample) < len(data)
    return out


class HTTP2Observer:
    def __init__(self, connection_id: str, direction: str, client_side: bool):
        self.connection_id = connection_id
        self.direction = direction
        self.client_side = client_side
        self._conn = None
        self._raw_buf = b""
        if h2 is not None:
            cfg = h2.config.H2Configuration(client_side=client_side, header_encoding="utf-8")
            self._conn = h2.connection.H2Connection(config=cfg)

    def feed(self, data: bytes) -> None:
        if not data:
            return
        if self._conn is None:
            self._feed_raw_frames(data)
            return
        try:
            for ev in self._conn.receive_data(data):
                self._emit_h2_event(ev)
        except Exception as exc:
            _event({
                "type": "h2_decode_error",
                "connection_id": self.connection_id,
                "direction": self.direction,
                "error": str(exc),
                **_payload_meta(data),
            })
            self._conn = None
            self._feed_raw_frames(data)

    def _emit_h2_event(self, ev) -> None:
        base = {
            "connection_id": self.connection_id,
            "direction": self.direction,
            "event_class": ev.__class__.__name__,
        }
        stream_id = getattr(ev, "stream_id", None)
        if stream_id is not None:
            base["stream_id"] = stream_id
        if isinstance(ev, (h2.events.RequestReceived, h2.events.ResponseReceived, h2.events.TrailersReceived)):
            headers = [[str(k), str(v)] for k, v in (getattr(ev, "headers", None) or [])]
            _event({"type": "h2_headers", **base, "headers": headers})
            return
        if isinstance(ev, h2.events.DataReceived):
            data = getattr(ev, "data", b"") or b""
            record = {"type": "h2_data", **base, **_payload_meta(data)}
            for msg in self._grpc_messages(data):
                _event({"type": "grpc_message", **base, **msg})
            _event(record)
            return
        if isinstance(ev, h2.events.StreamEnded):
            _event({"type": "h2_stream_end", **base})
            return
        if isinstance(ev, h2.events.StreamReset):
            _event({"type": "h2_stream_reset", **base, "error_code": getattr(ev, "error_code", None)})

    def _feed_raw_frames(self, data: bytes) -> None:
        self._raw_buf += data
        preface = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"
        if self._raw_buf.startswith(preface):
            _event({"type": "h2_preface", "connection_id": self.connection_id, "direction": self.direction})
            self._raw_buf = self._raw_buf[len(preface):]
        while len(self._raw_buf) >= 9:
            length = int.from_bytes(self._raw_buf[:3], "big")
            if len(self._raw_buf) < 9 + length:
                return
            header = self._raw_buf[:9]
            payload = self._raw_buf[9:9 + length]
            self._raw_buf = self._raw_buf[9 + length:]
            ftype = header[3]
            flags = header[4]
            stream_id = int.from_bytes(header[5:9], "big") & 0x7FFFFFFF
            _event({
                "type": "h2_frame",
                "connection_id": self.connection_id,
                "direction": self.direction,
                "frame_type": ftype,
                "flags": flags,
                "stream_id": stream_id,
                **_payload_meta(payload),
            })

    @staticmethod
    def _grpc_messages(data: bytes) -> list[dict]:
        out = []
        pos = 0
        while pos + 5 <= len(data):
            compressed = data[pos]
            length = int.from_bytes(data[pos + 1:pos + 5], "big")
            if length < 0 or pos + 5 + length > len(data):
                break
            payload = data[pos + 5:pos + 5 + length]
            rec = {"compressed": compressed, **_payload_meta(payload)}
            out.append(rec)
            pos += 5 + length
        return out


class HTTP1Observer:
    def __init__(self, connection_id: str, direction: str, shared_state: dict | None = None):
        self.connection_id = connection_id
        self.direction = direction
        self.shared_state = shared_state if shared_state is not None else {}
        self._buf = b""
        self._state = "headers"
        self._remaining = 0
        self._chunk_remaining = None
        self._current = {}
        self._body_buf = b""
        self._body_total_bytes = 0
        self._sse_buf = ""

    def feed(self, data: bytes) -> None:
        if not data:
            return
        self._buf += data
        while self._buf:
            if self._state == "headers":
                if not self._parse_headers():
                    return
                continue
            if self._state == "fixed_body":
                if not self._parse_fixed_body():
                    return
                continue
            if self._state == "chunked_body":
                if not self._parse_chunked_body():
                    return
                continue
            self._emit_body(self._buf)
            self._buf = b""
            return

    def _parse_headers(self) -> bool:
        sep = self._buf.find(b"\r\n\r\n")
        if sep < 0:
            if len(self._buf) > 65536:
                self._emit_body(self._buf)
                self._buf = b""
            return False
        head = self._buf[:sep].decode("iso-8859-1", errors="replace")
        self._buf = self._buf[sep + 4:]
        lines = head.split("\r\n")
        first = lines[0] if lines else ""
        headers = []
        header_map = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            if name:
                clean_name = name.strip()
                clean_value = value.strip()
                header_map[clean_name.lower()] = clean_value
                headers.append([clean_name, _redact_header(clean_name, clean_value)])
        event = {
            "type": "http1_headers",
            "connection_id": self.connection_id,
            "direction": self.direction,
            "first_line": first,
            "headers": headers,
        }
        if self.direction == "client_to_upstream":
            parts = first.split(" ", 2)
            if len(parts) >= 2:
                event["method"] = parts[0]
                event["path"] = parts[1]
        else:
            parts = first.split(" ", 2)
            if len(parts) >= 2 and parts[0].startswith("HTTP/"):
                event["status"] = parts[1]
        _event(event)
        self._current = {
            "method": event.get("method", ""),
            "path": event.get("path", ""),
            "status": event.get("status", ""),
            "content_type": header_map.get("content-type", ""),
            "content_encoding": header_map.get("content-encoding", ""),
        }
        if self.direction == "client_to_upstream" and self._current.get("path"):
            self.shared_state["last_request_path"] = self._current["path"]
            self.shared_state["last_request_method"] = self._current.get("method", "")
        elif self.direction == "upstream_to_client":
            self._current["path"] = self.shared_state.get("last_request_path", "")
            self._current["method"] = self.shared_state.get("last_request_method", "")
        self._body_buf = b""
        self._body_total_bytes = 0
        transfer_encoding = header_map.get("transfer-encoding", "").lower()
        if "chunked" in transfer_encoding:
            self._state = "chunked_body"
            self._chunk_remaining = None
            return True
        try:
            self._remaining = int(header_map.get("content-length", "0") or "0")
        except ValueError:
            self._remaining = 0
        self._state = "fixed_body" if self._remaining > 0 else "headers"
        return True

    def _parse_fixed_body(self) -> bool:
        if not self._buf:
            return False
        take = min(self._remaining, len(self._buf))
        part = self._buf[:take]
        self._buf = self._buf[take:]
        self._remaining -= take
        self._emit_body(part)
        if self._remaining <= 0:
            self._emit_body_summary()
            self._state = "headers"
        return bool(self._buf)

    def _parse_chunked_body(self) -> bool:
        while True:
            if self._chunk_remaining is None:
                sep = self._buf.find(b"\r\n")
                if sep < 0:
                    return False
                line = self._buf[:sep].decode("ascii", errors="replace").split(";", 1)[0].strip()
                self._buf = self._buf[sep + 2:]
                try:
                    self._chunk_remaining = int(line, 16)
                except ValueError:
                    self._emit_body(self._buf)
                    self._buf = b""
                    return False
                if self._chunk_remaining == 0:
                    self._emit_body_summary()
                    if self._buf.startswith(b"\r\n"):
                        self._buf = self._buf[2:]
                    trailer_end = self._buf.find(b"\r\n\r\n")
                    if trailer_end >= 0:
                        self._buf = self._buf[trailer_end + 4:]
                    self._chunk_remaining = None
                    self._state = "headers"
                    return bool(self._buf)
            if len(self._buf) < self._chunk_remaining + 2:
                return False
            part = self._buf[:self._chunk_remaining]
            self._buf = self._buf[self._chunk_remaining + 2:]
            self._chunk_remaining = None
            self._emit_body(part)

    def _emit_body(self, data: bytes) -> None:
        if not data:
            return
        text = data[:2048].decode("utf-8", errors="replace")
        record = {"type": "http1_body", "connection_id": self.connection_id,
                  "direction": self.direction, **_payload_meta(data)}
        for key in ("method", "path", "status", "content_type"):
            if self._current.get(key):
                record[key] = self._current[key]
        self._body_total_bytes += len(data)
        if len(self._body_buf) < MAX_BODY_CAPTURE_BYTES:
            self._body_buf += data[:MAX_BODY_CAPTURE_BYTES - len(self._body_buf)]
        if "text/event-stream" in text or text.startswith("data:") or "\ndata:" in text:
            record["sse_line_count"] = sum(1 for line in text.splitlines() if line.startswith("data:"))
            if self.direction == "upstream_to_client":
                self._emit_sse_deltas(text)
        _event(record)

    def _emit_sse_deltas(self, text: str) -> None:
        self._sse_buf += text.replace("\r\n", "\n").replace("\r", "\n")
        while "\n\n" in self._sse_buf:
            block, self._sse_buf = self._sse_buf.split("\n\n", 1)
            payload_lines = []
            for line in block.splitlines():
                if line.startswith("data:"):
                    payload_lines.append(line[5:].strip())
            if not payload_lines:
                continue
            payload = "\n".join(payload_lines).strip()
            if not payload:
                continue
            if payload == "[DONE]":
                _event({
                    "type": "ag_text_delta",
                    "connection_id": self.connection_id,
                    "direction": self.direction,
                    "path": self._current.get("path", ""),
                    "done": True,
                })
                continue
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                continue
            delta = _semantic_model_delta(parsed)
            if delta:
                _event({
                    "type": "ag_text_delta",
                    "connection_id": self.connection_id,
                    "direction": self.direction,
                    "path": self._current.get("path", ""),
                    **delta,
                })

    def _emit_body_summary(self) -> None:
        if not self._body_buf:
            return
        body = _decode_http_body(self._body_buf, self._current.get("content_encoding", ""))
        text = body.decode("utf-8", errors="replace")
        summary = {
            "type": "http1_body_summary",
            "connection_id": self.connection_id,
            "direction": self.direction,
            "captured_bytes": len(self._body_buf),
            "observed_body_bytes": self._body_total_bytes,
            **{k: v for k, v in self._current.items() if v},
        }
        if self._body_total_bytes > len(self._body_buf):
            summary["body_truncated"] = True
        if body is not self._body_buf:
            summary["decoded_content_encoding"] = self._current.get("content_encoding", "")
            summary["decoded_bytes"] = len(body)
        if "json" in self._current.get("content_type", ""):
            try:
                parsed = json.loads(text)
                summary["json_shape"] = _json_shape(parsed)
                if self.direction == "client_to_upstream":
                    tool_results = _extract_tool_results(parsed)
                    if tool_results:
                        _event({
                            "type": "ag_text_delta",
                            "connection_id": self.connection_id,
                            "direction": self.direction,
                            "method": self._current.get("method", ""),
                            "path": self._current.get("path", ""),
                            "request_id": hashlib.sha256(body).hexdigest()[:16],
                            "tool_results": tool_results,
                        })
                    prompt = _semantic_user_prompt(parsed)
                    if prompt:
                        _event({
                            "type": "ag_user_prompt",
                            "connection_id": self.connection_id,
                            "direction": self.direction,
                            "method": self._current.get("method", ""),
                            "path": self._current.get("path", ""),
                            "request_id": hashlib.sha256(body).hexdigest()[:16],
                            "text": prompt,
                        })
            except Exception as exc:
                summary["json_error"] = str(exc)
        if "text/event-stream" in self._current.get("content_type", "") or text.startswith("data:") or "\ndata:" in text:
            events = []
            for line in text.splitlines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    events.append({"done": payload == "[DONE]"})
                    continue
                try:
                    parsed = json.loads(payload)
                    events.append({"json_shape": _json_shape(parsed)})
                except Exception:
                    events.append({"bytes": len(payload)})
            summary["sse_events"] = events[:20]
            summary["sse_event_count"] = len(events)
        _event(summary)


def _redact_header(name: str, value: str) -> str:
    if name.lower() in {"authorization", "cookie", "x-goog-api-key"}:
        return "<redacted>"
    return value


def _decode_http_body(data: bytes, encoding: str) -> bytes:
    enc = (encoding or "").lower()
    if not data:
        return data
    try:
        if "gzip" in enc:
            return gzip.decompress(data)
        if "deflate" in enc:
            return zlib.decompress(data)
    except Exception as exc:
        _event({"type": "http1_decode_error", "encoding": encoding, "error": str(exc)})
    return data


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


def _normalize_tool_call(name: str, args: dict) -> tuple[str, dict]:
    raw_name = str(name or "")
    payload = args if isinstance(args, dict) else {}
    if raw_name != "call_mcp_tool":
        return raw_name, payload
    server_name = str(
        payload.get("ServerName") or payload.get("serverName")
        or payload.get("server_name") or "")
    tool_name = str(
        payload.get("ToolName") or payload.get("toolName")
        or payload.get("tool_name") or raw_name)
    inner = (
        payload.get("Arguments") if "Arguments" in payload
        else payload.get("arguments", payload.get("Parameters", payload.get("parameters", {})))
    )
    display_name = f"{server_name}/{tool_name}" if server_name and tool_name else tool_name
    return display_name, _json_dict(inner)


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
                name, args = _normalize_tool_call(str(name or ""), args)
                out.append({
                    "id": str(call.get("id") or call.get("tool_call_id") or _stable_tool_id("ag_tool", call)),
                    "name": str(name or ""),
                    "arguments": args,
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
                name, _args = _normalize_tool_call(
                    str(result.get("name") or result.get("tool") or ""), result)
                out.append({
                    "tool_use_id": str(result.get("id") or result.get("tool_call_id") or _stable_tool_id("ag_tool", result)),
                    "name": str(name or ""),
                    "content": _tool_result_content(response),
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


def _resolve_upstream_ips() -> list[str]:
    configured = os.environ.get("PAWFLOW_AG_UPSTREAM_IPS", "")
    if configured:
        return [p.strip() for p in configured.split(",") if p.strip()]
    infos = socket.getaddrinfo(UPSTREAM_HOST, UPSTREAM_PORT, type=socket.SOCK_STREAM)
    seen = []
    for info in infos:
        ip = info[4][0]
        if ip not in seen and ip != "127.0.0.1":
            seen.append(ip)
    return seen


def _connect_upstream(alpn: str) -> ssl.SSLSocket:
    ctx = ssl.create_default_context()
    # Mirror the downstream protocol. If the client did not negotiate ALPN,
    # treat it as HTTP/1.1; otherwise an upstream h2 selection sends HTTP/2
    # frames to a client using Go's HTTP/1.x transport.
    upstream_alpn = alpn if alpn in {"h2", "http/1.1"} else "http/1.1"
    ctx.set_alpn_protocols([upstream_alpn])
    last_exc = None
    for ip in _resolve_upstream_ips():
        try:
            raw = socket.create_connection((ip, UPSTREAM_PORT), timeout=20)
            upstream = ctx.wrap_socket(raw, server_hostname=UPSTREAM_HOST)
            upstream.settimeout(None)
            return upstream
        except Exception as exc:
            last_exc = exc
    raise ConnectionError(f"failed to connect upstream {UPSTREAM_HOST}: {last_exc}")


def _pipe(src: ssl.SSLSocket, dst: ssl.SSLSocket, observer) -> None:
    while True:
        data = src.recv(65536)
        if not data:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError as exc:
                _event({"type": "socket_shutdown_error", "error": str(exc)})
            return
        observer.feed(data)
        dst.sendall(data)


def handle_client(client: ssl.SSLSocket) -> None:
    connection_id = uuid.uuid4().hex[:12]
    upstream = None
    alpn = client.selected_alpn_protocol() or ""
    try:
        upstream = _connect_upstream(alpn)
        upstream_alpn = upstream.selected_alpn_protocol() or ""
        _event({
            "type": "connection_start",
            "connection_id": connection_id,
            "client_alpn": alpn,
            "upstream_alpn": upstream_alpn,
            "upstream_host": UPSTREAM_HOST,
        })
        errors = []

        def run(src, dst, observer):
            try:
                _pipe(src, dst, observer)
            except Exception as exc:
                errors.append(str(exc))

        if alpn == "h2" or upstream_alpn == "h2":
            c2u_observer = HTTP2Observer(connection_id, "client_to_upstream", client_side=False)
            u2c_observer = HTTP2Observer(connection_id, "upstream_to_client", client_side=True)
        else:
            shared_state = {}
            c2u_observer = HTTP1Observer(connection_id, "client_to_upstream", shared_state)
            u2c_observer = HTTP1Observer(connection_id, "upstream_to_client", shared_state)

        c2u = threading.Thread(
            target=run,
            args=(client, upstream, c2u_observer),
            daemon=True,
        )
        u2c = threading.Thread(
            target=run,
            args=(upstream, client, u2c_observer),
            daemon=True,
        )
        c2u.start()
        u2c.start()
        c2u.join()
        u2c.join()
        _event({"type": "connection_stop", "connection_id": connection_id, "errors": errors})
    except Exception as exc:
        _event({"type": "connection_error", "connection_id": connection_id, "error": str(exc)})
        _stderr(f"client handler failed: {exc}")
    finally:
        for sock in (upstream, client):
            if sock:
                try:
                    sock.close()
                except OSError as exc:
                    _event({"type": "socket_close_error", "connection_id": connection_id, "error": str(exc)})


def main() -> None:
    os.makedirs(os.path.dirname(LOG_FILE) or ".", exist_ok=True)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT_FILE, KEY_FILE)
    ctx.set_alpn_protocols(["h2", "http/1.1"])
    lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    lsock.bind((LISTEN_HOST, LISTEN_PORT))
    lsock.listen(128)
    _stderr(f"listening on {LISTEN_HOST}:{LISTEN_PORT} upstream={UPSTREAM_HOST} log={LOG_FILE}")
    _event({"type": "proxy_start", "listen_host": LISTEN_HOST, "listen_port": LISTEN_PORT, "upstream_host": UPSTREAM_HOST})
    while True:
        raw, _addr = lsock.accept()
        try:
            client = ctx.wrap_socket(raw, server_side=True)
        except Exception as exc:
            _stderr(f"TLS accept failed: {exc}")
            raw.close()
            continue
        threading.Thread(target=handle_client, args=(client,), daemon=True).start()


if __name__ == "__main__":
    main()
