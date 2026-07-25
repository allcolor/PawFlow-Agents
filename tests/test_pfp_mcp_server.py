"""Tests for PFP mcp_server support (installing MCP connections with no
manual step after install).

Covers:
  - mcp_server is a supported installable object type
  - structural validation: http needs url, stdio needs command
  - risk classification: high for stdio, medium for http-only
  - install writes a ready-to-use `mcp` resource with the declared fields
  - uninstall removes the resource
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import pfp_package


def _write_mcp_pkg(root: Path, keypair, *, version: str = "1.0.0",
                   package_id: str = "examples.mcp-hello",
                   name: str = "justicelibre",
                   mcp_data=None, secrets=None):
    pkg = root / f"{package_id}.pfpdir"
    mcp_dir = pkg / "content" / "mcp"
    mcp_dir.mkdir(parents=True)
    data = mcp_data if mcp_data is not None else {
        "url": "https://justicelibre.org/mcp",
        "transport": "http",
        "auth": {"Authorization": "Bearer ${justicelibre_api_key}"},
    }
    (mcp_dir / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    obj = {
        "id": "mcp_server:" + name,
        "type": "mcp_server",
        "name": name,
        "path": f"content/mcp/{name}.json",
    }
    if secrets is not None:
        obj["secrets"] = secrets
    manifest = {
        "format": "pawflow.package.v1",
        "package": package_id,
        "version": version,
        "description": "mcp_server test fixture",
        "developer": {
            "email": "dev@example.com",
            "public_key": keypair["public_key"],
        },
        "objects": [obj],
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


@pytest.fixture(autouse=True)
def _reset_repo(tmp_path, monkeypatch):
    import core.paths as paths
    from core.repository import ScopedRepository
    from core.resource_store import ResourceStore

    monkeypatch.setattr(paths, "REPOSITORY_DIR", tmp_path / "repository")
    monkeypatch.setattr(paths, "RUNTIME_DIR", tmp_path / "runtime")
    ScopedRepository.reset()
    ResourceStore.reset()


# ── Validation ───────────────────────────────────────────────────────────────────

def test_mcp_server_is_an_installable_type():
    assert "mcp_server" in pfp_package._INSTALLABLE_TYPES
    assert pfp_package._RESOURCE_TYPES["mcp_server"] == "mcp"


def test_mcp_server_http_inspect_ok_and_medium_risk(tmp_path, keypair):
    pkgdir = _write_mcp_pkg(tmp_path, keypair)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    row = next(r for r in plan["objects"] if r["type"] == "mcp_server")
    assert row["status"] == "new"
    assert row["installable"] is True
    assert row["risk"] == "medium"


def test_mcp_server_stdio_is_high_risk(tmp_path, keypair):
    pkgdir = _write_mcp_pkg(tmp_path, keypair, mcp_data={
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@example/mcp-server"],
    })
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    row = next(r for r in plan["objects"] if r["type"] == "mcp_server")
    assert row["status"] == "new"
    assert row["risk"] == "high"


def test_mcp_server_http_without_url_is_blocked(tmp_path, keypair):
    pkgdir = _write_mcp_pkg(tmp_path, keypair, mcp_data={"transport": "http"})
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    row = next(r for r in plan["objects"] if r["type"] == "mcp_server")
    assert row["status"] == "blocked"
    assert "url" in row["reason"]


def test_mcp_server_stdio_without_command_is_blocked(tmp_path, keypair):
    pkgdir = _write_mcp_pkg(tmp_path, keypair, mcp_data={"transport": "stdio"})
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    row = next(r for r in plan["objects"] if r["type"] == "mcp_server")
    assert row["status"] == "blocked"
    assert "command" in row["reason"]


def test_mcp_server_rejects_unknown_transport(tmp_path, keypair):
    pkgdir = _write_mcp_pkg(tmp_path, keypair, mcp_data={
        "transport": "websocket", "url": "wss://example.com",
    })
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    row = next(r for r in plan["objects"] if r["type"] == "mcp_server")
    assert row["status"] == "blocked"
    assert "transport" in row["reason"]


# ── Install ─────────────────────────────────────────────────────────────────

def _install_mcp_pkg(tmp_path, keypair, **kw):
    pkgdir = _write_mcp_pkg(tmp_path, keypair, **kw)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    return pfp_package.install_pfp(
        built["path"], user_id="alice",
        include=["mcp_server:justicelibre"]), built


def test_mcp_server_install_creates_ready_to_use_mcp_resource(tmp_path, keypair):
    result, _ = _install_mcp_pkg(tmp_path, keypair)
    assert result["ok"] is True, result
    assert any(o["id"] == "mcp_server:justicelibre" for o in result["installed"])

    from core.resource_store import ResourceStore
    stored = ResourceStore.instance().get("mcp", "justicelibre", "alice")
    assert stored is not None
    assert stored["url"] == "https://justicelibre.org/mcp"
    assert stored["transport"] == "http"
    assert stored["auth"]["Authorization"] == "Bearer ${justicelibre_api_key}"
    # No manual reconnection step required: the resource is complete and
    # ready for the user to enable, exactly as if it had been hand-built.
    assert stored["installed_from"]["package"] == "examples.mcp-hello"


def test_mcp_server_stdio_install_carries_command_and_args(tmp_path, keypair):
    result, _ = _install_mcp_pkg(tmp_path, keypair, mcp_data={
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@example/mcp-server"],
        "env": {"EXAMPLE_MODE": "prod"},
    })
    assert result["ok"] is True, result

    from core.resource_store import ResourceStore
    stored = ResourceStore.instance().get("mcp", "justicelibre", "alice")
    assert stored["transport"] == "stdio"
    assert stored["command"] == "npx"
    assert stored["args"] == ["-y", "@example/mcp-server"]
    assert stored["env"] == {"EXAMPLE_MODE": "prod"}


def test_mcp_server_uninstall_removes_resource(tmp_path, keypair):
    _install_mcp_pkg(tmp_path, keypair)
    from core.resource_store import ResourceStore
    assert ResourceStore.instance().get("mcp", "justicelibre", "alice") is not None

    out = pfp_package.uninstall_pfp(
        "examples.mcp-hello", user_id="alice", scope="user")
    assert out["ok"] is True
    assert ResourceStore.instance().get("mcp", "justicelibre", "alice") is None
