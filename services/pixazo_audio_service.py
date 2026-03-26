"""Pixazo audio generation service — supports Pixazo audio models.

Implements BaseAudioGenerationService for the Pixazo gateway API.
Async: POST generate -> id, POST poll -> status/url, download audio.
Same pattern as Pixazo image/video services.
"""

import json
import logging
import time
import http.client
import ssl
import urllib.request

from core import ServiceFactory, ServiceError
from services.base_audio_generation import BaseAudioGenerationService

logger = logging.getLogger(__name__)

_GATEWAY = "gateway.pixazo.ai"

# Verified Pixazo audio models (from pixazo.ai/models/audio-generation)
PIXAZO_AUDIO_MODELS = {
    "minimax-music": {
        "label": "MiniMax Music 2.0 (Hailuo AI)",
        "generate_endpoint": "/minimax-hailuo-ai-music/v1/getAudio",
        "poll_endpoint": "/minimax-hailuo-ai-music/v1/getAudioResult",
        "id_field": "task_id",
        "poll_id_field": "task_id",
    },
    "ace-step": {
        "label": "Ace Step 1.5 (ACE Studio)",
        "generate_endpoint": "/ace-step/v1/generate",
        "poll_endpoint": "/ace-step/v1/status",
        "id_field": "task_id",
        "poll_id_field": "task_id",
    },
    "lyria": {
        "label": "Lyria 2 (Google)",
        "generate_endpoint": "/lyria-2/v1/lyria-2/generate",
        "poll_endpoint": "/lyria-2/v1/lyria-2/prediction",
        "id_field": "request_id",
        "poll_id_field": "prediction_id",  # asymmetric!
    },
    "elevenlabs": {
        "label": "ElevenLabs Music",
        "generate_endpoint": "/elevenlabs-music-api-368/v1/elevenlabs-music-api-request",
        "poll_endpoint": "/elevenlabs-music-api-368/v1/elevenlabs-music-api-request-result",
        "id_field": "request_id",
        "poll_id_field": "request_id",
    },
    "tracks": {
        "label": "Pixazo Tracks",
        "generate_endpoint": "/tracks/v1/generate",
        "poll_endpoint": "/tracks/v1/status",
        "id_field": "task_id",
        "poll_id_field": "task_id",
    },
}

_AUDIO_MODEL_OPTIONS = ["custom"] + sorted(PIXAZO_AUDIO_MODELS.keys())


