"""P3 tests: LiveKit webchat wiring (static introspection, house pattern)
and the vendored SDK endpoint."""

import json
from pathlib import Path


def test_livekit_js_module_is_wired():
    serve = Path("tasks/io/serve_chat_ui.py").read_text(encoding="utf-8")
    assert '"conversation_livekit.js",' in serve
    # must load after conversation_voice.js (overlay helpers)
    assert serve.index('"conversation_voice.js",') < \
        serve.index('"conversation_livekit.js",')

    js = Path("tasks/io/chat_ui/conversation_livekit.js").read_text(
        encoding="utf-8")
    assert "async function startLiveKitVoiceMode" in js
    assert "function stopLiveKitVoiceMode" in js
    assert "'/api/realtime/livekit/start'" in js
    assert "'/api/realtime/livekit/stop'" in js
    assert "'/api/realtime/livekit/sdk.js'" in js  # lazy SDK load
    assert "setMicrophoneEnabled" in js
    assert "setCameraEnabled" in js
    assert "setScreenShareEnabled" in js
    # camera/screen controls gated by the service's video config
    assert "video_input" in js and "video_source" in js
    # SSE captions filtered to the active session
    assert "data.session_id !== _lkSession.session_id" in js
    # stop tells the server unless the server initiated the close
    assert "reason !== 'closed'" in js


def test_voice_button_routes_by_engine():
    js = Path("tasks/io/chat_ui/conversation_voice.js").read_text(
        encoding="utf-8")
    assert "svcEntry.engine === 'livekit'" in js
    assert "startLiveKitVoiceMode(cid, svcEntry)" in js
    # active-state + toggle account for a LiveKit session
    assert "_lkActive" in js
    assert "stopLiveKitVoiceMode('user')" in js
    # settings panel shows the engine
    assert "'LiveKit'" in js or "LiveKit/" in js.replace("'", "")


def test_sse_wires_realtime_listeners():
    sse = Path("tasks/io/chat_ui/sse.js").read_text(encoding="utf-8")
    assert "_lkWireSSE" in sse
    lk = Path("tasks/io/chat_ui/conversation_livekit.js").read_text(
        encoding="utf-8")
    for event in ("realtime.session.ready", "realtime.agent.state",
                  "realtime.user.transcript.delta",
                  "realtime.agent.transcript.final",
                  "realtime.tool.started", "realtime.session.closed"):
        assert event in lk


def test_media_action_exposes_engine():
    media = Path("tasks/ai/actions/media.py").read_text(encoding="utf-8")
    assert '"engine": (_cfg.get("engine", "legacy")' in media
    assert '"video_input"' in media


def test_vendored_sdk_present_and_pinned():
    sdk = Path("tasks/io/chat_ui/vendor/livekit-client.umd.min.js")
    assert sdk.is_file()
    assert sdk.stat().st_size > 100_000  # a real bundle, not a stub
    notices = Path("THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "livekit-client" in notices and "Apache-2.0" in notices


def test_sdk_endpoint_serves_bundle():
    from services._livekit_sessions import _sdk_endpoint
    from services._http_base import PendingRequest
    req = PendingRequest(request_id="r", method="GET",
                         path="/api/realtime/livekit/sdk.js", headers={},
                         body=b"")
    _sdk_endpoint(req)
    assert req.response_status == 200
    assert "javascript" in req.response_headers["Content-Type"]
    assert len(req.response_body) > 100_000


def test_livekit_routes_include_sdk_and_bootstrap():
    src = Path("services/_livekit_sessions.py").read_text(encoding="utf-8")
    for route in ("/api/realtime/livekit/start",
                  "/api/realtime/livekit/stop",
                  "/api/realtime/livekit/worker/bootstrap",
                  "/api/realtime/livekit/sdk.js",
                  "/ws/realtime-worker/"):
        assert route in src


def test_i18n_has_livekit_keys():
    for lang in ("en", "fr", "es"):
        data = json.loads(Path(f"tasks/io/chat_ui/i18n/{lang}.json")
                          .read_text(encoding="utf-8"))
        for key in ("lkCamOn", "lkCamOff", "lkScreenOn", "lkScreenOff",
                    "lkStartFailed"):
            assert key in data, f"{key} missing in {lang}.json"
