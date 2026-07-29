"""Conversation full-text index + the conversation_search tool (P4).

What the tests pin, beyond "search finds the message":

- an encrypted conversation is never indexed, and one encrypted *after* being
  indexed is purged -- the index is plaintext, so this is the difference
  between encryption meaning something and not;
- the refresh is incremental and its watermark counts transcript rows, not
  indexed rows, so a transcript full of tool output does not get re-read on
  every search;
- a query with FTS5 syntax errors in it searches for the words instead of
  failing the call.
"""

import pytest

import core.paths as _paths
from core.conversation_index import (ConversationIndex, FTSUnavailable,
                                     sanitize_query)
from core.conversation_store import ConversationStore

USER = "alice"
OTHER = "bob"


@pytest.fixture(autouse=True)
def index_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "CONVERSATION_INDEX_DIR",
                        tmp_path / "conversation_index")
    ConversationIndex.reset()
    yield
    ConversationIndex.reset()


@pytest.fixture(autouse=True)
def reset_store():
    ConversationStore.reset()
    yield
    ConversationStore.reset()


@pytest.fixture
def store(tmp_path):
    return ConversationStore(store_dir=str(tmp_path / "conversations"))


@pytest.fixture
def index():
    return ConversationIndex.for_user(USER)


def _conv(store, messages, user_id=USER, title=""):
    cid = store.generate_id()
    store.save(cid, messages, user_id=user_id)
    if title:
        store.set_extra(cid, "title", title, user_id=user_id)
    return cid


class TestIndexing:

    def test_a_message_is_searchable_after_a_refresh(self, store, index):
        cid = _conv(store, [
            {"role": "user", "content": "the relay keeps dropping websockets"},
            {"role": "assistant", "content": "raise the ping interval"},
        ])
        index.refresh(store)

        hits = index.search("websockets")
        assert [h["conversation_id"] for h in hits] == [cid]
        assert "websockets" in hits[0]["snippet"].lower()

    def test_only_user_and_assistant_rows_are_indexed(self, store, index):
        _conv(store, [
            {"role": "user", "content": "run the migration"},
            {"role": "tool", "content": "applied 41 migrations successfully"},
        ])
        index.refresh(store)

        assert index.search("migrations successfully") == []
        assert len(index.search("migration")) == 1

    def test_another_users_conversation_is_not_in_this_index(self, store, index):
        _conv(store, [{"role": "user", "content": "bob's private notes"}],
              user_id=OTHER)
        index.refresh(store)

        assert index.search("private notes") == []
        assert index.stats()["conversations"] == 0

    def test_a_second_refresh_reads_only_the_new_rows(self, store, index):
        cid = _conv(store, [{"role": "user", "content": "first question"}])
        index.refresh(store)
        store.append_message(cid, {"role": "user", "content": "second question",
                                   "source": {"target_agent": "claude"}})

        stats = index.refresh(store)

        assert stats["messages"] == 1  # not 2 -- the first row was not re-read
        assert len(index.search("question")) == 2

    def test_an_unchanged_conversation_is_never_reopened(self, store, index):
        # Without this, every search would read every transcript from disk --
        # "incremental" in name only.
        _conv(store, [{"role": "user", "content": "first question"}])
        index.refresh(store)

        reads = []
        real_load = store.load
        store.load = lambda cid, **kw: (reads.append(cid),
                                        real_load(cid, **kw))[1]
        stats = index.refresh(store)

        assert reads == []
        assert stats["unchanged"] == 1 and stats["indexed"] == 0

    def test_a_touched_conversation_is_reopened(self, store, index):
        cid = _conv(store, [{"role": "user", "content": "first question"}])
        index.refresh(store)
        store.append_message(cid, {"role": "user", "content": "second question",
                                   "source": {"target_agent": "claude"}})

        stats = index.refresh(store)

        assert stats["unchanged"] == 0 and stats["messages"] == 1

    def test_the_watermark_counts_rows_read_not_rows_stored(self, store, index):
        # A transcript that is mostly tool output must not be re-read whole on
        # every refresh just because few of its rows end up indexed.
        cid = _conv(store, [{"role": "user", "content": "build it"}])
        for _ in range(5):
            store.append_message(cid, {"role": "tool", "content": "ok"})
        index.refresh(store)

        assert index.refresh(store)["messages"] == 0

    def test_a_shrunken_transcript_is_reindexed_whole(self, store, index):
        cid = _conv(store, [
            {"role": "user", "content": "alpha topic"},
            {"role": "user", "content": "beta topic"},
        ])
        index.refresh(store)
        # A rollback / context edit leaves fewer rows than the watermark.
        store.save(cid, [{"role": "user", "content": "gamma topic"}],
                   user_id=USER)

        index.refresh(store)

        assert [h["snippet"] for h in index.search("topic")]
        assert index.search("alpha") == []
        assert len(index.search("gamma")) == 1

    def test_a_deleted_conversation_leaves_the_index(self, store, index):
        cid = _conv(store, [{"role": "user", "content": "ephemeral thought"}])
        index.refresh(store)
        store.delete(cid)

        stats = index.refresh(store)

        assert stats["purged"] == 1
        assert index.search("ephemeral") == []

    def test_a_renamed_conversation_reports_its_new_title(self, store, index):
        cid = _conv(store, [{"role": "user", "content": "pick a codec"}],
                    title="Old name")
        index.refresh(store)
        store.set_extra(cid, "title", "Codec choice", user_id=USER)

        index.refresh(store)

        assert index.search("codec")[0]["title"] == "Codec choice"


