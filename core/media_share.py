"""Temporarily expose FileStore media refs as publicly fetchable URLs.

External media-generation providers (Pixazo/ByteDance, WaveSpeed,
OpenAI-compatible video, ...) fetch reference inputs (image/video/audio)
over public HTTP. FileStore files default to ``ACCESS_PRIVATE``, so a
private gateway serves the challenge page instead of the bytes, and a
localhost ``file_base_url`` is not reachable from the provider at all.

``TemporaryPublicRefs`` rewrites ``fs://filestore/<id>/<name>`` reference
inputs to a gateway-key share URL (``/files/<id>?k=<hmac>`` — no login,
bypasses the private gateway) for the lifetime of one generation call,
then restores each file's original access level on exit — success or
failure. The exposure is therefore scoped to the generation, not
permanent.

Refs that are not FileStore-backed (``http(s)://``, ``data:``) and
providers that declare ``ACCEPTS_FILESTORE_URLS`` (they read FileStore
locally) are passed through unchanged. When the configured base URL is
not internet-reachable (localhost / RFC1918) no access flip is performed
— a public URL cannot be produced anyway — and the legacy
``<base>/files/<id>`` form is returned so behaviour is unchanged for
local/dev setups.
"""

import ipaddress
import logging
import urllib.parse
from typing import Dict

logger = logging.getLogger(__name__)

_FS_PREFIX = "fs://filestore/"


def _is_public_base(base_url: str) -> bool:
    """True when base_url is an internet-reachable HTTP(S) URL.

    Mirrors the media-webhook public-URL check: a localhost/loopback or
    private (RFC1918 / link-local) host cannot serve a reference asset to
    an external provider, so there is no point flipping access for it.
    """
    try:
        parsed = urllib.parse.urlparse(base_url or "")
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    host = (parsed.hostname or "").strip().lower()
    if host in ("", "localhost"):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_unspecified):
        return False
    return True


class TemporaryPublicRefs:
    """Context manager that publicly shares FileStore refs for one call.

    Usage::

        with TemporaryPublicRefs(base_url, user_id) as share:
            image_url = share.public_url(image_url, service=service)
            result = service.image_to_video(image_url=image_url, ...)
        # original access levels restored here

    All files flipped via ``public_url`` are restored to their previous
    access level on ``__exit__``, even when the body raises.
    """

    def __init__(self, base_url: str, user_id: str = ""):
        self._base_url = (base_url or "").rstrip("/")
        self._user_id = user_id or ""
        self._public = _is_public_base(self._base_url)
        # file_id -> previous access level (only files we actually flipped)
        self._restore: Dict[str, str] = {}

    def _effective_base(self, service=None):
        """Resolve the public base URL to rewrite a ref against.

        The handler ``base_url`` comes from the tool relay ``file_base_url``
        and is frequently the dead ``http://localhost:9090`` dev default
        when no relay file base is configured. The media *service* already
        carries the correct internet-reachable root in its
        ``public_callback_base_url`` (the same value used for provider
        webhooks). Prefer it when the handler base is not public, so a
        single configured value (the service callback base) drives both
        webhooks and reference sharing — no separate ``file_base_url``
        needed. Returns ``(base, is_public)``.
        """
        if self._public:
            return self._base_url, True
        if service is not None:
            svc_base = (getattr(service, "public_callback_base_url", "")
                        or getattr(service, "_callback_base_url", "") or "")
            svc_base = svc_base.rstrip("/")
            if svc_base and _is_public_base(svc_base):
                return svc_base, True
        return self._base_url, False

    def public_url(self, url: str, service=None) -> str:
        """Return a provider-fetchable URL for a reference input.

        ``fs://filestore/<id>/<name>`` is flipped to a gateway-key share
        URL when a public base URL is available (the handler base, or the
        service ``public_callback_base_url`` as fallback); otherwise the
        legacy HTTP form is returned. Non-FileStore refs and
        ``ACCEPTS_FILESTORE_URLS`` services are returned unchanged.
        """
        if not url or not url.startswith(_FS_PREFIX):
            return url
        if service is not None and getattr(service, "ACCEPTS_FILESTORE_URLS", False):
            return url
        file_id = url[len(_FS_PREFIX):].split("/", 1)[0]
        if not file_id:
            return url
        base, is_public = self._effective_base(service)
        if is_public:
            self._flip_to_gateway_key(file_id)
            from core.file_store import FileStore
            share = FileStore.instance().get_share_url(
                file_id, base_url=base)
            if share:
                return share
        # No internet-reachable base could be resolved (no public
        # file_base_url and no service public_callback_base_url). The
        # returned URL is a non-public host (typically the dead
        # localhost:9090 dev default) that an external provider cannot
        # fetch — warn loudly with the actionable fix rather than letting
        # a cryptic provider-side 403 "Asset proxy failed" be the only clue.
        provider = getattr(service, "TYPE", service.__class__.__name__) \
            if service is not None else ""
        logger.warning(
            "media_share: ref %s resolved against non-public base %r%s; an "
            "external provider cannot fetch it. Set public_callback_base_url "
            "on the media service (or file_base_url on the tool relay) to a "
            "public HTTPS root.",
            file_id, base, f" (service {provider})" if provider else "")
        return f"{base}/files/{file_id}"

    def _flip_to_gateway_key(self, file_id: str) -> None:
        from core.file_store import (FileStore, ACCESS_GATEWAY_KEY,
                                     ACCESS_PUBLIC)
        if file_id in self._restore:
            return
        store = FileStore.instance()
        prev = store.get_access_level(file_id)
        # Unknown file, or already publicly fetchable: nothing to flip and
        # nothing to restore.
        if not prev or prev in (ACCESS_GATEWAY_KEY, ACCESS_PUBLIC):
            return
        if store.set_access(file_id, ACCESS_GATEWAY_KEY,
                            owner_user_id=self._user_id):
            self._restore[file_id] = prev
        else:
            logger.warning(
                "media_share: could not flip file %s to gateway_key "
                "(not owner?) — provider fetch may be blocked", file_id)

    def restore(self) -> None:
        """Restore the original access level of every flipped file.

        Idempotent: safe to call from a ``finally`` and again from
        ``__exit__``. Each file is restored at most once.
        """
        if not self._restore:
            return
        from core.file_store import FileStore
        store = FileStore.instance()
        for file_id, prev in self._restore.items():
            try:
                store.set_access(file_id, prev, owner_user_id=self._user_id)
            except Exception:
                logger.debug("media_share: restore failed for %s", file_id,
                             exc_info=True)
        self._restore.clear()

    def __enter__(self) -> "TemporaryPublicRefs":
        return self

    def __exit__(self, *_exc) -> bool:
        self.restore()
        return False


__all__ = ["TemporaryPublicRefs", "_is_public_base"]
