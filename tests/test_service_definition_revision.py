from types import SimpleNamespace

import pytest

from core.service_definition_revision import (
    compute_service_definition_revision,
    service_sensitive_keys,
)


def _definition(**overrides):
    values = {
        "service_type": "llmConnection",
        "created_at": 100.0,
        "enabled": True,
        "config": {
            "provider": "openai", "api_key": "secret-a",
            "default_model": "m", "_service_id": "runtime-only"},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_schema_sensitive_classifier_is_shared():
    assert "api_key" in service_sensitive_keys("llmConnection")


def test_revision_is_deterministic_and_secret_free():
    first = compute_service_definition_revision(_definition())
    reordered = compute_service_definition_revision(_definition(config={
        "default_model": "m", "api_key": "secret-b", "provider": "openai"}))
    assert first == reordered
    assert len(first) == 64
    int(first, 16)


def test_material_config_recreation_and_enabled_state_change_revision():
    base = compute_service_definition_revision(_definition())
    assert base != compute_service_definition_revision(
        _definition(config={"provider": "openai", "api_key": "secret-a",
                            "default_model": "other"}))
    assert base != compute_service_definition_revision(_definition(created_at=101))
    assert base != compute_service_definition_revision(_definition(enabled=False))


@pytest.mark.parametrize("created_at", [None, 0, float("nan")])
def test_revision_rejects_invalid_created_at(created_at):
    with pytest.raises(ValueError, match="created_at"):
        compute_service_definition_revision(_definition(created_at=created_at))


def test_revision_rejects_non_finite_or_unsupported_config():
    with pytest.raises(ValueError, match="non-finite"):
        compute_service_definition_revision(_definition(config={"x": float("inf")}))
    with pytest.raises(ValueError, match="Unsupported"):
        compute_service_definition_revision(_definition(config={"x": object()}))
