"""Tests for the i18n module."""

import json
import os
import pytest

# Reset module state before each test
import gui.i18n as i18n_module


@pytest.fixture(autouse=True)
def reset_i18n():
    """Reset i18n state before each test."""
    i18n_module._translations.clear()
    i18n_module._current_locale = "en"
    yield
    i18n_module._translations.clear()
    i18n_module._current_locale = "en"


def test_init_default_locale():
    """init() with no args defaults to English."""
    i18n_module.init()
    assert i18n_module.get_locale() == "en"


def test_init_french_locale():
    """init('fr') sets French as current locale."""
    i18n_module.init("fr")
    assert i18n_module.get_locale() == "fr"


def test_init_invalid_locale_falls_back_to_en():
    """init() with unsupported locale falls back to English."""
    i18n_module.init("xx")
    assert i18n_module.get_locale() == "en"


def test_t_returns_english_text():
    """t() returns correct English translation."""
    i18n_module.init("en")
    assert i18n_module.t("app.name") == "OpenPaw"
    assert i18n_module.t("nav.dashboard") == "Dashboard"
    assert i18n_module.t("common.save") == "Save"


def test_t_returns_french_text():
    """t() returns correct French translation."""
    i18n_module.init("fr")
    assert i18n_module.t("nav.dashboard") == "Tableau de bord"
    assert i18n_module.t("common.save") == "Enregistrer"


def test_t_returns_spanish_text():
    """t() returns correct Spanish translation."""
    i18n_module.init("es")
    assert i18n_module.t("nav.dashboard") == "Panel de control"
    assert i18n_module.t("common.save") == "Guardar"


def test_t_interpolation():
    """t() supports {variable} interpolation."""
    i18n_module.init("en")
    result = i18n_module.t("connection.connected", url="http://localhost")
    assert result == "Connected to http://localhost"


def test_t_interpolation_french():
    """t() interpolation works in French too."""
    i18n_module.init("fr")
    result = i18n_module.t("connection.connected", url="http://localhost")
    assert "http://localhost" in result


def test_t_fallback_to_english():
    """t() falls back to English when key missing in current locale."""
    i18n_module.init("fr")
    # Temporarily remove a key from French to test fallback
    fr_translations = i18n_module._translations["fr"]
    original = fr_translations.pop("app.name", None)
    try:
        result = i18n_module.t("app.name")
        assert result == "OpenPaw"  # Falls back to English
    finally:
        if original is not None:
            fr_translations["app.name"] = original


def test_t_fallback_to_key():
    """t() returns the key itself when not found in any locale."""
    i18n_module.init("en")
    result = i18n_module.t("nonexistent.key.here")
    assert result == "nonexistent.key.here"


def test_set_locale():
    """set_locale() changes the current language."""
    i18n_module.init("en")
    assert i18n_module.get_locale() == "en"
    i18n_module.set_locale("fr")
    assert i18n_module.get_locale() == "fr"
    assert i18n_module.t("common.save") == "Enregistrer"


def test_get_locale():
    """get_locale() returns the current locale code."""
    i18n_module.init("es")
    assert i18n_module.get_locale() == "es"


def test_get_available_locales():
    """get_available_locales() returns all 3 supported locales."""
    locales = i18n_module.get_available_locales()
    assert len(locales) == 3
    assert "en" in locales
    assert "fr" in locales
    assert "es" in locales
    assert locales["en"] == "English"
    assert locales["fr"] == "Français"
    assert locales["es"] == "Español"


def _load_json(locale: str) -> dict:
    path = os.path.join(os.path.dirname(i18n_module.__file__), f"{locale}.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_fr_has_all_en_keys():
    """All keys in en.json must exist in fr.json."""
    en = _load_json("en")
    fr = _load_json("fr")
    missing = set(en.keys()) - set(fr.keys())
    assert not missing, f"Keys in en.json missing from fr.json: {missing}"


def test_es_has_all_en_keys():
    """All keys in en.json must exist in es.json."""
    en = _load_json("en")
    es = _load_json("es")
    missing = set(en.keys()) - set(es.keys())
    assert not missing, f"Keys in en.json missing from es.json: {missing}"


def test_no_extra_keys_in_fr():
    """fr.json should not have keys that don't exist in en.json."""
    en = _load_json("en")
    fr = _load_json("fr")
    extra = set(fr.keys()) - set(en.keys())
    assert not extra, f"Extra keys in fr.json not in en.json: {extra}"


def test_no_extra_keys_in_es():
    """es.json should not have keys that don't exist in en.json."""
    en = _load_json("en")
    es = _load_json("es")
    extra = set(es.keys()) - set(en.keys())
    assert not extra, f"Extra keys in es.json not in en.json: {extra}"
