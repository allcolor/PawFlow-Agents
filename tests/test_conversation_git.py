from unittest.mock import MagicMock


def test_commit_turn_uses_store_snapshot_timeout_default(monkeypatch):
    import core.conversation_git as cg

    writer = MagicMock()
    monkeypatch.setattr(
        cg.ConversationWriter,
        "for_conversation",
        MagicMock(return_value=writer),
    )

    store = MagicMock()
    monkeypatch.setattr(cg.ConversationStore, "instance", MagicMock(return_value=store))

    cg.commit_turn("conv123456789", "done")

    writer.flush.assert_called_once_with(timeout=10.0)
    store.git_snapshot.assert_called_once_with("conv123456789", "done")


def test_git_snapshot_has_no_implicit_subprocess_timeout(tmp_path):
    from core.conversation_store import ConversationStore

    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    assert store.git_snapshot.__func__.__defaults__ == ("", None)
    assert store._git.__func__.__kwdefaults__ == {"check": True, "timeout": None}
