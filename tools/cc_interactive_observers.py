"""HTTP/SSE/JSON response observers for the CC interactive proxy
(split from cc_interactive_proxy.py for <=800 lines)."""
from __future__ import annotations

import gzip
import hashlib
import json
import threading
import zlib
from urllib.parse import urlsplit

try:
    from cc_interactive_filters import (
        normalize_observed_tool, observed_call_ids, observed_call_item,
        observed_call_output, observed_tool_origin)
except ImportError:  # Unit tests import this file as tools.cc_interactive_proxy.
    from tools.cc_interactive_filters import (
        normalize_observed_tool, observed_call_ids, observed_call_item,
        observed_call_output, observed_tool_origin)

try:  # standalone (/opt/pawflow on path) vs package (tools.cc_interactive_common)
    from cc_interactive_common import (  # noqa: F401
        EVENTS, _log, _preview, _content_text, _content_length, _header_map,
        _is_chunked, _is_messages_endpoint, _is_quota_probe,
        HTTPExchangeTracker)
except ImportError:
    from tools.cc_interactive_common import (  # noqa: F401
        EVENTS, _log, _preview, _content_text, _content_length, _header_map,
        _is_chunked, _is_messages_endpoint, _is_quota_probe,
        HTTPExchangeTracker)

try:  # standalone (/opt/pawflow on path) vs package (tools.cc_interactive_ws)
    from cc_interactive_ws import (  # noqa: F401
        MAX_PENDING_BYTES, WebSocketExchange, WebSocketMessageDecoder, is_websocket_upgrade, negotiated_extensions)
except ImportError:
    from tools.cc_interactive_ws import (  # noqa: F401
        MAX_PENDING_BYTES, WebSocketExchange, WebSocketMessageDecoder, is_websocket_upgrade, negotiated_extensions)


class SSEObserver:
    def __init__(self, base_event: dict):
        self.base_event = base_event
        self.buf = b""

    def feed(self, data: bytes):
        self.buf += data
        while b"\n\n" in self.buf or b"\r\n\r\n" in self.buf:
            if b"\r\n\r\n" in self.buf:
                raw, self.buf = self.buf.split(b"\r\n\r\n", 1)
            else:
                raw, self.buf = self.buf.split(b"\n\n", 1)
            self._emit(raw.decode("utf-8", errors="replace"))

    def _emit(self, raw: str):
        event_name = "message"
        data_lines = []
        for line in raw.splitlines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if not data_lines:
            return
        data = "\n".join(data_lines)
        if data == "[DONE]":
            payload = {"done": True}
        else:
            try:
                payload = json.loads(data)
            except Exception:
                payload = {"raw": data[:1000]}
        ev = dict(self.base_event)
        ev.update({"event": event_name, "payload": payload})
        if isinstance(payload, dict):
            ptype = payload.get("type") or event_name
            if ptype == "content_block_delta":
                delta = payload.get("delta") or {}
                dtype = delta.get("type", "")
                text = delta.get("text", "") if dtype == "text_delta" else ""
                _log(
                    f"emit sse request={self.base_event.get('request_id', '')} "
                    f"event={event_name} type={ptype} delta={dtype} "
                    f"text_len={len(text)} preview={_preview(text)!r}")
            else:
                _log(
                    f"emit sse request={self.base_event.get('request_id', '')} "
                    f"event={event_name} type={ptype} keys={sorted(payload.keys())[:8]}")
        else:
            _log(
                f"emit sse request={self.base_event.get('request_id', '')} "
                f"event={event_name} payload_type={type(payload).__name__}")
        EVENTS.emit(ev)

