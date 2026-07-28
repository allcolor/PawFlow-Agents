"""Azure OpenAI and GitHub Copilot: same wire format, different envelope.

Both speak OpenAI chat-completions bodies, so they reuse the whole OpenAI
mixin. What differs is the envelope around the body — where the request goes
and how it authenticates — and that is all this module decides.

Azure: the key travels in ``api-key`` rather than ``Authorization``, the model
is addressed as a *deployment* in the path, and an ``api-version`` query
parameter is mandatory.

Copilot: the stored credential is a GitHub token, which is not what the chat
endpoint accepts; it is exchanged for a short-lived Copilot token. The endpoint
also refuses requests that do not identify an editor client.
"""

from __future__ import annotations

from typing import Any, Callable, Dict
from urllib.parse import urlparse

#: Providers routed through the OpenAI mixin with a different envelope.
DIALECTS = ("azure-openai", "copilot")

COPILOT_BASE_URL = "https://api.githubcopilot.com"

#: Azure requires an explicit api-version and rejects requests without one.
#: Pinned rather than "latest" so a silent service-side default never changes
#: the wire format under a running deployment.
DEFAULT_AZURE_API_VERSION = "2024-10-21"


def endpoint_path(provider: str, base_url: str, model: str,
                  cfg: Callable[[str, Any], Any]) -> str:
    """Path to append to ``base_url`` for a chat-completions call.

    A base URL that already names the full endpoint is left alone, so an
    operator who pasted a complete Azure target from the portal is not
    second-guessed.
    """
    path = urlparse(base_url or "").path.rstrip("/")
    if path.endswith("/chat/completions"):
        return ""
    if provider == "copilot":
        return "/chat/completions"
    if provider == "azure-openai":
        deployment = str(cfg("azure_deployment", "") or "").strip() or (model or "").strip()
        if not deployment:
            raise ValueError(
                "azure-openai needs a deployment name: set azure_deployment, "
                "or a default_model matching the deployment")
        version = str(cfg("azure_api_version", "") or "").strip() or DEFAULT_AZURE_API_VERSION
        prefix = "" if path.endswith("/openai") else "/openai"
        return f"{prefix}/deployments/{deployment}/chat/completions?api-version={version}"
    raise ValueError(f"Not an OpenAI dialect: {provider}")


def auth_headers(provider: str, api_key: str) -> Dict[str, str]:
    """Authentication headers for one dialect.

    For Copilot this performs the token exchange (memoised), so it is a
    network call on the first request of a session and free afterwards.
    """
    if provider == "azure-openai":
        if not api_key:
            raise ValueError("azure-openai requires an API key")
        return {"api-key": api_key}
    if provider == "copilot":
        from core import copilot_auth
        return {
            "Authorization": f"Bearer {copilot_auth.copilot_token(api_key)}",
            "Copilot-Integration-Id": copilot_auth.INTEGRATION_ID,
            "Editor-Version": copilot_auth.editor_version(),
            "Editor-Plugin-Version": copilot_auth.plugin_version(),
            "User-Agent": "PawFlow",
        }
    raise ValueError(f"Not an OpenAI dialect: {provider}")
