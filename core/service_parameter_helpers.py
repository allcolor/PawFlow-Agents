"""Service parameter fill helpers.

Helpers are metadata-driven suggestions used by the service configuration UI.
They never persist values by themselves; the UI fills the selected field and the
normal install/update path stores the resulting config.
"""

from __future__ import annotations

import copy
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.relay_proxy_url import CONV_RELAY_EXPR


def _conv_relay_url(path: str) -> str:
    return f"relay://{CONV_RELAY_EXPR}/{path.lstrip('/')}"


OPENAI_BASE_URLS = [
    ("OpenAI", "https://api.openai.com/v1", "Native OpenAI API."),
    ("Ollama cloud", "https://ollama.com/v1", "Ollama-hosted cloud models; free tier available. Create an API key at https://ollama.com/settings/keys."),
    ("OpenRouter", "https://openrouter.ai/api/v1", "OpenAI-compatible gateway; model helper can use the public catalog."),
    ("DeepSeek", "https://api.deepseek.com", "OpenAI-compatible DeepSeek endpoint."),
    ("xAI", "https://api.x.ai/v1", "OpenAI-compatible xAI endpoint."),
    ("DashScope Beijing", "https://dashscope.aliyuncs.com/compatible-mode/v1", "Qwen OpenAI-compatible endpoint in Beijing."),
    ("DashScope Singapore", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "Qwen OpenAI-compatible endpoint in Singapore."),
    ("DashScope Virginia", "https://dashscope-us.aliyuncs.com/compatible-mode/v1", "Qwen OpenAI-compatible endpoint in Virginia."),
    ("Ollama local relay", _conv_relay_url("localhost:11434/v1"), "Relay-routed local Ollama server."),
    ("Local relay", _conv_relay_url("localhost:1234/v1"), "Relay-routed local OpenAI-compatible server."),
]

ANTHROPIC_BASE_URLS = [
    ("Anthropic", "https://api.anthropic.com", "Native Anthropic Messages API."),
    ("DeepSeek Anthropic", "https://api.deepseek.com/anthropic", "DeepSeek Anthropic-compatible endpoint."),
]

LOCAL_BASE_URLS = [
    ("Relay localhost 8000", _conv_relay_url("localhost:8000"), "Relay-routed local HTTP endpoint."),
    ("Relay localhost 7788", _conv_relay_url("localhost:7788"), "Common Supertonic local endpoint."),
    ("Relay localhost 17493", _conv_relay_url("localhost:17493"), "Common Voicebox local endpoint."),
]

LLM_MODELS = {
    "openai": [
        ("gpt-5.5", "OpenAI flagship default in PawFlow."),
        ("gpt-5", "OpenAI general-purpose model."),
        ("gpt-5-mini", "Lower-cost OpenAI model."),
        ("gpt-4.1", "OpenAI GPT-4.1 family."),
        ("gpt-4.1-mini", "Smaller GPT-4.1 model."),
    ],
    "anthropic": [
        ("claude-opus-4-7", "Anthropic high-capability Claude model."),
        ("claude-sonnet-4-6", "Anthropic balanced Claude model."),
        ("claude-haiku-4-5", "Anthropic fast Claude model."),
    ],
    "openrouter": [
        ("openai/gpt-5.5", "OpenAI through OpenRouter."),
        ("anthropic/claude-opus-4.7", "Claude through OpenRouter."),
        ("google/gemini-3.1-pro", "Gemini through OpenRouter."),
        ("deepseek/deepseek-v4-pro", "DeepSeek through OpenRouter."),
    ],
    "deepseek": [
        ("deepseek-v4-pro", "DeepSeek reasoning/chat model."),
        ("deepseek-v4-flash", "Lower-latency DeepSeek model."),
        ("deepseek-chat", "Compatibility alias."),
        ("deepseek-reasoner", "Compatibility reasoning alias."),
    ],
    "xai": [
        ("grok-4", "xAI text model."),
        ("grok-4-fast", "xAI faster text model."),
        ("grok-3-mini", "xAI small model."),
    ],
    "dashscope": [
        ("qwen3.7-max", "Qwen flagship model."),
        ("qwen3.7-plus", "Qwen balanced model."),
        ("qwen3.6-flash", "Qwen fast model."),
        ("deepseek-v4-pro", "Third-party model via DashScope."),
        ("kimi-k2.6", "Third-party model via DashScope."),
    ],
    "ollama": [
        ("gpt-oss:120b", "Open-weight OpenAI model; light usage level, good free-tier default."),
        ("gpt-oss:20b", "Small open-weight OpenAI model; cheapest on free-tier usage."),
        ("qwen3-coder:480b", "Large Qwen coding model."),
        ("deepseek-v4-flash", "Lower-latency DeepSeek model."),
        ("deepseek-v4-pro", "DeepSeek reasoning model; heavy usage level."),
        ("kimi-k2.6", "Moonshot Kimi general model."),
        ("glm-5", "Zhipu GLM general model."),
    ],
}

