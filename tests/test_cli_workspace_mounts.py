import os
from pathlib import Path

from core.cli_workspace_mounts import (
    build_cli_workspace_mount_args,
    build_skill_mount_args,
    normalize_workspace_mount_mode,
)


def test_workspace_mount_mode_defaults_to_rw(monkeypatch):
    monkeypatch.delenv("PAWFLOW_CLI_WORKSPACE_MOUNT", raising=False)
    assert normalize_workspace_mount_mode() == "rw"


def test_workspace_mount_mode_prefers_explicit_value(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CLI_WORKSPACE_MOUNT", "rw")
    assert normalize_workspace_mount_mode("ro") == "ro"


def test_build_cli_workspace_mount_args_maps_default_and_linked_relays(tmp_path, monkeypatch):
    from core import relay_bindings

    default_root = tmp_path / "default"
    other_root = tmp_path / "other"
    default_root.mkdir()
    other_root.mkdir()

    monkeypatch.setattr(relay_bindings, "get_linked", lambda cid, agent="": ["relay.default", "relay/other"])
    monkeypatch.setattr(relay_bindings, "get_default", lambda cid, agent="": "relay.default")
    monkeypatch.setattr(relay_bindings, "list_available_relays", lambda user_id="": [
        {"relay_id": "relay.default", "connected": True, "host_root": str(default_root)},
        {"relay_id": "relay/other", "connected": True, "host_root": str(other_root)},
    ])

    args = build_cli_workspace_mount_args("conv1", "assistant", user_id="u1", mode="ro")

    assert args == [
        "-v", f"{default_root}:/workspace:ro",
        "-v", f"{default_root}:/relay/relay.default:ro",
        "-v", f"{other_root}:/relay/relay_other:ro",
    ]


def test_build_cli_workspace_mount_args_rw_has_no_ro_suffix(tmp_path, monkeypatch):
    from core import relay_bindings

    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(relay_bindings, "get_linked", lambda cid, agent="": ["relay1"])
    monkeypatch.setattr(relay_bindings, "get_default", lambda cid, agent="": "relay1")
    monkeypatch.setattr(relay_bindings, "list_available_relays", lambda user_id="": [
        {"relay_id": "relay1", "connected": True, "host_root": str(root)},
    ])

    args = build_cli_workspace_mount_args("conv1", "assistant", user_id="u1", mode="rw")

    assert args == ["-v", f"{root}:/workspace", "-v", f"{root}:/relay/relay1"]


def test_build_cli_workspace_mount_args_skips_remote_relays_without_host_root(monkeypatch):
    from core import relay_bindings

    monkeypatch.setattr(relay_bindings, "get_linked", lambda cid, agent="": ["remote"])
    monkeypatch.setattr(relay_bindings, "get_default", lambda cid, agent="": "remote")
    monkeypatch.setattr(relay_bindings, "list_available_relays", lambda user_id="": [
        {"relay_id": "remote", "connected": True, "host_root": ""},
    ])

    assert build_cli_workspace_mount_args("conv1", "assistant", mode="ro") == []


def test_build_cli_workspace_mount_args_skips_internal_conversations(monkeypatch):
    from core import relay_bindings

    monkeypatch.setenv("PAWFLOW_CLI_WORKSPACE_MOUNT", "rw")

    def _boom(*_args, **_kwargs):
        raise AssertionError("internal conversations must not read relay bindings")

    monkeypatch.setattr(relay_bindings, "get_linked", _boom)
    monkeypatch.setattr(relay_bindings, "get_default", _boom)

    assert build_cli_workspace_mount_args("_compact", "compact") == []


def test_server_start_exposes_workspace_mount_flag():
    src = Path("cli.py").read_text(encoding="utf-8")
    assert "--workspace-mount" in src
    assert "set_workspace_mount_mode" in src
    assert "PAWFLOW_CLI_WORKSPACE_MOUNT" in Path("core/cli_workspace_mounts.py").read_text(encoding="utf-8")


def test_cli_provider_pools_accept_contextual_workspace_mounts():
    for path in (
        "core/claude_code_pool.py",
        "core/codex_pool.py",
        "core/gemini_pool.py",
    ):
        src = Path(path).read_text(encoding="utf-8")
        assert "workspace_mount_args" in src
        assert "if not workspace_mount_args and self._ready" in src
        assert "*workspace_mount_args" in src


def test_cli_image_prepares_workspace_mountpoints():
    src = Path("docker/claude-code/Dockerfile").read_text(encoding="utf-8")
    assert "mkdir -p /opt/pawflow /workspace /relay /cc_sessions" in src
    assert "chown pawflow:pawflow /opt/pawflow /workspace /relay /cc_sessions" in src


def test_cli_providers_pass_identity_to_workspace_mount_builder():
    checks = {
        "core/llm_providers/claude_code.py": ("build_cli_workspace_mount_args", "conversation_id=conv_id"),
        "core/llm_providers/codex_app_server.py": ("build_cli_workspace_mount_args", "conversation_id=conv_id"),
        "core/llm_providers/gemini.py": ("build_cli_workspace_mount_args", "conversation_id=conv_id"),
    }
    for path, needles in checks.items():
        src = Path(path).read_text(encoding="utf-8")
        for needle in needles:
            assert needle in src


def test_cli_provider_namespace_workdirs_drop_user_segment(tmp_path, monkeypatch):
    from core.claude_code_interactive_pool import InteractiveClaudeCodePool
    from core.llm_providers import codex_app_server, gemini
    from core.llm_providers.codex_app_server import LLMCodexAppServerMixin
    from core.llm_providers.gemini import LLMGeminiMixin

    workdir = tmp_path / "user1" / "conv1" / "assistant"
    monkeypatch.setattr(codex_app_server, "_get_sessions_base", lambda: str(tmp_path))
    monkeypatch.setattr(gemini, "_get_sessions_base", lambda: str(tmp_path))

    assert LLMCodexAppServerMixin._codex_app_container_dir(
        str(workdir)) == "/cc_sessions/conv1/assistant"
    assert LLMGeminiMixin._gemini_acp_container_dir(
        str(workdir)) == "/cc_sessions/conv1/assistant"
    assert InteractiveClaudeCodePool._physical_container_workdir(
        "user1", "conv1", "assistant") == "/cc_sessions/user1/conv1/assistant"
    assert InteractiveClaudeCodePool._container_workdir(
        "user1", "conv1", "assistant") == "/cc_sessions/conv1/assistant"


class _FakeStore:
    def __init__(self):
        self.data = {"linked": {}, "default": {}}
        self.invalidated_all = 0
        self.invalidated_agents = []

    def get_extra_cached(self, _cid, _key, default=None):
        return self.data or default

    def set_extra(self, _cid, _key, value):
        self.data = value

    def invalidate_claude_sessions(self, _cid):
        self.invalidated_all += 1

    def invalidate_claude_session_for_agent(self, _cid, agent):
        self.invalidated_agents.append(agent)


def test_relay_binding_changes_invalidate_cli_sessions_when_mount_enabled(monkeypatch):
    from core import relay_bindings

    store = _FakeStore()
    monkeypatch.setenv("PAWFLOW_CLI_WORKSPACE_MOUNT", "ro")
    monkeypatch.setattr(relay_bindings, "_get_store", lambda: store)

    assert relay_bindings.link_relay("conv1", "relay1") is True
    assert store.invalidated_all == 1

    assert relay_bindings.link_relay("conv1", "relay2", agent="assistant") is True
    assert store.invalidated_agents == ["assistant"]


def test_interactive_cc_pool_mounts_skill_dirs():
    # The persistent interactive CC container must bind-mount skill scope
    # dirs so SKILL.md assets resolve, like the batch claude-code pool.
    src = Path("core/claude_code_interactive_pool.py").read_text(encoding="utf-8")
    assert "build_skill_mount_args" in src
    assert "_spawn_container(" in src


def test_build_skill_mount_args_mounts_scope_dirs(tmp_path, monkeypatch):
    from core import docker_utils, paths

    repo = tmp_path / "repository"
    monkeypatch.setattr(paths, "REPOSITORY_DIR", repo)
    monkeypatch.setattr(docker_utils, "to_host_path", lambda p: p)
    monkeypatch.setattr(docker_utils, "translate_path", lambda p: p)

    args = build_skill_mount_args("conv1", "assistant", user_id="u1")

    skills = (repo / "skills").resolve()
    assert args == [
        "-v", f"{skills / 'global'}:/skills/global:ro",
        "-v", f"{skills / 'users' / 'u1'}:/skills/users/u1:ro",
    ]
    # Mount points are created so a skill written mid-session is visible.
    assert (skills / "global").is_dir()
    assert (skills / "users" / "u1").is_dir()


def test_build_skill_mount_args_global_only_without_user(tmp_path, monkeypatch):
    from core import docker_utils, paths

    repo = tmp_path / "repository"
    monkeypatch.setattr(paths, "REPOSITORY_DIR", repo)
    monkeypatch.setattr(docker_utils, "to_host_path", lambda p: p)
    monkeypatch.setattr(docker_utils, "translate_path", lambda p: p)

    args = build_skill_mount_args("conv1", "assistant")

    skills = (repo / "skills").resolve()
    assert args == ["-v", f"{skills / 'global'}:/skills/global:ro"]


def test_skill_mount_dir_mirrors_repo_layout(tmp_path, monkeypatch):
    from core import paths, skill_resolver

    monkeypatch.setattr(paths, "REPOSITORY_DIR", tmp_path / "repository")
    skills = tmp_path / "repository" / "skills"

    global_def = {"skill_root": str(skills / "global" / "review-pr")}
    assert skill_resolver.skill_mount_dir(
        "review-pr", global_def) == "/skills/global/review-pr"

    conv_def = {"skill_root": str(skills / "users" / "u1" / "c1" / "deploy")}
    assert skill_resolver.skill_mount_dir(
        "deploy", conv_def) == "/skills/users/u1/c1/deploy"

    # Unknown skill root falls back to a flat path.
    assert skill_resolver.skill_mount_dir("review-pr", {}) == "/skills/review-pr"
