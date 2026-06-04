"""Tests for transient FileStore TTL resolution."""

from core.file_ttl import resolve_ttl_seconds


class _Store:
    def __init__(self, params):
        self.params = params

    def get_extra(self, conversation_id, key):
        assert conversation_id == "conv-1"
        assert key == "conv_parameters"
        return self.params


def test_resolve_ttl_prefers_conversation_parameter(monkeypatch):
    from core.conversation_store import ConversationStore

    monkeypatch.setenv("PAWFLOW_WEBCHAT_UPLOAD_TTL_SECONDS", "7200")
    monkeypatch.setattr(
        ConversationStore, "instance", classmethod(lambda cls: _Store({
            "webchat_upload_ttl_seconds": "1800",
        })))

    assert resolve_ttl_seconds(
        conversation_id="conv-1",
        conv_keys=("webchat_upload_ttl_seconds", "attachment_ttl_seconds"),
        env_key="PAWFLOW_WEBCHAT_UPLOAD_TTL_SECONDS",
        default=3600,
    ) == 1800


def test_resolve_ttl_uses_alias_and_clamps_minimum(monkeypatch):
    from core.conversation_store import ConversationStore

    monkeypatch.setenv("PAWFLOW_WEBCHAT_UPLOAD_TTL_SECONDS", "7200")
    monkeypatch.setattr(
        ConversationStore, "instance", classmethod(lambda cls: _Store({
            "attachment_ttl_seconds": "10",
        })))

    assert resolve_ttl_seconds(
        conversation_id="conv-1",
        conv_keys=("webchat_upload_ttl_seconds", "attachment_ttl_seconds"),
        env_key="PAWFLOW_WEBCHAT_UPLOAD_TTL_SECONDS",
        default=3600,
    ) == 60


def test_resolve_ttl_falls_back_to_env_then_default(monkeypatch):
    from core.conversation_store import ConversationStore

    monkeypatch.setattr(
        ConversationStore, "instance", classmethod(lambda cls: _Store({})))
    monkeypatch.setenv("PAWFLOW_WEBCHAT_UPLOAD_TTL_SECONDS", "2400")

    assert resolve_ttl_seconds(
        conversation_id="conv-1",
        conv_keys=("webchat_upload_ttl_seconds",),
        env_key="PAWFLOW_WEBCHAT_UPLOAD_TTL_SECONDS",
        default=3600,
    ) == 2400

    monkeypatch.setenv("PAWFLOW_WEBCHAT_UPLOAD_TTL_SECONDS", "invalid")
    assert resolve_ttl_seconds(
        conversation_id="conv-1",
        conv_keys=("webchat_upload_ttl_seconds",),
        env_key="PAWFLOW_WEBCHAT_UPLOAD_TTL_SECONDS",
        default=3600,
    ) == 3600
