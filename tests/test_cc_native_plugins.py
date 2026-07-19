"""Native Claude Code plugin support: claude_plugins / claude_marketplaces
params -> enabledPlugins / extraKnownMarketplaces merged into the session's
.claude/settings.json without clobbering other keys (CCI hooks etc.).
"""

import json
import os

from core.llm_providers.claude_code_session import ClaudeCodeSessionMixin


class _Host(ClaudeCodeSessionMixin):
    def __init__(self, config=None):
        self.config = config or {}


def _settings(tmp_path):
    path = os.path.join(str(tmp_path), ".claude", "settings.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class TestPluginSettings:
    def test_no_config_no_file(self, tmp_path):
        _Host()._cc_write_plugin_settings(str(tmp_path))
        assert not os.path.exists(
            os.path.join(str(tmp_path), ".claude", "settings.json"))

    def test_plugins_and_marketplaces_written(self, tmp_path):
        _Host({
            "claude_plugins": "pr-review@acme, deploy@acme",
            "claude_marketplaces":
                "acme=acme-corp/cc-plugins, lab=https://git.example/lab.git",
        })._cc_write_plugin_settings(str(tmp_path))
        settings = _settings(tmp_path)
        assert settings["enabledPlugins"] == {"pr-review@acme": True,
                                              "deploy@acme": True}
        assert settings["extraKnownMarketplaces"]["acme"] == {
            "source": {"source": "github", "repo": "acme-corp/cc-plugins"}}
        assert settings["extraKnownMarketplaces"]["lab"] == {
            "source": {"source": "git", "url": "https://git.example/lab.git"}}

    def test_merge_preserves_other_keys(self, tmp_path):
        cc_dir = tmp_path / ".claude"
        cc_dir.mkdir()
        (cc_dir / "settings.json").write_text(json.dumps({
            "hooks": {"Stop": []},
            "permissions": {"deny": ["Bash"]},
        }), encoding="utf-8")
        _Host({"claude_plugins": "x@m",
               "claude_marketplaces": "m=o/r"})._cc_write_plugin_settings(
            str(tmp_path))
        settings = _settings(tmp_path)
        assert settings["hooks"] == {"Stop": []}
        assert settings["permissions"] == {"deny": ["Bash"]}
        assert settings["enabledPlugins"] == {"x@m": True}

    def test_removed_config_clears_stale_keys(self, tmp_path):
        _Host({"claude_plugins": "x@m",
               "claude_marketplaces": "m=o/r"})._cc_write_plugin_settings(
            str(tmp_path))
        _Host({})._cc_write_plugin_settings(str(tmp_path))
        settings = _settings(tmp_path)
        assert "enabledPlugins" not in settings
        assert "extraKnownMarketplaces" not in settings

    def test_malformed_marketplace_entries_skipped(self, tmp_path):
        _Host({"claude_plugins": "x@m",
               "claude_marketplaces": "nosource, =o/r, ok=o/r"}
              )._cc_write_plugin_settings(str(tmp_path))
        settings = _settings(tmp_path)
        assert list(settings["extraKnownMarketplaces"]) == ["ok"]

    def test_called_from_setup_mcp_config(self):
        import inspect
        src = inspect.getsource(ClaudeCodeSessionMixin._setup_mcp_config)
        assert "_cc_write_plugin_settings" in src


class TestServiceSchema:
    def test_params_declared_and_scoped_to_cc_providers(self):
        from services.llm_connection import LLMConnectionService
        svc = LLMConnectionService.__new__(LLMConnectionService)
        schema = LLMConnectionService.get_parameter_schema(svc)
        assert "claude_plugins" in schema
        assert "claude_marketplaces" in schema
        rules = LLMConnectionService.get_parameter_rules(svc)
        visible_for = sorted(
            p for r in rules
            if r["set"].get("claude_plugins", {}).get("visible")
            for p in r["when"]["provider"])
        assert visible_for == ["claude-code", "claude-code-interactive"]
