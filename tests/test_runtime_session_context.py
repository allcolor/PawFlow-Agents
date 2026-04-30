import json

from tasks.ai.actions.context_ops import (
    _load_codex_session_context,
    _load_gemini_session_context,
)


class _Store:
    def __init__(self, extras, user_id="user1"):
        self._extras = extras
        self._user_id = user_id

    def get_extra(self, _cid, key):
        return self._extras.get(key, "")

    def get_user_id(self, _cid):
        return self._user_id


def test_codex_session_context_assigns_msg_id_when_rollout_lacks_id(tmp_path, monkeypatch):
    from core.llm_providers import codex_session

    monkeypatch.setattr(codex_session, "_get_sessions_base", lambda: str(tmp_path))
    workdir = tmp_path / "user1" / "conv1" / "assistant"
    sessions_dir = workdir / ".codex" / "sessions"
    sessions_dir.mkdir(parents=True)
    rollout = sessions_dir / "rollout-thread-1.jsonl"
    rollout.write_text(
        json.dumps({
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "text", "text": "hello codex"}],
            }
        }) + "\n",
        encoding="utf-8",
    )

    store = _Store({"codex_app_server_thread:assistant": "thread-1"})
    messages = _load_codex_session_context("conv1", "assistant", store, user_id="user1")

    assert messages[0]["content"] == "hello codex"
    assert messages[0]["msg_id"].startswith("codex:thread-1:")


def test_gemini_session_context_assigns_msg_id_when_history_lacks_id(tmp_path, monkeypatch):
    from core.llm_providers import gemini_session

    monkeypatch.setattr(gemini_session, "_get_sessions_base", lambda: str(tmp_path))
    workdir = tmp_path / "user1" / "conv1" / "assistant"
    chats_dir = workdir / ".gemini" / "tmp" / "gemini" / "chats"
    chats_dir.mkdir(parents=True)
    history = chats_dir / "session-1.jsonl"
    history.write_text(
        json.dumps({
            "sessionId": "session-1",
            "type": "user",
            "content": [{"type": "text", "text": "hello gemini"}],
        }) + "\n",
        encoding="utf-8",
    )

    store = _Store({"gemini_acp_session:assistant": "session-1"})
    messages = _load_gemini_session_context("conv1", "assistant", store, user_id="user1")

    assert messages[0]["content"] == "hello gemini"
    assert messages[0]["msg_id"].startswith("gemini:session-1.jsonl:")
