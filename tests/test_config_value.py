"""Tests for ConfigValue, ConfigStore, and spill-to-disk integration."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from core.config_value import ConfigValue
from core.config_store import ConfigStore
from core.stream import SPILL_THRESHOLD


# ---- ConfigValue unit tests ----

class TestConfigValueSmall:
    def test_str_returns_value(self):
        cv = ConfigValue(value="hello")
        assert str(cv) == "hello"

    def test_as_str(self):
        cv = ConfigValue(value="world")
        assert cv.as_str() == "world"

    def test_as_bytes(self):
        cv = ConfigValue(value="test")
        assert cv.as_bytes() == b"test"

    def test_is_large_false(self):
        cv = ConfigValue(value="small")
        assert cv.is_large is False

    def test_size(self):
        cv = ConfigValue(value="abc")
        assert cv.size == 3

    def test_preview_short(self):
        cv = ConfigValue(value="short")
        assert cv.preview() == "short"

    def test_preview_truncated(self):
        cv = ConfigValue(value="x" * 300)
        p = cv.preview(max_chars=10)
        assert len(p) == 13  # 10 + "..."
        assert p.endswith("...")

    def test_get_stream(self):
        cv = ConfigValue(value="stream_test")
        s = cv.get_stream()
        assert s.read() == b"stream_test"
        s.close()

    def test_repr(self):
        cv = ConfigValue(value="hi")
        assert "hi" in repr(cv)

    def test_eq_same(self):
        a = ConfigValue(value="same")
        b = ConfigValue(value="same")
        assert a == b

    def test_eq_str(self):
        cv = ConfigValue(value="test")
        assert cv == "test"

    def test_eq_different(self):
        a = ConfigValue(value="a")
        b = ConfigValue(value="b")
        assert a != b

    def test_from_bytes_small(self):
        cv = ConfigValue(data=b"hello bytes")
        assert cv.is_large is False
        assert cv.as_str() == "hello bytes"

    def test_empty_value(self):
        cv = ConfigValue()
        assert str(cv) == ""
        assert cv.size == 0
        assert cv.is_large is False

    def test_release_noop_small(self):
        cv = ConfigValue(value="safe")
        cv.release()  # Should not crash
        assert str(cv) == "safe"  # str_value still there


class TestConfigValueLarge:
    def test_is_large(self):
        data = b"x" * (SPILL_THRESHOLD + 1)
        cv = ConfigValue(data=data)
        assert cv.is_large is True

    def test_str_shows_placeholder(self):
        data = b"x" * (SPILL_THRESHOLD + 1)
        cv = ConfigValue(data=data)
        s = str(cv)
        assert s.startswith("<large:")
        assert "MB>" in s

    def test_as_str_loads_all(self):
        data = b"A" * (SPILL_THRESHOLD + 100)
        cv = ConfigValue(data=data)
        assert len(cv.as_str()) == SPILL_THRESHOLD + 100

    def test_as_bytes(self):
        data = b"B" * (SPILL_THRESHOLD + 50)
        cv = ConfigValue(data=data)
        assert cv.as_bytes() == data

    def test_size(self):
        data = b"C" * (SPILL_THRESHOLD + 200)
        cv = ConfigValue(data=data)
        assert cv.size == SPILL_THRESHOLD + 200

    def test_get_stream(self):
        data = b"D" * (SPILL_THRESHOLD + 10)
        cv = ConfigValue(data=data)
        s = cv.get_stream()
        assert s.read() == data
        s.close()

    def test_preview(self):
        data = b"E" * (SPILL_THRESHOLD + 10)
        cv = ConfigValue(data=data)
        p = cv.preview(50)
        assert len(p) <= 54  # 50 + "..."
        assert p.endswith("...")

    def test_release_cleans_up(self):
        data = b"F" * (SPILL_THRESHOLD + 10)
        cv = ConfigValue(data=data)
        assert cv.is_large
        cv.release()
        assert cv._content_ref is None

    def test_from_string_large(self):
        big_str = "G" * (SPILL_THRESHOLD + 10)
        cv = ConfigValue(value=big_str)
        assert cv.is_large is True
        assert cv.as_str() == big_str

    def test_repr_large(self):
        data = b"H" * (SPILL_THRESHOLD + 10)
        cv = ConfigValue(data=data)
        r = repr(cv)
        assert "large" in r


# ---- ConfigStore params tests ----

class TestConfigStoreParams:
    def test_roundtrip_small(self, tmp_path):
        p = tmp_path / "params.json"
        data = {
            "key1": ConfigValue(value="val1"),
            "key2": ConfigValue(value="val2"),
        }
        ConfigStore.save_params(p, data)
        loaded = ConfigStore.load_params(p)
        assert str(loaded["key1"]) == "val1"
        assert str(loaded["key2"]) == "val2"
        assert not loaded["key1"].is_large

    def test_roundtrip_large(self, tmp_path):
        p = tmp_path / "params.json"
        big = b"X" * (SPILL_THRESHOLD + 100)
        data = {"big_key": ConfigValue(data=big)}
        ConfigStore.save_params(p, data)

        # Check sidecar was created
        sidecars = [f for f in tmp_path.iterdir() if f.name.endswith(".dat")]
        assert len(sidecars) == 1

        # Check JSON has $ref
        raw = json.loads(p.read_text())
        assert raw["big_key"]["$type"] == "spilled"
        assert "$ref" in raw["big_key"]

        # Load back
        loaded = ConfigStore.load_params(p)
        assert loaded["big_key"].is_large
        assert loaded["big_key"].as_bytes() == big

    def test_mixed_small_large(self, tmp_path):
        p = tmp_path / "params.json"
        data = {
            "small": ConfigValue(value="tiny"),
            "large": ConfigValue(data=b"Y" * (SPILL_THRESHOLD + 10)),
        }
        ConfigStore.save_params(p, data)
        loaded = ConfigStore.load_params(p)
        assert not loaded["small"].is_large
        assert str(loaded["small"]) == "tiny"
        assert loaded["large"].is_large

    def test_backward_compat_plain_json(self, tmp_path):
        """Loading a JSON without $ref works (all values become small ConfigValues)."""
        p = tmp_path / "params.json"
        p.write_text(json.dumps({"a": "one", "b": "two"}))
        loaded = ConfigStore.load_params(p)
        assert str(loaded["a"]) == "one"
        assert str(loaded["b"]) == "two"
        assert not loaded["a"].is_large

    def test_missing_file(self, tmp_path):
        loaded = ConfigStore.load_params(tmp_path / "nope.json")
        assert loaded == {}

    def test_sidecar_cleanup(self, tmp_path):
        p = tmp_path / "params.json"
        big = b"Z" * (SPILL_THRESHOLD + 10)
        data = {
            "keep": ConfigValue(data=big),
            "remove": ConfigValue(data=big),
        }
        ConfigStore.save_params(p, data)
        assert len([f for f in tmp_path.iterdir() if f.name.endswith(".dat")]) == 2

        # Remove one key and save
        del data["remove"]
        ConfigStore.save_params(p, data)
        sidecars = [f for f in tmp_path.iterdir() if f.name.endswith(".dat")]
        assert len(sidecars) == 1  # orphan cleaned


# ---- ConfigStore secrets tests ----

class TestConfigStoreSecrets:
    def test_roundtrip_small(self, tmp_path):
        p = tmp_path / "secrets.json"
        data = {"api_key": ConfigValue(value="secret123")}
        ConfigStore.save_secrets(p, data)
        loaded = ConfigStore.load_secrets(p)
        assert str(loaded["api_key"]) == "secret123"

    def test_roundtrip_large(self, tmp_path):
        p = tmp_path / "secrets.json"
        big = b"S" * (SPILL_THRESHOLD + 50)
        data = {"big_secret": ConfigValue(data=big)}
        ConfigStore.save_secrets(p, data)

        # Check .enc sidecar
        enc_files = [f for f in tmp_path.iterdir() if f.name.endswith(".dat.enc")]
        assert len(enc_files) == 1

        loaded = ConfigStore.load_secrets(p)
        assert loaded["big_secret"].as_bytes() == big

    def test_encrypted_inline(self, tmp_path):
        """Small secrets are encrypted inline."""
        p = tmp_path / "secrets.json"
        data = {"small": ConfigValue(value="pass")}
        ConfigStore.save_secrets(p, data)
        raw = json.loads(p.read_text())
        assert raw["small"].startswith("enc:")

    def test_backward_compat_encrypted(self, tmp_path):
        """Loading existing encrypted JSON without $ref works."""
        from core.secrets import get_secrets_manager
        sm = get_secrets_manager()
        p = tmp_path / "secrets.json"
        encrypted = sm.encrypt("mypass")
        p.write_text(json.dumps({"key1": encrypted}))
        loaded = ConfigStore.load_secrets(p)
        assert str(loaded["key1"]) == "mypass"

    def test_backward_compat_dict_entry(self, tmp_path):
        """Loading existing dict-style entries {value: enc:...} works."""
        from core.secrets import get_secrets_manager
        sm = get_secrets_manager()
        p = tmp_path / "secrets.json"
        encrypted = sm.encrypt("dictpass")
        p.write_text(json.dumps({"key1": {"value": encrypted}}))
        loaded = ConfigStore.load_secrets(p)
        assert str(loaded["key1"]) == "dictpass"

    def test_sidecar_cleanup_secrets(self, tmp_path):
        p = tmp_path / "secrets.json"
        big = b"T" * (SPILL_THRESHOLD + 10)
        data = {
            "keep": ConfigValue(data=big),
            "remove": ConfigValue(data=big),
        }
        ConfigStore.save_secrets(p, data)
        assert len([f for f in tmp_path.iterdir() if f.name.endswith(".dat.enc")]) == 2

        del data["remove"]
        ConfigStore.save_secrets(p, data)
        enc_files = [f for f in tmp_path.iterdir() if f.name.endswith(".dat.enc")]
        assert len(enc_files) == 1


# ---- Expression resolution with ConfigValue ----

class TestExpressionWithConfigValue:
    def test_small_param_resolves(self, tmp_path, monkeypatch):
        """Small global param resolves normally via expression."""
        params_file = tmp_path / "global_parameters.json"
        params_file.write_text(json.dumps({"env": "prod"}))
        monkeypatch.setattr(
            "core.expression._GLOBAL_PARAMS_FILE", params_file
        )
        from core.expression import resolve_expression
        result = resolve_expression("${env}")
        assert result == "prod"

    def test_large_param_not_interpolated(self, tmp_path, monkeypatch):
        """Large global param leaves expression unresolved with warning."""
        params_file = tmp_path / "global_parameters.json"
        big = "L" * (SPILL_THRESHOLD + 10)
        data = {"big": ConfigValue(value=big)}
        ConfigStore.save_params(params_file, data)
        monkeypatch.setattr(
            "core.expression._GLOBAL_PARAMS_FILE", params_file
        )
        from core.expression import resolve_expression
        result = resolve_expression("${big}")
        assert result == "${big}"  # Left unresolved


# ---- ParameterContext with get_raw ----

class TestParameterContextRaw:
    def test_get_raw_small(self):
        from core.parameter_context import ParameterContext
        ctx = ParameterContext({"key": "value"})
        assert ctx.get_raw("key") == "value"

    def test_get_raw_config_value(self):
        from core.parameter_context import ParameterContext
        cv = ConfigValue(data=b"X" * (SPILL_THRESHOLD + 10))
        ctx = ParameterContext({"big": cv})
        raw = ctx.get_raw("big")
        assert isinstance(raw, ConfigValue)
        assert raw.is_large

    def test_get_raw_default(self):
        from core.parameter_context import ParameterContext
        ctx = ParameterContext({})
        assert ctx.get_raw("missing", "default") == "default"

    def test_resolve_config_skips_large(self):
        from core.parameter_context import ParameterContext
        cv = ConfigValue(data=b"Y" * (SPILL_THRESHOLD + 10))
        ctx = ParameterContext({"x": "1"})
        config = {"normal": "hello", "big_val": cv}
        resolved = ctx.resolve_config(config)
        assert resolved["normal"] == "hello"
        assert isinstance(resolved["big_val"], ConfigValue)
        assert resolved["big_val"].is_large

    def test_repr_with_large(self):
        from core.parameter_context import ParameterContext
        cv = ConfigValue(data=b"Z" * (SPILL_THRESHOLD + 10))
        ctx = ParameterContext({"big": cv, "small": "val"})
        r = repr(ctx)
        assert "<large:" in r
        assert "small" in r


# ---- SecretsManager encrypt_bytes/decrypt_bytes ----

class TestSecretsManagerBytes:
    def test_roundtrip(self):
        from core.secrets import SecretsManager
        sm = SecretsManager(key="test_key")
        data = b"hello world bytes"
        encrypted = sm.encrypt_bytes(data)
        assert encrypted != data
        decrypted = sm.decrypt_bytes(encrypted)
        assert decrypted == data

    def test_large_roundtrip(self):
        from core.secrets import SecretsManager
        sm = SecretsManager(key="test_key")
        data = os.urandom(SPILL_THRESHOLD + 100)
        encrypted = sm.encrypt_bytes(data)
        decrypted = sm.decrypt_bytes(encrypted)
        assert decrypted == data

    def test_integrity_check(self):
        from core.secrets import SecretsManager
        sm = SecretsManager(key="test_key")
        data = b"integrity test"
        encrypted = sm.encrypt_bytes(data)
        # Tamper with data
        tampered = encrypted[:20] + bytes([encrypted[20] ^ 0xFF]) + encrypted[21:]
        with pytest.raises(ValueError, match="Integrity check failed"):
            sm.decrypt_bytes(tampered)

    def test_too_short(self):
        from core.secrets import SecretsManager
        sm = SecretsManager(key="test_key")
        with pytest.raises(ValueError, match="too short"):
            sm.decrypt_bytes(b"short")
