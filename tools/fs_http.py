"""HTTP fetch action for the relay — proxy LLM calls through the user's machine.

The relay executes HTTP requests on behalf of PawFlow, letting agents
reach services that are local to the user's machine (ex: llama-server)
without exposing them publicly.

Streaming is supported via on_chunk callback for SSE responses.
"""

import base64
import ipaddress
import logging
import socket
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


def _public_http_url(url: str) -> str:
    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must be absolute HTTP(S)")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL must not contain credentials")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        raise ValueError("URL must target a public host")
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        addresses = {str(ipaddress.ip_address(host))}
    except ValueError:
        rows = socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
        addresses = {str(row[4][0]) for row in rows if len(row) >= 5 and row[4]}
    if not addresses:
        raise ValueError("URL hostname could not be resolved")
    for value in addresses:
        address = ipaddress.ip_address(value)
        if (address.is_private or address.is_loopback or address.is_link_local
                or address.is_multicast or address.is_reserved
                or address.is_unspecified):
            raise ValueError("URL must resolve only to public addresses")
    return url


def action_http_fetch(root_dir: str, path: str, req: Dict[str, Any], *,
                       on_chunk: Optional[callable] = None) -> Dict[str, Any]:
    """HTTP fetch with optional streaming response.

    req: {
        "url": "http://127.0.0.1:8080/v1/messages",
        "method": "POST",
        "headers": {"Content-Type": "application/json", ...},
        "body": "<base64-encoded body or string>",
        "timeout": 300,
    }

    If on_chunk is provided, streams chunks as they arrive:
      on_chunk("start", {"status": int, "headers": dict})
      on_chunk("chunk", "<base64-encoded bytes>")
      on_chunk("end", None)

    Returns {"ok": True} (result is fully streamed via on_chunk) or
    {"ok": False, "error": "..."} on failure before the first chunk.
    """
    import urllib.request
    import urllib.error

    url = req.get("url", "")
    method = (req.get("method") or "GET").upper()
    headers = req.get("headers") or {}
    body_raw = req.get("body", "")
    timeout = int(req.get("timeout") or 300)
    max_bytes = max(0, int(req.get("max_bytes") or 0))
    public_only = bool(req.get("public_only"))

    if not url:
        return {"ok": False, "error": "Missing url"}

    # Decode body: accept base64 string, plain string, or empty
    body_bytes = None
    if body_raw:
        if isinstance(body_raw, str):
            try:
                body_bytes = base64.b64decode(body_raw)
            except Exception:
                body_bytes = body_raw.encode("utf-8")
        elif isinstance(body_raw, (bytes, bytearray)):
            body_bytes = bytes(body_raw)

    # Strip hop-by-hop headers the relay should not forward
    _drop = {"host", "connection", "content-length", "transfer-encoding"}
    _headers = {k: v for k, v in headers.items() if k.lower() not in _drop}

    try:
        if public_only:
            url = _public_http_url(url)

            class PublicRedirects(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, request, fp, code, msg, response_headers,
                                     new_url):
                    return super().redirect_request(
                        request, fp, code, msg, response_headers,
                        _public_http_url(new_url))

            opener = urllib.request.build_opener(PublicRedirects())
        else:
            opener = urllib.request.build_opener()
        req_obj = urllib.request.Request(
            url, data=body_bytes, headers=_headers, method=method)
        with opener.open(req_obj, timeout=timeout) as resp:  # nosec B310 - relay HTTP fetch tool intentionally fetches requested URLs.
            return _consume(resp, on_chunk, max_bytes=max_bytes)
    except urllib.error.HTTPError as e:
        # HTTP error status — still has a body we want to forward.
        try:
            return _consume(e, on_chunk, max_bytes=max_bytes)
        except Exception as inner:
            logger.warning("Failed to read HTTP error body: %s", inner)
            if on_chunk:
                try:
                    on_chunk("end", None)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            return {"ok": False, "status": e.code,
                    "error": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        logger.warning("http_fetch URL error for %s: %s", url, e)
        return {"ok": False, "error": f"URL error: {e.reason}"}
    except Exception as e:
        logger.warning("http_fetch failed for %s: %s", url, e)
        return {"ok": False, "error": str(e)}


def _consume(resp, on_chunk, max_bytes=0):
    """Either stream chunks (when on_chunk is set) or return the full
    body inline so a sync caller can use http_fetch as a drop-in
    replacement for `urllib.request.urlopen` (status, headers, body).

    Inline mode always returns:
        {"ok": True, "status": int, "headers": dict, "body": str}
    where `body` is base64-encoded so binary payloads survive JSON.
    """
    status = int(getattr(resp, "status", None)
                  or getattr(resp, "code", 200))
    _headers = {}
    for k, v in (resp.headers.items() if hasattr(resp.headers, "items") else []):
        if k.lower() in ("connection", "transfer-encoding"):
            continue
        _headers[k] = v
    if on_chunk:
        _emit_response_streaming(resp, on_chunk, status, _headers)
        return {"ok": True}
    declared_length = int(_headers.get("Content-Length") or 0)
    if max_bytes and declared_length > max_bytes:
        raise ValueError("HTTP response exceeds configured byte limit")
    body = resp.read(max_bytes + 1 if max_bytes else -1)
    if max_bytes and len(body) > max_bytes:
        raise ValueError("HTTP response exceeds configured byte limit")
    return {
        "ok": True,
        "status": status,
        "headers": _headers,
        "body": base64.b64encode(body).decode("ascii"),
        "url": str(resp.geturl()),
    }


def _emit_response_streaming(resp, on_chunk, status, _headers):
    """Stream chunks via on_chunk callback (start / chunk / end)."""
    on_chunk("start", {"status": int(status), "headers": _headers})
    # Read in small chunks to support SSE streaming (no fixed delimiter)
    while True:
        chunk = resp.read(4096)
        if not chunk:
            break
        on_chunk("chunk", base64.b64encode(chunk).decode("ascii"))
    on_chunk("end", None)
