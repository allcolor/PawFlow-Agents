"""Source invariants for the webchat Openspace 3D view."""

import json

from chat_ui_testing import rendered_chat_html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPENSPACE_MODULES = (
    "openspace.js",
    "openspace_environment.js",
    "openspace_scene.js",
    "openspace_room.js",
    "openspace_flow.js",
    "openspace_agents.js",
    "openspace_runtime.js",
    "openspace_dialogs.js",
)


def _text(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def _openspace_text():
    base = ROOT / "tasks/io/chat_ui"
    return "\n".join((base / name).read_text(encoding="utf-8")
                     for name in OPENSPACE_MODULES)


def test_openspace_module_is_served_and_three_is_vendored():
    source = _text("tasks/io/serve_chat_ui.py")
    for module in OPENSPACE_MODULES:
        assert f'"{module}"' in source
        assert len(_text(f"tasks/io/chat_ui/{module}").splitlines()) <= 800
    # Load order: after turn_view.js (shares the view-mode vocabulary),
    # before sse.js (whose connectSSE calls openspaceWireSSE).
    assert source.index('"turn_view.js"') < source.index('"openspace.js"')
    assert source.index('"openspace_environment.js"') < source.index('"openspace_scene.js"')
    assert source.index('"openspace_dialogs.js"') < source.index('"sse.js"')
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
    openspace = _openspace_text()
    assert "import('/chat/js/three.module.min.js" in openspace


def test_reload_waits_for_all_deferred_openspace_modules():
    openspace = _openspace_text()
    assert "function _osModulesReady()" in openspace
    assert "document.readyState !== 'loading'" in openspace
    assert "document.addEventListener('DOMContentLoaded', resolve, { once: true })" in openspace
    assert "_osModulesReady().then(() => _osEnsureThree())" in openspace


def test_environment_module_supports_restart_free_hotpatch_loading():
    openspace = _openspace_text()
    assert "function _osEnsureEnvironment()" in openspace
    assert "'/chat/js/openspace_environment.js?v='" in openspace
    assert ".then(() => _osEnsureEnvironment())" in openspace


def test_view_menu_offers_openspace_mode():
    template = rendered_chat_html()
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
    openspace = _openspace_text()
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
    openspace = _openspace_text()
    assert "OSV_BUBBLE_MAX_CHARS" in openspace
    assert "OSV_BUBBLE_COALESCE_MS" in openspace
    # The per-agent activity log backing the PC dialog is a bounded ring.
    assert "OSV_LOG_MAX" in openspace
    assert "rec.log.splice(0, rec.log.length - OSV_LOG_MAX)" in openspace


def test_pc_click_opens_stacked_activity_dialog():
    openspace = _openspace_text()
    assert "function openspaceOpenAgentDialog" in openspace
    assert "osvAgent" in openspace          # raycast hit → agent key
    # Mobile dialog conventions: pinned close cross + wrapping header.
    assert "cog-close" in openspace
    assert "cog-head" in openspace
    assert "osv-block" in openspace          # stacked detail blocks


def test_agent_click_selects_canonical_conversation_agent():
    openspace = _openspace_text()
    assert "function _osSelectAgent(key)" in openspace
    assert "rec.kind !== 'agent' || rec.guest" in openspace
    assert "_osKey(selectedAgent) === rec.key" in openspace
    assert "cmdAgentSelect(rec.name)" in openspace

    hit = openspace.index("if (ud && ud.osvAgent)")
    select = openspace.index("_osSelectAgent(ud.osvAgent)", hit)
    focus = openspace.index("_osFocusAgent(ud.osvAgent)", hit)
    dialog = openspace.index("openspaceOpenAgentDialog(ud.osvAgent)", hit)
    assert hit < select < focus < dialog


def test_user_message_source_never_creates_a_phantom_agent_desk():
    openspace = _openspace_text()
    event_agent = openspace[
        openspace.index("function _osEventAgent(data)"):
        openspace.index("// Deterministic pastel", openspace.index("function _osEventAgent(data)"))]
    assert "source.type !== 'user'" in event_agent
    assert "(data && data.agent_name) || sourceAgent" in event_agent


def test_openspace_i18n_keys_exist_in_all_locales():
    keys = (
        "openspaceView", "osvActivity", "osvNoActivity", "osvLoadError",
        "osvThought", "osvSaid", "osvAsksYou", "osvDelegatesTo", "osvDone",
        "osvBoardTitle", "osvBoardIdle", "osvHelp", "osvViewHome",
        "osvViewConversation", "osvViewBoard", "osvViewTv", "osvViewResources",
    )
    for locale in ("en", "fr", "es"):
        data = json.loads(_text(f"tasks/io/chat_ui/i18n/{locale}.json"))
        for key in keys:
            assert key in data, f"{key} missing in {locale}.json"


def test_render_loop_pauses_when_hidden():
    openspace = _openspace_text()
    assert "visibilitychange" in openspace
    assert "document.hidden" in openspace
    # Pixel ratio is capped for mobile GPUs.
    assert "Math.min(window.devicePixelRatio || 1, 2)" in openspace


def test_render_quality_adapts_and_software_webgl_stays_at_one_dpr():
    openspace = _openspace_text()
    assert "function _osUsesSoftwareWebGL" in openspace
    assert "swiftshader|llvmpipe|softpipe|software" in openspace
    assert "antialias: !_osSoftwareRenderer" in openspace
    assert "function _osAdaptPixelRatio" in openspace
    assert "_osFrameMs > 24" in openspace
    assert "_osFrameMs < 17" in openspace
    assert "_osSoftwareRenderer ? 1" in openspace


def test_last_bubble_is_persistent_and_seeded_from_history():
    openspace = _openspace_text()
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
    template = rendered_chat_html()
    assert ".osv-stale" in template


def test_all_conversation_agents_get_desks_even_when_inactive():
    openspace = _openspace_text()
    resources = _text("tasks/io/chat_ui/resources_render.js")
    # selectedAgent and activeInteractions are insufficient: an attached
    # agent may be idle or rate-limited and therefore emit no live event.
    assert "function openspaceSyncAgents(agents)" in openspace
    assert "openspaceSyncAgents(_lastResourcesData.agents)" in openspace
    # list_resources can finish after the 3D scene opens; its fresh roster
    # must therefore synchronize the room as well.
    assert "openspaceSyncAgents(data.agents)" in resources


def test_users_get_visitor_avatars_with_bubbles():
    openspace = _openspace_text()
    assert "function _osEnsureUser" in openspace
    assert "function _osBuildVisitor" in openspace
    # Shared conversations: one avatar per distinct human author.
    assert "src.type === 'user' && src.name" in openspace
    assert "'user:' + _osKey" in openspace
    template = rendered_chat_html()
    assert "osv-label-user" in template


def test_tool_calls_drop_props_that_fade_on_result():
    openspace = _openspace_text()
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
    openspace = _openspace_text()
    assert "function openspaceUserMessage" in openspace
    assert "\\u{1F4C1}" in openspace  # folder prop for attachments
    assert "_osWalkTo(rec, home)" in openspace  # and walks back home
    # A live new_message for an already-shown msg_id must not re-show.
    assert "if (d.msg_id && _osSeededIds.has(d.msg_id)) return;" in openspace


def test_tool_calls_announce_their_name_as_a_thought():
    openspace = _openspace_text()
    # Both the primary tool_call and sub_agent_tool paths bubble the name.
    assert openspace.count("_osToolEmoji(d.tool) + ' ' + (d.tool || 'tool')") == 2


def test_busy_agents_visibly_move():
    openspace = _openspace_text()
    # The capsule avatar is rotationally symmetric: rotation.y is invisible,
    # state animations must use lean (rotation.z) and bounce (position.y).
    assert "rotation.y = Math.sin" not in openspace
    assert "rec.avatar.rotation.z = !walking && sway" in openspace
    assert "emissiveIntensity" in openspace  # PC screen flickers while busy
    template = rendered_chat_html()
    # Thought bubbles read as thoughts: cloud tail + pulsing status chip.
    assert ".osv-thought::before" in template
    assert "osvStatusPulse" in template


def test_wall_screen_projects_the_live_simplified_view():
    openspace = _openspace_text()
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
    template = rendered_chat_html()
    assert ".osv-bigscreen" in template
    # The projected transcript stays scrollable (pointer-events on).
    assert "pointer-events: auto" in template


def test_thinking_events_reach_thought_bubbles():
    openspace = _openspace_text()
    # Thinking SSE payloads carry `text` (renderThinkingContent) and
    # delegate thinking carries `thinking` — never `content`.
    assert "d.text || d.content || ''" in openspace
    assert "_osStreamBubble(rec, 'thought', d.thinking || '')" in openspace


def test_office_has_decor_walkable_floor_and_camera_pan():
    openspace = _openspace_text()
    assert "function _osBuildEnvironment" in openspace
    assert "function _osWoodTexture" in openspace
    assert "function _osBuildConferenceZone" in openspace
    assert "function _osBuildLoungeZone" in openspace
    assert "function _osBuildVacantDesks" in openspace
    assert "shadowMap.enabled = !_osSoftwareRenderer" in openspace
    # Clicking the floor walks the viewer's own avatar there and moves
    # its home spot with it.
    assert "me.homeSeat = { x: gx, z: gz };" in openspace
    # Right-drag or shift-drag pans the camera target; the context menu
    # is suppressed on the canvas.
    assert "pan: !e.ctrlKey && (e.button === 2 || e.shiftKey)" in openspace
    assert "contextmenu" in openspace


def test_wall_fixtures_stay_on_the_room_side():
    environment = _text("tasks/io/chat_ui/openspace_environment.js")
    scene = _text("tasks/io/chat_ui/openspace_scene.js")
    room = _text("tasks/io/chat_ui/openspace_room.js")

    # Windows and the door are openings in segmented walls, not meshes pasted
    # over full solid walls. Shared constants keep the door and opening aligned.
    assert "function _osBuildWallWithOpenings" in environment
    assert "{ at: OSV_DOOR_X - 8.0, width: 2.4" in environment
    assert "g.position.set(OSV_DOOR_X, 0, OSV_DOOR_Z)" in room
    # Resource panels use the accessible office face of the meeting partition;
    # the black support posts that clipped the scene are gone.
    assert "const OSV_RESOURCE_WALL" in environment
    assert "const x = OSV_RESOURCE_WALL.faceX;" in scene
    assert "new T.BoxGeometry(0.12, 2.0, 0.12)" not in scene
    assert "const x = OSV_RESOURCE_WALL.faceX - 0.12;" in room
    # The transcript screen and title fit below the 4.2-unit partition.
    assert "const sw = 5.4" in scene
    assert "const screenBottom = 0.25" in scene
    assert "const pole = new T.Mesh" not in scene


def test_camera_has_frontal_surface_presets_and_level_side_views():
    source = _openspace_text()
    template = rendered_chat_html()
    assert "function _osSetCameraView(kind)" in source
    for kind in ("conversation", "board", "tv", "resources"):
        assert kind + ": {" in source
    assert "_osCamHeight = Math.max(0" in source
    assert "Math.max(3, Math.min(90" in source
    assert "new T.PerspectiveCamera(36, 1, 0.03, 250)" in source
    assert "osv-camera-views" in source and ".osv-camera-views" in template


def test_room_style_is_seeded_by_the_loaded_conversation():
    source = _openspace_text()
    assert "function _osApplyRoomStyle(seed)" in source
    assert "_osApplyRoomStyle(cid);" in source
    assert "_osRoomMats.walls" in source


def test_agents_are_chibi_mascots_with_batteries_and_a_roster_board():
    openspace = _openspace_text()
    # Mascot avatars replace the plain capsule for agents.
    assert "function _osBuildChibi" in openspace
    assert "rec.rig = { body: body, arms: arms, feet: feet, eyes: eyes" in openspace
    assert "function _osAnimateRig" in openspace
    assert "rig.mouth.scale.y" in openspace
    assert "rig.eyes.forEach" in openspace
    assert "rig.arms.forEach" in openspace
    assert "CapsuleGeometry" not in openspace.split("_osBuildVisitor")[0].split(
        "function _osBuildDesk")[1]
    # Battery above each head mirrors the shared context-usage cache.
    assert "window._contextUsage" in openspace
    assert "function _osRefreshBatteries" in openspace
    # Blackboard roster of active agents, projected like the wall screen.
    assert "function _osUpdateBoard" in openspace
    assert "_osProjectPanel(_osBoardEl, _osBoardCorners" in openspace
    template = rendered_chat_html()
    assert ".osv-batt" in template
    assert ".osv-board" in template


def test_bubble_streams_survive_expiry_and_layout_resizes():
    openspace = _openspace_text()
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
    template = rendered_chat_html()
    assert ".osv-help" in template


def test_multimodal_content_never_renders_object_object():
    openspace = _openspace_text()
    # Stored messages with attachments carry content as block arrays;
    # bubbles and logs extract the text parts instead of String(array).
    assert "function _osText" in openspace
    assert "_osText(text).replace" in openspace          # _osTrim
    assert "body: _osText(body)" in openspace            # activity log
    assert "_osText(m.content).replace" in openspace     # history seeding


def test_bubbles_are_fully_readable_and_anchored():
    openspace = _openspace_text()
    # Bubbles show the whole message/thought in a scrollable body pinned
    # to the newest text; no 200-char display truncation.
    assert "function _osSetBubbleText" in openspace
    assert "body.scrollTop = body.scrollHeight" in openspace
    assert "OSV_BUBBLE_MAX_CHARS = 8000" in openspace
    # Ctrl+drag lifts the camera target above the floor plane.
    assert "lift: e.ctrlKey" in openspace
    assert "_osCamPan.y" in openspace
    template = rendered_chat_html()
    assert ".osv-bubble-body { max-height" in template
    # Bubble tails are centered so they point at the avatar underneath.
    assert ".osv-thought::before { content: ''; position: absolute; left: 50%;" in template
    assert ".osv-speech::after" in template


def test_resource_posters_open_their_panels():
    src = _openspace_text()
    # One raycast-targeted poster per resources-menu entry; clicking one
    # opens the matching regular panel, never a re-implementation.
    assert "OSV_POSTERS" in src
    assert "osvPoster" in src
    for opener in ("openspaceOpenFlowsDialog", "cmdShowMemories", "cmdShowKg",
                   "cmdShowDiary", "cmdShowProjectGraph", "cmdShowProjectWiki",
                   "cmdShowScratchpad"):
        assert opener in src
    tmux_poster = re.search(r"\['tmux'.*?\],\n", src, re.DOTALL)
    assert tmux_poster is not None
    assert "cmdAgentTmux()" in tmux_poster.group()
    assert "toggleGrab" not in tmux_poster.group()


def test_flows_dialog_projects_a_live_3d_flow():
    src = _openspace_text()
    # Same data source as the sidebar Flows section: list_resources sees
    # every scope (conversation/user/global), unlike list_conv_flows.
    assert "action$('list_resources'" in src
    assert "action$('list_conv_flows'" not in src
    assert "flow_runtime_graph" in src
    # Live current: dots run along active links, backpressure turns them
    # red; geometry is built once and polls only recolor.
    assert "function _osTickFlow" in src
    assert "backpressured" in src
    # Closing the stage stops polling and restores the camera framing.
    assert "clearInterval(f.timer)" in src
    assert "prevPan" in src
    # Process groups / subflows drill down (click a blue block), with a
    # 3D up-arrow to pop one level; the poll follows the stack's top.
    assert "function _osFlowDrill" in src
    assert "function _osFlowUp" in src
    assert "subflow_ref" in src
    assert "osvFlowUp" in src
    template = rendered_chat_html()
    assert ".osv-flow-close" in template


def test_roster_board_has_per_agent_stop_controls():
    src = _openspace_text()
    assert "interruptSingle" in src
    assert "stopSingle" in src
    template = rendered_chat_html()
    # The projected board must opt back into clicks (overlay default is
    # pointer-events: none).
    assert ".osv-board { pointer-events: auto; }" in template
    assert ".osv-board-btn" in template


def test_openspace_v4_i18n_keys_exist_in_all_locales():
    for locale in ("en", "fr", "es"):
        data = json.loads(_text(f"tasks/io/chat_ui/i18n/{locale}.json"))
        for key in ("osvFlowPick", "osvFlowView", "osvFlowClose"):
            assert key in data, f"{key} missing in {locale}"


def test_resources_poster_pops_boards_that_open_submenu_dialogs():
    src = _openspace_text()
    # Resources poster → one labeled board per sidebar sub-section →
    # clicking a board opens that sub-menu as a live interactive dialog.
    assert "function openspaceToggleResourceBoards" in src
    assert "function openspaceOpenResSectionDialog" in src
    assert "osvResSection" in src
    assert 'res-section-' in src
    # The dialog body is a neutralized clone of the sidebar section: no
    # duplicate ids, no collapse toggle, inline handlers intact.
    assert "cloneNode(true)" in src
    assert 'removeAttribute(\'id\')' in src.replace('"', "'")
    assert "_toggleSection" in src
    # The wall of permanently projected clones is gone.
    assert "_osSyncResScreens" not in src
    template = rendered_chat_html()
    assert ".osv-resdialog-body" in template
    assert ".osv-resscreen" not in template


def test_door_opens_conversation_picker_and_rooms_are_seeded():
    src = _openspace_text()
    assert "osvDoor" in src
    assert "function openspaceOpenConvDialog" in src
    assert "resumeConv" in src
    # Same conversation → same room palette, derived from the id alone.
    assert "function _osApplyRoomStyle" in src
    assert "_osHashSeed(cid)" in src
    # Conversation title framed above the wall screen.
    assert "_osTitleCorners" in src
    template = rendered_chat_html()
    assert ".osv-convtitle" in template


def test_flow_stage_close_is_robust():
    src = _openspace_text()
    # Three independent close paths: Escape, the hardened DOM button,
    # and a raycast ✕ sprite inside the stage itself (immune to any
    # DOM overlay eating pointer events).
    assert "'Escape'" in src
    assert "osvFlowClose" in src
    template = rendered_chat_html()
    seg = template.split(".osv-flow-close")[1][:400]
    assert "z-index: 9999" in seg
    assert "pointer-events: auto" in seg


def test_projected_panels_cull_backfaces_and_depth_sort():
    src = _openspace_text()
    # A quad seen from behind or edge-on must hide, not smear a mirror
    # image across the scene; nearer panels stack above farther ones.
    assert "ux * wy - uy * wx" in src
    assert "zIndex" in src


def test_flash_guests_retire_and_delegates_walk_and_return():
    src = _openspace_text()
    assert "function _osRetireAgent" in src
    assert "_osFreeSeats" in src
    # In-conv delegation: walk to the desk, hand over, walk home.
    assert "src.awayAt !== dst.key" in src
    # Cross-conversation work (a2a) is a trip to the door.
    assert "function _osDoorTrip" in src
    assert "/a2a/i" in src


def test_walks_face_the_destination_and_keep_world_space_speed():
    src = _openspace_text()
    assert "Math.atan2(dx, dz)" in src
    assert "distance / OSV_WALK_UNITS_PER_SEC * 1000" in src
    assert "OSV_WALK_MIN_MS" in src
    assert "OSV_WALK_MAX_MS" in src


def test_clicking_a_participant_smoothly_focuses_the_camera():
    src = _openspace_text()
    assert "function _osFocusAgent" in src
    assert "_osFocusAgent(ud.osvAgent)" in src
    assert "let me = _osFocusKey ? _osAgents.get(_osFocusKey) : null" in src
    assert "_osCamPan.x += dx * 0.06" in src


def test_mobile_touch_controls_and_calm_resize():
    src = _openspace_text()
    # Pinch zoom + two-finger pan on the canvas, D-pad buttons on coarse
    # pointers, and keyboard-driven resize storms must not blink.
    assert "function _osPinchState" in src
    assert "_osResizeTimer" in src
    template = rendered_chat_html()
    assert ".osv-mobile-ctl" in template
    assert "pointer: coarse" in template


def test_composer_returns_to_default_size_when_empty():
    src = _text("tasks/io/chat_ui/attachments.js")
    # After a send (or with an empty value) the inline height is cleared
    # so the composer falls back to its stylesheet size on mobile too.
    assert "input.style.height = ''" in src
    assert "if (!input.value) { input.style.height = ''; return; }" in src
    # IME composition (Android keyCode 229) must not trigger the height
    # reflow that blinked and dropped the composed text.
    assert "e.isComposing || e.keyCode === 229" in src


def test_bubbles_flush_before_reset_and_are_dismissable():
    src = _openspace_text()
    # Turn-end resets flush the pending 250ms coalesce first, so a
    # thought never freezes one tick short (mid-sentence).
    assert "function _osFlushBubbles" in src
    for marker in ("on('done'", "on('turn_complete'", "on('sub_agent_done'"):
        assert marker in src
    assert src.count("_osFlushBubbles(rec)") >= 4
    # Every bubble carries a ✕ so it can be dismissed when it spoils the
    # view; the next message shows it again.
    assert "osv-bubble-close" in src
    template = rendered_chat_html()
    assert ".osv-bubble-close" in template


def test_conversation_switch_empties_the_room():
    src = _openspace_text()
    # Rooms are per-conversation: switching retires every desk/avatar of
    # the previous conversation and resets the seat allocator; the seed
    # then repopulates with the new participants.
    reset = src.split("function openspaceResetTransient")[1].split("\n}")[0]
    assert "_osRetireAgent(rec)" in reset
    assert "_osSeatCount = 0" in reset
    assert "_osUserCount = 0" in reset
    assert "_osFreeSeats.length = 0" in reset


def test_state_orbiters_circle_the_agent_ring():
    src = _openspace_text()
    # The floor ring doubles as a status carousel: brains orbit while
    # thinking, tools while one runs, Zzz while idle.
    assert "OSV_ORBIT_EMOJI" in src
    assert "\\u{1F9E0}" in src   # brain (thinking)
    assert "\\u{1F4A4}" in src   # Zzz (idle)
    assert "function _osTickOrbits" in src
    assert "_osTickOrbits(ts)" in src   # wired into the render loop
    # Kind is derived from the live state every frame, so a state change
    # swaps the carousel without any extra bookkeeping.
    assert "_osEnsureOrbit(rec, OSV_ORBIT_EMOJI[rec.state] ? rec.state : '')" in src
    # Thinking brains zoom in/out; tools spin on themselves.
    tick = src.split("function _osTickOrbits")[1].split("\nfunction ")[0]
    assert "sp.scale.set(z, z, 1)" in tick
    assert "sp.material.rotation" in tick
    # Sprite textures are disposed on swap (same discipline as tool props).
    clear = src.split("function _osClearOrbit")[1].split("\nfunction ")[0]
    assert "map.dispose()" in clear
    # Retiring a desk clears its orbiters too — the avatar traverse does
    # not reach sprite texture maps.
    retire = src.split("function _osRetireAgent")[1].split("\nfunction ")[0]
    assert "_osClearOrbit(rec)" in retire


def test_running_agents_never_drift_to_idle_while_tracked():
    src = _openspace_text()
    # The active-agents tracker (server poll + SSE hints) is the liveness
    # reference: a flash delegate running a 30s bash, or thinking without a
    # streamed preview, stayed quiet longer than the linger window and was
    # put to sleep (Zzz) while it was actually working.
    assert "function _osLiveAgents()" in src
    live = src.split("function _osLiveAgents()")[1].split("\nfunction ")[0]
    assert "Object.values(activeInteractions)" in live
    expire = src.split("function _osExpireBubbles(now)")[1].split("\nfunction ")[0]
    assert "const live = _osLiveAgents();" in expire
    assert "const it = live.get(rec.key);" in expire
    # Tracked → never auto-idled; an idle avatar still reported by a fresher
    # tracker entry wakes up (tool if one is in flight, thinking otherwise).
    assert "if (rec.state === 'idle' && (it.updatedAt || 0) > rec.stateSince)" in expire
    assert "_osSetState(rec, busyTool ? 'tool' : 'thinking', busyTool)" in expire
    tracked, untracked = expire.split("const it = live.get(rec.key);")[1].split("return;\n    }")
    assert "OSV_IDLE_AFTER_MS" not in tracked
    # The quiet-timeout fallback only applies to agents the tracker omits.
    assert "now - rec.stateSince > OSV_BUBBLE_LINGER_MS + OSV_IDLE_AFTER_MS" in untracked


def test_user_bubbles_fade_and_idle_agents_show_their_last_message():
    src = _openspace_text()
    # Live user bubbles are transient (10s fade); the bubble restored from
    # history at load stays until a live one replaces it.
    assert "const OSV_USER_BUBBLE_FADE_MS = 10000;" in src
    expire = src.split("function _osExpireBubbles(now)")[1].split("\nfunction ")[0]
    assert "if (shown && !rec.speechSeeded" in expire
    assert "now - rec.speechAt > OSV_USER_BUBBLE_FADE_MS" in expire
    restore = src.split("function _osRestoreBubbles")[1].split("\nfunction ")[0]
    assert "if (kind === 'speech') rec.speechSeeded = true;" in restore
    show = src.split("function _osShowBubble")[1].split("\nfunction ")[0]
    assert "rec.speechSeeded = false;" in show
    # Zzz rule: an idle agent shows its last MESSAGE, never its thinking;
    # a bubble the viewer dismissed with the close cross is not restored.
    idle = expire.split("if (rec.state === 'idle') {")[1].split("// The last bubble never disappears")[0]
    assert "rec.thoughtEl.style.display = 'none';" in idle
    assert "rec.lastSpeech && !rec.speechDismissed" in idle
    assert "_osSetBubbleText(rec, 'speech', _osFull(rec.lastSpeech.text));" in idle
    assert "if (el === speech) rec.speechDismissed = true; else rec.thoughtDismissed = true;" in src
    assert "rec.speechDismissed = false;" in show


def test_filestore_tv_plays_conversation_files():
    source = _openspace_text()
    # A clickable TV mesh opens the FileStore picker...
    assert "ud.osvTv) { _osSetCameraView('tv'); openspaceOpenTvDialog(); return; }" in source
    assert "action$('list_conv_files', { conversation_id: conversationId })" in source
    # ...and the picked file renders by content_type on a projected panel:
    # video/image/audio elements, unsupported formats point at the Files menu.
    assert "type.startsWith('video/')" in source
    assert "type.startsWith('image/')" in source
    assert "type.startsWith('audio/')" in source
    assert "t('osvTvUnsupported')" in source
    assert "_osProjectPanel(_osTvEl, _osTvCorners, OSV_TV_W, OSV_TV_H)" in source
    # File URLs go through the FileStore HTTP route.
    assert "'/files/' + encodeURIComponent(f.file_id)" in source
    # Media stops on room switch and on view deactivation (no ghost audio).
    assert source.count("openspaceTvStop()") >= 3
    template = rendered_chat_html()
    assert ".osv-tv " in template and ".osv-tv-body" in template


def test_poster_wall_covers_all_side_panels():
    source = _openspace_text()
    for key, opener in [
        ("'todos'", "showTodosDialog"),
        ("'cost'", "showUsageCostPanel"),
        ("'context'", "cmdShowContext"),
        ("'plans'", "togglePlansPanel"),
        ("'scheduled'", "toggleSchedsPanel"),
        ("'files'", "toggleFilesPanel"),
        ("'desktop'", "cmdDesktop"),
        ("'terminal'", "cmdTerminal"),
        ("'tmux'", "cmdAgentTmux"),
    ]:
        assert key in source, key
        assert opener in source, opener
    # Posters and transient boards share the compact accessible gallery grid.
    assert "OSV_POSTERS_PER_ROW" in source
    assert "OSV_POSTERS_PER_ROW = OSV_RESOURCE_WALL.columns" in source
    assert "Math.floor(i / OSV_RESOURCE_WALL.columns) * 0.94" in source
