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

REALTIME_PROTOCOLS = ("openai_realtime", "gemini_live")

# Which llmConnection provider each protocol takes its credentials from.
_PROTOCOL_PROVIDERS = {"openai_realtime": "openai", "gemini_live": "gemini"}


class RealtimeVoiceConnectionService(BaseService):
    """Provider-backed realtime voice sessions for PawFlow agents."""

    TYPE = "realtimeVoiceConnection"
    CATEGORY = "voice"

    def __init__(self, config):
        super().__init__(config)
        # engine: 'legacy' = shipped custom bridge; 'livekit' = LiveKit
        # engine (docs/REALTIME_MULTIMODAL_LIVEKIT_PLAN.md). Legacy configs
        # have no engine key and keep the custom bridge until the P3 route
        # switch; their LiveKit mapping is produced on demand by
        # `livekit_config()` (compatibility loader).
        self.engine = (self.config.get("engine", "legacy")
                       or "legacy").strip().lower()
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
        self.context_mode = (self.config.get("context_mode", "summary:2000")
                             or "isolated").strip().lower()
        self._runtime_user_id = ""
        self._runtime_conversation_id = ""

    def set_runtime_context(self, user_id: str = "", conversation_id: str = "",
                            agent_name: str = "", **_: object):
        self._runtime_user_id = user_id or ""
        self._runtime_conversation_id = conversation_id or ""

    # -- BaseService ----------------------------------------------------

    def _create_connection(self):
        if self.engine == "livekit":
            # Full LiveKit config validation (fails clearly on missing
            # livekit_url / api key / secret / provider / model).
            self.livekit_config()
            return {"ready": True}
        if self.engine != "legacy":
            raise ServiceError(
                f"Unknown realtime engine '{self.engine}'. "
                "Supported: legacy, livekit")
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

    def livekit_config(self) -> dict:
        """Resolved LiveKit engine config (compatibility loader).

        Works for engine=livekit configs and for legacy configs whose
        provider is covered by LiveKit (protocol -> provider mapping).
        Raises ServiceError when required LiveKit settings are missing.
        """
        from services._livekit_engine import resolve_livekit_config
        return resolve_livekit_config(self.config)

    def _close_connection(self):
        pass

    def health_check(self):
        return {"protocol": self.protocol, "model": self.model, "ready": True}

    # -- credentials -----------------------------------------------------

    def _resolve_llm_service(self, user_id: str = "", conv_id: str = ""):
        from core.service_registry import ServiceRegistry
        reg = ServiceRegistry.get_instance()
        svc_def = reg.resolve_definition(
            self.llm_service, user_id=user_id, conv_id=conv_id)
        if svc_def is None:
            raise ServiceError(f"LLM service '{self.llm_service}' not found")
        if getattr(svc_def, "service_type", "") != "llmConnection":
            raise ServiceError(
                f"Service '{self.llm_service}' is not an llmConnection service")
        svc = reg.resolve(self.llm_service, user_id=user_id, conv_id=conv_id)
        if svc is None:
            raise ServiceError(
                f"LLM service '{self.llm_service}' could not connect")
        provider = getattr(svc, "provider", "")
        required = _PROTOCOL_PROVIDERS.get(self.protocol, "openai")
        if provider != required:
            raise ServiceError(
                f"protocol '{self.protocol}' requires a '{required}' "
                f"llmConnection for credentials, got "
                f"{provider or 'unknown'}")
        if not getattr(svc, "api_key", ""):
            raise ServiceError(
                f"LLM service '{self.llm_service}' has no api_key")
        return svc

    # -- sessions ---------------------------------------------------------

    def open_session(self, *, instructions: str = "", tools: list = None,
                     vad: str = "", user_id: str = None,
                     conversation_id: str = None, resume_handle: str = ""):
        """Connect a provider session and return the live adapter.

        `instructions` overrides the config-level instructions (the bridge
        passes the conversation agent's system prompt when
        `instructions_mode == 'agent'`). `vad` overrides the configured
        turn detection — turn-based callers (Telegram voice notes) force
        'manual' regardless of the live-session setting.

        `user_id`/`conversation_id` scope the llmConnection lookup. Pass
        them explicitly: registry instances are SHARED across sessions, so
        the set_runtime_context fields can be overwritten by a concurrent
        session on another conversation before this one resolves — the
        fields are only a fallback for legacy callers.
        """
        self._create_connection()  # re-validate on every session
        svc = self._resolve_llm_service(
            user_id=(self._runtime_user_id if user_id is None else user_id),
            conv_id=(self._runtime_conversation_id
                     if conversation_id is None else conversation_id),
        )
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
            vad=(vad or self.vad),
            input_format=self.input_audio_format,
            output_format=self.output_audio_format,
            resume_handle=resume_handle,
        )
        return adapter

    # -- UI schema ---------------------------------------------------------

    def get_parameter_schema(self) -> dict:
        return {
            "engine": {
                "type": "select", "required": False, "default": "legacy",
                "options": ["legacy", "livekit"],
                "description": "legacy = built-in provider bridge (current default). livekit = LiveKit engine (media via LiveKit server + sidecar worker; requires the livekit_* keys below).",
            },
            "livekit_url": {"type": "string", "required": False, "default": "",
                             "description": "livekit engine: LiveKit server URL (ws://localhost:7880 for the docker-compose dev server, or a LiveKit Cloud wss:// URL)."},
            "livekit_api_key": {"type": "string", "required": False, "default": "",
                                 "description": "livekit engine: LiveKit API key (devkey on the dev server)."},
            "livekit_api_secret": {"type": "password", "required": False, "default": "",
                                    "description": "livekit engine: LiveKit API secret (secret on the dev server)."},
            "provider": {
                "type": "select", "required": False, "default": "",
                "options": ["", "openai", "gemini", "azure_openai", "xai",
                             "aws_nova", "local_pipeline"],
                "description": "livekit engine: realtime provider plugin. Empty = derived from protocol (openai_realtime→openai, gemini_live→gemini). local_pipeline = zero-cloud-audio cascade (local VAD/STT/TTS + any llmConnection).",
            },
            "modalities": {"type": "string", "required": False, "default": "audio,text",
                            "description": "livekit engine: comma list of audio, text, video."},
            "video_input": {"type": "boolean", "required": False, "default": False,
                             "description": "livekit engine: enable camera/screen frame ingestion (also implied by 'video' in modalities)."},
            "video_source": {
                "type": "select", "required": False, "default": "camera",
                "options": ["camera", "screen", "both"],
                "description": "livekit engine: which video sources the browser may publish.",
            },
            "turn_detection": {
                "type": "select", "required": False, "default": "",
                "options": ["", "provider_default", "semantic_vad",
                             "server_vad", "manual"],
                "description": "livekit engine: replaces legacy vad (empty = derived: server→server_vad, manual→manual, else provider_default).",
            },
            "video_fps_active": {"type": "number", "required": False, "default": 1.0,
                                  "description": "livekit engine: video frames per second while the user is speaking (provider frame sampling)."},
            "video_fps_idle": {"type": "number", "required": False, "default": 0.33,
                                "description": "livekit engine: video frames per second while idle."},
            "local_stt_url": {"type": "string", "required": False, "default": "",
                               "description": "local_pipeline: OpenAI-compatible STT server URL (e.g. faster-whisper-server). Empty = worker env/default."},
            "local_stt_model": {"type": "string", "required": False, "default": "",
                                 "description": "local_pipeline: STT model id."},
            "local_tts_url": {"type": "string", "required": False, "default": "",
                               "description": "local_pipeline: OpenAI-compatible TTS server URL (e.g. kokoro-fastapi). Empty = worker env/default."},
            "local_tts_model": {"type": "string", "required": False, "default": "",
                                 "description": "local_pipeline: TTS model id. Keep tts-1 (the default) unless the local server implements the OpenAI SSE stream format — the plugin selects the wire format from the model name, and non-tts-1 names use SSE, which kokoro-fastapi/speaches do not speak."},
            "local_tts_voice": {"type": "string", "required": False, "default": "",
                                 "description": "local_pipeline: TTS voice id."},
            "recording_policy": {
                "type": "select", "required": False, "default": "transcript",
                "options": ["none", "transcript", "audio", "audio_video"],
                "description": "livekit engine: what is persisted. Default transcript only — raw audio/video recording is explicit opt-in (P8).",
            },
            "llm_service": {
                "type": "service_ref", "service_type": "llmConnection",
                "required": True,
                "description": "LLM service used for credentials/base URL: an 'openai' connection for openai_realtime, a 'gemini' connection (with api_key set) for gemini_live.",
            },
            "protocol": {
                "type": "select", "required": False, "default": "openai_realtime",
                "options": list(REALTIME_PROTOCOLS),
                "description": "Realtime wire protocol adapter: openai_realtime (also Azure OpenAI and compatibles) or gemini_live (Google Live API, supports session resumption).",
            },
            "model": {"type": "string", "required": True, "default": "gpt-realtime",
                       "description": "Realtime voice model, e.g. gpt-realtime, gpt-4o-realtime-preview, or gemini-2.5-flash-native-audio-preview-09-2025 for gemini_live."},
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
                                     "description": "openai_realtime only: model transcribing user speech (whisper-1, or gpt-4o-transcribe on newer endpoints). gemini_live ignores it — Gemini Live transcribes both sides natively."},
            "input_audio_format": {"type": "string", "required": False, "default": "pcm16",
                                    "description": "Provider-side input audio format."},
            "output_audio_format": {"type": "string", "required": False, "default": "pcm16",
                                     "description": "Provider-side output audio format."},
            "max_session_seconds": {"type": "integer", "required": False, "default": 600,
                                     "description": "Hard cap on a single voice session; the bridge closes the session when reached."},
            "tool_profile": {"type": "string", "required": False, "default": "",
                              "description": "Comma-separated PawFlow tools exposed to the voice model (e.g. 'recall,remember,web_search,read'). Approval is silent: exempt/pre-approved tools run, anything needing a dialog is refused; long tools detach to the background and announce their result. Empty = no tools."},
            "context_mode": {"type": "string", "required": False, "default": "summary:2000",
                              "description": "Conversation context given to the voice model at session start — same modes as sub-agents: 'isolated' (none), 'last:N' (last N messages), 'summary:N' (compact summary, ~N tokens), 'full'."},
        }


ServiceFactory.register(RealtimeVoiceConnectionService)