EMBEDDING_MODELS = {
    "openai": [
        ("text-embedding-3-small", "OpenAI small embedding model."),
        ("text-embedding-3-large", "OpenAI large embedding model."),
    ],
    "openrouter": [],
    "deepseek": [],
    "dashscope": [
        ("text-embedding-v4", "DashScope embedding model."),
        ("text-embedding-v3", "DashScope embedding model."),
    ],
}

STATIC_MODELS = {
    "openaiCompatibleSTT": [
        ("openai/whisper-1", "OpenRouter Whisper STT model."),
        ("openai/whisper-large-v3", "OpenRouter Whisper STT model."),
        ("gpt-4o-transcribe", "OpenAI transcription model."),
        ("gpt-4o-mini-transcribe", "Lower-cost OpenAI transcription model."),
        ("whisper-1", "OpenAI Whisper endpoint model."),
        ("whisper-large-v3-turbo", "Common OpenAI-compatible Whisper model."),
    ],
    "openaiCompatibleTTS": [
        ("gpt-4o-mini-tts", "OpenAI TTS model."),
        ("tts-1", "OpenAI TTS model."),
        ("tts-1-hd", "OpenAI high-definition TTS model."),
        ("openai/gpt-4o-mini-tts-2025-12-15", "OpenRouter OpenAI TTS model."),
    ],
    "openaiImageGeneration": [
        ("gpt-image-1", "OpenAI image generation model."),
        ("dall-e-3", "DALL-E 3 image model."),
        ("dall-e-2", "DALL-E 2 image model."),
    ],
    "openaiCompatibleImageGeneration": [
        ("gpt-image-1", "OpenAI images API."),
        ("dall-e-3", "OpenAI images API."),
        ("google/gemini-2.5-flash-image", "OpenRouter image model."),
        ("black-forest-labs/flux-1.1-pro", "OpenRouter image model."),
    ],
    "openaiCompatibleVideoGeneration": [
        ("sora-2", "OpenAI-compatible video model."),
        ("sora-2-pro", "OpenAI-compatible video model."),
        ("google/veo-3", "OpenRouter video model."),
        ("minimax/hailuo-02", "OpenRouter video model."),
    ],
    "realtimeVoiceConnection": [
        ("gpt-realtime", "OpenAI realtime speech-to-speech model."),
        ("gpt-realtime-mini", "Smaller/cheaper OpenAI realtime model."),
        ("gpt-4o-realtime-preview", "Earlier OpenAI realtime preview model."),
    ],
    "grokImageGeneration": [
        ("grok-imagine-image-quality", "xAI image generation and editing model."),
        ("grok-imagine-image-quality-latest", "Latest xAI image generation model alias."),
        ("grok-imagine-image", "Legacy xAI image generation model."),
    ],
    "grokVideoGeneration": [("grok-imagine-video", "xAI video generation and editing model.")],
    "xaiTTS": [("grok-voice-latest", "xAI voice model."), ("grok-voice-fast-1.0", "xAI fast voice model.")],
    "xaiSTT": [("grok-transcribe", "xAI speech-to-text model.")],
    "klingVideoGeneration": [
        ("kling-v2.6-pro", "Kling Pro video model."),
        ("kling-v2.6-std", "Kling standard video model."),
        ("kling-v2.5-turbo", "Kling turbo video model."),
    ],
    "sunoAudioGeneration": [
        ("V5_5", "Suno V5.5."),
        ("V5", "Suno V5."),
        ("V4_5PLUS", "Suno V4.5 Plus."),
        ("V4_5", "Suno V4.5."),
    ],
    "elevenLabsVoiceClone": [
        ("eleven_multilingual_v2", "ElevenLabs multilingual model."),
        ("eleven_turbo_v2_5", "ElevenLabs low-latency model."),
        ("eleven_flash_v2_5", "ElevenLabs flash model."),
    ],
    "fishAudioVoiceClone": [("speech-1.6", "Fish Audio TTS model."), ("s1", "Fish Audio S1 model.")],
    "pocketTTS": [("kyutai/pocket-tts", "Default Kyutai Pocket TTS model.")],
    "luxTTS": [("hnhx/lux-tts", "Default LuxTTS Hugging Face model.")],
    "voxcpmTTS": [("openbmb/VoxCPM2", "Default VoxCPM model."), ("openbmb/VoxCPM", "Legacy VoxCPM model.")],
    "voicebox": [("large-v3-turbo", "Whisper fast STT model."), ("large-v3", "Whisper large STT model."), ("small", "Whisper small STT model.")],
}

