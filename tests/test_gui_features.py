"""Tests for GUI-related features: auth helpers, secrets, parameter contexts."""

import json
import os
import tempfile
import pytest
from pathlib import Path

from core.security import SecurityManager, Role, User, Session
from core.secrets import SecretsManager, get_secrets_manager
from core.parameter_context import ParameterContext


# --- Auth helpers ---

class TestSecretsManager:

    def test_encrypt_decrypt(self):
        sm = SecretsManager(key="testkey")
        encrypted = sm.encrypt("hello world")
        assert encrypted.startswith("enc:")
        assert sm.decrypt(encrypted) == "hello world"

    def test_is_encrypted(self):
        sm = SecretsManager(key="testkey")
        assert not sm.is_encrypted("plain text")
        assert sm.is_encrypted("enc:abc123")

    def test_decrypt_plaintext_passthrough(self):
        sm = SecretsManager(key="testkey")
        assert sm.decrypt("plain") == "plain"
        assert sm.decrypt("") == ""

    def test_encrypt_already_encrypted(self):
        sm = SecretsManager(key="testkey")
        encrypted = sm.encrypt("data")
        # Re-encrypting already encrypted data returns it as-is
        assert sm.encrypt(encrypted) == encrypted

    def test_wrong_key_fails(self):
        sm1 = SecretsManager(key="key1")
        encrypted = sm1.encrypt("secret")
        sm2 = SecretsManager(key="key2")
        with pytest.raises(ValueError):
            sm2.decrypt(encrypted)


# --- Parameter Context ---

class TestParameterContext:

    def test_basic_get(self):
        ctx = ParameterContext({"env": "prod", "batch": "100"})
        assert ctx.get("env") == "prod"
        assert ctx.get("batch") == "100"
        assert ctx.get("missing") is None
        assert ctx.get("missing", "default") == "default"

    def test_has(self):
        ctx = ParameterContext({"key": "val"})
        assert ctx.has("key")
        assert not ctx.has("other")

    def test_with_overrides(self):
        ctx = ParameterContext({"env": "dev", "db": "local"})
        new_ctx = ctx.with_overrides({"env": "prod"})
        assert new_ctx.get("env") == "prod"
        assert new_ctx.get("db") == "local"
        # Original unchanged
        assert ctx.get("env") == "dev"

    def test_parameters_returns_copy(self):
        ctx = ParameterContext({"a": "1"})
        params = ctx.parameters
        params["b"] = "2"
        assert not ctx.has("b")

    def test_with_mapping(self):
        parent = ParameterContext({"env": "prod", "key": "abc"})
        child = parent.with_mapping({
            "sub_env": "${env}",
            "mode": "fast",
        })
        assert child.get("sub_env") == "prod"
        assert child.get("mode") == "fast"

    def test_resolve_expression(self):
        ctx = ParameterContext({"name": "world"})
        result = ctx.resolve("Hello ${name}!")
        assert result == "Hello world!"
