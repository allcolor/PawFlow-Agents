"""Suno music generation service — via sunoapi.org wrapper.

Implements BaseAudioGenerationService for the Suno API.
Generates 2 songs per request, returns the first one.
"""

import json
import logging
import time
import http.client
import ssl
import urllib.request
from urllib.parse import quote, urlparse

from core import ServiceFactory, ServiceError
from services.base_audio_generation import BaseAudioGenerationService

logger = logging.getLogger(__name__)

_API_HOST = "api.sunoapi.org"

SUNO_MODELS = ["V5_5", "V5", "V4_5PLUS", "V4_5ALL", "V4_5", "V4"]
_FAILURE_STATES = {
    "callback_exception",
    "create_task_failed",
    "error",
    "failed",
    "generate_audio_failed",
    "sensitive_word_error",
}


class SunoAudioService(BaseAudioGenerationService):
    TYPE = "sunoAudioGeneration"
    VERSION = "1.0.0"
    NAME = "Suno Music Generation"
    DESCRIPTION = "Generate music via Suno API (vocals, instrumental, multiple genres)"

    def get_parameter_schema(self) -> dict:
        return {
            "api_key": {
                "type": "string", "required": True, "sensitive": True,
                "description": "Suno API key (from sunoapi.org)",
            },
            "model": {
                "type": "select", "required": False,
                "default": "V4_5ALL",
                "options": SUNO_MODELS,
                "description": "Suno model version",
            },
            "poll_interval": {
                "type": "integer", "required": False, "default": 10,
                "description": "Seconds between status polls",
            },
            "callback_url": {
                "type": "string", "required": False, "default": "",
                "description": (
                    "Absolute callback URL required by Suno. When omitted, "
                    "PawFlow derives one from the runtime file_base_url."
                ),
            },
        }

    def __init__(self, config):
        super().__init__(config)
        self.api_key = self.config.get("api_key", "")
        self.model = self.config.get("model", "V4_5ALL")
        self.poll_interval = int(self.config.get("poll_interval", 10))
        self.callback_url = (self.config.get("callback_url", "") or "").strip()
        self._callback_base_url = ""

    def set_callback_base_url(self, base_url: str):
        self._callback_base_url = (base_url or "").rstrip("/")

    def _create_connection(self):
        if not self.api_key:
            raise ServiceError("api_key is required for Suno service")
        return {"ready": True}

    def _close_connection(self):
        pass

    def _api_request(self, method: str, path: str, body: dict = None) -> dict:
        """Request to Suno API."""
        ctx = ssl.create_default_context()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        conn = http.client.HTTPSConnection(_API_HOST, timeout=60, context=ctx)
        json_body = json.dumps(body).encode("utf-8") if body else None
        if json_body:
            headers["Content-Length"] = str(len(json_body))
        conn.request(method, path, body=json_body, headers=headers)
        resp = conn.getresponse()
        resp_body = resp.read().decode("utf-8", errors="replace")
        conn.close()
        if resp.status >= 400:
            raise ServiceError(f"Suno API error ({resp.status}): {resp_body[:300]}")
        return json.loads(resp_body) if resp_body.strip() else {}

    def _callback_url(self, kwargs: dict) -> str:
        url = (
            kwargs.get("callBackUrl")
            or kwargs.get("callback_url")
            or self.callback_url
        )
        url = (url or "").strip()
        if not url and self._callback_base_url:
            url = f"{self._callback_base_url}/webhooks/suno/callback"

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ServiceError(
                "Suno callback_url is required; configure callback_url on "
                "the sunoAudioGeneration service or provide a file_base_url "
                "so PawFlow can derive callBackUrl."
            )
        return url

    def generate(self, prompt="", lyrics="", duration=None,
                 instrumental=False, style="", title="", **kwargs) -> dict:
        if not prompt and not lyrics:
            raise ServiceError("No prompt or lyrics provided")
        self.ensure_connected()

        # Custom mode if lyrics or style provided
        custom_mode = bool(lyrics or style)
        body = {
            "model": self.model,
            "customMode": custom_mode,
            "instrumental": bool(instrumental),
            "callBackUrl": self._callback_url(kwargs),
        }

        if custom_mode:
            body["prompt"] = lyrics or prompt
            if style:
                body["style"] = style
            body["title"] = title or "Generated"
        else:
            body["prompt"] = prompt

        # Pass through extra kwargs (negativeTags, vocalGender, etc.)
        for k, v in kwargs.items():
            if k not in body and k not in (
                "destination", "path", "_service", "duration", "callback_url"
            ):
                body[k] = v

        logger.info("[SUNO] Generating: prompt=%s..., model=%s, instrumental=%s, style=%s",
                    (prompt or lyrics)[:80], self.model, instrumental, style or "auto")

        # Submit
        result = self._api_request("POST", "/api/v1/generate", body) or {}
        if not isinstance(result, dict):
            raise ServiceError(f"Unexpected Suno response: {json.dumps(result)[:300]}")
        if result.get("code") != 200:
            raise ServiceError(f"Suno generation failed: {result.get('msg', 'unknown error')}")

        submit_data = result.get("data") or {}
        if not isinstance(submit_data, dict):
            submit_data = {}
        task_id = submit_data.get("taskId", "")
        if not task_id:
            raise ServiceError(f"No taskId in response: {json.dumps(result)[:300]}")

        # Poll for completion (~30-120s)
        start = time.time()
        while True:
            time.sleep(self.poll_interval)
            status = self._api_request(
                "GET",
                f"/api/v1/generate/record-info?taskId={quote(task_id, safe='')}",
            )
            elapsed = int(time.time() - start)

            if not isinstance(status, dict):
                status = {}
            data = status.get("data") or {}
            if not isinstance(data, dict):
                data = {}
            response = data.get("response") or {}
            if not isinstance(response, dict):
                response = {}
            suno_data = response.get("sunoData") or []

            # Suno generates 2 songs per request — return the first,
            # include info about the second in metadata
            ready = [s for s in suno_data if s.get("audioUrl")]
            if ready:
                logger.info("[SUNO] Complete (%ds): %d variation(s)",
                            elapsed, len(ready))
                # Download ALL variations
                variations = []
                for s in ready:
                    dl = self._download_audio(s["audioUrl"])
                    dl["title"] = s.get("title", "")
                    dl["duration"] = s.get("duration", 0)
                    dl["tags"] = s.get("tags", "")
                    variations.append(dl)
                # Primary result = first variation
                result = variations[0]
                result["variations"] = variations
                return result

            # Check for errors
            state = data.get("status", "").lower()
            if state in _FAILURE_STATES:
                raise ServiceError(f"Suno generation failed: {data.get('errorMessage', state)}")

            logger.info("[SUNO] Poll %s (%ds): waiting...", task_id[:16], elapsed)

    def _download_audio(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": "PawFlow-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            audio_bytes = resp.read()
            content_type = resp.headers.get("Content-Type", "audio/mpeg")
        return {"audio_bytes": audio_bytes, "content_type": content_type}


ServiceFactory.register(SunoAudioService)
