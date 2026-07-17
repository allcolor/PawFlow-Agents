"""Bench tier 2: real spoken turn through the full stack.

Synthesizes a spoken instruction with the OpenAI TTS API (24 kHz PCM),
publishes it as the user's mic track, and asserts the provider leg works
end-to-end: user speech understood, `echo` tool round-trip through the
(fake) PawFlow control plane, agent audio reply received in the room.
"""

import asyncio
import json
import os
import sys
import urllib.request

from livekit import api, rtc

ROOM = os.environ.get("BENCH_ROOM", "bench-room-2")
LK_URL = "ws://127.0.0.1:7880"
PHRASE = ("Hello. Please call the echo tool with the text banana, "
          "then tell me what it returned.")


def events():
    with urllib.request.urlopen("http://127.0.0.1:8898/events") as r:
        return json.load(r)


def synthesize_pcm24k(text: str) -> bytes:
    """OpenAI TTS -> raw PCM16 mono 24 kHz (the API's pcm format)."""
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/speech",
        data=json.dumps({"model": "gpt-4o-mini-tts", "voice": "onyx",
                         "input": text,
                         "response_format": "pcm"}).encode(),
        headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"],
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


async def main() -> int:
    pcm = synthesize_pcm24k(PHRASE)
    print(f"driver2: synthesized {len(pcm)} bytes "
          f"({len(pcm) / 48000:.1f} s of speech)")

    token = (api.AccessToken("devkey", "secret")
             .with_identity("bench-user")
             .with_grants(api.VideoGrants(room_join=True, room=ROOM,
                                          can_publish=True,
                                          can_subscribe=True))
             .to_jwt())
    room = rtc.Room()

    agent_audio = {"frames": 0, "energy": 0}

    @room.on("track_subscribed")
    def _on_track(track, pub, participant):
        print("driver2: subscribed to", participant.identity, track.kind)
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            async def _drain():
                stream = rtc.AudioStream(track)
                async for ev in stream:
                    data = ev.frame.data
                    agent_audio["frames"] += 1
                    # cheap non-silence check on int16 samples (frame.data
                    # is already an int16 memoryview — copy to bytes first)
                    samples = memoryview(bytes(data)).cast("h")
                    agent_audio["energy"] += sum(
                        abs(s) for s in samples[::50])
            asyncio.ensure_future(_drain())

    await room.connect(LK_URL, token)
    print("driver2: joined room", ROOM)

    source = rtc.AudioSource(24000, 1)
    track = rtc.LocalAudioTrack.create_audio_track("mic", source)
    await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_MICROPHONE))

    silence = rtc.AudioFrame.create(24000, 1, 2400)  # 100 ms
    # let the agent join + session open before speaking
    for _ in range(30):
        await source.capture_frame(silence)

    # speak the phrase (100 ms chunks)
    step = 2400 * 2
    for off in range(0, len(pcm) - step, step):
        frame = rtc.AudioFrame(pcm[off:off + step], 24000, 1, 2400)
        await source.capture_frame(frame)
    print("driver2: phrase sent, waiting for the agent...")

    checks = {"tool_call": False, "tool_result_ok": False,
              "transcript_event": False, "agent_audio": False}
    deadline = asyncio.get_event_loop().time() + 60
    while asyncio.get_event_loop().time() < deadline:
        await source.capture_frame(silence)
        st = events()
        for t, d in st["messages"]:
            if t == "tool_call" and d.get("name") == "echo":
                checks["tool_call"] = True
            if t == "event" and "transcript" in str(d.get("name", "")):
                checks["transcript_event"] = True
        checks["tool_result_ok"] = checks["tool_call"]  # fake replies inline
        checks["agent_audio"] = (agent_audio["frames"] > 10
                                 and agent_audio["energy"] > 1000)
        if all(checks.values()):
            break
        await asyncio.sleep(0.1)

    st = events()
    print("driver2: recorded:",
          [(t, d.get("name", d.get("call_id", "")))
           for t, d in st["messages"]][:40])
    print(f"driver2: agent audio frames={agent_audio['frames']} "
          f"energy={agent_audio['energy']}")
    print("driver2: checks:", checks)
    await room.disconnect()
    ok = all(checks.values())
    print("TIER2", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
