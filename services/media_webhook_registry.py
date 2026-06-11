"""Temporary media-provider webhook receiver registry.

Media providers such as Pixazo can POST asynchronous completion payloads to a
caller-provided URL. This module creates one short-lived public route per media
job, correlates the incoming POST with the waiting service call, and removes the
route when the job completes, fails, is cancelled, or times out.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core import ServiceError


logger = logging.getLogger(__name__)

_PROVIDER_RE = re.compile(r"[^a-z0-9_-]+")
_MAX_WEBHOOK_BODY_BYTES = 2 * 1024 * 1024


def _provider_slug(provider: str) -> str:
    slug = _PROVIDER_RE.sub("-", (provider or "media").strip().lower()).strip("-")
    return slug or "media"


def _validate_public_base_url(base_url: str) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse((base_url or "").strip().rstrip("/"))
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ServiceError(
            "media webhook mode requires public_callback_base_url or "
            "file_base_url to be an absolute HTTP(S) URL")

    host = (parsed.hostname or "").strip().lower()
    if host in {"", "localhost", "127.0.0.1"}:
        raise ServiceError(
            "media webhook mode requires an internet-reachable public URL; "
            "localhost/127.0.0.1 cannot receive provider callbacks")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_unspecified):
        raise ServiceError(
            "media webhook mode requires an internet-reachable public URL; "
            "private/LAN IP addresses cannot receive provider callbacks")
    return parsed


@dataclass
class MediaWebhookTicket:
    token: str
    provider: str
    url: str
    route_path: str
    _registry: "MediaWebhookRegistry"
    _event: threading.Event = field(default_factory=threading.Event)
    _payload: Any = None
    _error: str = ""
    _created_at: float = field(default_factory=time.time)

    def complete(self, payload: Any) -> None:
        if self._event.is_set():
            return
        self._payload = payload
        self._event.set()

    def fail(self, error: str) -> None:
        if self._event.is_set():
            return
        self._error = error or "webhook failed"
        self._event.set()

    def try_result(self) -> Tuple[bool, Any]:
        """Non-blocking peek. Returns ``(ready, payload)``.

        ``ready`` is False while no callback has arrived yet. Raises
        ServiceError if the callback reported a failure. Lets a caller poll
        a provider status endpoint in lockstep with the webhook so a
        callback that never arrives can't hang the call.
        """
        if not self._event.is_set():
            return False, None
        if self._error:
            raise ServiceError(self._error)
        return True, self._payload

    def wait(self, *, timeout: float = 0,
             cancel_event: Optional[threading.Event] = None,
             poll_interval: float = 1.0) -> Any:
        deadline = time.time() + timeout if timeout and timeout > 0 else 0
        interval = max(0.1, min(float(poll_interval or 1.0), 2.0))
        while True:
            remaining = None
            if deadline:
                remaining = max(0.0, deadline - time.time())
                if remaining <= 0:
                    raise ServiceError(
                        f"media webhook timed out after {int(timeout)}s")
                remaining = min(remaining, interval)
            if self._event.wait(remaining if remaining is not None else interval):
                if self._error:
                    raise ServiceError(self._error)
                return self._payload
            if cancel_event is not None and cancel_event.is_set():
                raise ServiceError("media webhook wait cancelled by user")

    def close(self) -> None:
        self._registry.unregister(self.token)


class MediaWebhookRegistry:
    """Process-local registry for one-shot provider callback routes."""

    _instance: Optional["MediaWebhookRegistry"] = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self._lock = threading.RLock()
        self._tickets: Dict[str, MediaWebhookTicket] = {}
        self._owners: Dict[str, List[Tuple[Any, str]]] = {}

    @classmethod
    def instance(cls) -> "MediaWebhookRegistry":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def register(self, provider: str, base_url: str) -> MediaWebhookTicket:
        parsed = _validate_public_base_url(base_url)
        provider_slug = _provider_slug(provider)
        token = secrets.token_urlsafe(32)
        base_path = parsed.path.rstrip("/")
        route_path = f"{base_path}/webhooks/media/{provider_slug}/{token}"
        if not route_path.startswith("/"):
            route_path = "/" + route_path
        url = urllib.parse.urlunparse((
            parsed.scheme, parsed.netloc, route_path, "", "", ""))

        ticket = MediaWebhookTicket(
            token=token, provider=provider_slug, url=url,
            route_path=route_path, _registry=self)

        from services.http_listener_service import HTTPListenerService
        listeners = HTTPListenerService.all_instances()
        owners: List[Tuple[Any, str]] = []
        for port, listener in listeners.items():
            owner = f"media-webhook:{provider_slug}:{token}:{port}"
            listener.register_route(
                "POST", route_path, owner,
                lambda req, _ticket=ticket: self._handle_request(_ticket, req),
                public=True, gateway_exempt=True)
            owners.append((listener, owner))

        if not owners:
            raise ServiceError(
                "media webhook mode requires a running HTTPListenerService")

        with self._lock:
            self._tickets[token] = ticket
            self._owners[token] = owners
        logger.info("[media-webhook] registered %s callback route %s/<token>",
                    provider_slug, route_path.rsplit("/", 1)[0])
        return ticket

    def unregister(self, token: str) -> None:
        with self._lock:
            self._tickets.pop(token, None)
            owners = self._owners.pop(token, [])
        for listener, owner in owners:
            try:
                listener.unregister_routes(owner)
            except Exception:
                logger.debug("media webhook unregister failed", exc_info=True)

    def _handle_request(self, ticket: MediaWebhookTicket, req) -> None:
        body = req.body or b""
        headers = {"Content-Type": "application/json"}
        if len(body) > _MAX_WEBHOOK_BODY_BYTES:
            ticket.fail("media webhook payload exceeded 2 MiB")
            req.complete(413, headers, b'{"ok": false, "error": "payload too large"}')
            return
        try:
            payload = json.loads(body.decode("utf-8", errors="replace")) if body else {}
        except json.JSONDecodeError:
            payload = {"raw_body": body.decode("utf-8", errors="replace")}
        logger.info("[media-webhook] callback received on %s (%d bytes)",
                    ticket.route_path.rsplit("/", 1)[0] + "/<token>", len(body))
        ticket.complete(payload)
        req.complete(200, headers, b'{"ok": true}')


__all__ = ["MediaWebhookRegistry", "MediaWebhookTicket"]
