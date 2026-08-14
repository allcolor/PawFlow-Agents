"""Native mobile login endpoints and WebView cookie handoff."""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from core import FlowFile, TaskFactory
from core.base_task import BaseTask
from core.mobile_auth import mobile_auth_store

MOBILE_CALLBACK_URI = "pawflow://oauth/callback"


class MobileAuthTask(BaseTask):
    """Expose provider discovery, native login and PKCE handoff routes."""

    TYPE = "mobileAuth"
    VERSION = "1.0.0"
    NAME = "Mobile Auth"
    DESCRIPTION = "Native Android authentication with PKCE WebView handoff"
    ICON = "smartphone"

    def get_parameter_schema(self) -> dict[str, Any]:
        return {
            "auth_service_id": {
                "type": "string", "required": True, "default": "auth",
                "description": "AuthGateway service ID",
            },
            "oauth_service_id": {
                "type": "string", "required": True, "default": "oauth",
                "description": "OAuth provider service ID",
            },
            "cookie_name": {
                "type": "string", "required": False, "default": "pawflow_token",
                "description": "PawFlow login cookie name",
            },
            "cookie_max_age": {
                "type": "integer", "required": False, "default": 28800,
                "description": "Login cookie lifetime in seconds",
            },
        }

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        path = flowfile.get_attribute("http.path") or ""
        method = (flowfile.get_attribute("http.method") or "GET").upper()
        try:
            if method == "GET" and path == "/auth/mobile/providers":
                return [self._providers(flowfile)]
            if method == "POST" and path == "/auth/mobile/start":
                return [self._start_oauth(flowfile)]
            if method == "POST" and path == "/auth/mobile/builtin":
                return [self._builtin(flowfile)]
            if method == "POST" and path == "/auth/mobile/consume":
                return [self._consume(flowfile)]
            return [self._json(flowfile, 404, {"error": "Unknown mobile auth route"})]
        except ValueError as exc:
            return [self._json(flowfile, 400, {"error": str(exc)})]

    def _providers(self, flowfile: FlowFile) -> FlowFile:
        auth = self._service(self.config.get("auth_service_id", "auth"))
        providers = []
        for item in auth.get_enabled_providers():
            providers.append({
                "name": str(item.get("name") or ""),
                "display_name": str(item.get("display_name") or item.get("name") or ""),
                "icon": str(item.get("icon") or ""),
                "type": "password" if item.get("name") == "builtin"
                else ("oauth2" if item.get("is_oauth") else "web"),
            })
        return self._json(flowfile, 200, {"providers": providers})

    def _start_oauth(self, flowfile: FlowFile) -> FlowFile:
        body = self._body(flowfile)
        provider_name = str(body.get("provider") or "").strip()
        challenge = str(body.get("code_challenge") or "").strip()
        auth = self._service(self.config.get("auth_service_id", "auth"))
        provider = auth.get_provider(provider_name)
        enabled = {p.get("name"): p for p in auth.get_enabled_providers()}
        if not provider or not enabled.get(provider_name, {}).get("is_oauth"):
            raise ValueError("OAuth provider is not enabled")

        flow_id = mobile_auth_store.start(provider_name, challenge)
        oauth = self._service(self.config.get("oauth_service_id", "oauth"))
        state = oauth.generate_state(metadata={
            "provider": provider_name,
            "mobile_flow_id": flow_id,
        })
        mobile_auth_store.bind_state(flow_id, state)
        redirect_uri = self._base_url(flowfile) + "/auth/callback"
        authorize_url = provider.get_authorize_url(state, redirect_uri)
        return self._json(flowfile, 200, {
            "flow_id": flow_id,
            "authorization_url": authorize_url,
            "callback_uri": MOBILE_CALLBACK_URI,
        })

    def _builtin(self, flowfile: FlowFile) -> FlowFile:
        body = self._body(flowfile)
        username = str(body.get("username") or "")
        password = str(body.get("password") or "")
        challenge = str(body.get("code_challenge") or "")
        auth = self._service(self.config.get("auth_service_id", "auth"))
        result = auth.authenticate_builtin(
            username, password,
            ip=flowfile.get_attribute("http.remote.addr") or "")
        if not result.success:
            return self._json(flowfile, 401, {"error": result.error})

        from core.security import SecurityManager
        sm = SecurityManager.get_instance()
        user = sm.get_user(result.username)
        if not user:
            return self._json(flowfile, 500, {"error": "User not found"})
        session = sm._create_session(user, oauth_provider="builtin")
        flow_id = mobile_auth_store.start("builtin", challenge)
        code = mobile_auth_store.complete(
            flow_id, session.session_id, session.username, session.role.value)
        return self._json(flowfile, 200, {
            "flow_id": flow_id,
            "handoff_code": code,
            "callback_uri": MOBILE_CALLBACK_URI,
        })

    def _consume(self, flowfile: FlowFile) -> FlowFile:
        body = self._body(flowfile)
        handoff = mobile_auth_store.consume(
            str(body.get("code") or ""),
            str(body.get("code_verifier") or ""),
        )
        gateway_cookie = self._gateway_cookie(flowfile)
        secure = "; Secure" if self._scheme(flowfile) == "https" else ""
        login_name = str(self.config.get("cookie_name", "pawflow_token"))
        login_age = int(self.config.get("cookie_max_age", 28800))
        cookies = [
            gateway_cookie + secure,
            (f"{login_name}={handoff['session_id']}; Path=/; Max-Age={login_age}; "
             f"HttpOnly; SameSite=Lax{secure}"),
        ]
        flowfile.set_content(b"")
        flowfile.set_attribute("http.response.status", "302")
        flowfile.set_attribute("http.response.header.Location", "/chat")
        flowfile.set_attribute("http.response.header.Set-Cookie", "\n".join(cookies))
        flowfile.set_attribute("http.response.header.Cache-Control", "no-store")
        return flowfile

    def _gateway_cookie(self, flowfile: FlowFile) -> str:
        from services import private_gateway
        config = private_gateway._active_gateway_config()
        cookie_name = str(private_gateway._raw_value(
            config.get("cookie_name")) or private_gateway._COOKIE_NAME)
        max_age = int(private_gateway._raw_value(
            config.get("cookie_max_age")) or private_gateway._COOKIE_MAX_AGE)
        headers = {}
        forwarded = flowfile.get_attribute("http.header.x-forwarded-for") or ""
        if forwarded:
            headers["X-Forwarded-For"] = forwarded
        ip = private_gateway._effective_client_ip(
            flowfile.get_attribute("http.remote.addr") or "", headers, config)
        value = private_gateway._make_cookie_value(ip, config.get("secret_refs", ""))
        return (f"{cookie_name}={value}; Path=/; Max-Age={max_age}; "
                "HttpOnly; SameSite=Lax")

    def _service(self, service_id: str):
        service = getattr(self, "_services", {}).get(service_id)
        if service is None:
            raise ValueError(f"Service not configured: {service_id}")
        return service

    @staticmethod
    def _body(flowfile: FlowFile) -> dict:
        raw = flowfile.get_content().decode("utf-8", errors="replace")
        content_type = flowfile.get_attribute("http.header.content-type") or ""
        if "application/json" in content_type or raw.lstrip().startswith("{"):
            value = json.loads(raw or "{}")
            if not isinstance(value, dict):
                raise ValueError("Request body must be an object")
            return value
        return {key: values[0] for key, values in urllib.parse.parse_qs(raw).items()}

    @staticmethod
    def _scheme(flowfile: FlowFile) -> str:
        if flowfile.get_attribute("http.header.x-forwarded-proto") == "https":
            return "https"
        if flowfile.get_attribute("http.scheme") == "https":
            return "https"
        host = flowfile.get_attribute("http.header.host") or ""
        return "https" if host.endswith(":443") else "http"

    @classmethod
    def _base_url(cls, flowfile: FlowFile) -> str:
        host = flowfile.get_attribute("http.header.host") or "localhost:9090"
        return f"{cls._scheme(flowfile)}://{host}"

    @staticmethod
    def _json(flowfile: FlowFile, status: int, payload: dict) -> FlowFile:
        flowfile.set_content(json.dumps(payload).encode("utf-8"))
        flowfile.set_attribute("http.response.status", str(status))
        flowfile.set_attribute("http.response.header.Content-Type", "application/json")
        flowfile.set_attribute("http.response.header.Cache-Control", "no-store")
        return flowfile

    def set_services(self, services: dict[str, Any]):
        self._services = services


TaskFactory.register(MobileAuthTask)