def _emit_observed_tool_blocks(request_id: str, path: str, body: bytes) -> None:
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return
    if urlsplit(path).path.rstrip("/").endswith("/responses"):
        items = payload.get("input") or []
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            output = observed_call_output(item)
            if output:
                call_id, result = output
                EVENTS.emit({
                    "type": "tool_result",
                    "request_id": request_id,
                    "path": path,
                    "tool_use_id": call_id,
                    "content": result,
                    "is_error": False,
                })
                continue
            call = observed_call_item(item)
            if call:
                call_id, tool_name, args = call
                display_name, display_args = normalize_observed_tool(
                    tool_name, args if isinstance(args, dict) else {})
                EVENTS.emit({
                    "type": "tool_use",
                    "request_id": request_id,
                    "path": path,
                    "tool_use_id": call_id,
                    "alias_ids": list(observed_call_ids(item)),
                    "name": display_name,
                    "arguments": display_args,
                    "tool_origin": observed_tool_origin(tool_name),
                })
        return
    if not _is_messages_endpoint(path):
        return
    messages = payload.get("messages") or []
    if not isinstance(messages, list):
        return
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content") or []
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if role == "assistant" and btype == "tool_use":
                tool_use_id = block.get("id") or ""
                if not tool_use_id:
                    continue
                args = block.get("input") or {}
                tool_name = block.get("name", "")
                display_name, display_args = normalize_observed_tool(tool_name, args)
                _log(
                    f"emit tool_use request={request_id} path={path} "
                    f"tool_use_id={tool_use_id} name={display_name}")
                EVENTS.emit({
                    "type": "tool_use",
                    "request_id": request_id,
                    "path": path,
                    "tool_use_id": tool_use_id,
                    "name": display_name,
                    "arguments": display_args,
                    "tool_origin": observed_tool_origin(tool_name),
                })
            elif role == "user" and btype == "tool_result":
                tool_use_id = block.get("tool_use_id") or block.get("id") or ""
                if not tool_use_id:
                    continue
                result = _content_text(block.get("content"))
                _log(
                    f"emit tool_result request={request_id} path={path} "
                    f"tool_use_id={tool_use_id} result_len={len(result)} "
                    f"preview={_preview(result)!r}")
                EVENTS.emit({
                    "type": "tool_result",
                    "request_id": request_id,
                    "path": path,
                    "tool_use_id": tool_use_id,
                    "content": result,
                    "is_error": bool(block.get("is_error")),
                })

class _ClientWebSocketReader:
    """Client -> upstream frames: the turn input, read for its tool blocks.

    Codex sends one ``response.create`` message whose ``input`` array carries
    the same ``function_call`` / ``function_call_output`` items the HTTP body
    used to carry, so the existing extraction applies unchanged.
    """

    def __init__(self, exchange):
        self.exchange = exchange
        self.decoder = None
        self.pending = b""
        # The handshake settles on the response thread, and it flushes what
        # this half buffered meanwhile, so two threads can reach feed().
        self.lock = threading.Lock()
        exchange.on_ready = self.flush

    def flush(self):
        self.feed(b"")

    def feed(self, data: bytes):
        with self.lock:
            self._feed(data)

    def _feed(self, data: bytes):
        if self.decoder is None:
            if not self.exchange.ready.is_set():
                self.pending += data
                if len(self.pending) > MAX_PENDING_BYTES:
                    _log(
                        "websocket_client_buffer_dropped request="
                        f"{self.exchange.request_id} bytes={len(self.pending)}")
                    self.pending = b""
                return
            if self.exchange.error:
                self.pending = b""
                return
            self.decoder = WebSocketMessageDecoder(
                self.exchange.inflater(peer="client"))
            data, self.pending = self.pending + data, b""
        for message in self.decoder.feed(data):
            _log(
                f"websocket_client_message request={self.exchange.request_id} "
                f"bytes={len(message)}")
            _emit_observed_tool_blocks(
                self.exchange.request_id, self.exchange.path, message)