class TestEncryptionIsNeverIndexed:
    """The index is plaintext on disk; indexing ciphered content would put it
    back in the clear and make the encryption decorative."""

    def test_an_encrypted_conversation_is_skipped(self, store, index,
                                                  monkeypatch):
        _conv(store, [{"role": "user", "content": "the passphrase is hunter2"}])
        monkeypatch.setattr(store, "encryption_status",
                            lambda cid: {"enabled": True})

        stats = index.refresh(store)

        assert stats["skipped_encrypted"] == 1
        assert index.search("passphrase") == []

    def test_encrypting_an_indexed_conversation_purges_it(self, store, index,
                                                          monkeypatch):
        _conv(store, [{"role": "user", "content": "the passphrase is hunter2"}])
        index.refresh(store)
        assert len(index.search("passphrase")) == 1

        monkeypatch.setattr(store, "encryption_status",
                            lambda cid: {"enabled": True})
        stats = index.refresh(store)

        assert stats["purged"] == 1
        assert index.search("passphrase") == []

    def test_an_unreadable_encryption_state_is_treated_as_encrypted(
            self, store, index, monkeypatch):
        _conv(store, [{"role": "user", "content": "the passphrase is hunter2"}])

        def boom(cid):
            raise RuntimeError("vault unreachable")

        monkeypatch.setattr(store, "encryption_status", boom)
        stats = index.refresh(store)

        assert stats["skipped_encrypted"] == 1
        assert index.search("passphrase") == []


class TestQuerying:

    def test_a_phrase_query_is_honoured(self, store, index):
        _conv(store, [
            {"role": "user", "content": "backpressure on the queue"},
            {"role": "user", "content": "the queue had backpressure"},
        ])
        index.refresh(store)

        assert len(index.search('"backpressure on the queue"')) == 1

    def test_broken_fts_syntax_searches_for_the_words(self, store, index):
        _conv(store, [{"role": "user", "content": "the relay dropped a frame"}])
        index.refresh(store)

        # An unbalanced quote is a syntax error in FTS5; raising it at the
        # agent would be a tool failure for a perfectly clear intent.
        assert len(index.search('relay "frame')) == 1

    def test_sanitize_turns_operators_into_words(self):
        assert sanitize_query('relay "frame') == '"relay" AND "frame"'
        assert sanitize_query("***") == ""

    def test_the_agent_filter_narrows_results(self, store, index):
        cid = _conv(store, [{"role": "user", "content": "who owns the deploy"}])
        store.append_message(
            cid, {"role": "assistant", "content": "the deploy is mine",
                  "source": {"type": "agent", "name": "ops"}},
            agent_name="ops")
        index.refresh(store)

        hits = index.search("deploy", agent="ops")

        assert len(hits) == 1 and hits[0]["agent"] == "ops"

    def test_the_limit_is_capped(self, store, index):
        _conv(store, [{"role": "user", "content": f"repeated topic {i}"}
                      for i in range(60)])
        index.refresh(store)

        assert len(index.search("topic", limit=999)) == 50

    def test_an_empty_query_returns_nothing(self, index):
        assert index.search("   ") == []


