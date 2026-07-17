"""Bench tier 3: Gemini Live with video-frame input.

Publishes a synthetic camera track (RED square on gray, the spike's
validation pattern) plus a spoken question, and asserts the agent actually
SAW the frame: its transcript must name the color. Also checks the user
transcript event and a non-silent agent audio reply.
"""

import asyncio
import json
import os
import sys
import urllib.request

from livekit import api, rtc

ROOM = os.environ.get("BENCH_ROOM", "bench-room-gemini-1")
LK_URL = "ws://127.0.0.1:7880"
PHRASE = ("Look at my camera. What is the color of the square you can "
          "see? Answer with just the color name.")
WIDTH, HEIGHT = 640, 480


def events():
    with urllib.request.urlopen("http://127.0.0.1:8898/events") as r:
        return json.load(r)


def synthesize_pcm24k(text: str) -> bytes:
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/speech",
        data=json.dumps({"model": "gpt-4o-mini-tts", "voice": "onyx",
                         "input": text,
                         "response_format": "pcm"}).encode(),
        headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"],
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def red_square_frame() -> rtc.VideoFrame:
    row_bg = bytes([128, 128, 128, 255]) * WIDTH
    x0, x1 = WIDTH // 4, 3 * WIDTH // 4
    y0, y1 = HEIGHT // 4, 3 * HEIGHT // 4
    row_sq = (bytes([128, 128, 128, 255]) * x0
              + bytes([220, 40, 40, 255]) * (x1 - x0)
              + bytes([128, 128, 128, 255]) * (WIDTH - x1))
    buf = bytearray()
    for y in range(HEIGHT):
        buf += row_sq if y0 <= y < y1 else row_bg
    return rtc.VideoFrame(WIDTH, HEIGHT, rtc.VideoBufferType.RGBA, bytes(buf))


async def main() -> int:
    pcm = synthesize_pcm24k(PHRASE)
    print(f"driver3: synthesized {len(pcm)} bytes of speech")

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
        print("driver3: subscribed to", participant.identity, track.kind)
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            async def _drain():
                stream = rtc.AudioStream(track)
                async for ev in stream:
                    agent_audio["frames"] += 1
                    samples = memoryview(bytes(ev.frame.data)).cast("h")
                    agent_audio["energy"] += sum(
                        abs(s) for s in samples[::50])
            asyncio.ensure_future(_drain())

    await room.connect(LK_URL, token)
    print("driver3: joined room", ROOM)

    # camera: constant red square at ~5 fps
    vsource = rtc.VideoSource(WIDTH, HEIGHT)
    vtrack = rtc.LocalVideoTrack.create_video_track("camera", vsource)
    await room.local_participant.publish_track(
        vtrack, rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_CAMERA))
    frame = red_square_frame()

    async def _video_loop():
        while True:
            vsource.capture_frame(frame)
            await asyncio.sleep(0.2)
    video_task = asyncio.ensure_future(_video_loop())

    asource = rtc.AudioSource(24000, 1)
    atrack = rtc.LocalAudioTrack.create_audio_track("mic", asource)
    await room.local_participant.publish_track(
        atrack, rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_MICROPHONE))

    silence = rtc.AudioFrame.create(24000, 1, 2400)
    for _ in range(50):  # 5 s: agent joins + first frames sampled
        await asource.capture_frame(silence)

    step = 2400 * 2
    for off in range(0, len(pcm) - step, step):
        await asource.capture_frame(
            rtc.AudioFrame(pcm[off:off + step], 24000, 1, 2400))
    print("driver3: question sent, waiting for the agent...")

    checks = {"user_transcript": False, "agent_transcript": False,
              "color_correct": False, "agent_audio": False}
    agent_texts = []
    deadline = asyncio.get_event_loop().time() + 60
    while asyncio.get_event_loop().time() < deadline:
        await asource.capture_frame(silence)
        st = events()
        for t, d in st["messages"]:
            name = str(d.get("name", ""))
            text = str((d.get("data") or {}).get("text", ""))
            if name == "realtime.user.transcript.final":
                checks["user_transcript"] = True
            if name == "realtime.agent.transcript.final" and text:
                checks["agent_transcript"] = True
                agent_texts.append(text)
                if "red" in text.lower():
                    checks["color_correct"] = True
        checks["agent_audio"] = (agent_audio["frames"] > 10
                                 and agent_audio["energy"] > 20000)
        if all(checks.values()):
            break
        await asyncio.sleep(0.1)

    video_task.cancel()
    print("driver3: agent said:", agent_texts)
    print(f"driver3: audio frames={agent_audio['frames']} "
          f"energy={agent_audio['energy']}")
    print("driver3: checks:", checks)
    await room.disconnect()
    ok = all(checks.values())
    print("TIER3", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
