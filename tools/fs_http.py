"""HTTP fetch action for the relay — proxy LLM calls through the user's machine.

The relay executes HTTP requests on behalf of PawFlow, letting agents
reach services that are local to the user's machine (ex: llama-server)
without exposing them publicly.

Streaming is supported via on_chunk callback for SSE responses.
"""

import base64
import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


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

    req_obj = urllib.request.Request(url, data=body_bytes, headers=_headers, method=method)

    try:
        with urllib.request.urlopen(req_obj, timeout=timeout) as resp:
            _emit_response(resp, on_chunk)
        return {"ok": True}
    except urllib.error.HTTPError as e:
        # HTTP error status — still has a body we want to forward
        try:
            _emit_response(e, on_chunk)
        except Exception as inner:
            logger.warning("Failed to stream HTTP error body: %s", inner)
            if on_chunk:
                try:
                    on_chunk("end", None)
                except Exception:
                    pass
        return {"ok": True}
    except urllib.error.URLError as e:
        logger.warning("http_fetch URL error: %s", e)
        return {"ok": False, "error": f"URL error: {e.reason}"}
    except Exception as e:
        logger.warning("http_fetch failed: %s", e)
        return {"ok": False, "error": str(e)}


def _emit_response(resp, on_chunk):
    """Stream an HTTP response through on_chunk callback."""
    if not on_chunk:
        # Non-streaming fallback: read everything (caller can't consume chunks)
        resp.read()
        return
    status = getattr(resp, "status", None) or getattr(resp, "code", 200)
    _headers = {}
    for k, v in (resp.headers.items() if hasattr(resp.headers, "items") else []):
        # Skip hop-by-hop headers on the way back too
        if k.lower() in ("connection", "transfer-encoding"):
            continue
        _headers[k] = v
    on_chunk("start", {"status": int(status), "headers": _headers})
    # Read in small chunks to support SSE streaming (no fixed delimiter)
    while True:
        chunk = resp.read(4096)
        if not chunk:
            break
        on_chunk("chunk", base64.b64encode(chunk).decode("ascii"))
    on_chunk("end", None)
