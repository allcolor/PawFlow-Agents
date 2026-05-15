"""Tests for PFP UI extension server handlers (phase 3).

Covers:
  - manifest validation for the `handlers` field of `ui_extension`
  - install record records each handler with its own SHA-256 + grants
  - resolve_ui_handler returns the runtime info needed to invoke
  - the `pfp_runtime.invoke_ui_handler` envelope shape (kind=ui_handler)
  - the `_handle_pfp_ui` action dispatcher routes `_ext`-tagged bodies
    through the relay bridge and returns the handler's JSON result
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import FlowFile, pfp_package, pfp_runtime


def _write_ui_pkg_with_handler(root: Path, keypair, *,
                               action: str = "hello.ping",
                               handler_runner: str = "python",
                               handler_path: str = "content/handlers/ping.py",
                               duplicate_handler: bool = False,
                               bad_action: str = "",
                               handler_allowed_tools=None):
    pkg = root / "ui-hello.pfpdir"
    ui_dir = pkg / "content" / "ui"
    handler_dir = pkg / "content" / "handlers"
    ui_dir.mkdir(parents=True)
    handler_dir.mkdir(parents=True)
    (ui_dir / "extension.js").write_text(
        "pawflow.register('examples.ui-hello', function (pfp) {});\n",
        encoding="utf-8")
    (handler_dir / "ping.py").write_text(
        "from pawflow import pfp\n"
        "args = pfp.payload.get('arguments', {})\n"
        "pfp.result({'echo': args.get('message', '')})\n",
        encoding="utf-8")
    handlers = [{
        "action": bad_action or action,
        "path": handler_path,
        "runner": handler_runner,
        "description": "Echo handler",
        "allowed_tools": handler_allowed_tools or [],
        "allowed_services": [],
    }]
    if duplicate_handler:
        handlers.append({
            "action": action, "path": handler_path, "runner": "python",
            "allowed_tools": [], "allowed_services": [],
        })
    manifest = {
        "format": "pawflow.package.v1",
        "package": "examples.ui-hello",
        "version": "0.1.0",
        "developer": {
            "email": "dev@example.com",
            "public_key": keypair["public_key"],
        },
        "objects": [
            {
                "id": "ui_extension:hello",
                "type": "ui_extension",
                "name": "hello",
                "version_compat": "ui.v1",
                "assets": {"scripts": ["content/ui/extension.js"]},
                "slots": [{"slot": "action_menu", "id": "hello.open"}],
                "hooks": ["boot"],
                "handlers": handlers,
            },
        ],
    }
    (pkg / "pfp.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return pkg


@pytest.fixture
def keypair():
    return pfp_package.create_signing_key()


# ── Validation ────────────────────────────────────────────────────────────────────────

def test_handlers_validate_runner_must_be_python(tmp_path, keypair):
    pkgdir = _write_ui_pkg_with_handler(
        tmp_path, keypair, handler_runner="shell")
    built = pfp_package.build_pfp(
        str(pkgdir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    row = next(r for r in plan["objects"] if r["type"] == "ui_extension")
    assert row["status"] == "blocked"
    assert "python" in row["reason"]


def test_handlers_validate_action_pattern(tmp_path, keypair):
    pkgdir = _write_ui_pkg_with_handler(
        tmp_path, keypair, bad_action="BAD ACTION")
    built = pfp_package.build_pfp(
        str(pkgdir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    row = next(r for r in plan["objects"] if r["type"] == "ui_extension")
    assert row["status"] == "blocked"
    assert "invalid action" in row["reason"] or "BAD ACTION" in row["reason"]


def test_handlers_validate_duplicate_action(tmp_path, keypair):
    pkgdir = _write_ui_pkg_with_handler(
        tmp_path, keypair, duplicate_handler=True)
    built = pfp_package.build_pfp(
        str(pkgdir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    row = next(r for r in plan["objects"] if r["type"] == "ui_extension")
    assert row["status"] == "blocked"
    assert "duplicate" in row["reason"]


def test_handlers_validate_path_extension(tmp_path, keypair):
    pkgdir = _write_ui_pkg_with_handler(
        tmp_path, keypair, handler_path="content/handlers/ping.txt")
    # Drop a .txt placeholder; the validator only rejects after seeing it.
    (pkgdir / "content" / "handlers" / "ping.txt").write_text("x", encoding="utf-8")
    built = pfp_package.build_pfp(
        str(pkgdir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    row = next(r for r in plan["objects"] if r["type"] == "ui_extension")
    assert row["status"] == "blocked"
    assert ".py" in row["reason"]


# ── Install record + resolver ─────────────────────────────────────────────────────────────────

def _install_pkg(tmp_path, keypair, **kw):
    pkgdir = _write_ui_pkg_with_handler(tmp_path, keypair, **kw)
    built = pfp_package.build_pfp(
        str(pkgdir), private_key=keypair["private_key"])
    return pfp_package.install_pfp(
        built["path"], user_id="alice",
        include=["ui_extension:hello"])


def test_install_record_stores_handler_sha256(tmp_path, keypair, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    _install_pkg(tmp_path, keypair)
    records = pfp_package.list_installed_ui_extensions(
        user_id="alice", scope="user")
    assert records
    handlers = records[0]["handlers"]
    assert len(handlers) == 1
    h = handlers[0]
    assert h["action"] == "hello.ping"
    assert h["runner"] == "python"
    assert h["sha256"].startswith("sha256:")
    assert h["path"] == "content/handlers/ping.py"


def test_resolve_ui_handler_returns_runtime(tmp_path, keypair, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    _install_pkg(tmp_path, keypair)
    resolved = pfp_package.resolve_ui_handler(
        "examples.ui-hello", "hello.ping", user_id="alice", scope="user")
    assert resolved is not None
    runtime = resolved["package_runtime"]
    assert runtime["package"] == "examples.ui-hello"
    assert runtime["entrypoint"] == "content/handlers/ping.py"
    assert runtime["runner"] == "python"
    assert runtime["hash"].startswith("sha256:")
    assert Path(runtime["content_dir"]).is_dir()
    # installed_from is repointed at the handler's own hash so the runtime
    # entrypoint check validates THIS file rather than the manifest hash.
    assert resolved["installed_from"]["hash"] == runtime["hash"]


def test_resolve_ui_handler_returns_none_for_unknown_action(tmp_path, keypair, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    _install_pkg(tmp_path, keypair)
    assert pfp_package.resolve_ui_handler(
        "examples.ui-hello", "hello.unknown",
        user_id="alice", scope="user") is None
    assert pfp_package.resolve_ui_handler(
        "no.such.package", "hello.ping",
        user_id="alice", scope="user") is None


# ── Runtime envelope shape ───────────────────────────────────────────────────────────────────

def test_build_ui_handler_invocation_tags_envelope(tmp_path, keypair, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    _install_pkg(tmp_path, keypair)
    resolved = pfp_package.resolve_ui_handler(
        "examples.ui-hello", "hello.ping", user_id="alice", scope="user")
    envelope = pfp_runtime.build_ui_handler_invocation(
        resolved["package_runtime"], resolved["installed_from"],
        "hello.ping", {"message": "world"},
        context={"user_id": "alice", "conversation_id": "", "scope": "user"})
    assert envelope["format"] == pfp_runtime.RUNTIME_INVOKE_FORMAT
    assert envelope["kind"] == "ui_handler"
    assert envelope["payload"]["action"] == "hello.ping"
    assert envelope["payload"]["arguments"] == {"message": "world"}
    assert envelope["context"]["user_id"] == "alice"


def test_build_ui_handler_invocation_rejects_empty_action():
    runtime = {
        "package": "x", "object_id": "ui_extension:y",
        "runtime": "python", "runner": "python",
        "entrypoint": "x.py", "hash": "sha256:0",
        "content_dir": "/tmp",
    }
    with pytest.raises(pfp_runtime.PackageRuntimeError):
        pfp_runtime.build_ui_handler_invocation(
            runtime, {}, "", {})


# ── Action dispatcher ────────────────────────────────────────────────────────────────────

def _make_action_ff(body: dict, *, principal: str = "alice") -> FlowFile:
    ff = FlowFile(content=json.dumps(body).encode("utf-8"))
    if principal:
        ff.set_attribute("http.auth.principal", principal)
    return ff


def test_handle_pfp_ui_returns_none_when_no_ext_field(tmp_path):
    from tasks.ai.actions.pfp_ui import _handle_pfp_ui
    ff = _make_action_ff({"action": "some_builtin"})
    out = _handle_pfp_ui(None, "some_builtin", json.loads(ff.get_content()),
                          None, "alice", ff)
    assert out is None  # built-in handlers get a chance to run


def test_handle_pfp_ui_requires_auth(tmp_path):
    from tasks.ai.actions.pfp_ui import _handle_pfp_ui
    body = {"action": "hello.ping", "_ext": "examples.ui-hello"}
    ff = _make_action_ff(body, principal="")
    out = _handle_pfp_ui(None, "hello.ping", body, None, "", ff)
    assert out is not None
    assert out[0].get_attribute("http.response.status") == "401"
    payload = json.loads(out[0].get_content())
    assert "authentication" in payload["error"]


def test_handle_pfp_ui_404_when_handler_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    from tasks.ai.actions.pfp_ui import _handle_pfp_ui
    body = {"action": "hello.ping", "_ext": "examples.ui-hello"}
    ff = _make_action_ff(body)
    out = _handle_pfp_ui(None, "hello.ping", body, None, "alice", ff)
    assert out is not None
    assert out[0].get_attribute("http.response.status") == "404"


def test_handle_pfp_ui_dispatches_to_relay_bridge(tmp_path, keypair, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    _install_pkg(tmp_path, keypair)
    seen = {}

    def _fake_bridge(request):
        seen["request"] = request
        return {
            "format": pfp_runtime.RUNTIME_RESULT_FORMAT,
            "ok": True,
            "result": {"echo": "pong"},
        }

    monkeypatch.setattr(pfp_runtime, "_invoke_bridge", _fake_bridge)

    from tasks.ai.actions.pfp_ui import _handle_pfp_ui
    body = {
        "action": "hello.ping",
        "_ext": "examples.ui-hello",
        "message": "pong",
        "_call_id": "abc",
        "_reply_conversation_id": "reply",
    }
    ff = _make_action_ff(body)
    out = _handle_pfp_ui(None, "hello.ping", body, None, "alice", ff)
    assert out is not None
    assert out[0].get_attribute("http.response.status") == "200"
    payload = json.loads(out[0].get_content())
    assert payload["result"] == {"echo": "pong"}
    assert payload["_ext"] == "examples.ui-hello"
    assert payload["action"] == "hello.ping"

    req = seen["request"]
    assert req["kind"] == "ui_handler"
    assert req["payload"]["action"] == "hello.ping"
    # _call_id / _reply_conversation_id / action / _ext must NOT leak into
    # the handler's `arguments` payload.
    assert req["payload"]["arguments"] == {"message": "pong"}
    assert req["context"]["user_id"] == "alice"


def test_handle_pfp_ui_502_when_runtime_raises(tmp_path, keypair, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    _install_pkg(tmp_path, keypair)

    def _fake_bridge(request):
        raise pfp_runtime.PackageRuntimeError("forced failure")

    monkeypatch.setattr(pfp_runtime, "_invoke_bridge", _fake_bridge)

    from tasks.ai.actions.pfp_ui import _handle_pfp_ui
    body = {"action": "hello.ping", "_ext": "examples.ui-hello"}
    ff = _make_action_ff(body)
    out = _handle_pfp_ui(None, "hello.ping", body, None, "alice", ff)
    assert out is not None
    assert out[0].get_attribute("http.response.status") == "502"
    payload = json.loads(out[0].get_content())
    assert "forced failure" in payload["error"]


def test_action_handlers_list_runs_pfp_ui_first():
    from tasks.ai.agent_actions import _ACTION_HANDLERS
    from tasks.ai.actions.pfp_ui import _handle_pfp_ui
    assert _ACTION_HANDLERS[0] is _handle_pfp_ui
