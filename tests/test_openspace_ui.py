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
    # The message list survives underneath: hidden unless projected onto
    # the openspace wall screen.
    assert "body.openspace-active .messages:not(.osv-projected) { display: none; }" in template


def test_view_mode_plumbing_accepts_openspace():
    conversations = _text("tasks/io/chat_ui/conversations.js")
    assert "['classic', 'simplified', 'openspace']" in conversations
    assert "openspaceSetActive" in conversations
    # Openspace rides on simplified rendering underneath (projected onto
    # the wall screen).
    assert "turnViewSetMode(mode === 'openspace' ? 'simplified' : mode)" in conversations
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
        "osvBoardTitle", "osvBoardIdle", "osvHelp",
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


def test_local_user_send_is_mirrored_with_attachment_dropoff():
    # The sender's own message never echoes back on SSE: the composer
    # reports it to the openspace directly, and attachments make the
    # avatar walk to the target agent's desk and drop folder props.
    attachments = _text("tasks/io/chat_ui/attachments.js")
    assert "openspaceUserMessage(text || '', attachmentsForDisplay" in attachments
    openspace = _text("tasks/io/chat_ui/openspace.js")
    assert "function openspaceUserMessage" in openspace
    assert "\\u{1F4C1}" in openspace  # folder prop for attachments
    assert "_osWalkTo(rec, home)" in openspace  # and walks back home
    # A live new_message for an already-shown msg_id must not re-show.
    assert "if (d.msg_id && _osSeededIds.has(d.msg_id)) return;" in openspace


def test_tool_calls_announce_their_name_as_a_thought():
    openspace = _text("tasks/io/chat_ui/openspace.js")
    # Both the primary tool_call and sub_agent_tool paths bubble the name.
    assert openspace.count("_osToolEmoji(d.tool) + ' ' + (d.tool || 'tool')") == 2


def test_busy_agents_visibly_move():
    openspace = _text("tasks/io/chat_ui/openspace.js")
    # The capsule avatar is rotationally symmetric: rotation.y is invisible,
    # state animations must use lean (rotation.z) and bounce (position.y).
    assert "rotation.y = Math.sin" not in openspace
    assert "rec.avatar.rotation.z = sway" in openspace
    assert "emissiveIntensity" in openspace  # PC screen flickers while busy
    template = _text("tasks/io/chat_ui/template.html")
    # Thought bubbles read as thoughts: cloud tail + pulsing status chip.
    assert ".osv-thought::before" in template
    assert "osvStatusPulse" in template


def test_wall_screen_projects_the_live_simplified_view():
    openspace = _text("tasks/io/chat_ui/openspace.js")
    # The picture is the real #messages element, reparented (not copied)
    # and restored on deactivation.
    assert "function _osProjectMessages" in openspace
    assert "_osScreenHome.parent.insertBefore(messages, _osScreenHome.next)" in openspace
    # Perspective mapping uses the same v.project() camera as the bubbles.
    assert "function _osQuadTransform" in openspace
    assert "matrix3d(" in openspace
    # The stylesheet default is display:none; showing the panel must set
    # an explicit value, not clear the inline style.
    assert "el.style.display = 'block'" in openspace
    # onDone callbacks may chain a new walk: finished tweens run their
    # callbacks only after _osTweens has been reassigned.
    assert "finished.forEach((tw) => { if (tw.onDone) tw.onDone(); });" in openspace
    template = _text("tasks/io/chat_ui/template.html")
    assert ".osv-bigscreen" in template
    # The projected transcript stays scrollable (pointer-events on).
    assert "pointer-events: auto" in template


def test_thinking_events_reach_thought_bubbles():
    openspace = _text("tasks/io/chat_ui/openspace.js")
    # Thinking SSE payloads carry `text` (renderThinkingContent) and
    # delegate thinking carries `thinking` — never `content`.
    assert "d.text || d.content || ''" in openspace
    assert "_osStreamBubble(rec, 'thought', d.thinking || '')" in openspace