class PixazoAudioService(BaseAudioGenerationService):
    TYPE = "pixazoAudioGeneration"
    VERSION = "1.0.0"
    NAME = "Pixazo Audio Generation"
    DESCRIPTION = "Generate audio/music via Pixazo API (MiniMax, Ace Step, Lyria, ElevenLabs, Tracks)"

    def get_parameter_schema(self) -> dict:
        return {
            "api_key": {
                "type": "string", "required": True, "sensitive": True,
                "description": "Pixazo API key (Ocp-Apim-Subscription-Key)",
            },
            "model": {
                "type": "select", "required": False,
                "default": "minimax-music",
                "options": _AUDIO_MODEL_OPTIONS,
                "description": "Pixazo audio model. Use 'custom' for unlisted models.",
            },
            "custom_generate_endpoint": {
                "type": "string", "required": False, "default": "",
                "description": "Custom generate endpoint (only when model='custom')",
                "show_when": {"model": ["custom"]},
            },
            "custom_poll_endpoint": {
                "type": "string", "required": False, "default": "",
                "description": "Custom poll endpoint (only when model='custom')",
                "show_when": {"model": ["custom"]},
            },
            "poll_interval": {
                "type": "integer", "required": False, "default": 5,
                "description": "Seconds between status polls",
            },
        }

    def __init__(self, config):
        super().__init__(config)
        self.api_key = self.config.get("api_key", "")
        self.poll_interval = int(self.config.get("poll_interval", 5))

        model_key = self.config.get("model", "minimax-music")
        if model_key == "custom":
            self._gen_endpoint = self.config.get("custom_generate_endpoint", "")
            self._poll_endpoint = self.config.get("custom_poll_endpoint", "")
            self._id_field = "request_id"
            self._poll_id_field = "request_id"
        elif model_key in PIXAZO_AUDIO_MODELS:
            m = PIXAZO_AUDIO_MODELS[model_key]
            self._gen_endpoint = m["generate_endpoint"]
            self._poll_endpoint = m["poll_endpoint"]
            self._id_field = m["id_field"]
            self._poll_id_field = m["poll_id_field"]
        else:
            m = PIXAZO_AUDIO_MODELS["minimax-music"]
            self._gen_endpoint = m["generate_endpoint"]
            self._poll_endpoint = m["poll_endpoint"]
            self._id_field = m["id_field"]
            self._poll_id_field = m["poll_id_field"]

    def _create_connection(self):
        if not self.api_key:
            raise ServiceError("api_key is required for Pixazo audio service")
        if not self._gen_endpoint:
            raise ServiceError("No endpoint configured (set model or custom endpoints)")
        return {"ready": True}

    def _close_connection(self):
        pass

    def _gateway_post(self, path: str, body: dict) -> dict:
        """POST to Pixazo gateway."""
        ctx = ssl.create_default_context()
        json_body = json.dumps(body).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "Ocp-Apim-Subscription-Key": self.api_key,
            "Content-Length": str(len(json_body)),
        }
        conn = http.client.HTTPSConnection(_GATEWAY, timeout=60, context=ctx)
        conn.request("POST", path, body=json_body, headers=headers)
        resp = conn.getresponse()
        resp_body = resp.read().decode("utf-8", errors="replace")
        conn.close()
        if resp.status >= 400:
            raise ServiceError(f"Pixazo API error ({resp.status}): {resp_body[:300]}")
        return json.loads(resp_body) if resp_body.strip() else {}

    def generate(self, prompt="", lyrics="", duration=30,
                 instrumental=False, style="", **kwargs) -> dict:
        if not prompt:
            raise ServiceError("No prompt provided")
        self.ensure_connected()

        body = {"prompt": prompt}
        if lyrics:
            body["lyrics"] = lyrics
        if duration:
            body["duration"] = int(duration)
        if instrumental:
            body["instrumental"] = True
        if style:
            body["style"] = style
        # Pass through extra kwargs (model-specific params)
        for k, v in kwargs.items():
            if k not in body and k not in ("destination", "path", "_service"):
                body[k] = v

        logger.info("[PIXAZO-AUDIO] Generating: prompt=%s..., model=%s, endpoint=%s, duration=%ds",
                    prompt[:80], self.config.get("model", "minimax-music"),
                    self._gen_endpoint, duration)

        # Submit generation
        result = self._gateway_post(self._gen_endpoint, body)

        # Extract request ID
        req_id = ""
        for field in (self._id_field, "request_id", "requestId", "id"):
            req_id = result.get(field, "")
            if req_id:
                break
        if not req_id:
            raise ServiceError(f"No request ID in Pixazo response: {json.dumps(result)[:300]}")

        # Poll for completion
        start = time.time()
        while True:
            time.sleep(self.poll_interval)
            poll_body = {self._poll_id_field: req_id}
            if self._poll_id_field != "request_id":
                poll_body["request_id"] = req_id  # some models need both
            status = self._gateway_post(self._poll_endpoint, poll_body)
            state = (status.get("status") or "").lower()
            elapsed = int(time.time() - start)
            logger.info("[PIXAZO-AUDIO] Poll %s (%ds): status=%s",
                        req_id[:16], elapsed, state)

            if state in ("completed", "done", "success", "ready"):
                audio_url = (status.get("audio_url", "")
                             or status.get("output", "")
                             or status.get("url", ""))
                if isinstance(audio_url, list):
                    audio_url = audio_url[0] if audio_url else ""
                if not audio_url:
                    raise ServiceError(f"No audio URL in completed response: "
                                       f"{json.dumps(status)[:300]}")
                return self._download_audio(audio_url)

            if state in ("failed", "error"):
                raise ServiceError(f"Pixazo audio generation failed: "
                                   f"{status.get('message', state)}")

    def _download_audio(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": "PawFlow-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            audio_bytes = resp.read()
            content_type = resp.headers.get("Content-Type", "audio/mpeg")
        return {"audio_bytes": audio_bytes, "content_type": content_type}


ServiceFactory.register(PixazoAudioService)
