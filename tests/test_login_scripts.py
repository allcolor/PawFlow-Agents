from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_gemini_server_login_opens_captured_oauth_url_in_browser():
    script = (ROOT / "docker" / "claude-code" / "gemini_auth_login.sh").read_text(
        encoding="utf-8"
    )

    assert "xterm" not in script
    assert "https://accounts\\.google\\.com/o/oauth2/" in script
    assert "/usr/local/bin/open-browser \"$AUTH_URL\"" in script
    assert "printf '/exit\\n' | gemini" in script
    assert "oauth_creds.json" in script


def test_codex_and_claude_server_login_still_keep_debug_terminal():
    # Codex and Claude Code already launch their OAuth browser flow directly;
    # the terminal is only a debug aid there. Gemini needs explicit URL capture.
    for name in ("auth_login.sh", "codex_auth_login.sh"):
        script = (ROOT / "docker" / "claude-code" / name).read_text(encoding="utf-8")
        assert "xterm" in script