class TestHandler:

    @pytest.fixture
    def handler(self, store, monkeypatch):
        from core.handlers.conversation_search import ConversationSearchHandler
        monkeypatch.setattr("core.conversation_store.ConversationStore.instance",
                            staticmethod(lambda: store))
        h = ConversationSearchHandler()
        h.set_user_id(USER)
        h.set_agent_name("claude")
        return h

    def test_it_refuses_without_a_user(self):
        from core.handlers.conversation_search import ConversationSearchHandler
        assert "user_id not set" in ConversationSearchHandler().execute(
            {"query": "anything"})

    def test_it_requires_a_query(self, handler):
        assert "query is required" in handler.execute({})

    def test_it_renders_hits_with_the_conversation_id(self, store, handler):
        cid = _conv(store, [{"role": "user", "content": "the certificate expired"}],
                    title="TLS incident")

        out = handler.execute({"query": "certificate"})

        assert cid[:8] in out and "TLS incident" in out
        assert "1 hit(s)" in out

    def test_the_current_conversation_is_excluded_by_default(self, store,
                                                             handler):
        cid = _conv(store, [{"role": "user", "content": "the certificate expired"}])
        handler.set_conversation_id(cid)

        assert "No match" in handler.execute({"query": "certificate"})
        assert cid[:8] in handler.execute({"query": "certificate",
                                           "include_current": True})

    def test_no_hits_reports_what_was_searched(self, store, handler):
        _conv(store, [{"role": "user", "content": "unrelated"}])

        out = handler.execute({"query": "kubernetes"})

        assert "No match" in out and "1 conversations" in out

    def test_summarize_without_a_summarizer_says_so(self, store, handler,
                                                    monkeypatch):
        _conv(store, [{"role": "user", "content": "the certificate expired"}])
        handler.set_conversation_id("current-conv")
        monkeypatch.setattr(handler, "_summarizer_client", lambda: None)

        out = handler.execute({"query": "certificate", "summarize": True})

        assert "needs a summarizer_service" in out

    def test_summarize_gets_the_message_not_just_the_snippet(self, store,
                                                             handler,
                                                             monkeypatch):
        _conv(store, [{"role": "user", "content": (
            "the certificate expired and we rotated it with certbot renew "
            "after the cron job had silently failed for a month")}])
        handler.set_conversation_id("current-conv")
        seen = {}

        class _Client:
            def complete(self, **kwargs):
                seen["prompt"] = kwargs["messages"][0].content
                return type("R", (), {"content": "ok"})()

        monkeypatch.setattr(handler, "_summarizer_client", lambda: _Client())
        handler.execute({"query": "certificate", "summarize": True})

        # A summarizer handed only "… [certificate] …" can do nothing but
        # paraphrase the query back.
        assert "cron job had silently failed" in seen["prompt"]

    def test_summarize_uses_the_summarizer(self, store, handler, monkeypatch):
        _conv(store, [{"role": "user", "content": "the certificate expired"}])
        handler.set_conversation_id("current-conv")

        class _Resp:
            content = "You hit an expired TLS cert and rotated it."

        class _Client:
            def complete(self, **kwargs):
                return _Resp()

        monkeypatch.setattr(handler, "_summarizer_client", lambda: _Client())
        out = handler.execute({"query": "certificate", "summarize": True})

        assert "rotated it" in out

    def test_a_failing_summarizer_falls_back_to_the_excerpts(self, store,
                                                             handler,
                                                             monkeypatch):
        cid = _conv(store, [{"role": "user", "content": "the certificate expired"}])
        handler.set_conversation_id("current-conv")

        class _Client:
            def complete(self, **kwargs):
                raise RuntimeError("model unreachable")

        monkeypatch.setattr(handler, "_summarizer_client", lambda: _Client())
        out = handler.execute({"query": "certificate", "summarize": True})

        assert "model unreachable" in out and cid[:8] in out

    def test_missing_fts5_is_reported_not_raised(self, handler, monkeypatch):
        def boom(user_id):
            raise FTSUnavailable("no such module: fts5")

        monkeypatch.setattr(ConversationIndex, "for_user", staticmethod(boom))
        out = handler.execute({"query": "anything"})

        assert "FTS5" in out

    def test_summarize_without_a_conversation_context_is_refused(self, store,
                                                                 handler):
        _conv(store, [{"role": "user", "content": "the certificate expired"}])

        out = handler.execute({"query": "certificate", "summarize": True})

        assert "needs a conversation context" in out


