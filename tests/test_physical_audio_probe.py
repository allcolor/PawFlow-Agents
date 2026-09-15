"""Reject missing or crossed signals in physical audio acceptance evidence."""

import base64
import queue
import struct
from types import SimpleNamespace

import pytest

from tests import physical_audio_probe as probe


def test_tone_playback_refuses_non_disposable_execution(monkeypatch):
    monkeypatch.delenv("PAWFLOW_DISPOSABLE_ACCEPTANCE", raising=False)
    with pytest.raises(RuntimeError, match="explicitly disposable"):
        probe.play_tone("alpha")


@pytest.mark.parametrize("name", ["alpha", "beta"])
def test_expected_tone_is_detected_and_sibling_is_rejected(name):
    samples = probe.tone_samples(name, 1)
    report = probe.validate_samples(samples, name)
    assert report["strengths"][name] > 5900
    sibling = "beta" if name == "alpha" else "alpha"
    assert report["strengths"][sibling] < 1
    with pytest.raises(RuntimeError, match="tone missing"):
        probe.validate_samples(samples, sibling)


def test_mixed_audio_is_rejected():
    samples = [first + second for first, second in zip(
        probe.tone_samples("alpha", 1), probe.tone_samples("beta", 1))]
    with pytest.raises(RuntimeError, match="crossed logical identities"):
        probe.validate_samples(samples, "alpha")


def test_silence_and_insufficient_audio_are_rejected():
    with pytest.raises(RuntimeError, match="tone missing"):
        probe.validate_samples([0] * probe.RATE, "alpha")
    with pytest.raises(RuntimeError, match="Insufficient"):
        probe.validate_samples([0], "alpha")


def test_partial_tunnel_open_failure_closes_only_the_owned_session():
    calls = []

    def alpha(action, **kwargs):
        calls.append((action, kwargs))
        assert action in ("desktop_audio_open", "desktop_audio_close")
        return {"ok": True}

    def beta(action, **kwargs):
        assert action == "desktop_audio_open"
        return {"ok": False, "error": "refused"}

    peers = {"alpha": SimpleNamespace(command=alpha), "beta": SimpleNamespace(command=beta)}
    desktops = {name: {"desktop": {"audio_port": 6180}} for name in peers}
    with pytest.raises(RuntimeError, match="Audio tunnel failed"):
        probe.exercise(peers, desktops, 1)
    assert [call[0] for call in calls] == ["desktop_audio_open", "desktop_audio_close"]
    assert calls[0][1]["session_id"] == calls[1][1]["session_id"]


@pytest.mark.skipif(not probe.ctypes.util.find_library("opus"), reason="libopus is required")
@pytest.mark.parametrize("name", ["alpha", "beta"])
def test_real_opus_round_trip_retains_each_tone(name):
    from tools.audio_capture import OpusEncoder

    encoder = OpusEncoder()
    samples = probe.tone_samples(name, 3)
    events = queue.Queue()
    for start in range(0, len(samples), 960):
        packet = encoder.encode(struct.pack("<960h", *samples[start:start + 960]))
        events.put({"type": "desktop_audio_data", "session_id": name,
                    "data": base64.b64encode(packet).decode()})
    peer = SimpleNamespace(name=name, audio_events=events)
    decoded = probe.capture_samples(peer, name)
    assert len(decoded) == probe.RATE * 3
    assert probe.validate_samples(decoded, name)["strengths"][name] > 5000


@pytest.mark.skipif(not probe.ctypes.util.find_library("opus"), reason="libopus is required")
@pytest.mark.parametrize("event,reason", [
    ({"type": "desktop_audio_data", "session_id": "beta"}, "crossed identities"),
    ({"type": "desktop_audio_close", "session_id": "alpha"}, "closed early"),
    ({"type": "desktop_audio_data", "session_id": "alpha", "data": ""}, "Empty"),
    ({"type": "desktop_audio_data", "session_id": "alpha",
      "data": base64.b64encode(bytes([3])).decode()}, "Unexpected"),
])
def test_audio_decoder_rejects_crossed_closed_or_invalid_packets(event, reason):
    events = queue.Queue()
    events.put(event)
    with pytest.raises(RuntimeError, match=reason):
        probe.capture_samples(SimpleNamespace(name="alpha", audio_events=events), "alpha")
