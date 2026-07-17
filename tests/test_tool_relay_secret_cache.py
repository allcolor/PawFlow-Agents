"""Secrets env/values caches must refresh when the secret config changes.

Regression: the caches stored a config fingerprint but never compared it on
hits, so a secret added mid-conversation never reached tool env (and never
entered redaction) until a server restart.
"""

import pytest

from services.tool_relay_service import ToolRelayService
from services import _tool_relay_base as _trb


@pytest.fixture()
def clean_caches():
    with ToolRelayService._runtime_cache_lock:
        ToolRelayService._secret_env_cache.clear()
        ToolRelayService._secret_values_cache.clear()
    yield
    with ToolRelayService._runtime_cache_lock:
        ToolRelayService._secret_env_cache.clear()
        ToolRelayService._secret_values_cache.clear()


@pytest.fixture()
def stubbed(monkeypatch, clean_caches):
    state = {"fingerprint": ("v1",), "env": {"A": "1"},
             "values": ({"1"}, {"1": "A"}), "env_calls": 0}
    monkeypatch.setattr(
        ToolRelayService, "_secret_config_fingerprint",
        classmethod(lambda cls, uid, conv: state["fingerprint"]))
    monkeypatch.setattr(
        ToolRelayService, "_root_conversation_id",
        classmethod(lambda cls, conv: conv))

    def _resolve_env(uid, conv):
        state["env_calls"] += 1
        return dict(state["env"])

    monkeypatch.setattr(_trb, "resolve_secrets_env", _resolve_env)
    monkeypatch.setattr(_trb, "resolve_secret_values",
                        lambda uid, conv: (set(state["values"][0]),
                                           dict(state["values"][1])))
    return state


def test_secret_env_cache_hits_while_config_unchanged(stubbed):
    assert ToolRelayService._cached_secrets_env("u", "c") == {"A": "1"}
    assert ToolRelayService._cached_secrets_env("u", "c") == {"A": "1"}
    assert stubbed["env_calls"] == 1  # second call served from cache


def test_secret_added_mid_conversation_reaches_env(stubbed):
    assert ToolRelayService._cached_secrets_env("u", "c") == {"A": "1"}
    # user adds OPENAI_API_KEY -> secrets file mtime changes the fingerprint
    stubbed["fingerprint"] = ("v2",)
    stubbed["env"] = {"A": "1", "OPENAI_API_KEY": "sk-new"}
    env = ToolRelayService._cached_secrets_env("u", "c")
    assert env["OPENAI_API_KEY"] == "sk-new"
    assert stubbed["env_calls"] == 2


def test_secret_values_refresh_for_redaction(stubbed):
    values, names = ToolRelayService._cached_secret_values("u", "c")
    assert values == {"1"}
    # new secret value must enter the redaction set too
    stubbed["fingerprint"] = ("v2",)
    stubbed["values"] = ({"1", "sk-new"}, {"1": "A", "sk-new": "OPENAI"})
    values, names = ToolRelayService._cached_secret_values("u", "c")
    assert "sk-new" in values
    assert names["sk-new"] == "OPENAI"
