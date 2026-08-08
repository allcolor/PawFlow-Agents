import pytest

from core.cli_process_config import (
    deep_merge,
    merge_cli_environment,
    parse_codex_models,
    parse_toml_fragment,
    resolve_cli_environment,
    shell_cli_environment,
)


class _Client:
    def __init__(self, config):
        self._config_ref = config


class _CapturePool:
    def __init__(self):
        self.extra_env = None

    def acquire(self, workspace_mount_args=None):
        return "container"

    def _exec(self, *args, extra_env=None, **kwargs):
        self.extra_env = extra_env
        return "proc"

    exec_claude = _exec
    exec_codex = _exec
    exec_gemini = _exec


def _disable_cli_mounts(monkeypatch):
    monkeypatch.setattr(
        "core.cli_workspace_mounts.build_cli_workspace_mount_args",
        lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "core.cli_workspace_mounts.build_skill_mount_args",
        lambda *args, **kwargs: [])


def test_cli_environment_parses_comments_empty_values_and_first_equals(monkeypatch):
    client = _Client({
        "cli_environment": "# endpoint\nTOKEN=${secret}\nEMPTY=\nURL=a=b\n",
    })
    seen = []

    def resolve(value, **kwargs):
        seen.append((value, kwargs))
        return "resolved-secret"

    monkeypatch.setattr("core.expression.resolve_expression", resolve)
    assert resolve_cli_environment(client, "u1", "c1") == {
        "TOKEN": "resolved-secret", "EMPTY": "", "URL": "a=b",
    }
    assert seen == [("${secret}", {
        "owner": "u1", "conversation_id": "c1",
    })]


@pytest.mark.parametrize("raw", ["NO_EQUALS", "BAD-NAME=x", "1BAD=x"])
def test_cli_environment_rejects_invalid_lines(raw):
    with pytest.raises(ValueError, match="cli_environment line 1"):
        resolve_cli_environment(_Client({"cli_environment": raw}))


def test_deep_merge_keeps_user_provider_but_pawflow_overrides_critical_leaf():
    user = {
        "model": "deepseek-v4-flash",
        "model_provider": "deepseek",
        "model_providers": {"deepseek": {"base_url": "https://api.deepseek.com"}},
        "mcp_servers": {"pawflow": {"command": "evil"}, "custom": {"command": "ok"}},
    }
    managed = {"mcp_servers": {"pawflow": {"command": "/usr/bin/python3"}}}
    result = deep_merge(user, managed)
    assert result["model"] == "deepseek-v4-flash"
    assert result["model_providers"]["deepseek"]["base_url"].startswith("https://")
    assert result["mcp_servers"]["custom"]["command"] == "ok"
    assert result["mcp_servers"]["pawflow"]["command"] == "/usr/bin/python3"


def test_codex_fragments_are_validated():
    client = _Client({
        "codex_config_toml": 'model = "deepseek-v4-flash"\n'
                             '[model_providers.deepseek]\nname = "DeepSeek"\n',
        "codex_models_json": '{"models":[{"slug":"deepseek-v4-flash"}]}',
    })
    assert parse_toml_fragment(client)["model"] == "deepseek-v4-flash"
    assert parse_codex_models(client)["models"][0]["slug"] == "deepseek-v4-flash"


def test_invalid_codex_fragments_fail_before_files_are_written():
    with pytest.raises(ValueError, match="invalid TOML"):
        parse_toml_fragment(_Client({"codex_config_toml": "[broken"}))
    with pytest.raises(ValueError, match="models array"):
        parse_codex_models(_Client({"codex_models_json": "{}"}))


def test_cli_environment_keeps_managed_runtime_values_authoritative():
    client = _Client({
        "cli_environment": (
            "CUSTOM=hello world\nHOME=/tmp/escape\n"
            "PAWFLOW_INTERNAL_TOKEN=evil\nOPENAI_BASE_URL=https://user\n"),
    })
    assert merge_cli_environment(client, {
        "OPENAI_BASE_URL": "https://managed",
        "PAWFLOW_INTERNAL_TOKEN": "real",
    }) == {
        "CUSTOM": "hello world",
        "OPENAI_BASE_URL": "https://managed",
        "PAWFLOW_INTERNAL_TOKEN": "real",
    }


def test_shell_cli_environment_quotes_values():
    client = _Client({"cli_environment": "CUSTOM=hello world\nEMPTY=\n"})
    assert shell_cli_environment(client) == "CUSTOM='hello world' EMPTY=''"


def test_claude_batch_injects_resolved_environment(monkeypatch):
    from core.claude_code_pool import ClaudeCodePool
    from core.llm_client import LLMClient

    pool = _CapturePool()
    monkeypatch.setattr(ClaudeCodePool, "instance", lambda: pool)
    _disable_cli_mounts(monkeypatch)
    client = LLMClient(provider="claude-code", config={
        "api_key": "managed-key",
        "base_url": "https://managed.example",
        "cli_environment": (
            "CUSTOM=value\nEMPTY=\nHOME=/escape\n"
            "ANTHROPIC_API_KEY=user-key\n"),
    })
    proc, container = client._pool_popen(
        "/tmp/session", ["claude"], user_id="u", conversation_id="c")
    assert (proc, container) == ("proc", "container")
    assert pool.extra_env["CUSTOM"] == "value"
    assert pool.extra_env["EMPTY"] == ""
    assert "HOME" not in pool.extra_env
    assert pool.extra_env["ANTHROPIC_API_KEY"] == "managed-key"


def test_codex_batch_injects_resolved_environment(monkeypatch):
    from core.codex_pool import CodexPool
    from core.llm_client import LLMClient

    pool = _CapturePool()
    monkeypatch.setattr(CodexPool, "instance", lambda: pool)
    _disable_cli_mounts(monkeypatch)
    client = LLMClient(provider="codex-app-server", config={
        "api_key": "managed-key",
        "base_url": "https://managed.example/v1",
        "cli_environment": "CUSTOM=value\nOPENAI_API_KEY=user-key\n",
    })
    client._codex_pool_popen(
        "/tmp/session", ["app-server"], user_id="u", conversation_id="c")
    assert pool.extra_env["CUSTOM"] == "value"
    assert pool.extra_env["OPENAI_API_KEY"] == "managed-key"
    assert pool.extra_env["OPENAI_BASE_URL"] == "https://managed.example/v1"


def test_gemini_batch_injects_resolved_environment(monkeypatch):
    from core.gemini_pool import GeminiPool
    from core.llm_client import LLMClient

    pool = _CapturePool()
    monkeypatch.setattr(GeminiPool, "instance", lambda: pool)
    _disable_cli_mounts(monkeypatch)
    client = LLMClient(provider="gemini", config={
        "api_key": "managed-key",
        "cli_environment": "CUSTOM=value\nGEMINI_API_KEY=user-key\n",
    })
    client._gemini_pool_popen(
        "/tmp/session", ["--acp"], user_id="u", conversation_id="c")
    assert pool.extra_env["CUSTOM"] == "value"
    assert pool.extra_env["GEMINI_API_KEY"] == "managed-key"
