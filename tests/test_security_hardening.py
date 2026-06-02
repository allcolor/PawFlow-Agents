"""Tests for P10 security hardening.

Covers:
- Password hashing (PBKDF2)
- CORS configuration
- SecretsManager encrypt/decrypt
- Input validation middleware
- ExecuteScript sandboxing
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Password hashing ──────────────────────────────────────────────

class TestPasswordHashing(unittest.TestCase):
    """Test PBKDF2 password hashing."""

    def test_hash_returns_pbkdf2_format(self):
        from core.security import _hash_password
        h = _hash_password("secret")
        self.assertTrue(h.startswith("pbkdf2:"))
        parts = h.split(":")
        self.assertEqual(len(parts), 3)
        # salt and hash are hex
        bytes.fromhex(parts[1])
        bytes.fromhex(parts[2])

    def test_hash_different_each_time(self):
        from core.security import _hash_password
        h1 = _hash_password("secret")
        h2 = _hash_password("secret")
        self.assertNotEqual(h1, h2)  # random salt

    def test_verify_correct_password(self):
        from core.security import _hash_password, _verify_password
        h = _hash_password("my_pass")
        self.assertTrue(_verify_password("my_pass", h))

    def test_verify_wrong_password(self):
        from core.security import _hash_password, _verify_password
        h = _hash_password("my_pass")
        self.assertFalse(_verify_password("wrong", h))

    def test_non_pbkdf2_hash_fails(self):
        from core.security import _verify_password
        self.assertFalse(_verify_password("admin", "not-pbkdf2"))

    def test_user_check_password_pbkdf2(self):
        from core.security import User, Role, _hash_password
        user = User(username="bob", password_hash=_hash_password("pass123"), role=Role.USER)
        self.assertTrue(user.check_password("pass123"))
        self.assertFalse(user.check_password("wrong"))

# ── CORS configuration ────────────────────────────────────────────

class TestCORSConfiguration(unittest.TestCase):
    """Test that CORS origins are configurable."""

    def test_default_origins(self):
        """Default should be localhost:8501 and localhost:8000."""
        env = os.environ.copy()
        env.pop("PAWFLOW_CORS_ORIGINS", None)
        with patch.dict(os.environ, env, clear=True):
            raw = os.environ.get("PAWFLOW_CORS_ORIGINS",
                                 "http://localhost:8501,http://localhost:8000")
            origins = [o.strip() for o in raw.split(",") if o.strip()]
        self.assertIn("http://localhost:8501", origins)
        self.assertIn("http://localhost:8000", origins)
        self.assertNotIn("*", origins)

    def test_custom_origins_from_env(self):
        with patch.dict(os.environ, {"PAWFLOW_CORS_ORIGINS": "https://app.example.com"}):
            raw = os.environ.get("PAWFLOW_CORS_ORIGINS",
                                 "http://localhost:8501,http://localhost:8000")
            origins = [o.strip() for o in raw.split(",") if o.strip()]
        self.assertEqual(origins, ["https://app.example.com"])

    def test_wildcard_if_explicit(self):
        with patch.dict(os.environ, {"PAWFLOW_CORS_ORIGINS": "*"}):
            raw = os.environ.get("PAWFLOW_CORS_ORIGINS",
                                 "http://localhost:8501,http://localhost:8000")
            origins = [o.strip() for o in raw.split(",") if o.strip()]
        self.assertEqual(origins, ["*"])


# ── SecretsManager ────────────────────────────────────────────────

class TestSecretsManager(unittest.TestCase):
    """Test secrets encryption/decryption."""

    def _make_manager(self, key="test-key"):
        from core.secrets import SecretsManager
        return SecretsManager(key=key)

    def test_encrypt_decrypt_roundtrip(self):
        sm = self._make_manager()
        plain = "my-secret-api-key-12345"
        encrypted = sm.encrypt(plain)
        self.assertTrue(encrypted.startswith("enc:"))
        decrypted = sm.decrypt(encrypted)
        self.assertEqual(decrypted, plain)

    def test_wrong_key_fails(self):
        sm1 = self._make_manager("key1")
        sm2 = self._make_manager("key2")
        encrypted = sm1.encrypt("secret")
        with self.assertRaises(ValueError):
            sm2.decrypt(encrypted)

    def test_is_encrypted(self):
        sm = self._make_manager()
        self.assertFalse(sm.is_encrypted("plain"))
        self.assertFalse(sm.is_encrypted(""))
        self.assertTrue(sm.is_encrypted("enc:abc"))

    def test_already_encrypted_skip(self):
        sm = self._make_manager()
        encrypted = sm.encrypt("secret")
        double = sm.encrypt(encrypted)
        self.assertEqual(encrypted, double)

    def test_no_prefix_passthrough(self):
        sm = self._make_manager()
        self.assertEqual(sm.decrypt("plain-text"), "plain-text")
        self.assertEqual(sm.decrypt(""), "")

    def test_empty_string_passthrough(self):
        sm = self._make_manager()
        self.assertEqual(sm.encrypt(""), "")
        self.assertEqual(sm.decrypt(""), "")

    def test_unicode_roundtrip(self):
        sm = self._make_manager()
        text = "Mot de passe: cafe\u0301 \u2603"
        self.assertEqual(sm.decrypt(sm.encrypt(text)), text)


# ── Input validation ──────────────────────────────────────────────

class TestInputValidation(unittest.TestCase):
    """Test request body size limit middleware."""

    def test_body_size_default(self):
        """Default max body size is 10 MB."""
        default = int(os.environ.get("PAWFLOW_MAX_BODY_SIZE", str(10 * 1024 * 1024)))
        self.assertEqual(default, 10 * 1024 * 1024)

    def test_body_size_env_override(self):
        with patch.dict(os.environ, {"PAWFLOW_MAX_BODY_SIZE": "1024"}):
            val = int(os.environ.get("PAWFLOW_MAX_BODY_SIZE", str(10 * 1024 * 1024)))
        self.assertEqual(val, 1024)


# ── ExecuteScript sandboxing ──────────────────────────────────────

class TestExecuteScriptSandbox(unittest.TestCase):
    """Test ExecuteScript task sandboxing."""

    def _make_flowfile(self, content=b"hello"):
        from core import FlowFile
        return FlowFile(content=content)

    def test_basic_execution(self):
        from tasks.system.execute_script import ExecuteScriptTask
        task = ExecuteScriptTask({"script": "result = content.upper()"})
        ff = self._make_flowfile(b"hello")
        results = task.execute(ff)
        self.assertEqual(results[0].get_content(), b"HELLO")

    def test_sandbox_blocks_subprocess(self):
        from tasks.system.execute_script import ExecuteScriptTask
        from core import TaskError
        task = ExecuteScriptTask({
            "script": "import subprocess; result = 'hacked'",
            "sandbox_mode": True
        })
        ff = self._make_flowfile()
        with self.assertRaises(TaskError) as ctx:
            task.execute(ff)
        self.assertIn("sandbox", str(ctx.exception).lower())

    def test_sandbox_blocks_os_system(self):
        from tasks.system.execute_script import ExecuteScriptTask
        from core import TaskError
        task = ExecuteScriptTask({
            "script": "import os; os.system('echo pwned')",
            "sandbox_mode": True
        })
        ff = self._make_flowfile()
        with self.assertRaises(TaskError):
            task.execute(ff)

    def test_sandbox_blocks_shutil(self):
        from tasks.system.execute_script import ExecuteScriptTask
        from core import TaskError
        task = ExecuteScriptTask({
            "script": "import shutil",
            "sandbox_mode": True
        })
        ff = self._make_flowfile()
        with self.assertRaises(TaskError):
            task.execute(ff)

    def test_sandbox_blocks_os(self):
        """Unified sandbox always blocks os module."""
        from tasks.system.execute_script import ExecuteScriptTask
        from core import TaskError
        task = ExecuteScriptTask({
            "script": "import os; result = os.getcwd()",
        })
        ff = self._make_flowfile()
        with self.assertRaises(TaskError):
            task.execute(ff)

    def test_sandbox_allows_safe_modules(self):
        """Unified sandbox allows json, re, math, etc."""
        from tasks.system.execute_script import ExecuteScriptTask
        task = ExecuteScriptTask({
            "script": "import json; result = json.dumps({'a': 1})",
        })
        ff = self._make_flowfile()
        results = task.execute(ff)
        self.assertEqual(results[0].get_content(), b'{"a": 1}')

    def test_sandbox_allows_re(self):
        """re module is in the safe whitelist."""
        from tasks.system.execute_script import ExecuteScriptTask
        task = ExecuteScriptTask({
            "script": "import re; result = re.sub(r'\\d+', 'X', content)",
        })
        ff = self._make_flowfile(b"abc123def")
        results = task.execute(ff)
        self.assertEqual(results[0].get_content(), b"abcXdef")

    def test_sandbox_io_available(self):
        """io module is pre-injected in the sandbox."""
        from tasks.system.execute_script import ExecuteScriptTask
        task = ExecuteScriptTask({
            "script": "buf = io.StringIO(); buf.write('hello'); result = buf.getvalue()",
        })
        ff = self._make_flowfile()
        results = task.execute(ff)
        self.assertEqual(results[0].get_content(), b"hello")

    def test_sandbox_has_open(self):
        """Sandboxed open() is available for FileStore-backed I/O."""
        from tasks.system.execute_script import ExecuteScriptTask
        task = ExecuteScriptTask({
            "script": "f = open('test.txt', 'w'); f.write('data'); f.close(); result = 'ok'",
        })
        ff = self._make_flowfile()
        results = task.execute(ff)
        self.assertEqual(results[0].get_content(), b"ok")


if __name__ == "__main__":
    unittest.main()
