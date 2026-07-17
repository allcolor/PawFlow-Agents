"""Bench: minimal OpenAI-compatible local STT server (faster-whisper).

Stands in for speaches/faster-whisper-server: POST /v1/audio/transcriptions
(multipart file) -> {"text": ...}. CPU int8, model from LOCAL_STT_MODEL
(default: base). No audio leaves the machine.
"""

import os
import tempfile

from aiohttp import web
from faster_whisper import WhisperModel

MODEL = WhisperModel(os.environ.get("LOCAL_STT_MODEL", "base"),
                     device="cpu", compute_type="int8")
print("stt: model loaded", flush=True)


async def transcribe(request):
    reader = await request.multipart()
    audio = b""
    async for part in reader:
        if part.name == "file":
            audio = await part.read(decode=False)
    if not audio:
        return web.json_response({"error": "no file"}, status=400)
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        f.write(audio)
        f.flush()
        segments, _info = MODEL.transcribe(f.name, language="en")
        text = " ".join(s.text.strip() for s in segments).strip()
    print("stt:", repr(text), flush=True)
    return web.json_response({"text": text})


app = web.Application(client_max_size=64 * 1024 * 1024)
app.router.add_post("/v1/audio/transcriptions", transcribe)

if __name__ == "__main__":
    web.run_app(app, host="127.0.0.1", port=8001, print=None)
