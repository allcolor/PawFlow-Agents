"""Chat theme repository and action invariants."""

import base64
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from core import FlowFile
from core.chat_themes import css_from_upload, list_themes
from core.conversation_store import ConversationStore
from core.resource_store import ResourceStore
from tasks.ai.actions.agent_resource import _handle_agent_resource


@pytest.fixture(autouse=True)
def reset_repositories(tmp_path):
    ResourceStore.reset()
    ConversationStore.reset()
    from core import paths as paths_mod
    from core.repository import ScopedRepository
    repo_dir = tmp_path / "repository"
    repo_dir.mkdir()
    ConversationStore.instance()._store_dir = tmp_path / "conversations"
    with patch.object(paths_mod, "REPOSITORY_DIR", repo_dir):
        ScopedRepository.reset()
        yield tmp_path
    ScopedRepository.reset()
    ResourceStore.reset()
    ConversationStore.reset()


def _ff(admin=False):
    f = FlowFile(b"")
    f.set_attribute("http.auth.roles", "admin" if admin else "user")
    return f


def _call(action, body, user_id="u1", admin=False):
    store = ConversationStore.instance()
    ff = _ff(admin=admin)
    result = _handle_agent_resource(None, action, body, store, user_id, ff)
    assert result == [ff]
    return json.loads(ff.content.decode("utf-8"))


def _write_theme(scope, name, css="", title="", user_id="u1", conversation_id=""):
    from core.chat_themes import THEME_META
    from core.paths import repo_dir

    repo_scope = "conv" if scope == "conversation" else scope
    root = repo_dir("theme", repo_scope, user_id if repo_scope != "global" else "", conversation_id)
    theme_dir = root / name
    theme_dir.mkdir(parents=True, exist_ok=True)
    (theme_dir / THEME_META).write_text(json.dumps({
        "name": name,
        "title": title or name,
        "description": "test theme",
    }), encoding="utf-8")
    (theme_dir / "theme.css").write_text(css or ":root { --pf-bg: #fff; --pf-accent: #000; --pf-code-bg: #eee; }", encoding="utf-8")
    return theme_dir


def test_shipped_themes_are_directory_resources():
    root = Path("data/repository/theme/global")
    assert root.is_dir()
    assert not Path("data/repository/themes").exists()

    expected = {
        "pawflow_dark", "matrix", "mr_robot", "light",
        "paper", "nord_light", "sage_light", "rose_light",
        "claude", "chatgpt", "qwen", "deepseek", "grok", "gemini",
        "solarized_dark", "dracula", "midnight_blue", "high_contrast",
    }
    found = {p.name for p in root.iterdir() if p.is_dir()}
    assert found >= expected
    for name in expected:
        assert (root / name / "theme.json").is_file()
        assert list((root / name).glob("*.css"))


def test_repository_themes_are_listed_from_scope_dirs():
    _write_theme("global", "global_theme", title="Global Theme")
    _write_theme("user", "user_theme", title="User Theme")
    _write_theme("conversation", "conv_theme", title="Conversation Theme", conversation_id="c1")

    themes = list_themes("u1", "c1")

    assert {t["ref"] for t in themes} >= {
        "global:global_theme",
        "user:user_theme",
        "conversation:conv_theme",
    }
    assert any(t["title"] == "Global Theme" for t in themes)


def test_resource_store_theme_create_uses_directory_resource():
    ResourceStore.instance().create("theme", "store_theme", "u1", {
        "title": "Store Theme",
        "css": ":root { --pf-bg: #111; --pf-accent: #eee; --pf-code-bg: #222; }",
    })

    from core.paths import repo_dir
    theme_dir = repo_dir("theme", "user", "u1") / "store_theme"
    assert theme_dir.is_dir()
    assert (theme_dir / "theme.json").is_file()
    assert (theme_dir / "theme.css").is_file()
    assert not (repo_dir("theme", "user", "u1") / "store_theme.json").exists()

    loaded = ResourceStore.instance().get("theme", "store_theme", "u1")
    assert loaded["title"] == "Store Theme"
    assert "--pf-bg" in loaded["css"]


