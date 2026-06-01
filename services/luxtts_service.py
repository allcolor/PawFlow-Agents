"""LuxTTS local zero-shot TTS and voice-cloning service."""

import io
import logging
import os
import tempfile
import urllib.request
from typing import Any

from core import ServiceFactory, ServiceError, safe_float
from services.base_audio_generation import BaseAudioGenerationService
from services.base_voice_clone import BaseVoiceCloneService

logger = logging.getLogger(__name__)


class LuxTTSService(BaseAudioGenerationService, BaseVoiceCloneService):
    TYPE = "luxTTS"
    VERSION = "1.0.0"
    NAME = "LuxTTS Local Voice Clone"
    CATEGORY = "audio"
    SUPPORTS_NATIVE_TTS_VOICES = False
    ACCEPTS_FILESTORE_URLS = False

    def get_parameter_schema(self) -> dict:
        return {
            "model_id": {
                "type": "string", "required": False, "default": "YatharthS/LuxTTS",
                "description": "Hugging Face model id or local LuxTTS model path.",
            },
            "device": {
                "type": "select", "required": False, "default": "cpu",
                "options": ["cpu", "cuda", "mps"],
                "description": "Inference device.",
            },
            "threads": {
                "type": "integer", "required": False, "default": 2,
                "description": "CPU threads when loading on CPU.",
            },
            "prompt_audio": {
                "type": "string", "required": False, "default": "",
                "description": "Default reference audio path for plain speak()/generate().",
            },
            "rms": {"type": "number", "required": False, "default": 0.01},
            "ref_duration": {"type": "number", "required": False, "default": 5},
            "num_steps": {"type": "integer", "required": False, "default": 4},
            "t_shift": {"type": "number", "required": False, "default": 0.9},
            "speed": {"type": "number", "required": False, "default": 1.0},
            "return_smooth": {"type": "boolean", "required": False, "default": False},
            "timeout": {"type": "integer", "required": False, "default": 120},
        }

    def __init__(self, config):
        super().__init__(config)
        self.model_id = str(self.config.get("model_id") or "YatharthS/LuxTTS")
        self.device = str(self.config.get("device") or "cpu")
        self.threads = int(self.config.get("threads") or 2)
        self.prompt_audio = str(self.config.get("prompt_audio") or "")
        self.rms = safe_float(self.config.get("rms"), 0.01)
        self.ref_duration = safe_float(self.config.get("ref_duration"), 5)
        self.num_steps = int(self.config.get("num_steps") or 4)
        self.t_shift = safe_float(self.config.get("t_shift"), 0.9)
        self.speed = safe_float(self.config.get("speed"), 1.0)
        self.return_smooth = str(self.config.get("return_smooth", False)).lower() in {"1", "true", "yes"}
        self.timeout = int(self.config.get("timeout") or 120)
        self._model = None

    def _create_connection(self):
        return {"lazy": True, "model_id": self.model_id, "device": self.device}

    def _close_connection(self):
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return self._model
        try:
            from zipvoice.luxvoice import LuxTTS  # type: ignore
        except Exception as exc:
            raise ServiceError(
                "LuxTTS is not installed. Install ysharma3501/LuxTTS requirements first.") from exc
        kwargs: dict[str, Any] = {"device": self.device}
        if self.device == "cpu":
            kwargs["threads"] = self.threads
        self._model = LuxTTS(self.model_id, **kwargs)
        return self._model

    def _reference_path(self, reference_audio_url: str = "",
                        reference_audio_bytes: bytes = None):
        if reference_audio_bytes:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
            tmp.write(reference_audio_bytes)
            tmp.close()
            return tmp.name, True
        if reference_audio_url:
            if reference_audio_url.startswith("http://") or reference_audio_url.startswith("https://"):
                with urllib.request.urlopen(reference_audio_url, timeout=self.timeout) as resp:  # nosec B310 - configured reference URL.
                    data = resp.read()
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
                tmp.write(data)
                tmp.close()
                return tmp.name, True
            return reference_audio_url, False
        if self.prompt_audio:
            return self.prompt_audio, False
        raise ServiceError("LuxTTS requires prompt_audio or reference_audio")

    def clone_speak(self, text: str = "", reference_audio_url: str = "",
                    reference_text: str = "", language: str = "",
                    reference_audio_bytes: bytes = None, rms: float = 0,
                    ref_duration: float = 0, num_steps: int = 0,
                    t_shift: float = 0, speed: float = 0,
                    return_smooth: bool = None, **kwargs) -> dict:
        if not text:
            raise ServiceError("text is required")
        self.ensure_connected()
        model = self._load_model()
        ref_path, delete_ref = self._reference_path(reference_audio_url, reference_audio_bytes)
        try:
            encoded = model.encode_prompt(
                ref_path,
                duration=safe_float(ref_duration, self.ref_duration),
                rms=safe_float(rms, self.rms),
            )
            wav = model.generate_speech(
                text,
                encoded,
                num_steps=int(num_steps or self.num_steps),
                t_shift=safe_float(t_shift, self.t_shift),
                speed=safe_float(speed, self.speed),
                return_smooth=self.return_smooth if return_smooth is None else bool(return_smooth),
            )
            try:
                arr = wav.detach().cpu().numpy().squeeze()
            except AttributeError:
                arr = wav.numpy().squeeze() if hasattr(wav, "numpy") else wav
            import soundfile as sf  # type: ignore
            out = io.BytesIO()
            sf.write(out, arr, 48000, format="WAV")
            audio = out.getvalue()
        finally:
            if delete_ref:
                try:
                    os.unlink(ref_path)
                except OSError:
                    pass
        if not audio:
            raise ServiceError("LuxTTS returned empty audio")
        logger.info("[LUXTTS] tts ok: %d bytes", len(audio))
        return {"audio_bytes": audio, "content_type": "audio/wav", "source_url": ""}

    def speak(self, text: str, voice: str = "", language: str = "",
              **kwargs) -> dict:
        reference = kwargs.pop("reference_audio_url", "") or voice or self.prompt_audio
        return self.clone_speak(text=text, reference_audio_url=reference, language=language, **kwargs)

    def generate(self, prompt: str = "", text: str = "", **kwargs) -> dict:
        return self.speak(text=text or prompt, **kwargs)


ServiceFactory.register(LuxTTSService)

