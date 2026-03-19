"""Internationalization for OpenPaw GUI."""

import json
import os
from typing import Dict, Optional

_translations: Dict[str, Dict[str, str]] = {}
_current_locale: str = "en"
_fallback_locale: str = "en"

SUPPORTED_LOCALES = {
    "en": "English",
    "fr": "Français",
    "es": "Español",
}


def _load_locale(locale: str) -> Dict[str, str]:
    """Load translation file for a locale."""
    path = os.path.join(os.path.dirname(__file__), f"{locale}.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def init(locale: str = "en"):
    """Initialize i18n with given locale."""
    global _current_locale, _translations
    _current_locale = locale if locale in SUPPORTED_LOCALES else "en"
    if _current_locale not in _translations:
        _translations[_current_locale] = _load_locale(_current_locale)
    if _fallback_locale not in _translations:
        _translations[_fallback_locale] = _load_locale(_fallback_locale)


def set_locale(locale: str):
    """Change current locale."""
    global _current_locale
    if locale in SUPPORTED_LOCALES:
        _current_locale = locale
        if locale not in _translations:
            _translations[locale] = _load_locale(locale)


def get_locale() -> str:
    return _current_locale


def t(key: str, **kwargs) -> str:
    """Translate a key. Supports {variable} interpolation.

    Usage:
        t("dashboard.title")
        t("flow.task_count", count=5)
    """
    # Try current locale
    text = _translations.get(_current_locale, {}).get(key)
    # Fallback to English
    if text is None:
        text = _translations.get(_fallback_locale, {}).get(key)
    # Fallback to key itself
    if text is None:
        return key
    # Interpolation
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass
    return text


def get_available_locales() -> Dict[str, str]:
    """Return {locale_code: display_name}."""
    return dict(SUPPORTED_LOCALES)
