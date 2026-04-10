"""LLM profile resolution — maps profile names to LLMConnection config.

Profiles are defined in data/config/llm_profiles.json and provide ready-made
provider/base_url/model defaults so users don't have to configure them manually.
"""

import json
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

from core.paths import LLM_PROFILES_FILE, ensure_seed_file


def load_profiles() -> Dict[str, Dict[str, Any]]:
    """Load profiles from llm_profiles.json. Returns empty dict on error."""
    if not LLM_PROFILES_FILE.exists():
        ensure_seed_file(LLM_PROFILES_FILE, "llm_profiles.json")
    try:
        return json.loads(LLM_PROFILES_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.debug("llm_profiles.json not found at %s", LLM_PROFILES_FILE)
        return {}
    except Exception as e:
        logger.warning("Failed to load llm_profiles.json: %s", e)
        return {}


def apply_profile(profile_name: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Resolve a profile name to a config dict, applying any overrides.

    Returns the merged config ready to pass to service_install / LLMConnectionService.
    Raises ValueError if the profile is unknown.
    """
    profiles = load_profiles()
    if profile_name not in profiles:
        available = ", ".join(sorted(profiles))
        raise ValueError(
            f"Unknown LLM profile '{profile_name}'. "
            f"Available: {available}"
        )
    config = dict(profiles[profile_name])
    # Remove UI-only keys
    config.pop("description", None)
    config.pop("requires_api_key", None)
    config.pop("models", None)
    # Apply caller overrides (e.g. api_key, default_model)
    if overrides:
        config.update({k: v for k, v in overrides.items() if v not in ("", None)})
    return config


def get_profile_info(profile_name: str) -> Optional[Dict[str, Any]]:
    """Return full profile info (including description, models list), or None."""
    return load_profiles().get(profile_name)
