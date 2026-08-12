"""Configured search-cli backend for the web_search agent tool."""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404
import tempfile
from typing import Any, Dict, List

from core import ServiceError, ServiceFactory
from core.base_service import BaseService


_PROVIDER_ENV = {
    "parallel": "SEARCH_KEYS_PARALLEL",
    "brave": "SEARCH_KEYS_BRAVE",
    "serper": "SEARCH_KEYS_SERPER",
    "exa": "SEARCH_KEYS_EXA",
    "jina": "SEARCH_KEYS_JINA",
    "linkup": "SEARCH_KEYS_LINKUP",
    "firecrawl": "SEARCH_KEYS_FIRECRAWL",
    "tavily": "SEARCH_KEYS_TAVILY",
    "serpapi": "SEARCH_KEYS_SERPAPI",
    "perplexity": "SEARCH_KEYS_PERPLEXITY",
    "browserless": "SEARCH_KEYS_BROWSERLESS",
    "xai": "SEARCH_KEYS_XAI",
}
_BUNDLED_BINARY = "/usr/local/bin/search"


class WebSearchConnectionService(BaseService):
    """Run the bundled search-cli with scope-encrypted provider credentials."""

    TYPE = "webSearchConnection"
    VERSION = "1.0.0"
    NAME = "Web Search Connection"
    DESCRIPTION = "Multi-provider search-cli backend with encrypted API keys and free PawFlow fallback"
    CATEGORY = "network"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.providers = self._split_providers(self.config.get("providers", ""))
        self.default_mode = str(
            self.config.get("default_mode", "general") or "general").strip()
        self.timeout = max(1, min(120, int(self.config.get("timeout", 10))))
        self.fallback_to_free = self._truthy(
            self.config.get("fallback_to_free", True))

    @staticmethod
    def _truthy(value: Any) -> bool:
        return str(value).strip().lower() not in {"0", "false", "no", "off"}

    @staticmethod
    def _split_providers(value: Any) -> List[str]:
        if isinstance(value, list):
            parts = value
        else:
            parts = str(value or "").replace(",", " ").split()
        return [str(item).strip().lower() for item in parts if str(item).strip()]

    def _create_connection(self):
        path = _BUNDLED_BINARY if os.access(_BUNDLED_BINARY, os.X_OK) else shutil.which("search")
        return {
            "binary": path or "",
            "binary_available": bool(path),
            "configured_providers": self.configured_providers(),
        }

    def _close_connection(self):
        pass

    def configured_providers(self) -> List[str]:
        configured = [
            name for name in _PROVIDER_ENV
            if str(self.config.get(f"{name}_api_key", "") or "").strip()
        ]
        if self.providers:
            return [name for name in self.providers if name in configured]
        return configured

    @property
    def available(self) -> bool:
        connection = self._get_connection()
        return bool(connection.get("binary_available")) and bool(
            self.configured_providers())

    @property
    def unavailable_reason(self) -> str:
        connection = self._get_connection()
        if not connection.get("binary_available"):
            return "the PawFlow server image does not contain the bundled search-cli binary"
        if not self.configured_providers():
            return "no search-cli provider API key is configured"
        return ""

    def search(
        self,
        query: str,
        *,
        count: int = 5,
        mode: str = "",
        providers: Any = None,
        freshness: str = "",
        include_domains: Any = None,
        exclude_domains: Any = None,
    ) -> Dict[str, Any]:
        self.ensure_connected()
        binary = str(self._connection.get("binary") or "")
        if not binary:
            raise ServiceError(
                "The bundled search-cli binary is unavailable; use the free backend")

        configured = self.configured_providers()
        requested = self._split_providers(providers)
        selected = [name for name in (requested or configured) if name in configured]
        if not selected:
            raise ServiceError(
                "No configured search-cli provider matches this request")

        command = [
            binary, "search", "-q", str(query), "-m",
            str(mode or self.default_mode), "-c", str(max(1, min(100, int(count)))),
            "-p", ",".join(selected), "--json", "--no-cache",
            "--max-chars", "1200",
        ]
        if freshness:
            command.extend(["-f", str(freshness)])
        for domain in self._split_providers(include_domains):
            command.extend(["-d", domain])
        for domain in self._split_providers(exclude_domains):
            command.extend(["--exclude-domain", domain])

        with tempfile.TemporaryDirectory(prefix="pawflow-search-") as temp_dir:
            env = os.environ.copy()
            for name, env_name in _PROVIDER_ENV.items():
                env.pop(env_name, None)
                env.pop(f"{name.upper()}_API_KEY", None)
            env.update({
                "SEARCH_LOG": "off",
                "SEARCH_SETTINGS_TIMEOUT": str(self.timeout),
                "XDG_CONFIG_HOME": os.path.join(temp_dir, "config"),
                "XDG_CACHE_HOME": os.path.join(temp_dir, "cache"),
                "XDG_DATA_HOME": os.path.join(temp_dir, "data"),
            })
            for name, env_name in _PROVIDER_ENV.items():
                value = str(self.config.get(f"{name}_api_key", "") or "").strip()
                if value:
                    env[env_name] = value
            result = subprocess.run(  # nosec B603
                command,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout + 5,
                check=False,
            )

        if result.returncode:
            detail = (result.stderr or result.stdout or "search-cli failed").strip()
            for provider in _PROVIDER_ENV:
                secret = str(self.config.get(f"{provider}_api_key", "") or "").strip()
                if secret:
                    detail = detail.replace(secret, "[REDACTED]")
            raise ServiceError(
                f"search-cli exited with code {result.returncode}: {detail[:500]}")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ServiceError("search-cli returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ServiceError("search-cli returned an invalid response envelope")
        return payload

    def get_parameter_schema(self) -> Dict[str, Any]:
        schema: Dict[str, Any] = {
            "providers": {
                "type": "string", "required": False, "default": "",
                "description": "Comma-separated enabled providers. Empty uses every provider with a configured key.",
            },
            "default_mode": {
                "type": "select", "required": False, "default": "general",
                "options": [
                    "general", "news", "academic", "deep", "people", "scholar",
                    "patents", "images", "places", "social", "similar", "extract",
                    "scrape",
                ],
                "description": "Default search-cli mode.",
            },
            "timeout": {
                "type": "integer", "required": False, "default": 10,
                "description": "Per-provider search-cli timeout in seconds.",
            },
            "fallback_to_free": {
                "type": "boolean", "required": False, "default": True,
                "description": "Use PawFlow's no-key search when search-cli is unavailable or fails.",
            },
        }
        for provider in _PROVIDER_ENV:
            schema[f"{provider}_api_key"] = {
                "type": "password",
                "required": False,
                "default": "",
                "sensitive": True,
                "description": f"{provider} search API key.",
            }
        return schema


ServiceFactory.register(WebSearchConnectionService)