class TestTheRightToBeForgotten:
    """Search must not keep serving text the transcript no longer holds.

    The refresh skips a conversation whose ``updated_at`` has not moved, and
    only purges when the row count shrank. Neither sees an edit: editing a
    message in place leaves ``updated_at`` untouched (it is the newest message
    timestamp) at a constant row count, and deleting the newest message moves
    it *backwards*, which reads as "older than what I hold". Redacted and
    deleted text kept coming back, indefinitely.
    """

    def test_redacted_text_stops_being_searchable(self, store, index):
        cid = _conv(store, [
            {"role": "user", "content": "my password is hunter2",
             "msg_id": "m1", "ts": 100.0},
            {"role": "assistant", "content": "noted", "msg_id": "m2",
             "ts": 101.0},
        ])
        index.refresh(store)
        assert len(index.search("hunter2")) == 1

        # Same row count, same timestamps: only the content changes.
        assert store.edit_message(cid, "m1", "my password is REDACTED",
                                  user_id=USER) == 1
        index.refresh(store)

        assert index.search("hunter2") == []
        assert len(index.search("REDACTED")) == 1

    def test_a_deleted_message_stops_being_searchable(self, store, index):
        cid = _conv(store, [
            {"role": "user", "content": "keep this one", "msg_id": "m1",
             "ts": 100.0},
            {"role": "assistant", "content": "forget this one",
             "msg_id": "m2", "ts": 101.0},
        ])
        index.refresh(store)
        assert len(index.search("forget")) == 1

        # The newest message: updated_at moves backwards after this.
        assert store.delete_message(cid, msg_id="m2", user_id=USER) is True
        index.refresh(store)

        assert index.search("forget") == []
        # And the rebuild did not lose what was still there.
        assert len(index.search("keep")) == 1

    def test_a_plain_append_is_still_read_incrementally(self, store, index):
        """The fix must not turn every refresh into a full reindex.

        A search pays for the refresh, so re-reading every transcript would
        trade a correctness bug for a performance one.
        """
        cid = _conv(store, [
            {"role": "user", "content": "alpha line", "msg_id": "m1",
             "ts": 100.0},
        ])
        index.refresh(store)
        assert index.refresh(store)["unchanged"] == 1

        store.save(cid, [
            {"role": "user", "content": "alpha line", "msg_id": "m1",
             "ts": 100.0},
            {"role": "assistant", "content": "beta line", "msg_id": "m2",
             "ts": 200.0},
        ], user_id=USER)

        stats = index.refresh(store)
        # One row read, not two: the watermark still applies.
        assert stats["messages"] == 1
        assert len(index.search("beta")) == 1
        assert index.refresh(store)["unchanged"] == 1

    def test_an_index_written_before_the_counter_is_rebuilt_once(self, store,
                                                                 tmp_path):
        """Such a database may already hold text that has since been deleted.

        Its rows carry no generation, so they must not be trusted: the missing
        column defaults to -1, which differs from every real generation.
        """
        import sqlite3

        db = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db))
        conn.executescript(
            "CREATE TABLE indexed_conversations ("
            " conversation_id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',"
            " rows_indexed INTEGER NOT NULL DEFAULT 0,"
            " source_updated_at REAL NOT NULL DEFAULT 0,"
            " updated_at REAL NOT NULL DEFAULT 0);"
            "CREATE VIRTUAL TABLE messages USING fts5("
            " content, conversation_id UNINDEXED, title UNINDEXED,"
            " agent UNINDEXED, role UNINDEXED, msg_id UNINDEXED,"
            " ts UNINDEXED, tokenize = 'unicode61');")
        cid = _conv(store, [
            {"role": "user", "content": "the current text", "msg_id": "m1",
             "ts": 100.0},
        ])
        # A row claiming this conversation is indexed and up to date, holding
        # text the transcript no longer has.
        conn.execute(
            "INSERT INTO indexed_conversations (conversation_id, rows_indexed,"
            " source_updated_at, updated_at) VALUES (?, 1, ?, ?)",
            (cid, 9e9, 9e9))
        conn.execute(
            "INSERT INTO messages (content, conversation_id, title, agent,"
            " role, msg_id, ts) VALUES ('a secret long since deleted', ?,"
            " '', '', 'user', 'gone', 1.0)", (cid,))
        conn.commit()
        conn.close()

        index = ConversationIndex(USER, path=str(db))
        index.refresh(store)

        assert index.search("secret") == []
        assert len(index.search("current text")) == 1


class TestToolWiring:

    def test_the_tool_is_registered(self):
        from core.tool_registry import create_default_registry
        assert create_default_registry().get("conversation_search") is not None

    def test_it_needs_no_approval_and_survives_read_only_mode(self):
        from core.tool_approval import ToolApprovalGate
        assert "conversation_search" in ToolApprovalGate.EXEMPT_TOOLS
        assert "conversation_search" in ToolApprovalGate.READ_ONLY_ALLOWED
