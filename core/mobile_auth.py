"""Short-lived PKCE handoffs for the native PawFlow mobile client."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time

_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")


class MobileAuthStore:
    """Keep OAuth state and one-use session handoffs in process memory."""

    def __init__(self, ttl: int = 300):
        self.ttl = int(ttl)
        self._flows: dict[str, dict] = {}
        self._states: dict[str, str] = {}
        self._codes: dict[str, str] = {}
        self._lock = threading.RLock()

    def start(self, provider: str, code_challenge: str) -> str:
        provider = str(provider or "").strip()
        challenge = str(code_challenge or "").strip()
        if not provider:
            raise ValueError("Provider is required")
        if not _CHALLENGE_RE.fullmatch(challenge):
            raise ValueError("A valid S256 PKCE code_challenge is required")
        with self._lock:
            self._cleanup_locked()
            flow_id = secrets.token_urlsafe(24)
            self._flows[flow_id] = {
                "provider": provider,
                "code_challenge": challenge,
                "expires_at": time.time() + self.ttl,
                "state": "",
                "code": "",
            }
            return flow_id

    def bind_state(self, flow_id: str, state: str) -> None:
        with self._lock:
            flow = self._active_flow_locked(flow_id)
            state = str(state or "").strip()
            if not state:
                raise ValueError("OAuth state is required")
            flow["state"] = state
            self._states[state] = flow_id

    def is_mobile_state(self, state: str) -> bool:
        with self._lock:
            self._cleanup_locked()
            return str(state or "") in self._states

    def complete(self, flow_id: str, session_id: str,
                 username: str, role: str) -> str:
        with self._lock:
            flow = self._active_flow_locked(flow_id)
            if flow.get("code"):
                raise ValueError("Mobile authentication flow is already complete")
            code = secrets.token_urlsafe(32)
            flow.update({
                "session_id": str(session_id or ""),
                "username": str(username or ""),
                "role": str(role or "user"),
                "code": code,
            })
            state = flow.get("state")
            if state:
                self._states.pop(state, None)
            self._codes[code] = flow_id
            return code

    def consume(self, code: str, code_verifier: str) -> dict:
        with self._lock:
            self._cleanup_locked()
            flow_id = self._codes.get(str(code or ""))
            if not flow_id:
                raise ValueError("Invalid or expired mobile authentication code")
            flow = self._flows.get(flow_id)
            if not flow:
                raise ValueError("Invalid or expired mobile authentication code")
            verifier = str(code_verifier or "")
            digest = hashlib.sha256(verifier.encode("ascii", errors="ignore")).digest()
            challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
            if not hmac.compare_digest(challenge, flow["code_challenge"]):
                raise ValueError("PKCE verification failed")
            self._codes.pop(code, None)
            self._flows.pop(flow_id, None)
            return {
                "session_id": flow["session_id"],
                "username": flow["username"],
                "role": flow["role"],
            }

    def cancel(self, flow_id: str) -> None:
        """Remove an unfinished flow after the provider rejects authentication."""
        with self._lock:
            flow = self._active_flow_locked(flow_id)
            self._flows.pop(flow_id, None)
            state = flow.get("state")
            code = flow.get("code")
            if state:
                self._states.pop(state, None)
            if code:
                self._codes.pop(code, None)

    def _active_flow_locked(self, flow_id: str) -> dict:
        self._cleanup_locked()
        flow = self._flows.get(str(flow_id or ""))
        if not flow:
            raise ValueError("Invalid or expired mobile authentication flow")
        return flow

    def _cleanup_locked(self) -> None:
        now = time.time()
        expired = [key for key, flow in self._flows.items()
                   if float(flow.get("expires_at", 0)) <= now]
        for flow_id in expired:
            flow = self._flows.pop(flow_id, {})
            state = flow.get("state")
            code = flow.get("code")
            if state:
                self._states.pop(state, None)
            if code:
                self._codes.pop(code, None)


mobile_auth_store = MobileAuthStore()