def test_zip_theme_import_inlines_relative_assets():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("theme/main.css", "body { background-image: url('../img/bg.png'); }")
        zf.writestr("img/bg.png", b"PNGDATA")
    upload = {
        "filename": "theme.zip",
        "base64": base64.b64encode(buf.getvalue()).decode("ascii"),
    }

    css = css_from_upload(upload)

    assert "theme/main.css" in css
    assert "data:image/png;base64," in css
    assert "../img/bg.png" not in css


def test_create_and_apply_conversation_theme_round_trip():
    store = ConversationStore.instance()
    cid = "conv_theme"
    store.save(cid, [], user_id="u1")
    _write_theme("global", "pawflow_dark")

    created = _call("create_chat_theme", {
        "conversation_id": cid,
        "name": "conv_theme",
        "title": "Conversation Theme",
        "scope": "conversation",
        "css": "body { color: red !important; }",
    })
    assert created["ok"] is True

    listed = _call("list_chat_themes", {"conversation_id": cid})
    assert any(t["ref"] == "conversation:conv_theme" for t in listed["themes"])

    applied = _call("apply_chat_theme", {
        "conversation_id": cid,
        "theme_ref": "conversation:conv_theme",
        "conversation_override": True,
    })
    assert applied["ok"] is True
    assert "color: red" in applied["css"]
    assert store.get_extra(cid, "theme_ref", user_id="u1") == "conversation:conv_theme"

    cleared = _call("apply_chat_theme", {
        "conversation_id": cid,
        "theme_ref": "global:pawflow_dark",
        "conversation_override": False,
    })
    assert cleared["ok"] is True
    assert store.get_extra(cid, "theme_ref", user_id="u1") is None


def test_apply_global_theme_without_conversation():
    _write_theme("global", "light", ":root { --pf-bg: #fff; --pf-accent: #111; --pf-code-bg: #eee; }")

    applied = _call("apply_chat_theme", {
        "theme_ref": "global:light",
        "conversation_override": False,
    })

    assert applied["ok"] is True
    assert applied["theme_ref"] == "global:light"
    assert "--pf-bg:" in applied["css"]


def test_theme_ui_selector_and_repository_entries_exist():
    template = open("tasks/io/chat_ui/template.html", encoding="utf-8").read()
    serve = open("tasks/io/serve_chat_ui.py", encoding="utf-8").read()
    resources = open("tasks/io/chat_ui/resources.js", encoding="utf-8").read()
    themes_js = open("tasks/io/chat_ui/themes.js", encoding="utf-8").read()

    assert template.index('id="themeSelect"') < template.index('id="permissionMode"')
    assert 'id="conversationThemeSelect"' in template
    assert template.index('id="conversationThemeSelect"') < template.index('id="resourcesPanel"')
    assert '"themes.js"' in serve
    assert "Themes Repository" in resources
    assert "showThemeCreator" in resources
    assert "accept=\".css,.zip,text/css,application/zip\"" in themes_js
    assert "pawflow_theme_ref" in themes_js
    assert "pawflow_conv_theme_refs" in themes_js
    assert "global:pawflow_dark" in themes_js
    assert "onGlobalThemeSelectChange" in themes_js
    assert "onConversationThemeSelectChange" in themes_js


def test_shipped_theme_css_only_sets_palette_variables():
    for css_file in Path("data/repository/theme/global").glob("*/theme.css"):
        css = css_file.read_text(encoding="utf-8")
        assert css.strip().startswith(":root {")
        assert ".msg" not in css
        assert ".sidebar" not in css
        assert "--pf-bg:" in css
        assert "--pf-accent:" in css
        assert "--pf-code-bg:" in css
