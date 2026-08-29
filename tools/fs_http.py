"""HTTP fetch action for the relay — proxy LLM calls through the user's machine.

The relay executes HTTP requests on behalf of PawFlow, letting agents
reach services that are local to the user's machine (ex: llama-server)
without exposing them publicly.

Streaming is supported via on_chunk callback for SSE responses.
"""

import base64
import hashlib
import ipaddress
import json
import logging
import os
import socket
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

import defusedxml.ElementTree as SafeET

logger = logging.getLogger(__name__)

_ASSET_KINDS = frozenset({
    "image", "stylesheet", "script", "font", "media", "manifest",
})
_GENERIC_CONTENT_TYPES = frozenset({
    "", "application/octet-stream", "binary/octet-stream", "text/plain",
})
_ASSET_EXTENSIONS = {
    "image": frozenset({".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".avif"}),
    "stylesheet": frozenset({".css"}),
    "script": frozenset({".js", ".mjs"}),
    "font": frozenset({".woff", ".woff2", ".ttf", ".otf", ".ttc"}),
    "media": frozenset({".mp3", ".mp4", ".m4a", ".mov", ".ogg", ".oga", ".ogv", ".wav", ".webm", ".avi"}),
    "manifest": frozenset({".json", ".webmanifest", ".xml"}),
}


def _normalized_content_type(headers: Dict[str, Any]) -> str:
    for key, value in headers.items():
        if str(key).casefold() == "content-type":
            return str(value or "").split(";", 1)[0].strip().casefold()
    return ""


def _declared_type_matches(kind: str, content_type: str) -> bool:
    if content_type in _GENERIC_CONTENT_TYPES:
        return True
    if kind == "image":
        return content_type.startswith("image/")
    if kind == "stylesheet":
        return content_type == "text/css"
    if kind == "script":
        return content_type in {
            "application/ecmascript", "application/javascript",
            "text/ecmascript", "text/javascript",
        }
    if kind == "font":
        return content_type.startswith("font/") or content_type in {
            "application/font-woff", "application/vnd.ms-fontobject",
            "application/x-font-opentype", "application/x-font-ttf",
            "application/x-font-woff",
        }
    if kind == "media":
        return (
            content_type.startswith("audio/")
            or content_type.startswith("video/")
            or content_type == "application/ogg"
        )
    return content_type in {
        "application/json", "application/manifest+json", "application/xml",
        "text/json", "text/xml",
    }


def _balanced_css(text: str) -> bool:
    depth = 0
    quote = ""
    escaped = False
    comment = False
    index = 0
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if comment:
            if char == "*" and following == "/":
                comment = False
                index += 2
                continue
        elif quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char == "/" and following == "*":
            comment = True
            index += 2
            continue
        elif char in {"'", '"'}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return False
        index += 1
    return depth == 0 and not quote and not comment


def _asset_signature(kind: str, data: bytes, suffix: str) -> str:
    stripped = data.lstrip()
    lowered = stripped[:256].lower()
    if kind in {"stylesheet", "script"} and (
        lowered.startswith(b"<!doctype html") or lowered.startswith(b"<html")
    ):
        raise ValueError(f"{kind} response is an HTML document")
    if kind == "image":
        for signature, inferred in (
            (b"\x89PNG\r\n\x1a\n", "image/png"),
            (b"\xff\xd8\xff", "image/jpeg"),
            (b"GIF87a", "image/gif"),
            (b"GIF89a", "image/gif"),
            (b"<svg", "image/svg+xml"),
        ):
            if stripped.startswith(signature):
                return inferred
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return "image/webp"
        if len(data) >= 12 and data[4:12] in {b"ftypavif", b"ftypavis"}:
            return "image/avif"
    elif kind == "stylesheet":
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("stylesheet is not valid UTF-8") from exc
        if not text.strip() or "\x00" in text or not _balanced_css(text):
            raise ValueError("stylesheet parsing failed")
        return "text/css"
    elif kind == "script":
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("script is not valid UTF-8") from exc
        if not text.strip() or "\x00" in text:
            raise ValueError("script parsing failed")
        return "application/javascript"
    elif kind == "font":
        if data.startswith(b"wOFF"):
            return "font/woff"
        if data.startswith(b"wOF2"):
            return "font/woff2"
        if data.startswith((b"\x00\x01\x00\x00", b"OTTO", b"ttcf")):
            return "font/ttf"
    elif kind == "media":
        if len(data) >= 12 and data[4:8] == b"ftyp":
            return "video/mp4"
        if data.startswith(b"\x1aE\xdf\xa3"):
            return "video/webm"
        if data.startswith(b"OggS"):
            return "application/ogg"
        if data.startswith(b"ID3") or data.startswith((b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")):
            return "audio/mpeg"
        if data.startswith(b"RIFF") and data[8:12] in {b"WAVE", b"AVI "}:
            return "audio/wav" if data[8:12] == b"WAVE" else "video/x-msvideo"
    elif kind == "manifest":
        if suffix in {".json", ".webmanifest"} or stripped.startswith((b"{", b"[")):
            try:
                parsed = json.loads(data)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("manifest JSON parsing failed") from exc
            if not isinstance(parsed, (dict, list)):
                raise ValueError("manifest JSON must be an object or array")
            return "application/manifest+json"
        if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
            raise ValueError("manifest XML declarations are prohibited")
        try:
            SafeET.fromstring(data)
        except (SafeET.ParseError, UnicodeDecodeError) as exc:
            raise ValueError("manifest XML parsing failed") from exc
        return "application/xml"
    raise ValueError(f"{kind} signature or parser validation failed")


def _validate_asset_file(path: Path, kind: str, content_type: str, final_url: str) -> str:
    if kind not in _ASSET_KINDS:
        raise ValueError("http_fetch_to_file expected_kind is unsupported")
    if not _declared_type_matches(kind, content_type):
        raise ValueError(
            f"{kind} response content type conflicts with the requested asset kind"
        )
    suffix = Path(urlsplit(final_url).path).suffix.casefold() or path.suffix.casefold()
    if content_type in _GENERIC_CONTENT_TYPES and suffix not in _ASSET_EXTENSIONS[kind]:
        raise ValueError(
            f"{kind} response has no recognized extension or content type"
        )
    if kind in {"stylesheet", "script", "manifest"}:
        data = path.read_bytes()
    else:
        with path.open("rb") as handle:
            data = handle.read(64 * 1024)
    inferred = _asset_signature(kind, data, suffix)
    return content_type if content_type not in _GENERIC_CONTENT_TYPES else inferred


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


def action_http_fetch_to_file(
    root_dir: str,
    path: str,
    req: Dict[str, Any],
) -> Dict[str, Any]:
    """Stream one public HTTP response to a bounded atomically replaced file."""

    import urllib.error
    import urllib.request

    url = str(req.get("url") or "").strip()
    method = str(req.get("method") or "GET").upper()
    headers = req.get("headers") or {}
    timeout = int(req.get("timeout") or 300)
    max_bytes = int(req.get("max_bytes") or 0)
    public_only = bool(req.get("public_only"))
    expected_kind = str(req.get("expected_kind") or "").strip().casefold()
    if not url:
        return {"ok": False, "error": "Missing url"}
    if method != "GET":
        return {"ok": False, "error": "http_fetch_to_file supports GET only"}
    if not public_only:
        return {
            "ok": False,
            "error": "http_fetch_to_file requires public_only=True",
        }
    if max_bytes <= 0:
        return {
            "ok": False,
            "error": "http_fetch_to_file requires a positive max_bytes",
        }

    target = Path(path)
    temporary = target.with_name(
        f".{target.name}.pawflow-http-{uuid.uuid4().hex}.part"
    )
    drop = {"host", "connection", "content-length", "transfer-encoding"}
    request_headers = {
        key: value for key, value in headers.items()
        if str(key).casefold() not in drop
    }
    try:
        url = _public_http_url(url)

        class PublicRedirects(urllib.request.HTTPRedirectHandler):
            def redirect_request(
                self, request, fp, code, msg, response_headers, new_url,
            ):
                return super().redirect_request(
                    request,
                    fp,
                    code,
                    msg,
                    response_headers,
                    _public_http_url(new_url),
                )

        opener = urllib.request.build_opener(PublicRedirects())
        request = urllib.request.Request(
            url, headers=request_headers, method="GET",
        )
        try:
            response = opener.open(request, timeout=timeout)  # nosec B310
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            status = int(
                getattr(response, "status", None)
                or getattr(response, "code", 200)
            )
            response_headers = {
                str(key): value
                for key, value in (
                    response.headers.items()
                    if hasattr(response.headers, "items")
                    else ()
                )
                if str(key).casefold() not in {"connection", "transfer-encoding"}
            }
            final_url = _public_http_url(str(response.geturl()))
            if status < 200 or status >= 300:
                return {
                    "ok": True,
                    "status": status,
                    "headers": response_headers,
                    "url": final_url,
                    "bytes": 0,
                    "sha256": "",
                    "saved": False,
                }
            try:
                declared_length = int(
                    next(
                        (
                            value for key, value in response_headers.items()
                            if key.casefold() == "content-length"
                        ),
                        0,
                    )
                    or 0
                )
            except (TypeError, ValueError):
                raise ValueError("HTTP response has an invalid Content-Length")
            if declared_length > max_bytes:
                raise ValueError("HTTP response exceeds configured byte limit")

            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            written = 0
            with temporary.open("xb") as handle:
                while True:
                    chunk = response.read(min(64 * 1024, max_bytes - written + 1))
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise ValueError(
                            "HTTP response exceeds configured byte limit"
                        )
                    handle.write(chunk)
                    digest.update(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            content_type = _normalized_content_type(response_headers)
            if expected_kind:
                content_type = _validate_asset_file(
                    temporary, expected_kind, content_type, final_url,
                )
            os.replace(temporary, target)
            return {
                "ok": True,
                "status": status,
                "headers": response_headers,
                "url": final_url,
                "bytes": written,
                "sha256": digest.hexdigest(),
                "content_type": content_type,
                "saved": True,
            }
    except urllib.error.URLError as exc:
        logger.warning("http_fetch_to_file URL error for %s: %s", url, exc)
        return {"ok": False, "error": f"URL error: {exc.reason}"}
    except Exception as exc:
        logger.warning("http_fetch_to_file failed for %s: %s", url, exc)
        return {"ok": False, "error": str(exc)}
    finally:
        temporary.unlink(missing_ok=True)


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