def test_office_has_decor_walkable_floor_and_camera_pan():
    openspace = _text("tasks/io/chat_ui/openspace.js")
    assert "function _osBuildDecor" in openspace
    # Clicking the floor walks the viewer's own avatar there and moves
    # its home spot with it.
    assert "me.homeSeat = { x: gx, z: gz };" in openspace
    # Right-drag or shift-drag pans the camera target; the context menu
    # is suppressed on the canvas.
    assert "pan: !e.ctrlKey && (e.button === 2 || e.shiftKey)" in openspace
    assert "contextmenu" in openspace


def test_agents_are_chibi_mascots_with_batteries_and_a_roster_board():
    openspace = _text("tasks/io/chat_ui/openspace.js")
    # Mascot avatars replace the plain capsule for agents.
    assert "function _osBuildChibi" in openspace
    assert "CapsuleGeometry" not in openspace.split("_osBuildVisitor")[0].split(
        "function _osBuildDesk")[1]
    # Battery above each head mirrors the shared context-usage cache.
    assert "window._contextUsage" in openspace
    assert "function _osRefreshBatteries" in openspace
    # Blackboard roster of active agents, projected like the wall screen.
    assert "function _osUpdateBoard" in openspace
    assert "_osProjectPanel(_osBoardEl, _osBoardCorners" in openspace
    template = _text("tasks/io/chat_ui/template.html")
    assert ".osv-batt" in template
    assert ".osv-board" in template


def test_bubble_streams_survive_expiry_and_layout_resizes():
    openspace = _text("tasks/io/chat_ui/openspace.js")
    # Expiry resets the stream buffer exactly once (guarded by the stale
    # class) — an unguarded per-frame reset wiped every stream after the
    # bubble's first turn.
    assert "if (!rec.speechEl.classList.contains('osv-stale'))" in openspace
    assert "if (!rec.thoughtEl.classList.contains('osv-stale'))" in openspace
    # The thought accumulates across the whole turn (tool names logged
    # inline) and resets when the turn actually ends.
    assert "rec.thoughtText = '';   // turn over" in openspace
    assert "rec.thoughtText = '';   // the answer closes" in openspace
    # Wrap resizes without window resize must not stretch the canvas away
    # from the overlay math.
    assert "new ResizeObserver(() => _osResize())" in openspace
    # Controls are discoverable in-scene.
    assert "osv-help" in openspace
    template = _text("tasks/io/chat_ui/template.html")
    assert ".osv-help" in template


def test_multimodal_content_never_renders_object_object():
    openspace = _text("tasks/io/chat_ui/openspace.js")
    # Stored messages with attachments carry content as block arrays;
    # bubbles and logs extract the text parts instead of String(array).
    assert "function _osText" in openspace
    assert "_osText(text).replace" in openspace          # _osTrim
    assert "body: _osText(body)" in openspace            # activity log
    assert "_osText(m.content).replace" in openspace     # history seeding


def test_bubbles_are_fully_readable_and_anchored():
    openspace = _text("tasks/io/chat_ui/openspace.js")
    # Bubbles show the whole message/thought in a scrollable body pinned
    # to the newest text; no 200-char display truncation.
    assert "function _osSetBubbleText" in openspace
    assert "body.scrollTop = body.scrollHeight" in openspace
    assert "OSV_BUBBLE_MAX_CHARS = 8000" in openspace
    # Ctrl+drag lifts the camera target above the floor plane.
    assert "lift: e.ctrlKey" in openspace
    assert "_osCamPan.y" in openspace
    template = _text("tasks/io/chat_ui/template.html")
    assert ".osv-bubble-body { max-height" in template
    # Bubble tails are centered so they point at the avatar underneath.
    assert ".osv-thought::before { content: ''; position: absolute; left: 50%;" in template
    assert ".osv-speech::after" in template
