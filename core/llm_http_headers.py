"""Shared outbound identity headers for direct LLM provider HTTP calls."""

from urllib.parse import urlparse


def pawflow_user_agent() -> str:
    """Return the versioned identity used for every direct provider request."""
    from core import __version__

    return f"PawFlow/{__version__}"


def _is_opencode_go_endpoint(base_url: str) -> bool:
    """Match only OpenCode's official Go API host and path namespace."""
    parsed = urlparse(str(base_url or ""))
    host = (parsed.hostname or "").lower().rstrip(".")
    segments = [segment for segment in parsed.path.split("/") if segment]
    return host == "opencode.ai" and segments[:2] == ["zen", "go"]


def llm_api_headers(base_url: str = "", *,
                    conversation_id: str = "") -> dict[str, str]:
    """Build shared identity headers, including OpenCode's cache session key.

    OpenCode Go requires its callers to identify the logical session so it can
    preserve prompt-cache affinity. PawFlow's conversation id is already the
    stable session boundary; inventing another id would fragment that cache.
    """
    headers = {"User-Agent": pawflow_user_agent()}
    if _is_opencode_go_endpoint(base_url):
        session_id = str(conversation_id or "").strip()
        if not session_id:
            raise ValueError(
                "conversation_id is required for OpenCode Go requests")
        headers["x-opencode-session"] = session_id
    return headers
