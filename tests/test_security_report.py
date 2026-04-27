"""Phase 9 tests: production-mode startup security report."""

import pytest

from core import paths
from core import security_report as sr


def test_capability_store_presence_uses_json_file(monkeypatch, tmp_path):
    cap_file = tmp_path / "capabilities.json"
    monkeypatch.setattr(paths, "CAPABILITIES_FILE", cap_file)
    assert sr._capability_store_present() is False

    cap_file.write_text("[]", encoding="utf-8")
    assert sr._capability_store_present() is True


def test_dev_mode_no_fatal(monkeypatch):
    monkeypatch.delenv("PAWFLOW_ENV", raising=False)
    monkeypatch.delenv("PAWFLOW_PUBLIC_MODE", raising=False)
    monkeypatch.delenv("PAWFLOW_SECRET_KEY_B64", raising=False)
    monkeypatch.delenv("PAWFLOW_SECRET_KEY", raising=False)
    monkeypatch.delenv("PAWFLOW_APPROVAL_FAIL_OPEN", raising=False)
    rep = sr.build_report()
    assert rep.production is False
    assert rep.fatal_errors == []


def test_production_blocks_on_fallback_secret(monkeypatch):
    monkeypatch.setenv("PAWFLOW_ENV", "production")
    monkeypatch.delenv("PAWFLOW_SECRET_KEY_B64", raising=False)
    monkeypatch.delenv("PAWFLOW_SECRET_KEY", raising=False)
    monkeypatch.delenv("PAWFLOW_APPROVAL_FAIL_OPEN", raising=False)
    rep = sr.build_report()
    assert rep.production is True
    assert any("SECRET_KEY_B64" in m or "SECRET_KEY" in m
               for m in rep.fatal_errors)


def test_production_blocks_on_fail_open(monkeypatch):
    monkeypatch.setenv("PAWFLOW_ENV", "production")
    monkeypatch.setenv("PAWFLOW_SECRET_KEY_B64", "x" * 44)  # any value
    monkeypatch.setenv("PAWFLOW_APPROVAL_FAIL_OPEN", "true")
    rep = sr.build_report()
    assert any("FAIL_OPEN" in m for m in rep.fatal_errors)


def test_production_with_proper_config(monkeypatch):
    monkeypatch.setenv("PAWFLOW_ENV", "production")
    monkeypatch.setenv("PAWFLOW_SECRET_KEY_B64", "x" * 44)
    monkeypatch.delenv("PAWFLOW_APPROVAL_FAIL_OPEN", raising=False)
    rep = sr.build_report()
    # No fatal errors with valid env config
    assert rep.fatal_errors == []
    assert rep.production is True


def test_public_mode_alias(monkeypatch):
    """PAWFLOW_PUBLIC_MODE=true is treated identically to PAWFLOW_ENV=production."""
    monkeypatch.delenv("PAWFLOW_ENV", raising=False)
    monkeypatch.setenv("PAWFLOW_PUBLIC_MODE", "true")
    monkeypatch.delenv("PAWFLOW_SECRET_KEY_B64", raising=False)
    monkeypatch.delenv("PAWFLOW_SECRET_KEY", raising=False)
    rep = sr.build_report()
    assert rep.production is True
    assert rep.fatal_errors  # fallback secret still blocks


def test_enforce_raises_on_fatal(monkeypatch):
    monkeypatch.setenv("PAWFLOW_ENV", "production")
    monkeypatch.delenv("PAWFLOW_SECRET_KEY_B64", raising=False)
    monkeypatch.delenv("PAWFLOW_SECRET_KEY", raising=False)
    rep = sr.build_report()
    with pytest.raises(SystemExit):
        sr.enforce(rep)


def test_enforce_passes_when_clean(monkeypatch):
    monkeypatch.delenv("PAWFLOW_ENV", raising=False)
    monkeypatch.delenv("PAWFLOW_PUBLIC_MODE", raising=False)
    rep = sr.build_report()
    sr.enforce(rep)  # must not raise