QUALITY_VALUES = [("auto", "Provider default."), ("low", "Fast/lower cost."), ("medium", "Balanced."), ("high", "Higher quality."), ("hd", "Legacy DALL-E high quality.")]
STYLE_VALUES = [("vivid", "More vivid OpenAI image style."), ("natural", "More natural OpenAI image style.")]
IMAGE_RESPONSE_FORMATS = [("url", "Return hosted URL when provider supports it."), ("b64_json", "Return base64 JSON payload.")]
AUDIO_RESPONSE_FORMATS = [("json", "JSON metadata response."), ("text", "Plain text."), ("srt", "SRT captions."), ("verbose_json", "Verbose JSON."), ("vtt", "VTT captions.")]
LANGUAGE_VALUES = [("en", "English."), ("fr", "French."), ("es", "Spanish."), ("de", "German."), ("ja", "Japanese."), ("ko", "Korean."), ("zh", "Chinese."), ("na", "Language-agnostic fallback.")]
VOICE_VALUES = [("M1", "Supertonic male voice."), ("M2", "Supertonic male voice."), ("F1", "Supertonic female voice."), ("F2", "Supertonic female voice."), ("ff_siwis", "Voicebox preset."), ("Ryan", "Voicebox preset.")]
CALLBACK_VALUES = [
    ("${agent.file_base_url}", "Use the agent runtime public file base URL when available."),
    ("https://webchat.example.org", "Public PawFlow webchat origin; replace with your deployment."),
]
PATH_VALUES = [
    ("/workspace/certs/server.crt", "Certificate path inside the relay container."),
    ("/workspace/certs/server.key", "Private key path inside the relay container."),
    ("/workspace/runtime/service", "Managed runtime directory inside the relay container."),
    ("/home/user/.ssh/id_ed25519", "Common host SSH key path when local relay access is enabled."),
]
REPO_VALUES = [
    ("https://github.com/PawFlow-AI/voicebox.git", "Voicebox-compatible checkout URL template."),
    ("https://github.com/PawFlow-AI/supertonic.git", "Supertonic-compatible checkout URL template."),
]
PACKAGE_VALUES = [("supertonic-tts", "Install Supertonic from pip."), (".", "Install the checked-out repository.")]
RCLONE_PROVIDERS = [("AWS", "Amazon S3."), ("Cloudflare", "Cloudflare R2."), ("Minio", "MinIO/S3-compatible."), ("Other", "Generic S3-compatible backend.")]
RCLONE_ENDPOINTS = [("https://s3.amazonaws.com", "AWS global endpoint."), ("https://<accountid>.r2.cloudflarestorage.com", "Cloudflare R2 endpoint template."), (_conv_relay_url("localhost:9000"), "Relay-routed MinIO endpoint.")]
RCLONE_REGIONS = [("auto", "Provider chooses region."), ("us-east-1", "AWS US East 1."), ("eu-west-1", "AWS EU West 1."), ("wnam", "Cloudflare R2 Western North America."), ("weur", "Cloudflare R2 Western Europe.")]
OAUTH_SCOPES = [
    ("openid email profile", "Generic OpenID Connect profile scopes."),
    ("read:user user:email", "GitHub user email scopes."),
    ("https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/userinfo.profile", "Google profile scopes."),
    ("offline_access Files.ReadWrite.All", "Microsoft Graph file access."),
]
OAUTH_URLS = {
    "authorize_url": [
        ("https://accounts.google.com/o/oauth2/v2/auth", "Google OAuth authorize endpoint."),
        ("https://github.com/login/oauth/authorize", "GitHub OAuth authorize endpoint."),
        ("https://login.microsoftonline.com/common/oauth2/v2.0/authorize", "Microsoft OAuth authorize endpoint."),
    ],
    "token_url": [
        ("https://oauth2.googleapis.com/token", "Google OAuth token endpoint."),
        ("https://github.com/login/oauth/access_token", "GitHub OAuth token endpoint."),
        ("https://login.microsoftonline.com/common/oauth2/v2.0/token", "Microsoft OAuth token endpoint."),
    ],
    "userinfo_url": [
        ("https://openidconnect.googleapis.com/v1/userinfo", "Google OIDC userinfo endpoint."),
        ("https://api.github.com/user", "GitHub user endpoint."),
        ("https://graph.microsoft.com/oidc/userinfo", "Microsoft OIDC userinfo endpoint."),
    ],
}
AUTH_GATEWAY_PROVIDERS_TEMPLATE = {
    "google": {
        "client_id": "${auth.google.client_id}",
        "client_secret": "${auth.google.client_secret}",  # nosec B105 - expression-language secret reference placeholder, not a secret value.
        "redirect_uri": "https://webchat.example.org/auth/google/callback",
        "scope": "openid email profile",
    },
    "github": {
        "client_id": "${auth.github.client_id}",
        "client_secret": "${auth.github.client_secret}",  # nosec B105 - expression-language secret reference placeholder, not a secret value.
        "redirect_uri": "https://webchat.example.org/auth/github/callback",
        "scope": "read:user user:email",
    },
}

