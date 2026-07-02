"""Realtime Voice Connection Service — speech-to-speech LLM sessions.

`realtimeVoiceConnection` is an LLM-family service, but it is NOT another
provider inside `llmConnection`: `generate()` is request/response while a
realtime voice model lives inside a stateful bidirectional session. Like
the OpenAI-compatible media services, it references an existing
`llmConnection` for credentials/base URL and adds the session protocol on
top, selected by `protocol` (multi-provider seam — see
`services/_realtime_adapters.py` and `docs/REALTIME_VOICE_PLAN.md`).

The service itself is thin: it validates config and opens provider
sessions (`open_session`). Everything session-scoped (browser leg, audio
pumps, transcripts, caps) lives in `services/_realtime_bridge.py`.
"""

import logging

from core import ServiceError, ServiceFactory
from core.base_service import BaseService

from services._realtime_adapters import build_adapter

logger = logging.getLogger(__name__)

REALTIME_PROTOCOLS = ("openai_realtime",)


class RealtimeVoiceConnectionService(BaseService):
    """Provider-backed realtime voice sessions for PawFlow agents."""

    TYPE = "realtimeVoiceConnection"
    CATEGORY = "voice"

    def __init__(self, config):
        super().__init__(config)
        self.llm_service = (self.config.get("llm_service", "") or "").strip()
        self.protocol = (self.config.get("protocol", "openai_realtime")
                         or "openai_realtime").strip().lower()
        self.model = (self.config.get("model", "") or "").strip()
        self.voice = (self.config.get("voice", "alloy") or "alloy").strip()
        self.instructions_mode = (self.config.get("instructions_mode", "agent")
                                  or "agent").strip().lower()
        self.instructions = (self.config.get("instructions", "") or "").strip()
        self.input_audio_format = (self.config.get("input_audio_format", "pcm16")
                                   or "pcm16").strip()
        self.output_audio_format = (self.config.get("output_audio_format", "pcm16")
                                    or "pcm16").strip()
        self.vad = (self.config.get("vad", "server") or "server").strip().lower()
        self.transcription_model = (self.config.get("transcription_model",
                                                    "whisper-1")
                                    or "whisper-1").strip()
        try:
            self.max_session_seconds = int(
                self.config.get("max_session_seconds", 600) or 600)
        except (TypeError, ValueError):
            self.max_session_seconds = 600
        self.tool_profile = (self.config.get("tool_profile", "") or "").strip()
        self._runtime_user_id = ""
        self._runtime_conversation_id = ""

    def set_runtime_context(self, user_id: str = "", conversation_id: str = "",
                            agent_name: str = "", **_: object):
        self._runtime_user_id = user_id or ""
        self._runtime_conversation_id = conversation_id or ""

    # -- BaseService ----------------------------------------------------

    def _create_connection(self):
        if not self.llm_service:
            raise ServiceError(
                "llm_service is required for realtimeVoiceConnection")
        if not self.model:
            raise ServiceError("model is required for realtimeVoiceConnection")
        if self.protocol not in REALTIME_PROTOCOLS:
            raise ServiceError(
                f"Unknown realtime protocol '{self.protocol}'. "
                f"Supported: {', '.join(REALTIME_PROTOCOLS)}")
        return {"ready": True}

    def _close_connection(self):
        pass

    def health_check(self):
        return {"protocol": self.protocol, "model": self.model, "ready": True}

    # -- credentials -----------------------------------------------------

    def _resolve_llm_service(self):
        from core.service_registry import ServiceRegistry
        reg = ServiceRegistry.get_instance()
        svc_def = reg.resolve_definition(
            self.llm_service,
            user_id=self._runtime_user_id,
            conv_id=self._runtime_conversation_id,
        )
        if svc_def is None:
            raise ServiceError(f"LLM service '{self.llm_service}' not found")
        if getattr(svc_def, "service_type", "") != "llmConnection":
            raise ServiceError(
                f"Service '{self.llm_service}' is not an llmConnection service")
        svc = reg.resolve(
            self.llm_service,
            user_id=self._runtime_user_id,
            conv_id=self._runtime_conversation_id,
        )
        if svc is None:
            raise ServiceError(
                f"LLM service '{self.llm_service}' could not connect")
        provider = getattr(svc, "provider", "")
        if provider != "openai":
            raise ServiceError(
                "realtimeVoiceConnection requires an openai llmConnection "
                f"for credentials, got {provider or 'unknown'}")
        if not getattr(svc, "api_key", ""):
            raise ServiceError(
                f"LLM service '{self.llm_service}' has no api_key")
        return svc

    # -- sessions ---------------------------------------------------------

    def open_session(self, *, instructions: str = "", tools: list = None):
        """Connect a provider session and return the live adapter.

        `instructions` overrides the config-level instructions (the bridge
        passes the conversation agent's system prompt when
        `instructions_mode == 'agent'`).
        """
        self._create_connection()  # re-validate on every session
        svc = self._resolve_llm_service()
        adapter = build_adapter(
            self.protocol,
            base_url=getattr(svc, "base_url", "") or "",
            api_key=getattr(svc, "api_key", "") or "",
            transcription_model=self.transcription_model,
        )
        adapter.connect(
            model=self.model,
            voice=self.voice,
            instructions=instructions or self.instructions,
            tools=tools or [],
            vad=self.vad,
            input_format=self.input_audio_format,
            output_format=self.output_audio_format,
        )
        return adapter

    # -- UI schema ---------------------------------------------------------

    def get_parameter_schema(self) -> dict:
        return {
            "llm_service": {
                "type": "service_ref", "service_type": "llmConnection",
                "provider": "openai", "required": True,
                "description": "OpenAI/API-compatible LLM service used for credentials and base URL.",
            },
            "protocol": {
                "type": "select", "required": False, "default": "openai_realtime",
                "options": list(REALTIME_PROTOCOLS),
                "description": "Realtime wire protocol adapter (openai_realtime also covers Azure OpenAI and compatible endpoints).",
            },
            "model": {"type": "string", "required": True, "default": "gpt-realtime",
                       "description": "Realtime voice model, e.g. gpt-realtime or gpt-4o-realtime-preview."},
            "voice": {"type": "string", "required": False, "default": "alloy",
                       "description": "Provider voice id."},
            "instructions_mode": {
                "type": "select", "required": False, "default": "agent",
                "options": ["agent", "custom"],
                "description": "agent = use the conversation agent's system prompt; custom = the instructions field below.",
            },
            "instructions": {"type": "text", "required": False, "default": "",
                              "description": "Session instructions when instructions_mode=custom."},
            "vad": {
                "type": "select", "required": False, "default": "server",
                "options": ["server", "manual"],
                "description": "server = provider voice-activity detection; manual = client push-to-talk commits.",
            },
            "transcription_model": {"type": "string", "required": False, "default": "whisper-1",
                                     "description": "Provider model used to transcribe user speech."},
            "input_audio_format": {"type": "string", "required": False, "default": "pcm16",
                                    "description": "Provider-side input audio format."},
            "output_audio_format": {"type": "string", "required": False, "default": "pcm16",
                                     "description": "Provider-side output audio format."},
            "max_session_seconds": {"type": "integer", "required": False, "default": 600,
                                     "description": "Hard cap on a single voice session; the bridge closes the session when reached."},
            "tool_profile": {"type": "string", "required": False, "default": "",
                              "description": "Reserved (P2): comma-separated PawFlow tools exposed to the voice model."},
        }


ServiceFactory.register(RealtimeVoiceConnectionService)
