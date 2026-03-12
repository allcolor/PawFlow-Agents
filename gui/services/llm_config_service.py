"""LLM configuration persistence service.

Saves and loads LLM settings (provider, base URL, model)
to config/llm_config.json. API keys are NOT persisted to disk
for security — only stored in session state during the session.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("config") / "llm_config.json"


def load_llm_config() -> Dict[str, Any]:
    """Load LLM config from disk (without API key)."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Cannot load LLM config: %s", e)
        return {}


def save_llm_config(config: Dict[str, Any]) -> bool:
    """Save LLM config to disk (strips API key for security)."""
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        safe_config = {
            "provider": config.get("provider", "openai"),
            "base_url": config.get("base_url", ""),
            "default_model": config.get("default_model", ""),
            "timeout": config.get("timeout", 60),
        }
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(safe_config, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error("Cannot save LLM config: %s", e)
        return False


def get_full_llm_config(api_key: str = "") -> Optional[Dict[str, Any]]:
    """Get full LLM config by merging persisted settings with runtime API key."""
    config = load_llm_config()
    if not api_key:
        return None
    config["api_key"] = api_key
    return config
