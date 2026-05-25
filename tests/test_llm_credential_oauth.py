from pathlib import Path
from types import SimpleNamespace
import inspect

from services.llm_connection import LLMConnectionService
from services.llm_credential_oauth import (
    LLMCredentialOAuthProviderService,
    normalize_provider,
    resolve_credential_service_id,
)
from core.service_registry import ServiceRegistry


def _sdef(service_id, service_type, config):
    return SimpleNamespace(service_id=service_id, service_type=service_type, config=config)


def test_llm_service_references_external_credential_provider():
    schema = LLMConnectionService({}).get_parameter_schema()
    assert "experimental" not in schema
    assert schema["credential_service_id"]["type"] == "service_ref"
    assert schema["credential_service_id"]["service_type"] == "llmCredentialOAuthProvider"
    assert schema["credential_service_id"]["provider_aliases"] == {
        "claude-code-interactive": "claude-code",
        "antigravity-interactive": "gemini",
    }
    assert LLMConnectionService({}).get_service_actions() == []


def test_credential_provider_exposes_login_and_pool_actions():
    actions = LLMCredentialOAuthProviderService({"provider": "codex-app-server"}).get_service_actions()
    ids = {a["id"] for a in actions}
    assert "credential_pool_manage" in ids
    assert "codex_login" in ids
    assert "gemini_login" in ids
    assert "claude_code_login" in ids
    manage = next(a for a in actions if a["id"] == "credential_pool_manage")
    assert manage["flow"] == "credential_table"
    assert manage["server_action"] == "llm_credential_pool_list"


def test_credential_pool_resolution_prefers_llm_reference(monkeypatch):
    llm = _sdef(
        "codex_appserver_llm_service",
        "llmConnection",
        {"provider": "codex-app-server", "credential_service_id": "codex_oauth_credentials"},
    )
    cred = _sdef(
        "codex_oauth_credentials",
        "llmCredentialOAuthProvider",
        {"provider": "codex-app-server"},
    )
    by_id = {llm.service_id: llm, cred.service_id: cred}
    monkeypatch.setattr(
        "services.llm_credential_oauth.get_service_def",
        lambda service_id, user_id="", conv_id="": by_id.get(service_id),
    )
    monkeypatch.setattr(
        "services.llm_credential_oauth._all_service_defs",
        lambda user_id="", conv_id="": list(by_id.values()),
    )

    assert resolve_credential_service_id("codex-app-server", llm.service_id) == cred.service_id
    assert resolve_credential_service_id("codex-app-server", cred.service_id) == cred.service_id


def test_claude_code_interactive_reuses_claude_code_credentials(monkeypatch):
    assert normalize_provider("claude-code-interactive") == "claude-code"
    llm = _sdef(
        "claude_code_interactive_llm_service",
        "llmConnection",
        {"provider": "claude-code-interactive", "credential_service_id": "claude_code_oauth_credentials"},
    )
    cred = _sdef(
        "claude_code_oauth_credentials",
        "llmCredentialOAuthProvider",
        {"provider": "claude-code"},
    )
    by_id = {llm.service_id: llm, cred.service_id: cred}
    monkeypatch.setattr(
        "services.llm_credential_oauth.get_service_def",
        lambda service_id, user_id="", conv_id="": by_id.get(service_id),
    )
    monkeypatch.setattr(
        "services.llm_credential_oauth._all_service_defs",
        lambda user_id="", conv_id="": list(by_id.values()),
    )

    assert resolve_credential_service_id("claude-code-interactive", llm.service_id) == cred.service_id
    assert resolve_credential_service_id("claude-code-interactive", cred.service_id) == cred.service_id


def test_antigravity_interactive_reuses_gemini_credentials(monkeypatch):
    assert normalize_provider("antigravity-interactive") == "gemini"
    llm = _sdef(
        "antigravity_interactive_llm_service",
        "llmConnection",
        {"provider": "antigravity-interactive", "credential_service_id": "gemini_oauth_credentials"},
    )
    cred = _sdef(
        "gemini_oauth_credentials",
        "llmCredentialOAuthProvider",
        {"provider": "gemini"},
    )
    by_id = {llm.service_id: llm, cred.service_id: cred}
    monkeypatch.setattr(
        "services.llm_credential_oauth.get_service_def",
        lambda service_id, user_id="", conv_id="": by_id.get(service_id),
    )
    monkeypatch.setattr(
        "services.llm_credential_oauth._all_service_defs",
        lambda user_id="", conv_id="": list(by_id.values()),
    )

    assert resolve_credential_service_id("antigravity-interactive", llm.service_id) == cred.service_id
    assert resolve_credential_service_id("antigravity-interactive", cred.service_id) == cred.service_id


def test_service_registry_has_startup_migration_for_legacy_llm_oauth():
    src = inspect.getsource(ServiceRegistry._migrate_llm_oauth_credentials)
    assert "credential_service_id" in src
    assert "default_credential_service_id" in src
    assert "llmCredentialOAuthProvider" in src or "SERVICE_TYPE" in src
    assert "_copy_legacy_llm_pool_secrets" in src
    assert "_migrate_llm_oauth_credentials" in inspect.getsource(ServiceRegistry._ensure_loaded)
    assert "_migrate_llm_oauth_credentials" in inspect.getsource(ServiceRegistry.reload_scope)


def test_credential_dialog_filters_provider_actions_without_parameter_rules():
    src = Path("tasks/io/chat_ui/resources.js").read_text(encoding="utf-8")
    assert "rules = rules || [];" in src
    assert "actions = actions || [];" in src
    assert "if (!rules || !rules.length) return;" not in src


def test_service_ref_ui_supports_provider_aliases():
    src = Path("tasks/io/chat_ui/resources.js").read_text(encoding="utf-8")
    assert "data-provider-aliases" in src
    assert "function _serviceRefProviderMatches" in src
    assert "s.provider === wantedProvider" not in src
