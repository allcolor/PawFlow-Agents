"""Tests for PFP web_app support (standalone pages at /apps/<package>/<name>/).

Covers:
  - manifest validation for the `web_app` object type
  - install records carry assets (with sha256+size) and the entry path
  - version_compat must be exactly `webapp.v1`
  - install / uninstall round-trip
  - `list_installed_web_apps` returns the expected manifest + url
  - the asset-serving task `servePfpWebAppAssets` serves the bare entry
    route and hashed asset route, and validates auth + hash + path
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import pfp_package


def _write_web_app_pkg(root: Path, keypair, *, version: str = "1.0.0",
                       package_id: str = "examples.web-hello",
                       name: str = "dashboard",
                       version_compat: str = "webapp.v1",
                       with_style: bool = True,
                       invalid_ext_path: str = "",
                       entry: str = "content/webapp/index.html",
                       entry_in_assets: bool = True):
    pkg = root / f"{package_id}.pfpdir"
    webapp_dir = pkg / "content" / "webapp"
    webapp_dir.mkdir(parents=True)
    (webapp_dir / "index.html").write_text(
        "<!doctype html><html><body>hello " + package_id + "</body></html>\n",
        encoding="utf-8")
    (webapp_dir / "app.js").write_text("console.log('hi');\n", encoding="utf-8")
    assets = ["content/webapp/index.html", "content/webapp/app.js"]
    if with_style:
        (webapp_dir / "style.css").write_text("body{margin:0}\n", encoding="utf-8")
        assets.append("content/webapp/style.css")
    if invalid_ext_path:
        invalid = webapp_dir / Path(invalid_ext_path).name
        invalid.write_bytes(b"binary")
        assets.append(invalid_ext_path)
    if not entry_in_assets:
        assets = [a for a in assets if a != entry]
    manifest = {
        "format": "pawflow.package.v1",
        "package": package_id,
        "version": version,
        "description": "web_app test fixture",
        "developer": {
            "email": "dev@example.com",
            "public_key": keypair["public_key"],
        },
        "objects": [
            {
                "id": "web_app:" + name,
                "type": "web_app",
                "name": name,
                "version_compat": version_compat,
                "entry": entry,
                "assets": assets,
            },
        ],
    }
    (pkg / "pfp.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return pkg


@pytest.fixture(autouse=True)
def _mock_llm_review(monkeypatch):
    """Stub the summarizer review so install plans do not hit a real LLM."""
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

def test_web_app_is_an_installable_type():
    assert "web_app" in pfp_package._INSTALLABLE_TYPES


def test_web_app_inspect_lists_entry_and_assets(tmp_path, keypair):
    pkgdir = _write_web_app_pkg(tmp_path, keypair)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    rows = [r for r in plan["objects"] if r["type"] == "web_app"]
    assert len(rows) == 1
    row = rows[0]
    assert row["installable"] is True
    assert row["status"] == "new"
    caps = row["capabilities"]["web_app"]
    assert caps["version_compat"] == "webapp.v1"
    assert caps["entry"] == "content/webapp/index.html"
    assert caps["asset_count"] == 3


def test_web_app_rejects_wrong_version_compat(tmp_path, keypair):
    pkgdir = _write_web_app_pkg(tmp_path, keypair, version_compat="webapp.v2")
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    row = next(r for r in plan["objects"] if r["type"] == "web_app")
    assert row["status"] == "blocked"
    assert "version_compat" in row["reason"]


def test_web_app_rejects_missing_entry_declaration(tmp_path, keypair):
    pkgdir = _write_web_app_pkg(tmp_path, keypair, entry_in_assets=False)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    row = next(r for r in plan["objects"] if r["type"] == "web_app")
    assert row["status"] == "blocked"
    assert "entry" in row["reason"]


def test_web_app_rejects_disallowed_asset_extension(tmp_path, keypair):
    pkgdir = _write_web_app_pkg(
        tmp_path, keypair, invalid_ext_path="content/webapp/payload.exe")
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    row = next(r for r in plan["objects"] if r["type"] == "web_app")
    assert row["status"] == "blocked"
    assert "payload.exe" in row["reason"] or "not allowed" in row["reason"]


def test_web_app_allows_html_entry_unlike_ui_extension(tmp_path, keypair):
    """`.html` is blocked for ui_extension but explicitly allowed for web_app."""
    pkgdir = _write_web_app_pkg(tmp_path, keypair)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    row = next(r for r in plan["objects"] if r["type"] == "web_app")
    assert row["status"] == "new"


# ── Install ────────────────────────────────────────────────────────────────────────────

def _install_web_app_pkg(tmp_path, keypair, **kw):
    pkgdir = _write_web_app_pkg(tmp_path, keypair, **kw)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    return pfp_package.install_pfp(
        built["path"], user_id="alice",
        include=["web_app:dashboard"]), built


def test_web_app_install_writes_record_with_assets(tmp_path, keypair, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    result, _ = _install_web_app_pkg(tmp_path, keypair)
    assert result["ok"] is True, result
    assert any(o["id"] == "web_app:dashboard" for o in result["installed"])
    records = pfp_package.list_installed_web_apps(user_id="alice", scope="user")
    assert len(records) == 1
    rec = records[0]
    assert rec["package"] == "examples.web-hello"
    assert rec["name"] == "dashboard"
    assert rec["version_compat"] == "webapp.v1"
    assert rec["entry"] == "content/webapp/index.html"
    assert rec["url"] == "/apps/examples.web-hello/dashboard/"
    assets = {a["path"]: a for a in rec["assets"]}
    assert "content/webapp/index.html" in assets
    assert assets["content/webapp/index.html"]["sha256"].startswith("sha256:")
    assert assets["content/webapp/index.html"]["size"] > 0
    assert Path(rec["content_dir"]).is_dir()
    asset_disk = Path(rec["content_dir"]) / "content/webapp/index.html"
    assert asset_disk.is_file()


def test_web_app_uninstall_removes_record_and_content(tmp_path, keypair, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    _install_web_app_pkg(tmp_path, keypair)
    assert pfp_package.list_installed_web_apps(user_id="alice", scope="user")
    out = pfp_package.uninstall_pfp(
        "examples.web-hello", user_id="alice", scope="user")
    assert out["ok"] is True
    assert pfp_package.list_installed_web_apps(user_id="alice", scope="user") == []


def test_web_app_dev_load_keeps_source_dir(tmp_path, keypair, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    pkgdir = _write_web_app_pkg(tmp_path, keypair)
    out = pfp_package.dev_load_pfp(
        str(pkgdir), user_id="alice", conversation_id="conv1",
        include=["web_app:dashboard"], scope="conversation")
    assert out["ok"] is True
    records = pfp_package.list_installed_web_apps(
        user_id="alice", conversation_id="conv1", scope="conversation")
    assert any(r["package"] == "examples.web-hello" for r in records)


# ── Asset serving task ─────────────────────────────────────────────────────────────────

def _build_request(http_path: str, *, principal: str = "alice"):
    from core import FlowFile
    ff = FlowFile(content=b"")
    ff.set_attribute("http.path", http_path)
    if principal:
        ff.set_attribute("http.auth.principal", principal)
    return ff


def _serve_webapp_task(http_path: str, principal: str = "alice"):
    from tasks.io.serve_pfp_webapp_assets import ServePfpWebAppAssetsTask
    task = ServePfpWebAppAssetsTask({})
    ff = _build_request(http_path, principal=principal)
    return task.execute(ff)[0]


def _get_asset_url(rec, file_path: str) -> str:
    asset = next(a for a in rec["assets"] if a["path"] == file_path)
    short = asset["sha256"].replace("sha256:", "")[:16]
    return f"/apps/{rec['package']}/{rec['name']}/{short}/{file_path}"


def test_pfp_webapp_serves_entry_at_bare_route(tmp_path, keypair, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    _install_web_app_pkg(tmp_path, keypair)
    out = _serve_webapp_task("/apps/examples.web-hello/dashboard/")
    assert out.get_attribute("http.response.status") == "200"
    assert out.get_attribute("http.response.header.Content-Type").startswith("text/html")
    assert b"hello examples.web-hello" in out.get_content()


def test_pfp_webapp_serves_entry_without_trailing_slash(tmp_path, keypair, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    _install_web_app_pkg(tmp_path, keypair)
    out = _serve_webapp_task("/apps/examples.web-hello/dashboard")
    assert out.get_attribute("http.response.status") == "200"


def test_pfp_webapp_serves_hashed_asset(tmp_path, keypair, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    _install_web_app_pkg(tmp_path, keypair)
    rec = pfp_package.list_installed_web_apps(user_id="alice", scope="user")[0]
    url = _get_asset_url(rec, "content/webapp/app.js")
    out = _serve_webapp_task(url)
    assert out.get_attribute("http.response.status") == "200"
    assert out.get_attribute("http.response.header.Content-Type").startswith("application/javascript")
    assert b"console.log" in out.get_content()


def test_pfp_webapp_rejects_unknown_package(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    out = _serve_webapp_task("/apps/no.such.pkg/dashboard/")
    assert out.get_attribute("http.response.status") == "404"


def test_pfp_webapp_rejects_wrong_hash(tmp_path, keypair, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    _install_web_app_pkg(tmp_path, keypair)
    out = _serve_webapp_task(
        "/apps/examples.web-hello/dashboard/0000000000000000/content/webapp/app.js")
    assert out.get_attribute("http.response.status") == "404"


def test_pfp_webapp_rejects_unauth(tmp_path, keypair, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    _install_web_app_pkg(tmp_path, keypair)
    out = _serve_webapp_task("/apps/examples.web-hello/dashboard/", principal="")
    assert out.get_attribute("http.response.status") == "404"


def test_pfp_webapp_rejects_disallowed_extension(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    out = _serve_webapp_task("/apps/x/dashboard/abc1234567890123/payload.exe")
    assert out.get_attribute("http.response.status") == "404"


def test_pfp_webapp_rejects_path_traversal(tmp_path, keypair, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    _install_web_app_pkg(tmp_path, keypair)
    rec = pfp_package.list_installed_web_apps(user_id="alice", scope="user")[0]
    short = rec["assets"][0]["sha256"].replace("sha256:", "")[:16]
    out = _serve_webapp_task(
        f"/apps/examples.web-hello/dashboard/{short}/../../../etc/passwd")
    assert out.get_attribute("http.response.status") == "404"


def test_pfp_webapp_detects_tampered_entry(tmp_path, keypair, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    _install_web_app_pkg(tmp_path, keypair)
    rec = pfp_package.list_installed_web_apps(user_id="alice", scope="user")[0]
    target = Path(rec["content_dir"]) / "content/webapp/index.html"
    target.write_text("<script>alert('pwn')</script>", encoding="utf-8")
    out = _serve_webapp_task("/apps/examples.web-hello/dashboard/")
    assert out.get_attribute("http.response.status") == "404"
