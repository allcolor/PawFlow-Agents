"""Source invariants for the webchat Openspace 3D view."""

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def test_openspace_module_is_served_and_three_is_vendored():
    source = _text("tasks/io/serve_chat_ui.py")
    assert '"openspace.js"' in source
    # Load order: after turn_view.js (shares the view-mode vocabulary),
    # before sse.js (whose connectSSE calls openspaceWireSSE).
    assert source.index('"turn_view.js"') < source.index('"openspace.js"')
    assert source.index('"openspace.js"') < source.index('"sse.js"')
    three = ROOT / "tasks/io/chat_ui/three.module.min.js"
    assert three.exists()
    # A truncated download must fail loudly, not ship a broken 3D engine.
    assert three.stat().st_size > 400_000
    head = three.read_text(encoding="utf-8", errors="replace")[:400]
    assert "Three.js" in head


def test_three_is_lazily_imported_not_a_load_time_module():
    # three.module.min.js must NOT be in _JS_MODULES (script defer would
    # make every visitor pay ~680KB); openspace.js dynamic-imports it on
    # first activation instead.
    source = _text("tasks/io/serve_chat_ui.py")
    assert '"three.module.min.js"' not in source
    openspace = _text("tasks/io/chat_ui/openspace.js")
    assert "import('/chat/js/three.module.min.js" in openspace


def test_view_menu_offers_openspace_mode():
    template = _text("tasks/io/chat_ui/template.html")
    assert 'id="viewItemOpenspace"' in template
    assert "onViewModeSelect('openspace')" in template
    assert 'id="openspaceWrap"' in template
    assert 'id="openspaceOverlay"' in template
    # The classic message list survives underneath (hidden, not removed).
    assert "body.openspace-active .messages { display: none; }" in template


def test_view_mode_plumbing_accepts_openspace():
    conversations = _text("tasks/io/chat_ui/conversations.js")
    assert "['classic', 'simplified', 'openspace']" in conversations
    assert "openspaceSetActive" in conversations
    # Openspace rides on classic rendering underneath.
    assert "turnViewSetMode(mode === 'openspace' ? 'classic' : mode)" in conversations
    server = _text("tasks/ai/actions/_conv_core.py")
    assert '"openspace"' in server


def test_sse_socket_is_wired_into_openspace():
    sse = _text("tasks/io/chat_ui/sse.js")
    assert "openspaceWireSSE(eventSource)" in sse
    openspace = _text("tasks/io/chat_ui/openspace.js")
    # The view mirrors the stream: talking, thinking, tools, delegation,
    # approval waits. Losing one of these silently degrades the scene.
    for event in (
        "'token'", "'thinking_delta'", "'thinking_content'",
        "'tool_call'", "'tool_result'", "'new_message'",
        "'ask_user'", "'tool_approval_request'", "'done'", "'turn_complete'",
        "'sub_agent_start'", "'sub_agent_text'", "'sub_agent_done'",
    ):
        assert re.search(r"on\(" + event, openspace), event


def test_openspace_bubbles_are_bounded_and_coalesced():
    openspace = _text("tasks/io/chat_ui/openspace.js")
    assert "OSV_BUBBLE_MAX_CHARS" in openspace
    assert "OSV_BUBBLE_COALESCE_MS" in openspace
    # The per-agent activity log backing the PC dialog is a bounded ring.
    assert "OSV_LOG_MAX" in openspace
    assert "rec.log.splice(0, rec.log.length - OSV_LOG_MAX)" in openspace


def test_pc_click_opens_stacked_activity_dialog():
    openspace = _text("tasks/io/chat_ui/openspace.js")
    assert "function openspaceOpenAgentDialog" in openspace
    assert "osvAgent" in openspace          # raycast hit → agent key
    # Mobile dialog conventions: pinned close cross + wrapping header.
    assert "cog-close" in openspace
    assert "cog-head" in openspace
    assert "osv-block" in openspace          # stacked detail blocks


def test_openspace_i18n_keys_exist_in_all_locales():
    keys = (
        "openspaceView", "osvActivity", "osvNoActivity", "osvLoadError",
        "osvThought", "osvSaid", "osvAsksYou", "osvDelegatesTo", "osvDone",
    )
    for locale in ("en", "fr", "es"):
        data = json.loads(_text(f"tasks/io/chat_ui/i18n/{locale}.json"))
        for key in keys:
            assert key in data, f"{key} missing in {locale}.json"


def test_render_loop_pauses_when_hidden():
    openspace = _text("tasks/io/chat_ui/openspace.js")
    assert "visibilitychange" in openspace
    assert "document.hidden" in openspace
    # Pixel ratio is capped for mobile GPUs.
    assert "Math.min(window.devicePixelRatio || 1, 2)" in openspace


def test_last_bubble_is_persistent_and_seeded_from_history():
    openspace = _text("tasks/io/chat_ui/openspace.js")
    # Expiry demotes the newest bubble to a dimmed style, never hides it.
    assert "classList.add('osv-stale')" in openspace
    assert "function _osRestoreBubbles" in openspace
    # Full history renders seed the scene (deduped by msg_id, reset on
    # conversation switch) so the last message/thought shows at load.
    assert "function openspaceSeedHistory" in openspace
    assert "function openspaceResetTransient" in openspace
    assert "_osSeededIds" in openspace
    conversations = _text("tasks/io/chat_ui/conversations.js")
    assert "openspaceSeedHistory(data.messages || [], conversationId)" in conversations
    template = _text("tasks/io/chat_ui/template.html")
    assert ".osv-stale" in template


def test_users_get_visitor_avatars_with_bubbles():
    openspace = _text("tasks/io/chat_ui/openspace.js")
    assert "function _osEnsureUser" in openspace
    assert "function _osBuildVisitor" in openspace
    # Shared conversations: one avatar per distinct human author.
    assert "src.type === 'user' && src.name" in openspace
    assert "'user:' + _osKey" in openspace
    template = _text("tasks/io/chat_ui/template.html")
    assert "osv-label-user" in template


def test_tool_calls_drop_props_that_fade_on_result():
    openspace = _text("tasks/io/chat_ui/openspace.js")
    assert "function _osDropTool" in openspace
    assert "function _osFadeTool" in openspace
    # Sprite props are bounded per desk and fully disposed.
    assert "OSV_TOOL_MAX" in openspace
    assert "material.map.dispose()" in openspace
    # tool_call drops, tool_result fades, idle sweeps leftovers.
    assert "_osDropTool(rec, d.tool || 'tool')" in openspace
    assert "_osFadeTool(rec, d.tool || '')" in openspace
    assert "state === 'idle' && rec.tools" in openspace