class _ServerWebSocketReader:
    """Upstream -> client frames: the Responses events the coordinator reads.

    Each message is one event with the same shape SSE delivered, so it is
    republished under the same ``sse`` envelope and no consumer has to know
    which transport carried it.
    """

    def __init__(self, exchange):
        self.exchange = exchange
        self.decoder = WebSocketMessageDecoder(
            exchange.inflater(peer="server"))

    def feed(self, data: bytes):
        for message in self.decoder.feed(data):
            self._emit(message)

    def _emit(self, message: bytes):
        request_id = self.exchange.request_id
        try:
            payload = json.loads(message.decode("utf-8"))
        except Exception:
            _log(
                f"websocket_message_not_json request={request_id} "
                f"bytes={len(message)}")
            return
        if not isinstance(payload, dict):
            return
        ptype = payload.get("type", "")
        if ptype == "response.output_text.delta":
            delta = payload.get("delta", "") or ""
            _log(
                f"emit ws request={request_id} type={ptype} "
                f"text_len={len(delta)} preview={_preview(delta)!r}")
        elif ptype == "response.output_item.done":
            # Name the item: which of the Responses call types Codex uses for
            # a given tool is what decides whether the turn shows it.
            item = payload.get("item") or {}
            item = item if isinstance(item, dict) else {}
            _log(
                f"emit ws request={request_id} type={ptype} "
                f"item_type={item.get('type', '')} item_name={item.get('name', '')} "
                f"call_id={item.get('call_id', '')} item_id={item.get('id', '')}")
        else:
            _log(
                f"emit ws request={request_id} type={ptype} "
                f"keys={sorted(payload.keys())[:8]}")
        EVENTS.emit({
            "type": "sse",
            "request_id": request_id,
            "event": ptype,
            "payload": payload,
        })


class HTTPRequestObserver:
    def __init__(self, tracker: HTTPExchangeTracker):
        self.tracker = tracker
        self.buf = b""
        self.websocket = None

    def feed(self, data: bytes):
        if self.websocket is not None:
            self.websocket.feed(data)
            return
        self.buf += data
        while True:
            if b"\r\n\r\n" not in self.buf:
                return
            header, rest = self.buf.split(b"\r\n\r\n", 1)
            start, headers = _header_map(header + b"\r\n\r\n")
            clen = _content_length(headers)
            if len(rest) < clen:
                return
            body = rest[:clen]
            self.buf = rest[clen:]
            parts = start.split(" ", 2)
            method = parts[0] if parts else ""
            path = parts[1] if len(parts) > 1 else ""
            request_id = self.tracker.new_request_id()
            reason = ""
            if _is_quota_probe(path, body):
                reason = "quota_probe"
            ignore_response = bool(reason)
            self.tracker.push({
                "request_id": request_id,
                "method": method,
                "path": path,
                "ignore_response": ignore_response,
                "ignore_reason": reason,
            })
            _log(
                f"request_start request={request_id} method={method} path={path} "
                f"body_bytes={len(body)} ignore_reason={reason or '-'}")
            EVENTS.emit({
                "type": "request_start",
                "request_id": request_id,
                "method": method,
                "path": path,
                "body_sha256": hashlib.sha256(body).hexdigest(),
                "body_bytes": len(body),
                "ignore_reason": reason,
            })
            _emit_observed_tool_blocks(request_id, path, body)
            if is_websocket_upgrade(headers):
                # Everything after an upgrade request is frames, not HTTP.
                # The exchange is published on the tracker so the response
                # half can hand back the negotiated extension parameters.
                exchange = WebSocketExchange(request_id, path)
                self.tracker.websocket = exchange
                self.websocket = _ClientWebSocketReader(exchange)
                _log(
                    f"websocket_upgrade_requested request={request_id} "
                    f"path={path}")
                rest, self.buf = self.buf, b""
                self.websocket.feed(rest)
                return

class ChunkedBodyObserver:
    def __init__(self, observer: SSEObserver):
        self.observer = observer
        self.buf = b""
        self.remaining = None
        self.done = False

    def feed(self, data: bytes):
        if self.done:
            return data
        self.buf += data
        while not self.done:
            if self.remaining is None:
                if b"\r\n" not in self.buf:
                    return None
                size_line, self.buf = self.buf.split(b"\r\n", 1)
                self.remaining = int(size_line.split(b";", 1)[0], 16)
                if self.remaining == 0:
                    if len(self.buf) < 2:
                        return None
                    if self.buf.startswith(b"\r\n"):
                        leftover = self.buf[2:]
                    else:
                        trailer_end = self.buf.find(b"\r\n\r\n")
                        if trailer_end < 0:
                            return None
                        leftover = self.buf[trailer_end + 4:]
                    self.buf = b""
                    self.done = True
                    finish = getattr(self.observer, "finish", None)
                    if finish:
                        finish()
                    return leftover
            if len(self.buf) < self.remaining + 2:
                return None
            chunk_data = self.buf[:self.remaining]
            self.buf = self.buf[self.remaining + 2:]
            self.remaining = None
            if chunk_data:
                self.observer.feed(chunk_data)
        return self.buf

