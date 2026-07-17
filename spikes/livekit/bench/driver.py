"""Bench tier 1 driver: join a room as a fake user -> LiveKit dispatches
the real worker -> assert the PawFlow glue fired (bootstrap fetch, control
WS hello, media/session events). Publishes a silent audio track so the
room looks like a real call.
"""

import asyncio
import json
import sys
import urllib.request

from livekit import api, rtc

ROOM = "bench-room-1"
LK_URL = "ws://127.0.0.1:7880"


def events():
    with urllib.request.urlopen("http://127.0.0.1:8898/events") as r:
        return json.load(r)


async def main() -> int:
    token = (api.AccessToken("devkey", "secret")
             .with_identity("bench-user")
             .with_grants(api.VideoGrants(room_join=True, room=ROOM,
                                          can_publish=True,
                                          can_subscribe=True))
             .to_jwt())
    room = rtc.Room()
    await room.connect(LK_URL, token)
    print("driver: joined room", ROOM)

    # publish a silent mic track so the session has audio input
    source = rtc.AudioSource(24000, 1)
    track = rtc.LocalAudioTrack.create_audio_track("mic", source)
    await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
    frame = rtc.AudioFrame.create(24000, 1, 2400)  # 100 ms of silence

    checks = {"bootstrap": False, "ws_open": False, "hello": False,
              "agent_joined": False, "media_connected": False}

    @room.on("participant_connected")
    def _on_participant(p):
        print("driver: participant joined:", p.identity)
        if p.identity.startswith("agent") or "agent" in p.identity.lower():
            checks["agent_joined"] = True

    for p in room.remote_participants.values():
        if "agent" in p.identity.lower():
            checks["agent_joined"] = True

    for i in range(300):  # 30 s budget, feeding silence the whole time
        await source.capture_frame(frame)
        if i % 10 == 0:
            st = events()
            checks["bootstrap"] = bool(st["bootstrap_calls"])
            checks["ws_open"] = any(t == "_ws_open" for t, _ in st["messages"])
            checks["hello"] = st["hello"] is not None
            checks["media_connected"] = any(
                t == "event" and d.get("name") == "realtime.media.connected"
                for t, d in st["messages"])
            if all(checks.values()):
                break
        await asyncio.sleep(0.1)

    st = events()
    print("driver: recorded messages:",
          [(t, d.get("name", "")) for t, d in st["messages"]][:20])
    print("driver: checks:", checks)
    await room.disconnect()
    # agent_joined + media event need a working provider in some paths;
    # the hard tier-1 gate is the PawFlow glue: bootstrap + WS + hello.
    hard = ["bootstrap", "ws_open", "hello"]
    ok = all(checks[k] for k in hard)
    print("TIER1", "PASSED" if ok else "FAILED",
          "| soft:", {k: checks[k] for k in checks if k not in hard})
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
