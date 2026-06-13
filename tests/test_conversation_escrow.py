"""Phase 7: optional recovery (escrow) wrap.

A separate recovery passphrase can unlock a conversation when the primary is
lost. Independent secret, stored in the container's escrow slot.
"""

import pytest

from core.conversation_store import ConversationLockedError, ConversationStore
import core.key_vault as key_vault
from core.key_vault import KeyUnwrapError

UID = "alice"
PRIMARY = "primary-pass"
RECOVERY = "recovery-pass"


@pytest.fixture(autouse=True)
def _reset():
    ConversationStore.reset()
    key_vault._reset_for_tests()
    yield
    ConversationStore.reset()
    key_vault._reset_for_tests()


@pytest.fixture
def enc(tmp_path):
    s = ConversationStore(store_dir=str(tmp_path / "c"))
    cid = s.generate_id()
    s.save(cid, [], user_id=UID)
    s.append_message(
        cid, {"role": "user", "content": "SECRET", "msg_id": "m1",
              "source": {"type": "user", "target_agent": "bot"}},
        agent_name="bot", user_id=UID)
    s.enable_encryption(cid, PRIMARY, session_id="sess-1")
    return s, cid


def test_set_escrow_then_status(enc):
    s, cid = enc
    assert s.encryption_status(cid)["has_escrow"] is False
    st = s.set_conv_escrow(cid, RECOVERY)
    assert st["has_escrow"] is True


def test_recover_with_escrow_after_losing_primary(enc):
    s, cid = enc
    s.set_conv_escrow(cid, RECOVERY)
    s.lock_encryption(cid)
    # primary still works...
    assert s.unlock_encryption(cid, PRIMARY) is True
    s.lock_encryption(cid)
    # ...and so does the recovery passphrase, independently
    assert s.unlock_encryption_with_recovery(cid, RECOVERY) is True
    assert s.encryption_status(cid)["state"] == "unlocked"
    # content is readable via the recovery-unlocked DEK
    page = s.load_page(cid, limit=10, offset=0)
    msgs = page["messages"] if isinstance(page, dict) else page
    assert any(m.get("content") == "SECRET" for m in msgs)


def test_wrong_recovery_rejected(enc):
    s, cid = enc
    s.set_conv_escrow(cid, RECOVERY)
    s.lock_encryption(cid)
    with pytest.raises(KeyUnwrapError):
        s.unlock_encryption_with_recovery(cid, "nope")


def test_recover_without_escrow_raises(enc):
    s, cid = enc
    s.lock_encryption(cid)
    with pytest.raises(KeyUnwrapError):
        s.unlock_encryption_with_recovery(cid, RECOVERY)  # no escrow set


def test_set_escrow_requires_unlocked(enc):
    s, cid = enc
    s.lock_encryption(cid)
    with pytest.raises(ConversationLockedError):
        s.set_conv_escrow(cid, RECOVERY)


def test_remove_escrow(enc):
    s, cid = enc
    s.set_conv_escrow(cid, RECOVERY)
    st = s.remove_conv_escrow(cid)
    assert st["has_escrow"] is False


def test_escrow_independent_of_primary(enc):
    # changing the primary passphrase must not affect recovery
    s, cid = enc
    s.set_conv_escrow(cid, RECOVERY)
    s.change_encryption_passphrase(cid, PRIMARY, "new-primary")
    s.lock_encryption(cid)
    assert s.unlock_encryption_with_recovery(cid, RECOVERY) is True
