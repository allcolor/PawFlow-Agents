"""Bench: minimal OpenAI-compatible local TTS server (piper).

Stands in for kokoro-fastapi: POST /v1/audio/speech {input, response_format}
-> mp3 (the livekit openai TTS plugin default). Piper synthesizes WAV 22.05k
locally; ffmpeg transcodes. No audio leaves the machine.
"""

import asyncio
import io
import os
import wave

from aiohttp import web
from piper import PiperVoice

VOICE = PiperVoice.load(os.environ.get(
    "PIPER_VOICE", "/tmp/bench/en_US-lessac-medium.onnx"))
print("tts: voice loaded", flush=True)


def _synth_wav(text: str) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        VOICE.synthesize_wav(text, w)
    return buf.getvalue()


async def speech(request):
    body = await request.json()
    text = body.get("input", "")
    fmt = body.get("response_format", "mp3")
    wav = await asyncio.get_event_loop().run_in_executor(
        None, _synth_wav, text)
    print("tts:", repr(text[:80]), len(wav), "bytes wav", flush=True)
    if fmt == "wav":
        return web.Response(body=wav, content_type="audio/wav")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
        "-ar", "24000", "-ac", "1", "-f", "mp3", "-b:a", "64k", "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
    mp3, _ = await proc.communicate(wav)
    return web.Response(body=mp3, content_type="audio/mpeg")


app = web.Application()
app.router.add_post("/v1/audio/speech", speech)

if __name__ == "__main__":
    web.run_app(app, host="127.0.0.1", port=8002, print=None)