class DecodingObserver:
    def __init__(self, observer, encoding: str, request_id: str):
        self.observer = observer
        self.encoding = (encoding or "").lower()
        self.request_id = request_id
        self.decoder = self._make_decoder()
        self.unsupported = False

    def _make_decoder(self):
        if not self.encoding or "identity" in self.encoding:
            return None
        if "gzip" in self.encoding:
            return zlib.decompressobj(16 + zlib.MAX_WBITS)
        if "deflate" in self.encoding:
            return zlib.decompressobj()
        self.unsupported = True
        _log(
            f"response_ignored request={self.request_id} "
            f"reason=unsupported_content_encoding encoding={self.encoding}")
        EVENTS.emit({
            "type": "response_ignored",
            "request_id": self.request_id,
            "reason": "unsupported_content_encoding",
            "payload_type": self.encoding,
        })
        return None

    def feed(self, data: bytes):
        if self.unsupported:
            return
        if not data:
            return
        if self.decoder is None:
            self.observer.feed(data)
            return
        decoded = self.decoder.decompress(data)
        if decoded:
            self.observer.feed(decoded)

    def finish(self):
        if self.unsupported:
            return
        if self.decoder is not None:
            decoded = self.decoder.flush()
            if decoded:
                self.observer.feed(decoded)
        finish = getattr(self.observer, "finish", None)
        if finish:
            finish()

