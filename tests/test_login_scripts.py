from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_gemini_server_login_runs_cli_inside_visible_tty():
    script = (ROOT / "docker" / "claude-code" / "gemini_auth_login.sh").read_text(
        encoding="utf-8"
    )

    assert "xterm" in script
    assert "gemini 2>&1 | tee -a /tmp/gemini-auth.log" in script
    assert "printf '/exit" not in script
    assert "oauth_creds.json" in script


def test_all_server_login_scripts_keep_a_visible_tty():
    for name in ("auth_login.sh", "codex_auth_login.sh", "gemini_auth_login.sh"):
        script = (ROOT / "docker" / "claude-code" / name).read_text(encoding="utf-8")
        assert "xterm" in script


def test_gemini_server_login_mounts_current_script():
    service_flow = (ROOT / "tasks" / "ai" / "actions" / "service_flow.py").read_text(
        encoding="utf-8"
    )

    assert "gemini_auth_login.sh:ro" in service_flow
    assert "to_host_path" in service_flow
    assert "translate_path" in service_flow
