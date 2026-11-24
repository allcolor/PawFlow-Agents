"""A new agent context is seeded from the compacted shared context."""

import time
import uuid

import pytest

from core.bucket_store import BucketStore
from core.conversation_store import ConversationStore


def _user(content, target):
    return {"role": "user", "content": content, "msg_id": uuid.uuid4().hex[:12],
            "ts": time.time(), "source": {"type": "user", "target_agent": target}}


@pytest.fixture(autouse=True)
def _reset_singleton():
    ConversationStore.reset()
    yield
    ConversationStore.reset()


@pytest.fixture
def conv(tmp_path):
    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    cid = store.generate_id()
    store.save(cid, [], user_id="alice")
    for index in range(30):
        store.append_message(cid, _user(f"m{index:02d}", "bot"),
                             agent_name="bot", user_id="alice")
    return store, cid


def _summarize_shared_up_to(store, cid, index):
    shared = store.load_shared_for_agent(cid, "bot")
    covered = shared[index]
    BucketStore.get(store._conv_dir(cid)).add_bucket(
        first_seq=int(shared[0]["seq"]), last_seq=int(covered["seq"]),
        first_ts=float(shared[0]["ts"]), last_ts=float(covered["ts"]),
        summary="SUMMARY-OF-OLD-MESSAGES", msg_count=index + 1)


def test_a_new_agent_starts_from_the_pyramid_plus_the_uncovered_tail(conv):
    store, cid = conv
    _summarize_shared_up_to(store, cid, 24)  # m00..m24 are summarized

    store.append_message(cid, _user("first for newbie", "newbie"),
                         agent_name="newbie", user_id="alice")

    context = store.load_agent_context(cid, "newbie")
    texts = [str(row.get("content") or "") for row in context]
    assert "SUMMARY-OF-OLD-MESSAGES" in texts[0]
    assert context[0]["source"] == {"type": "context"}
    assert context[1]["role"] == "assistant"
    body = texts[2:]
    assert len(body) == 6  # m25..m29 uncovered + the routed message
    assert [any(f"m{i:02d}" in t for t in body) for i in range(25, 30)] == [True] * 5
    assert not any("m24" in t or "m00" in t for t in body)
    assert body[-1].endswith("first for newbie")
    assert all(row.get("msg_id") and row.get("ts") for row in context)


def test_without_any_bucket_the_shared_context_is_seeded_whole(conv):
    store, cid = conv
    seed = store.load_compacted_shared_for_agent(cid, "newbie")
    assert len(seed) == 30
    assert not any("Conversation summary" in str(r.get("content")) for r in seed)
    # Reading must not create the pyramid directory (it changes rollbacks).
    assert not (store._conv_dir(cid) / "summaries").exists()


def test_the_seed_never_reads_rows_the_pyramid_covers(conv, monkeypatch):
    store, cid = conv
    _summarize_shared_up_to(store, cid, 24)
    seen = []
    from core.segmented_jsonl import SegmentedJsonl
    original = SegmentedJsonl.iter_rows_reverse

    def counting(self):
        for row in original(self):
            seen.append(row)
            yield row

    monkeypatch.setattr(SegmentedJsonl, "iter_rows_reverse", counting)
    store.load_compacted_shared_for_agent(cid, "newbie")
    assert len(seen) == 6  # 5 uncovered rows + the first covered one stops it