LLM_CONTEXT_HINTS = [("128000", "Common large context window."), ("200000", "Common Claude/Gemini context window."), ("1048576", "Million-token model window."), ("0", "Use model/provider default.")]
TOKEN_MULTIPLIERS = [("1.0", "OpenAI-compatible default."), ("1.1", "Typical Claude Sonnet/Haiku correction."), ("1.6", "Typical Claude Opus correction."), ("0", "Use PawFlow default.")]
PRICING_HINTS = [("0", "No local cost tracking."), ("0.15", "Low-cost input token price example."), ("1.25", "Mid-range input token price example."), ("10", "High-end input token price example.")]


def _values(items: List[Tuple[str, ...]], *, labels: Optional[Dict[str, str]] = None) -> List[Dict[str, str]]:
    out = []
    for item in items:
        if len(item) == 3:
            label, value, description = item
        else:
            value, description = item
            label = (labels or {}).get(value, value)
        out.append({"value": value, "label": (labels or {}).get(value, label), "description": description})
    return out


def _json_value(value: Any, label: str, description: str) -> Dict[str, Any]:
    return {"value": value, "label": label, "description": description}


def _provider_family(config: Dict[str, Any]) -> str:
    provider = str(config.get("provider") or "").lower()
    base_url = str(config.get("base_url") or "").lower()
    if "ollama" in base_url or ":11434" in base_url:
        return "ollama"
    if "openrouter" in base_url:
        return "openrouter"
    if "deepseek" in base_url:
        return "deepseek"
    if "x.ai" in base_url or "grok" in base_url:
        return "xai"
    if "dashscope" in base_url or "aliyuncs" in base_url:
        return "dashscope"
    return provider or "openai"


def _openrouter_output_modality(service_type: str) -> str:
    return {
        "openaiCompatibleTTS": "speech",
        "openaiCompatibleSTT": "transcription",
        "openaiCompatibleImageGeneration": "image",
        "openaiCompatibleVideoGeneration": "video",
    }.get(service_type, "")