class HTTPResponseObserver:
    def __init__(self, tracker: HTTPExchangeTracker):
        self.tracker = tracker
        self.buf = b""
        self.body_observer = None
        self.websocket = None

    def feed(self, data: bytes):
        self.buf += data
        while True:
            if self.websocket is not None:
                self.websocket.feed(self.buf)
                self.buf = b""
                return
            if self.body_observer:
                leftover = self.body_observer.feed(self.buf)
                if leftover is None:
                    self.buf = b""
                    return
                self.buf = leftover
                self.body_observer = None
                continue
            if b"\r\n\r\n" not in self.buf:
                return
            header, rest = self.buf.split(b"\r\n\r\n", 1)
            start, headers = _header_map(header + b"\r\n\r\n")
            if (self._status(start) == "101"
                    and is_websocket_upgrade(headers)):
                # A 101 has neither Content-Length nor chunking: left to the
                # HTTP path the frames that follow would be parsed as the
                # next response header and the whole stream would desync.
                # Only 101 switches -- a 426 also names the upgrade it wants,
                # and its body is ordinary HTTP.
                self.buf = rest
                self._start_websocket(start, headers)
                continue
            is_chunked = _is_chunked(headers)
            if is_chunked:
                self.buf = rest
                self._start_chunked_response(start, headers)
                continue
            else:
                clen = _content_length(headers)
                if clen and len(rest) < clen:
                    return
                body = rest[:clen] if clen else b""
                remaining = rest[clen:] if clen else rest
            self.buf = remaining
            self._emit_response(start, headers, body, is_chunked=False)

    def finish(self):
        if self.body_observer:
            finish = getattr(self.body_observer, "finish", None)
            if finish:
                finish()
        if self.buf:
            _log(f"response observer discarded incomplete trailing bytes={len(self.buf)}")

    def _start_websocket(self, start: str, headers) -> None:
        ctx = self.tracker.pop()
        request_id = ctx.get("request_id", self.tracker.connection_id)
        _, _, status = self._response_meta(start, headers)
        _log(
            f"response_start request={request_id} path={ctx.get('path', '')} "
            f"status={status} ctype=- body_bytes=0 encoding=- chunked=False")
        EVENTS.emit({
            "type": "response_start",
            "request_id": request_id,
            "path": ctx.get("path", ""),
            "status": status,
            "content_type": "",
            "content_length": 0,
            "content_encoding": "",
            "chunked": False,
        })
        exchange = getattr(self.tracker, "websocket", None)
        if exchange is None:
            # An upgrade we never saw requested: nothing tells us how the
            # frames are framed, so read none of them.
            _log(f"websocket_without_observed_request request={request_id}")
            self.websocket = _NullObserver()
            return
        exchange.accept(negotiated_extensions(headers))
        if exchange.error:
            # Fail the turn now. Left silent, the coordinator would keep
            # waiting on a stream it can never read until its no-event
            # timeout, and report nothing about why.
            _log(
                f"websocket_undecodable request={request_id} "
                f"reason={exchange.error}")
            EVENTS.emit({
                "type": "request_error",
                "request_id": request_id,
                "error": exchange.error,
            })
            self.websocket = _NullObserver()
            return
        _log(
            f"websocket_open request={request_id} path={exchange.path} "
            f"permessage_deflate={bool(exchange.params.get('enabled'))}")
        self.websocket = _ServerWebSocketReader(exchange)

    def _start_chunked_response(self, start: str, headers) -> None:
        ctx = self.tracker.pop()
        request_id = ctx.get("request_id", self.tracker.connection_id)
        ctype, encoding, status = self._response_meta(start, headers)
        _log(
            f"response_start request={request_id} path={ctx.get('path', '')} status={status} "
            f"ctype={ctype or '-'} body_bytes=chunked encoding={encoding or '-'} chunked=True")
        EVENTS.emit({
            "type": "response_start",
            "request_id": request_id,
            "path": ctx.get("path", ""),
            "status": status,
            "content_type": ctype,
            "content_length": 0,
            "content_encoding": encoding,
            "chunked": True,
        })
        if ctx.get("ignore_response"):
            EVENTS.emit({
                "type": "response_ignored",
                "request_id": request_id,
                "path": ctx.get("path", ""),
                "reason": ctx.get("ignore_reason", "request_ignored"),
            })
            self.body_observer = ChunkedBodyObserver(_NullObserver())
        elif "text/event-stream" in ctype:
            self.body_observer = ChunkedBodyObserver(
                DecodingObserver(
                    SSEObserver({"type": "sse", "request_id": request_id}),
                    encoding,
                    request_id,
                ))
        elif "json" in ctype:
            self.body_observer = ChunkedBodyObserver(JSONResponseObserver(
                {"type": "sse", "request_id": request_id},
                content_length=0,
                encoding=encoding,
            ))
        else:
            self.body_observer = ChunkedBodyObserver(_NullObserver())

    @staticmethod
    def _status(start: str) -> str:
        parts = start.split(" ", 2)
        return parts[1] if len(parts) > 1 else ""

    def _response_meta(self, start: str, headers):
        ctype = "\n".join(v for k, v in headers if k.lower() == "content-type").lower()
        encoding = "\n".join(v for k, v in headers if k.lower() == "content-encoding").lower()
        status = ""
        parts = start.split(" ", 2)
        if len(parts) > 1:
            status = parts[1]
        return ctype, encoding, status

    def _emit_response(self, start: str, headers, body: bytes, is_chunked: bool):
        ctx = self.tracker.pop()
        request_id = ctx.get("request_id", self.tracker.connection_id)
        ctype, encoding, status = self._response_meta(start, headers)
        _log(
            f"response_start request={request_id} path={ctx.get('path', '')} status={status} "
            f"ctype={ctype or '-'} body_bytes={len(body)} encoding={encoding or '-'} chunked={is_chunked}")
        EVENTS.emit({
            "type": "response_start",
            "request_id": request_id,
            "path": ctx.get("path", ""),
            "status": status,
            "content_type": ctype,
            "content_length": len(body),
            "content_encoding": encoding,
            "chunked": is_chunked,
        })
        if ctx.get("ignore_response"):
            EVENTS.emit({
                "type": "response_ignored",
                "request_id": request_id,
                "path": ctx.get("path", ""),
                "reason": ctx.get("ignore_reason", "request_ignored"),
            })
            return
        if "text/event-stream" in ctype:
            sse = SSEObserver({"type": "sse", "request_id": request_id})
            sse.feed(body)
            return
        if "json" in ctype:
            json_observer = JSONResponseObserver(
                {"type": "sse", "request_id": request_id},
                content_length=len(body),
                encoding=encoding,
            )
            json_observer.feed(body)
            json_observer.finish()

