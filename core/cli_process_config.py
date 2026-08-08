"""Runtime configuration helpers shared by CLI-based LLM providers."""

from __future__ import annotations

import json
import re
import shlex
from copy import deepcopy
from typing import Any, Mapping


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RUNTIME_ENV_NAMES = {
    "HOME", "USER", "PATH", "TERM", "NODE_OPTIONS", "NODE_EXTRA_CA_CERTS",
    "CLAUDE_CONFIG_DIR", "CODEX_HOME", "GEMINI_CLI_HOME",
}


def raw_client_config(client: Any, key: str, default: Any = "") -> Any:
    """Read a client config template without LazyResolveDict auto-resolution."""
    config = getattr(client, "_config_ref", None)
    if config is None:
        config = getattr(client, "config", None)
    if not config:
        return default
    try:
        return dict.__getitem__(config, key)
    except (KeyError, TypeError):
        return config.get(key, default)


def resolve_cli_environment(client: Any, user_id: str = "",
                            conversation_id: str = "") -> dict[str, str]:
    """Parse and resolve the llmConnection ``cli_environment`` block.

    Each non-empty, non-comment line must be ``NAME=value``. Values are
    resolved at process-launch time so conversation/user/global expressions
    use the identity of the turn that owns the CLI process.
    """
    raw = raw_client_config(client, "cli_environment", "")
    if raw in (None, ""):
        return {}
    if not isinstance(raw, str):
        raise ValueError("cli_environment must be a multiline string")
    resolved: dict[str, str] = {}
    for line_number, line in enumerate(raw.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(
                f"cli_environment line {line_number} must use NAME=value")
        name, value = line.split("=", 1)
        name = name.strip()
        if not _ENV_NAME.fullmatch(name):
            raise ValueError(
                f"cli_environment line {line_number} has invalid name {name!r}")
        value = value.strip()
        if "${" in value:
            from core.expression import resolve_expression
            value = resolve_expression(
                value, owner=user_id or None,
                conversation_id=conversation_id or None)
        resolved[name] = str(value)
    return resolved


def merge_cli_environment(client: Any, managed: Mapping[str, Any] | None = None,
                          user_id: str = "",
                          conversation_id: str = "") -> dict[str, str]:
    """Resolve custom CLI variables and overlay PawFlow-managed values.

    Process identity, filesystem isolation and PawFlow bridge variables cannot
    be overridden from a service. Provider credentials/endpoints remain usable
    as custom variables when PawFlow did not derive an authoritative value.
    """
    custom = resolve_cli_environment(client, user_id, conversation_id)
    merged = {
        key: value for key, value in custom.items()
        if key not in _RUNTIME_ENV_NAMES
        and not key.startswith("PAWFLOW_")
        and not key.startswith("GIT_CONFIG_")
    }
    for key, value in (managed or {}).items():
        if value is not None:
            merged[str(key)] = str(value)
    return merged


def shell_cli_environment(client: Any, managed: Mapping[str, Any] | None = None,
                          user_id: str = "",
                          conversation_id: str = "") -> str:
    """Return shell-safe ``NAME=value`` assignments for an interactive CLI."""
    return " ".join(
        f"{key}={shlex.quote(value)}"
        for key, value in merge_cli_environment(
            client, managed, user_id, conversation_id).items()
    )


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict:
    """Recursively merge mappings, with ``override`` authoritative."""
    merged = deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def parse_toml_fragment(client: Any) -> dict:
    """Parse the optional Codex configuration fragment from llmConnection."""
    raw = raw_client_config(client, "codex_config_toml", "")
    if raw in (None, ""):
        return {}
    if not isinstance(raw, str):
        raise ValueError("codex_config_toml must be a string")
    try:
        import tomllib
    except ImportError:  # pragma: no cover - Python 3.10 only
        import tomli as tomllib
    try:
        parsed = tomllib.loads(raw)
    except Exception as exc:
        raise ValueError(f"codex_config_toml is invalid TOML: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("codex_config_toml must contain a TOML document")
    return parsed


def parse_codex_models(client: Any) -> dict | None:
    """Parse and minimally validate the optional Codex model catalog."""
    raw = raw_client_config(client, "codex_models_json", "")
    if raw in (None, ""):
        return None
    if not isinstance(raw, str):
        raise ValueError("codex_models_json must be a string")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"codex_models_json is invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("models"), list):
        raise ValueError("codex_models_json must be an object containing a models array")
    return parsed
