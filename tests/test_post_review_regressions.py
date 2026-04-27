"""Regression tests for the post-review fixes:
  - ConfigStore.load_secrets must NOT silently return ciphertext on
    decrypt failure (P0/P1 #3).
  - read_only mode must take precedence over per-tool `allow`
    overrides (P1 #4).
  - The HTTP route registry's `{path+}` pattern requires at least
    one segment, so a registered `/code/{sid}/{tok}/{path+}` does
    NOT match `/code/s/t/`. Phase 2-5 hand the user the trailing-
    slash URL, so a root pattern (`/code/{sid}/{tok}/`) MUST also
    be registered (P0 #1).
"""

import json

import pytest


# ---------------------------------------------------------------------------
# P0/P1 #3 — ConfigStore.load_secrets fail-loud
# ---------------------------------------------------------------------------


def test_load_secrets_drops_undecryptable_value(tmp_path, monkeypatch):
    from core import secrets as secrets_mod
    from core.config_store import ConfigStore

    # Force a fresh manager bound to a known password so we can write
    # the on-disk file with a *different* password and watch decrypt
    # fail.
    secrets_mod._reset_for_tests()
    monkeypatch.setenv("PAWFLOW_SECRET_KEY", "writer-password")
    sm_writer = secrets_mod.get_secrets_manager()
    enc = sm_writer.encrypt("sk-real-secret")
    secrets_mod._reset_for_tests()

    p = tmp_path / "secrets.json"
    p.write_text(json.dumps({"api_key": enc}), encoding="utf-8")

    # Re-init with a different password — decrypt must fail.
    monkeypatch.setenv("PAWFLOW_SECRET_KEY", "reader-DIFFERENT-password")
    out = ConfigStore.load_secrets(p)
    assert "api_key" in out
    # MUST NOT be the ciphertext, MUST NOT be the plaintext, MUST
    # be the empty fallback so the caller fails visibly.
    assert out["api_key"].as_str() == ""

    secrets_mod._reset_for_tests()


# ---------------------------------------------------------------------------
# P1 #4 — read_only takes precedence over a stale per-tool `allow`
# ---------------------------------------------------------------------------


def test_read_only_blocks_write_tool_even_with_allow_override(tmp_path, monkeypatch):
    """In read_only mode, a leftover `tool_permissions['edit'] = 'allow'`
    from a previous mode must NOT let `edit` through. The relay
    consults `ToolApprovalGate.is_read_only_allowed` BEFORE the
    per-tool override."""
    # We don't need a full ConversationStore stand-up — the precedence
    # rule lives in tool_relay_service._do_execute. Inspect the source
    # to make sure the ordering is right; this matches the structural
    # checks we already use for the gauge invariants in JS.
    src = open("services/tool_relay_service.py", encoding="utf-8").read()
    # The read_only block must appear before the `_tool_perm == "allow"`
    # branch (otherwise a stale `allow` wins).
    ro_idx = src.index('if _perm_mode == "read_only":')
    allow_idx = src.index('elif _tool_perm == "allow":')
    assert ro_idx < allow_idx, (
        "read_only check must run BEFORE the per-tool allow override; "
        "otherwise a stale allow leaks through after switching to read_only.")


# ---------------------------------------------------------------------------
# P0 #1 — The trailing-slash root URL we hand to the user must match
# ---------------------------------------------------------------------------


def test_route_pattern_path_plus_does_not_match_empty_segment():
    """The `{path+}` pattern requires ≥1 segment after the slash,
    so without an explicit trailing-slash route the URL we hand to
    the user (`/code/<sid>/<tok>/`) lands on a 404. This locks in
    the requirement to register BOTH patterns."""
    from services.http_listener_service import RouteRegistry
    reg = RouteRegistry()
    reg.register("GET", "/code/{session_id}/{token}/{path+}",
                 "x", callback=lambda r: None)
    # Subroute matches.
    assert reg.match("GET", "/code/s/t/index.html") is not None
    # Trailing-slash root does NOT match the {path+} pattern alone.
    assert reg.match("GET", "/code/s/t/") is None
    # Adding the explicit root pattern makes the URL match.
    reg.register("GET", "/code/{session_id}/{token}/",
                 "x", callback=lambda r: None)
    assert reg.match("GET", "/code/s/t/") is not None


def test_fwd_root_url_requires_explicit_trailing_slash_route():
    from services.http_listener_service import RouteRegistry
    reg = RouteRegistry()
    reg.register("GET", "/fwd/{forward_id}/{token}/{path+}",
                 "x", callback=lambda r: None)
    assert reg.match("GET", "/fwd/f/t/") is None
    reg.register("GET", "/fwd/{forward_id}/{token}/",
                 "x", callback=lambda r: None)
    assert reg.match("GET", "/fwd/f/t/") is not None