class JSONResponseObserver:
    def __init__(self, base_event: dict, content_length: int = 0, encoding: str = ""):
        self.base_event = base_event
        self.content_length = max(0, int(content_length or 0))
        self.encoding = encoding or ""
        self.buf = b""
        self.emitted = False

    def feed(self, data: bytes):
        if self.emitted or not data:
            return
        self.buf += data
        if self.content_length and len(self.buf) >= self.content_length:
            self.finish()

    def finish(self):
        if self.emitted:
            return
        self.emitted = True
        raw = self.buf[:self.content_length] if self.content_length else self.buf
        if not raw:
            return
        if "gzip" in self.encoding:
            raw = gzip.decompress(raw)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            self._ignore("json_payload_not_object", "")
            return
        ptype = payload.get("type", "")
        _log(
            f"json_response request={self.base_event.get('request_id', '')} "
            f"type={ptype or '-'} keys={sorted(payload.keys())[:10]}")
        if ptype != "message":
            self._ignore("json_type_not_message", str(ptype))
            return
        content = payload.get("content") or []
        if not isinstance(content, list):
            self._ignore("message_content_not_list", "")
            return
        emitted_blocks = 0
        for idx, block in enumerate(content):
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "text":
                text = block.get("text", "")
                if not text:
                    continue
                emitted_blocks += 1
                _log(
                    f"emit json_text request={self.base_event.get('request_id', '')} "
                    f"index={idx} text_len={len(text)} preview={_preview(text)!r}")
                self._emit("content_block_start", {
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {"type": "text"},
                })
                for pos in range(0, len(text), 1800):
                    self._emit("content_block_delta", {
                        "type": "content_block_delta",
                        "index": idx,
                        "delta": {"type": "text_delta", "text": text[pos:pos + 1800]},
                    })
                self._emit("content_block_stop", {
                    "type": "content_block_stop",
                    "index": idx,
                })
            elif btype == "tool_use":
                emitted_blocks += 1
                self._emit("content_block_start", {
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {
                        "type": "tool_use",
                        "id": block.get("id", ""),
                        "name": block.get("name", ""),
                    },
                })
                tool_input = block.get("input") or {}
                self._emit("content_block_delta", {
                    "type": "content_block_delta",
                    "index": idx,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(tool_input, separators=(",", ":")),
                    },
                })
                self._emit("content_block_stop", {
                    "type": "content_block_stop",
                    "index": idx,
                })
        if emitted_blocks == 0:
            self._ignore("message_without_supported_content", "")
            return
        usage = payload.get("usage") or {}
        delta = payload.get("delta") or {}
        stop_reason = payload.get("stop_reason") or delta.get("stop_reason") or ""
        message_delta = {"type": "message_delta"}
        if usage:
            message_delta["usage"] = usage
        if stop_reason:
            message_delta["delta"] = {"stop_reason": stop_reason}
        if len(message_delta) > 1:
            self._emit("message_delta", message_delta)
        self._emit("message_stop", {"type": "message_stop"})

    def _ignore(self, reason: str, payload_type: str):
        ev = dict(self.base_event)
        ev.update({
            "type": "response_ignored",
            "reason": reason,
            "payload_type": payload_type,
        })
        _log(
            f"response_ignored request={self.base_event.get('request_id', '')} "
            f"reason={reason} payload_type={payload_type}")
        EVENTS.emit(ev)

    def _emit(self, event_name: str, payload: dict):
        ev = dict(self.base_event)
        ev.update({"event": event_name, "payload": payload})
        EVENTS.emit(ev)

class _NullObserver:
    def feed(self, data: bytes):
        return