def _helper_spec(service_type: str, parameter: str, pdef: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    pdef = pdef or {}
    if parameter in {"api_key", "secret_access_key", "access_key_id", "key", "sas_url", "cf_clearance", "token", "ssl_keyfile_password", "keyfile_password"}:
        return {"id": "secrets.refs", "label": "Secret references", "context_fields": [], "requires": []}
    if parameter == "api_key_env":
        return {"id": "env.names", "label": "Environment variables", "context_fields": [], "requires": []}
    if parameter == "base_url":
        return {"id": "base_urls", "label": "Base URLs", "context_fields": ["provider", "api_key"], "requires": []}
    if parameter in {"public_callback_base_url", "callback_url"}:
        return {"id": "callback.urls", "label": "Callback URLs", "context_fields": [], "requires": []}
    if parameter in {"redirect_uri"}:
        return {"id": "oauth.redirect_uris", "label": "Redirect URIs", "context_fields": ["provider"], "requires": []}
    if parameter in {"authorize_url", "token_url", "userinfo_url"}:
        return {"id": "oauth.urls", "label": "OAuth endpoints", "context_fields": ["provider"], "requires": []}
    if parameter == "scope" and service_type == "oauthProvider":
        return {"id": "oauth.scopes", "label": "OAuth scopes", "context_fields": ["provider"], "requires": []}
    if service_type == "authGateway" and parameter == "providers":
        return {"id": "auth_gateway.providers", "label": "Provider templates", "context_fields": [], "requires": []}
    if parameter in {"ssl_certfile", "ssl_keyfile", "certfile", "keyfile", "ca_certfile", "key_file", "service_account_file", "prompt_audio", "install_dir"}:
        return {"id": "paths.common", "label": "Path templates", "context_fields": [], "requires": []}
    if parameter in {"repo_url", "repo_ref", "start_command", "package_spec"}:
        return {"id": "runtime.templates", "label": "Runtime templates", "context_fields": [], "requires": []}
    if service_type == "rcloneFilesystem" and parameter in {"provider", "endpoint", "region", "url", "host", "port"}:
        return {"id": "rclone.backends", "label": "Rclone presets", "context_fields": ["rclone_type"], "requires": []}
    if parameter in {"default_model", "fallback_model", "embedding_model"} and service_type == "llmConnection":
        return {"id": "llm.models", "label": "Model catalog", "context_fields": ["provider", "base_url", "api_key"], "requires": ["api_key"], "fallback": "static"}
    if parameter == "model" or parameter.endswith("_model") or parameter == "model_id" or parameter == "stt_model":
        return {"id": "models", "label": "Model catalog", "context_fields": ["provider", "base_url", "api_key", "protocol"], "requires": ["api_key"], "fallback": "static"}
    if parameter in {"response_format", "output_format", "format"}:
        return {"id": "formats", "label": "Formats", "context_fields": [], "requires": []}
    if parameter in {"language", "lang", "profile_language"}:
        return {"id": "languages", "label": "Languages", "context_fields": [], "requires": []}
    if parameter in {"voice", "profile_voice_id", "default_profile"}:
        return {"id": "voices", "label": "Voices", "context_fields": ["base_url", "api_key"], "requires": []}
    if parameter in {"quality", "style"}:
        return {"id": "image.options", "label": "Image options", "context_fields": ["model"], "requires": []}
    if parameter in {"submit_path", "status_path_template", "openrouter_generation_path_template"}:
        return {"id": "video.paths", "label": "Video paths", "context_fields": ["protocol"], "requires": []}
    if parameter == "extra_body":
        return {"id": "extra_body.templates", "label": "JSON templates", "context_fields": ["provider", "base_url", "model"], "requires": []}
    if parameter in {"max_context_size", "token_multiplier", "cost_per_1m_input", "cost_per_1m_output", "cost_per_1m_cache_read", "cost_per_1m_cache_write"}:
        return {"id": "llm.metadata", "label": "Model metadata", "context_fields": ["default_model", "provider"], "requires": []}
    return None


def _append_helper_description(description: str, spec: Dict[str, Any]) -> str:
    description = description or ""
    if "Helper:" in description:
        return description
    helper_id = spec.get("id")
    if helper_id == "secrets.refs":
        note = "Helper: lists stored secret names only and fills `${secret_name}` references; it never displays secret values."
    elif spec.get("requires"):
        note = "Helper: live provider lookup is attempted only when the required fields (usually `api_key`) are already filled; otherwise bundled fallback values are shown."
    elif helper_id in {"callback.urls", "oauth.redirect_uris"}:
        note = "Helper: suggests URL templates; replace example hostnames with your public PawFlow deployment."
    elif helper_id == "paths.common":
        note = "Helper: suggests relay/container path patterns; verify the file exists on the selected relay."
    else:
        note = "Helper: opens curated fill suggestions for this parameter."
    return (description + "\n\n" + note).strip()


def apply_service_parameter_helpers(service_type: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    """Return a schema copy with fill helper metadata attached."""
    enriched = copy.deepcopy(schema or {})
    for parameter, pdef in enriched.items():
        if not isinstance(pdef, dict):
            continue
        spec = _helper_spec(service_type, parameter, pdef)
        if not spec:
            continue
        pdef["fill_helper"] = {
            "service_type": service_type,
            "parameter": parameter,
            **spec,
        }
        pdef["description"] = _append_helper_description(str(pdef.get("description") or ""), spec)
    return enriched


def _secret_values(user_id: str, conversation_id: str, store: Any) -> List[Dict[str, str]]:
    values: List[Dict[str, str]] = []
    try:
        from core.expression import _load_global_secrets, _load_user_secrets
        for key in sorted(_load_global_secrets().keys()):
            values.append({"value": "${" + key + "}", "label": key + " [global]", "description": "Global secret reference."})
        if user_id:
            for key in sorted(_load_user_secrets(user_id).keys()):
                values.append({"value": "${" + key + "}", "label": key + " [user]", "description": "User secret reference."})
    except Exception as exc:
        logging.getLogger(__name__).debug("Could not load global/user secret references", exc_info=exc)
    if conversation_id and store is not None:
        try:
            for key in sorted((store.get_extra(conversation_id, "conv_secrets") or {}).keys()):
                values.append({"value": "${" + key + "}", "label": key + " [conversation]", "description": "Conversation secret reference."})
        except Exception as exc:
            logging.getLogger(__name__).debug("Could not load conversation secret references", exc_info=exc)
    return values


def _catalog_json_models(filename: str, category: str = "") -> List[Dict[str, Any]]:
    root = Path(__file__).resolve().parents[1]
    for path in [root / "services" / filename, root / "config" / filename]:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logging.getLogger(__name__).debug("Could not load model catalog %s", path, exc_info=exc)
            continue
        models = data.get("models") or {}
        out = []
        for mid, meta in sorted(models.items()):
            if category and str(meta.get("category") or "") != category:
                continue
            label = str(meta.get("label") or mid)
            desc = str(meta.get("description") or meta.get("category") or "Catalog model.")
            out.append({"value": mid, "label": label, "description": desc[:240]})
        return out
    return []


def _category_for_service(service_type: str) -> str:
    for prefix in ("pixazo", "wavespeed"):
        if not service_type.startswith(prefix):
            continue
        rest = service_type[len(prefix):]
        return {
            "ImageGeneration": "image",
            "VideoGeneration": "video",
            "AudioGeneration": "audio",
            "3DGeneration": "3d",
            "TryOn": "try_on",
            "Lipsync": "lipsync",
            "Upscale": "upscale",
            "Trainer": "trainer",
            "VoiceClone": "voice_clone",
        }.get(rest, "")
    return ""


def _fetch_json(url: str, headers: Dict[str, str], timeout: int = 8) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - curated provider model endpoints.
        return json.loads(resp.read().decode("utf-8"))


def _live_model_values(
    config: Dict[str, Any],
    *,
    embedding: bool = False,
    service_type: str = "",
    user_id: str = "",
    conversation_id: str = "",
) -> Tuple[List[Dict[str, Any]], str, str]:
    family = _provider_family(config)
    api_key = str(config.get("api_key") or "")
    base_url = str(config.get("base_url") or "").rstrip("/")
    headers: Dict[str, str] = {}
    if "${" in api_key:
        try:
            from core.expression import resolve_value
            api_key = str(resolve_value(api_key, owner=user_id, conversation_id=conversation_id) or "")
        except Exception:
            api_key = ""
    if family == "anthropic":
        if not api_key:
            return [], "fallback", "api_key is missing; showing bundled fallback values."
        endpoint = (base_url or "https://api.anthropic.com").rstrip("/") + "/v1/models"
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    else:
        endpoint = (base_url or {
            "ollama": "https://ollama.com/v1",
            "openrouter": "https://openrouter.ai/api/v1",
            "deepseek": "https://api.deepseek.com",
            "xai": "https://api.x.ai/v1",
            "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        }.get(family, "https://api.openai.com/v1")).rstrip("/") + "/models"
        modality = _openrouter_output_modality(service_type) if family == "openrouter" else ""
        if modality:
            endpoint += "?" + urllib.parse.urlencode({"output_modalities": modality})
        if api_key:
            headers = {"Authorization": "Bearer " + api_key}
        elif family in {"openai", "deepseek", "xai", "dashscope"}:
            return [], "fallback", "api_key is missing; showing bundled fallback values."
    try:
        data = _fetch_json(endpoint, headers)
        rows = data.get("data") or data.get("models") or []
        values = []
        for row in rows:
            mid = row.get("id") if isinstance(row, dict) else str(row)
            if not mid:
                continue
            if embedding and "embed" not in mid.lower():
                continue
            values.append({"value": mid, "label": row.get("name") or mid if isinstance(row, dict) else mid, "description": "Live provider model."})
        return values[:200], "live", ""
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
        return [], "fallback", f"Live model lookup failed ({exc.__class__.__name__}); showing bundled fallback values."


def _fallback_models(service_type: str, parameter: str, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    if service_type == "llmConnection":
        family = _provider_family(config)
        if parameter == "embedding_model":
            return _values(EMBEDDING_MODELS.get(family) or EMBEDDING_MODELS.get(str(config.get("provider") or "openai"), []))
        return _values(LLM_MODELS.get(family) or LLM_MODELS.get(str(config.get("provider") or "openai"), []))
    if service_type.startswith("pixazo"):
        return _catalog_json_models("pixazo_catalog.json", _category_for_service(service_type))
    if service_type.startswith("wavespeed"):
        return _catalog_json_models("wavespeed_catalog.json", _category_for_service(service_type))
    if parameter in {"stt_model"}:
        return _values(STATIC_MODELS.get(service_type, STATIC_MODELS.get("voicebox", [])))
    return _values(STATIC_MODELS.get(service_type, []))


def _base_url_values(service_type: str, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    if service_type == "llmConnection" and str(config.get("provider") or "") == "anthropic":
        return _values(ANTHROPIC_BASE_URLS)
    if service_type in {"supertonicTTS", "pocketTTS", "voicebox", "voxcpmTTS", "httpClientService"}:
        return _values(LOCAL_BASE_URLS + OPENAI_BASE_URLS[:2])
    return _values(OPENAI_BASE_URLS)


def get_service_parameter_helper(
    service_type: str,
    parameter: str,
    config: Optional[Dict[str, Any]] = None,
    *,
    user_id: str = "",
    conversation_id: str = "",
    store: Any = None,
) -> Dict[str, Any]:
    """Return fill suggestions for one service parameter."""
    config = dict(config or {})
    spec = _helper_spec(service_type, parameter) or {}
    helper_id = spec.get("id", "")
    values: List[Dict[str, Any]] = []
    warning = ""
    source = "static"

    if helper_id == "secrets.refs":
        values = _secret_values(user_id, conversation_id, store)
        if not values:
            warning = "No stored secrets found. Store one first, then use this helper to fill a ${secret_name} reference."
    elif helper_id == "env.names":
        values = _values([("OPENAI_API_KEY", "OpenAI-compatible API key environment variable."), ("ANTHROPIC_API_KEY", "Anthropic API key environment variable."), ("VOXCPM_API_KEY", "VoxCPM bearer token environment variable.")])
    elif helper_id == "base_urls":
        values = _base_url_values(service_type, config)
    elif helper_id == "callback.urls":
        values = _values(CALLBACK_VALUES)
    elif helper_id == "oauth.redirect_uris":
        values = _values([("https://webchat.example.org/auth/callback", "Generic public callback URL."), ("http://localhost:9090/auth/callback", "Local development callback URL."), ("https://webchat.example.org/auth/${provider}/callback", "Provider-specific public callback URL template.")])
    elif helper_id == "oauth.urls":
        values = _values(OAUTH_URLS.get(parameter, []))
    elif helper_id == "oauth.scopes":
        values = _values(OAUTH_SCOPES)
    elif helper_id == "auth_gateway.providers":
        values = [_json_value(AUTH_GATEWAY_PROVIDERS_TEMPLATE, "Google + GitHub", "Provider map template with expression-backed client secrets.")]
    elif helper_id == "paths.common":
        values = _values(PATH_VALUES)
    elif helper_id == "runtime.templates":
        if parameter == "repo_url":
            values = _values(REPO_VALUES)
        elif parameter == "repo_ref":
            values = _values([("main", "Default branch."), ("v1.0.0", "Pinned release tag template.")])
        elif parameter == "package_spec":
            values = _values(PACKAGE_VALUES)
        else:
            values = _values([("python -m voicebox.server --host 0.0.0.0 --port 17493", "Voicebox API start command template."), ("python -m supertonic.server --host 0.0.0.0 --port 7788", "Supertonic API start command template.")])
    elif helper_id == "rclone.backends":
        if parameter == "provider":
            values = _values(RCLONE_PROVIDERS)
        elif parameter == "endpoint":
            values = _values(RCLONE_ENDPOINTS)
        elif parameter == "region":
            values = _values(RCLONE_REGIONS)
        elif parameter == "url":
            values = _values([("https://example.org/webdav", "WebDAV endpoint template."), (_conv_relay_url("localhost:8080"), "Relay-routed WebDAV endpoint.")])
        elif parameter == "host":
            values = _values([("sftp.example.org", "SFTP host template."), ("${conv.relay}", "Conversation relay host expression.")])
        elif parameter == "port":
            values = _values([("22", "SFTP default."), ("21", "FTP default."), ("9000", "Common MinIO port.")])
    elif helper_id in {"llm.models", "models"}:
        embedding = service_type == "llmConnection" and parameter == "embedding_model"
        if service_type == "llmConnection" or service_type in {"openaiCompatibleTTS", "openaiCompatibleSTT", "openaiImageGeneration", "openaiCompatibleImageGeneration", "openaiCompatibleVideoGeneration"}:
            values, source, warning = _live_model_values(
                config,
                embedding=embedding,
                service_type=service_type,
                user_id=user_id,
                conversation_id=conversation_id,
            )
        if not values:
            values = _fallback_models(service_type, parameter, config)
            source = "fallback"
            if not warning and spec.get("requires"):
                warning = "api_key is missing; showing bundled fallback values."
    elif helper_id == "formats":
        if service_type == "openaiCompatibleSTT":
            values = _values(AUDIO_RESPONSE_FORMATS)
        elif service_type == "openaiCompatibleTTS":
            values = _values([("mp3", "MP3 audio."), ("pcm", "Raw PCM audio."), ("wav", "WAV audio."), ("flac", "FLAC audio."), ("opus", "Opus audio."), ("aac", "AAC audio.")])
        elif parameter == "output_format" and service_type == "elevenLabsVoiceClone":
            values = _values([("mp3_44100_128", "MP3 44.1 kHz 128 kbps."), ("mp3_44100_192", "MP3 44.1 kHz 192 kbps."), ("pcm_16000", "PCM 16 kHz."), ("pcm_44100", "PCM 44.1 kHz.")])
        else:
            values = _values(IMAGE_RESPONSE_FORMATS if "Image" in service_type else [("wav", "WAV audio."), ("mp3", "MP3 audio."), ("flac", "FLAC audio."), ("ogg", "Ogg audio."), ("opus", "Opus audio.")])
    elif helper_id == "languages":
        values = _values(LANGUAGE_VALUES)
    elif helper_id == "voices":
        values = _values(VOICE_VALUES)
    elif helper_id == "image.options":
        values = _values(QUALITY_VALUES if parameter == "quality" else STYLE_VALUES)
    elif helper_id == "video.paths":
        values = _values([("/v1/video/generations", "OpenAI-compatible video submit path."), ("/v1/video/generations/{id}", "OpenAI-compatible status template."), ("/api/v1/generation?id={id}", "OpenRouter generation status template.")])
    elif helper_id == "extra_body.templates":
        values = [
            _json_value({"provider": {"sort": "throughput"}}, "OpenRouter throughput", "Prefer providers sorted by throughput."),
            _json_value({"provider": {"allow_fallbacks": True}, "include_reasoning": True}, "OpenRouter reasoning", "Allow fallbacks and include reasoning when supported."),
            _json_value({}, "Empty object", "Clear provider-specific body overrides."),
        ]
    elif helper_id == "llm.metadata":
        if parameter == "max_context_size":
            values = _values(LLM_CONTEXT_HINTS)
        elif parameter == "token_multiplier":
            values = _values(TOKEN_MULTIPLIERS)
        else:
            values = _values(PRICING_HINTS)

    return {
        "service_type": service_type,
        "parameter": parameter,
        "helper": helper_id,
        "title": spec.get("label") or "Parameter helper",
        "values": values,
        "source": source,
        "warning": warning,
    }
