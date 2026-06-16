"""FlowPawflowApi per-conversation encryption surface.

Proves the guarantee the public help bots rely on: a flow can encrypt the
conversations it creates with a passphrase only the visitor holds (their session
cookie / Telegram id), and the flow OWNER — who never has that passphrase —
cannot read the conversation at rest. The visitor, presenting their secret, can.
"""

import time
import uuid

import pytest

import core.key_vault as key_vault
from core.key_vault import KeyUnwrapError
from core.conversation_store import ConversationStore
from core.flow_pawflow_api import FlowPawflowApi
from core.flow_runtime_access import make_runtime_context

UID = "ownerbot"


@pytest.fixture(autouse=True)
def _reset():
    ConversationStore.reset()
    key_vault._reset_for_tests()
    yield
    ConversationStore.reset()
    key_vault._reset_for_tests()


@pytest.fixture
def api(tmp_path):
    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    ConversationStore._instance = store  # force the singleton the API/auth use
    cid = store.generate_id()
    store.save(cid, [], user_id=UID)
    flow = FlowPawflowApi(make_runtime_context(scope="user", user_id=UID),
                          requester_user_id=UID)
    return flow, store, cid


def _user(content):
    return {"role": "user", "content": content, "msg_id": uuid.uuid4().hex[:12],
            "ts": time.time(), "source": {"type": "user", "target_agent": "bot"}}


def _disk(store, cid):
    from core.segmented_jsonl import SegmentedJsonl
    SegmentedJsonl.flush_all_append_handles()
    out = []
    for path in store._content_log_paths(cid):
        seg = SegmentedJsonl(path)
        if seg.exists():
            out.extend(p.read_text() for p in seg.iter_paths())
    return "".join(out)


def test_api_exposes_encryption_methods():
    for name in ("enable_conv_encryption", "unlock_conv_encryption",
                 "lock_conv_encryption"):
        assert callable(getattr(FlowPawflowApi, name))


def test_owner_cannot_read_without_the_visitor_passphrase(api):
    flow, store, cid = api
    sid = "visitor-cookie-" + uuid.uuid4().hex  # lives only in the browser

    st = flow.enable_conv_encryption(cid, sid, session_id="vh")
    assert st["state"] == "unlocked" and st["has_pass_wrap"]
    store.append_message(cid, _user("VISITOR-SECRET-MSG"),
                         agent_name="bot", user_id=UID)

    flow.lock_conv_encryption(cid)
    assert store.encryption_status(cid)["state"] == "locked"
    blob = _disk(store, cid)
    assert "VISITOR-SECRET-MSG" not in blob        # ciphertext at rest
    assert "enc:cv1:" in blob

    # The owner, browsing the store, has no way to derive the cookie value.
    with pytest.raises((KeyUnwrapError, ValueError)):
        flow.unlock_conv_encryption(cid, "owner-guess", session_id="vh")

    # The visitor, presenting their cookie, unlocks and reads.
    assert flow.unlock_conv_encryption(cid, sid, session_id="vh") is True
    page = store.load_page(cid, limit=100, offset=0)
    msgs = page["messages"] if isinstance(page, dict) else page
    assert any(m.get("content") == "VISITOR-SECRET-MSG" for m in msgs)


def test_encryption_is_scope_bounded(api):
    flow, store, cid = api
    other = FlowPawflowApi(
        make_runtime_context(scope="user", user_id="someone-else"),
        requester_user_id="someone-else")
    with pytest.raises(Exception):
        other.enable_conv_encryption(cid, "x", session_id="y")
