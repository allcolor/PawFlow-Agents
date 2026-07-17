"""P0 spike helper: publish synthetic video frames into a LiveKit room.

Lets the Gemini video spike be validated without a physical camera: a
colored square cycles red -> green -> blue every 3 seconds on a gray
background. Ask the agent "what color is the square?" and check that its
answer follows the cycle.

Requires: pip install "pawflow[realtime-livekit]"
Env: LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET

Run:  python spikes/livekit/publish_synthetic_video.py <room-name>
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.livekit_deps import require_livekit  # noqa: E402

require_livekit()

from livekit import api, rtc  # noqa: E402

WIDTH, HEIGHT, FPS = 640, 480, 5
COLORS = [("red", (220, 40, 40)), ("green", (40, 200, 60)),
          ("blue", (50, 80, 230))]


def make_frame(rgb) -> rtc.VideoFrame:
    """Gray background with a centered colored square, RGBA byte buffer."""
    r, g, b = rgb
    row_bg = bytes([128, 128, 128, 255]) * WIDTH
    sq_x0, sq_x1 = WIDTH // 4, 3 * WIDTH // 4
    sq_y0, sq_y1 = HEIGHT // 4, 3 * HEIGHT // 4
    row_sq = (bytes([128, 128, 128, 255]) * sq_x0
              + bytes([r, g, b, 255]) * (sq_x1 - sq_x0)
              + bytes([128, 128, 128, 255]) * (WIDTH - sq_x1))
    buf = bytearray()
    for y in range(HEIGHT):
        buf += row_sq if sq_y0 <= y < sq_y1 else row_bg
    return rtc.VideoFrame(WIDTH, HEIGHT, rtc.VideoBufferType.RGBA, bytes(buf))


async def run(room_name: str) -> None:
    token = (
        api.AccessToken(os.environ["LIVEKIT_API_KEY"],
                        os.environ["LIVEKIT_API_SECRET"])
        .with_identity("synthetic-video")
        .with_grants(api.VideoGrants(room_join=True, room=room_name))
        .to_jwt())
    room = rtc.Room()
    await room.connect(os.environ.get("LIVEKIT_URL", "ws://localhost:7880"),
                       token)
    source = rtc.VideoSource(WIDTH, HEIGHT)
    track = rtc.LocalVideoTrack.create_video_track("synthetic", source)
    await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))
    print(f"publishing {WIDTH}x{HEIGHT}@{FPS}fps into room '{room_name}' "
          "(square cycles red/green/blue every 3 s, Ctrl-C to stop)")
    frames = {name: make_frame(rgb) for name, rgb in COLORS}
    tick = 0
    try:
        while True:
            name = COLORS[(tick // (FPS * 3)) % len(COLORS)][0]
            source.capture_frame(frames[name])
            tick += 1
            await asyncio.sleep(1 / FPS)
    finally:
        await room.disconnect()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: publish_synthetic_video.py <room-name>")
    asyncio.run(run(sys.argv[1]))
