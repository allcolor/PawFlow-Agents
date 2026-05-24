"""Tests for PFP UI extension support (phase 2).

Covers:
  - manifest validation for the `ui_extension` object type
  - install records carry assets (with sha256+size), slots, hooks, i18n
  - version_compat must be exactly `ui.v1`
  - dev_load / uninstall round-trip
  - `list_installed_ui_extensions` returns the expected manifest
  - the asset-serving task `servePfpExtensionAssets` validates hash + path
  - the chat boot block injects PAWFLOW_EXTENSIONS with hashed asset URLs
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from core import pfp_package


def _write_ui_extension_pkg(root: Path, keypair, *, version: str = "1.0.0",
                            package_id: str = "examples.ui-hello",
                            extra_assets=None, with_styles: bool = True,
                            with_i18n: bool = False,
                            version_compat: str = "ui.v1",
                            extra_slot=None, extra_hook=None,
                            invalid_ext_path: str = ""):
    pkg = root / f"{package_id}.pfpdir"
    ui_dir = pkg / "content" / "ui"
    ui_dir.mkdir(parents=True)
    (ui_dir / "extension.js").write_text(
        "pawflow.register('" + package_id + "', function (pfp) {});\n",
        encoding="utf-8")
    if with_styles:
        (ui_dir / "extension.css").write_text(
            "[data-pf-ext='" + package_id + "'] { color: inherit; }\n",
            encoding="utf-8")
    i18n_dir = ui_dir / "i18n"
    if with_i18n:
        i18n_dir.mkdir(parents=True)
        (i18n_dir / "en.json").write_text(
            json.dumps({"hello.menu": "Hello"}), encoding="utf-8")
    if invalid_ext_path:
        # Write a file with a disallowed extension so the validator can reject it.
        invalid = ui_dir / Path(invalid_ext_path).name
        invalid.write_bytes(b"binary")
    assets = {"scripts": ["content/ui/extension.js"]}
    if with_styles:
        assets["styles"] = ["content/ui/extension.css"]
    if with_i18n:
        assets["i18n"] = {"en": "content/ui/i18n/en.json"}
    if extra_assets:
        for key, value in extra_assets.items():
            assets[key] = value
    if invalid_ext_path:
        assets.setdefault("scripts", []).append(invalid_ext_path)
    slots = [
        {"slot": "action_menu", "id": "hello.open",
         "icon": "👋", "label_key": "hello.menu"},
    ]
    if extra_slot:
        slots.append(extra_slot)
    hooks = ["boot", "conversation_changed"]
    if extra_hook:
        hooks.append(extra_hook)
    manifest = {
        "format": "pawflow.package.v1",
        "package": package_id,
        "version": version,
        "description": "UI extension test fixture",
        "developer": {
            "email": "dev@example.com",
            "public_key": keypair["public_key"],
        },
        "objects": [
            {
                "id": "ui_extension:main",
                "type": "ui_extension",
                "name": "main",
                "version_compat": version_compat,
                "assets": assets,
                "slots": slots,
                "hooks": hooks,
            },
        ],
    }
    (pkg / "pfp.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return pkg


@pytest.fixture(autouse=True)
def _mock_llm_review(monkeypatch):
    """Stub the summarizer review so install plans do not hit a real LLM.

    Phase 4 review pipeline runs on every `ui_extension` install. Without an
    LLM-backed reviewer the runtime returns `risk=block` and refuses install,
    which would mask the actual fixture behaviour we want to exercise. The
    stub mirrors what a clean review on safe content would emit.
    """
    import core.package_review as package_review

    class _ReviewLLM:
        def complete(self, **kwargs):
            class _Response:
                content = json.dumps({
                    "risk": "low",
                    "allowed": True,
                    "requires_human_review": False,
                    "findings": [],
                    "sanitized_summary": "ok",
                    "recommended_changes": [],
                })
            return _Response()
    monkeypatch.setattr(
        package_review, "_resolve_review_llm",
        lambda user_id, conversation_id: (_ReviewLLM(), None, "review_llm"))


@pytest.fixture
def keypair():
    return pfp_package.create_signing_key()


# ── Validation ────────────────────────────────────────────────────────────────────────

def test_ui_extension_is_an_installable_type():
    assert "ui_extension" in pfp_package._INSTALLABLE_TYPES


def test_ui_extension_inspect_lists_slots_hooks_assets(tmp_path, keypair):
    pkgdir = _write_ui_extension_pkg(tmp_path, keypair)
    built = pfp_package.build_pfp(
        str(pkgdir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    rows = [r for r in plan["objects"] if r["type"] == "ui_extension"]
    assert len(rows) == 1
    row = rows[0]
    assert row["installable"] is True
    assert row["status"] == "new"
    caps = row["capabilities"]["ui_extension"]
    assert caps["version_compat"] == "ui.v1"
    assert {"slot": "action_menu", "id": "hello.open"} in caps["slots"]
    assert "boot" in caps["hooks"]
    assert caps["asset_count"] >= 2


def test_ui_extension_rejects_wrong_version_compat(tmp_path, keypair):
    pkgdir = _write_ui_extension_pkg(tmp_path, keypair, version_compat="ui.v2")
    built = pfp_package.build_pfp(
        str(pkgdir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    row = next(r for r in plan["objects"] if r["type"] == "ui_extension")
    assert row["status"] == "blocked"
    assert "version_compat" in row["reason"]


def test_ui_extension_rejects_unknown_slot(tmp_path, keypair):
    pkgdir = _write_ui_extension_pkg(
        tmp_path, keypair,
        extra_slot={"slot": "made_up_slot", "id": "x"})
    built = pfp_package.build_pfp(
        str(pkgdir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    row = next(r for r in plan["objects"] if r["type"] == "ui_extension")
    assert row["status"] == "blocked"
    assert "made_up_slot" in row["reason"]


def test_ui_extension_rejects_unknown_hook(tmp_path, keypair):
    pkgdir = _write_ui_extension_pkg(tmp_path, keypair, extra_hook="not_a_hook")
    built = pfp_package.build_pfp(
        str(pkgdir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    row = next(r for r in plan["objects"] if r["type"] == "ui_extension")
    assert row["status"] == "blocked"
    assert "not_a_hook" in row["reason"]


def test_ui_extension_rejects_disallowed_asset_extension(tmp_path, keypair):
    pkgdir = _write_ui_extension_pkg(
        tmp_path, keypair, invalid_ext_path="content/ui/payload.exe")
    built = pfp_package.build_pfp(
        str(pkgdir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    row = next(r for r in plan["objects"] if r["type"] == "ui_extension")
    assert row["status"] == "blocked"
    assert "payload.exe" in row["reason"] or "not allowed" in row["reason"]


# ── Install ────────────────────────────────────────────────────────────────────────────

def _install_ui_pkg(tmp_path, keypair, **kw):
    pkgdir = _write_ui_extension_pkg(tmp_path, keypair, **kw)
    built = pfp_package.build_pfp(
        str(pkgdir), private_key=keypair["private_key"])
    return pfp_package.install_pfp(
        built["path"], user_id="alice",
        include=["ui_extension:main"]), built


def test_ui_extension_install_writes_record_with_assets(tmp_path, keypair, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    result, _ = _install_ui_pkg(tmp_path, keypair)
    assert result["ok"] is True, result
    assert any(o["id"] == "ui_extension:main" for o in result["installed"])
    records = pfp_package.list_installed_ui_extensions(
        user_id="alice", scope="user")
    assert len(records) == 1
    rec = records[0]
    assert rec["package"] == "examples.ui-hello"
    assert rec["version_compat"] == "ui.v1"
    assets = {a["path"]: a for a in rec["assets"]}
    assert "content/ui/extension.js" in assets
    assert assets["content/ui/extension.js"]["sha256"].startswith("sha256:")
    assert assets["content/ui/extension.js"]["size"] > 0
    assert "content/ui/extension.css" in assets
    slots = {(s["slot"], s["id"]) for s in rec["slots"]}
    assert ("action_menu", "hello.open") in slots
    assert "boot" in rec["hooks"]
    assert Path(rec["content_dir"]).is_dir()
    asset_disk = Path(rec["content_dir"]) / "content/ui/extension.js"
    assert asset_disk.is_file()


def test_ui_extension_uninstall_removes_record_and_content(tmp_path, keypair, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    _install_ui_pkg(tmp_path, keypair)
    assert pfp_package.list_installed_ui_extensions(
        user_id="alice", scope="user")
    out = pfp_package.uninstall_pfp(
        "examples.ui-hello", user_id="alice", scope="user")
    assert out["ok"] is True
    assert pfp_package.list_installed_ui_extensions(
        user_id="alice", scope="user") == []


def test_ui_extension_dev_load_keeps_source_dir(tmp_path, keypair, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    pkgdir = _write_ui_extension_pkg(tmp_path, keypair)
    out = pfp_package.dev_load_pfp(
        str(pkgdir), user_id="alice", conversation_id="conv1",
        include=["ui_extension:main"], scope="conversation")
    assert out["ok"] is True
    records = pfp_package.list_installed_ui_extensions(
        user_id="alice", conversation_id="conv1", scope="conversation")
    assert any(r["package"] == "examples.ui-hello" for r in records)


# ── Asset serving task ─────────────────────────────────────────────────────────────────

def _build_asset_request(http_path: str, *, principal: str = "alice"):
    from core import FlowFile
    ff = FlowFile(content=b"")
    ff.set_attribute("http.path", http_path)
    if principal:
        ff.set_attribute("http.auth.principal", principal)
    return ff


def _get_asset_url(rec, file_path: str) -> str:
    asset = next(a for a in rec["assets"] if a["path"] == file_path)
    short = asset["sha256"].replace("sha256:", "")[:16]
    return f"/chat/ext/{rec['package']}/{short}/{file_path}"


def _serve_asset_task(http_path: str, principal: str = "alice"):
    from tasks.io.serve_pfp_ext_assets import ServePfpExtensionAssetsTask
    task = ServePfpExtensionAssetsTask({})
    ff = _build_asset_request(http_path, principal=principal)
    return task.execute(ff)[0]


def test_pfp_ext_assets_serves_installed_script(tmp_path, keypair, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    _install_ui_pkg(tmp_path, keypair)
    rec = pfp_package.list_installed_ui_extensions(
        user_id="alice", scope="user")[0]
    url = _get_asset_url(rec, "content/ui/extension.js")
    out = _serve_asset_task(url)
    assert out.get_attribute("http.response.status") == "200"
    assert out.get_attribute("http.response.header.Content-Type").startswith("application/javascript")
    assert b"pawflow.register" in out.get_content()


def test_pfp_ext_assets_rejects_unknown_package(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    out = _serve_asset_task("/chat/ext/no.such.pkg/abc1234567890123/extension.js")
    assert out.get_attribute("http.response.status") == "404"


def test_pfp_ext_assets_rejects_wrong_hash(tmp_path, keypair, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    _install_ui_pkg(tmp_path, keypair)
    out = _serve_asset_task(
        "/chat/ext/examples.ui-hello/0000000000000000/content/ui/extension.js")
    assert out.get_attribute("http.response.status") == "404"


def test_pfp_ext_assets_rejects_unauth(tmp_path, keypair, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    _install_ui_pkg(tmp_path, keypair)
    rec = pfp_package.list_installed_ui_extensions(
        user_id="alice", scope="user")[0]
    url = _get_asset_url(rec, "content/ui/extension.js")
    out = _serve_asset_task(url, principal="")
    assert out.get_attribute("http.response.status") == "404"


def test_pfp_ext_assets_rejects_disallowed_extension(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    out = _serve_asset_task("/chat/ext/x/abc1234567890123/payload.exe")
    assert out.get_attribute("http.response.status") == "404"


def test_pfp_ext_assets_rejects_path_traversal(tmp_path, keypair, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    _install_ui_pkg(tmp_path, keypair)
    rec = pfp_package.list_installed_ui_extensions(
        user_id="alice", scope="user")[0]
    short = rec["assets"][0]["sha256"].replace("sha256:", "")[:16]
    out = _serve_asset_task(
        f"/chat/ext/examples.ui-hello/{short}/../../../etc/passwd")
    assert out.get_attribute("http.response.status") == "404"


def test_pfp_ext_assets_detects_tampered_file(tmp_path, keypair, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    _install_ui_pkg(tmp_path, keypair)
    rec = pfp_package.list_installed_ui_extensions(
        user_id="alice", scope="user")[0]
    url = _get_asset_url(rec, "content/ui/extension.js")
    # Tamper with the file on disk after install.
    target = Path(rec["content_dir"]) / "content/ui/extension.js"
    target.write_text("alert('pwn');\n", encoding="utf-8")
    out = _serve_asset_task(url)
    assert out.get_attribute("http.response.status") == "404"


# ── Boot block ──────────────────────────────────────────────────────────────────────────

def test_initial_extensions_block_is_empty_without_user():
    from tasks.io.serve_chat_ui import _initial_extensions_block
    out = _initial_extensions_block(user_id="", conversation_id="")
    assert "window.PAWFLOW_EXTENSIONS=[]" in out


def test_initial_extensions_block_skips_pfp_import_without_records(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    import sys
    sys.modules.pop("core.pfp_package", None)

    from tasks.io.serve_chat_ui import _initial_extensions_block
    out = _initial_extensions_block(user_id="alice", conversation_id="conv1")

    assert "window.PAWFLOW_EXTENSIONS=[]" in out
    assert "core.pfp_package" not in sys.modules


def test_initial_extensions_block_emits_installed_packages(tmp_path, keypair, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    _install_ui_pkg(tmp_path, keypair)
    from tasks.io.serve_chat_ui import _initial_extensions_block
    out = _initial_extensions_block(user_id="alice")
    # JSON payload between PAWFLOW_EXTENSIONS= and ;</script>
    assert "examples.ui-hello" in out
    # Contains the hashed URL pattern.
    assert "/chat/ext/examples.ui-hello/" in out
    # The version_compat is exposed for the browser-side filter.
    assert "ui.v1" in out


# ── Tests for ext_runtime asset loader (structural) ──────────────────────────────────────────

def test_ext_runtime_has_asset_loader():
    src = Path("tasks/io/chat_ui/ext_runtime.js").read_text(encoding="utf-8")
    assert "_loadAllExtensions" in src
    assert "window.PAWFLOW_EXTENSIONS" in src
    assert "_loadOneAsset" in src
    # Style precedes scripts so script logic can read CSS variables.
    style_pos = src.index("a.kind === 'style'")
    script_pos = src.index("a.kind === 'script'")
    assert style_pos < script_pos


def test_ext_runtime_filters_by_version_compat():
    src = Path("tasks/io/chat_ui/ext_runtime.js").read_text(encoding="utf-8")
    assert "entry.version_compat !== UI_API_VERSION" in src
