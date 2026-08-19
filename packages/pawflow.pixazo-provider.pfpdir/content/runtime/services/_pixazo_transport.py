"""Pixazo HTTP transport primitives (split from _pixazo_base for <=800 lines)."""
import http.client
import json
import logging
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from core import ServiceError
from services._pixazo_helpers import _GATEWAY, _BROWSER_UA

logger = logging.getLogger(__name__)


class _MultipartFile:
    """A binary file part for ``_encode_multipart`` (form-data file upload)."""
    __slots__ = ("filename", "content_type", "data")

    def __init__(self, filename: str, content_type: str, data: bytes):
        self.filename = filename or "upload.bin"
        self.content_type = content_type or "application/octet-stream"
        self.data = data


class _PixazoTransportMixin:
    """HTTP encoding/sending primitives shared by all Pixazo services."""

    # ── HTTP primitives ────────────────────────────────────────────────

    def _make_headers(self, body_bytes: bytes,
                      *, multipart_boundary: str = "",
                      extra_headers: Optional[Dict[str, str]] = None
                      ) -> Dict[str, str]:
        if multipart_boundary:
            ctype = f"multipart/form-data; boundary={multipart_boundary}"
        else:
            ctype = "application/json"
        h = {
            "Content-Type": ctype,
            "Cache-Control": "no-cache",
            "Ocp-Apim-Subscription-Key": self.api_key,
            "Content-Length": str(len(body_bytes)),
            "User-Agent": self._cf_ua or _BROWSER_UA,
        }
        if self._cf_cookie:
            h["Cookie"] = f"cf_clearance={self._cf_cookie}"
        if extra_headers:
            for key, value in extra_headers.items():
                if key and value:
                    h[str(key)] = str(value)
        return h

    @staticmethod
    def _encode_multipart(fields: Dict[str, Any]) -> Tuple[bytes, str]:
        """Build a tiny multipart/form-data body.

        String fields become plain form fields. A ``_MultipartFile`` value is
        sent as a binary file part (filename + Content-Type) — used by
        describe/remix so Pixazo receives the image bytes directly instead of a
        URL it must fetch server-side (which fails for PawFlow-local filestore
        URLs unreachable from Pixazo).
        """
        import uuid as _uuid
        boundary = f"pawflowPixazoBoundary{_uuid.uuid4().hex}"
        lines = []
        for name, value in fields.items():
            lines.append(f"--{boundary}".encode())
            if isinstance(value, _MultipartFile):
                lines.append((
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{value.filename}"').encode())
                lines.append(f"Content-Type: {value.content_type}".encode())
                lines.append(b"")
                lines.append(value.data if isinstance(value.data, bytes)
                             else str(value.data).encode("utf-8"))
                continue
            lines.append(
                f'Content-Disposition: form-data; name="{name}"'.encode())
            lines.append(b"")
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            lines.append(str(value).encode("utf-8"))
        lines.append(f"--{boundary}--".encode())
        lines.append(b"")
        return b"\r\n".join(lines), boundary

    def _fetch_multipart_file(self, url: str) -> Optional["_MultipartFile"]:
        """Fetch an image URL to bytes for binary multipart upload.

        Pixazo's describe/remix endpoints otherwise fetch the supplied URL
        server-side, which fails (500) for PawFlow-local filestore URLs such as
        ``http://localhost:9090/files/...`` that Pixazo cannot reach. PawFlow
        *can* reach its own filestore, so we fetch here and upload the raw
        bytes. Returns None (caller falls back to the URL string) on any error
        or for non-fetchable schemes.
        """
        if not url or not isinstance(url, str):
            return None
        low = url.lower()
        if not (low.startswith("http://") or low.startswith("https://")
                or low.startswith("data:")):
            return None
        try:
            ctx = ssl.create_default_context()
            req = urllib.request.Request(
                url, headers={"User-Agent": self._cf_ua or _BROWSER_UA})
            with urllib.request.urlopen(  # nosec B310 - PawFlow filestore / caller-supplied image URL.
                    req, timeout=self.timeout, context=ctx) as resp:
                data = resp.read()
                ctype = (resp.headers.get("Content-Type", "")
                         or "application/octet-stream")
            ctype = ctype.split(";")[0].strip() or "application/octet-stream"
            from urllib.parse import urlparse
            name = urlparse(url).path.rsplit("/", 1)[-1] or "image"
            if "." not in name:
                import mimetypes
                name = "image" + (mimetypes.guess_extension(ctype) or ".png")
            return _MultipartFile(name, ctype, data)
        except Exception:
            logger.debug("[PIXAZO] multipart image fetch failed for %s",
                         self._short_url(url), exc_info=True)
            return None

    def _post(self, endpoint: str, body: Dict[str, Any],
              *, multipart: bool = False,
              extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """POST to Pixazo gateway with retry on 5xx."""
        if multipart:
            body_bytes, boundary = self._encode_multipart(body)
            headers = self._make_headers(
                body_bytes, multipart_boundary=boundary,
                extra_headers=extra_headers)
        else:
            body_bytes = json.dumps(body).encode("utf-8")
            headers = self._make_headers(body_bytes, extra_headers=extra_headers)
        ctx = ssl.create_default_context()
        resp_body = ""
        resp_status = 0
        for attempt in range(self.max_retries):
            conn = http.client.HTTPSConnection(
                _GATEWAY, timeout=self.timeout, context=ctx)
            conn.request("POST", endpoint, body=body_bytes, headers=headers)
            resp = conn.getresponse()
            resp_body = resp.read().decode("utf-8", errors="replace")
            resp_status = resp.status
            conn.close()
            if resp_status < 500:
                break
            delay = [3, 5, 8, 10][min(attempt, 3)]
            logger.warning("[PIXAZO] Attempt %d/%d got %d: %s, retrying in %ds...",
                           attempt + 1, self.max_retries, resp_status,
                           resp_body[:200], delay)
            time.sleep(delay)
        if resp_status >= 400:
            raise ServiceError(f"Pixazo API error ({resp_status}): {resp_body[:300]}")
        return json.loads(resp_body) if resp_body.strip() else {}

    @staticmethod
    def _short_url(url: str) -> str:
        """Trim a polling URL to its tail for compact log lines."""
        return url.rsplit("/", 1)[-1][:32] if url else url
