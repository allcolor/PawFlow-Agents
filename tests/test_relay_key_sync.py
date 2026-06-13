"""Phase 5b: server<->relay key delivery protocol, end-to-end via a fake channel.

Wires the server-side orchestration (core.relay_key_sync) to the relay-side
service (core.relay_key_service) through an in-process channel that performs the
same request/response the WS tunnel would. This validates the whole push-at-
connect / need-DEK / relay-gone-purge state machine without a live socket; only
the socket adapter itself is left to integration.
"""

import base64

import pytest

from core import relay_key_store as ks
from core import relay_key_sync as sync
from core.conversation_store import ConversationStore
import core.key_vault as key_vault
from core.key_vault import get_key_vault
from core.relay_key_service import RelayKeyService

UID = "alice"
PW = "relay-pass"


class FakeChannel:
    """In-process stand-in for the relay control tunnel: forwards requests
    straight to the relay-side service."""
    def __init__(self, service, connection_id):
        self._service = service
        self.connection_id = connection_id

    def request(self, method, params):
        return self._service.handle_request(method, params)


@pytest.fixture(autouse=True)
def _reset():
    ConversationStore.reset()
    key_vault._reset_for_tests()
    yield
    ConversationStore.reset()
    key_vault._reset_for_tests()


@pytest.fixture
def relay(tmp_path):
    ks.init_relay_key(tmp_path, PW)
    svc = RelayKeyService(tmp_path)
    svc.unlock(PW)
    return svc


@pytest.fixture
def enrolled(tmp_path, relay):
    """A store with an encrypted conv bound to the unlocked relay."""
    store = ConversationStore(store_dir=str(tmp_path / "c"))
    cid = store.generate_id()
    store.save(cid, [], user_id=UID)
    store.enable_encryption(cid, "pw", session_id="sess-1")
    pub_b64 = relay.pubkey_response()["pubkey"]
    store.set_conv_relay(cid, pub_b64)
    return store, cid, relay


def test_push_at_connect_unlocks_bound_conv(enrolled):
    store, cid, relay = enrolled
    store.lock_encryption(cid)
    assert store.encryption_status(cid)["state"] == "locked"

    ch = FakeChannel(relay, "relay-conn-1")
    n = sync.push_at_connect(ch, UID, store)
    assert n == 1
    assert store.encryption_status(cid)["state"] == "unlocked"


def test_relay_disconnect_relocks(enrolled):
    store, cid, relay = enrolled
    store.lock_encryption(cid)
    ch = FakeChannel(relay, "relay-conn-1")
    sync.push_at_connect(ch, UID, store)
    assert store.encryption_status(cid)["state"] == "unlocked"

    sync.on_relay_disconnect("relay-conn-1")
    assert store.encryption_status(cid)["state"] == "locked"


def test_need_dek_pull_single(enrolled):
    store, cid, relay = enrolled
    store.lock_encryption(cid)
    ch = FakeChannel(relay, "relay-conn-2")
    assert sync.need_dek(ch, cid, store) is True
    assert store.encryption_status(cid)["state"] == "unlocked"


def test_locked_relay_serves_nothing(enrolled):
    store, cid, relay = enrolled
    store.lock_encryption(cid)
    relay.lock()  # relay's private key dropped from RAM
    ch = FakeChannel(relay, "relay-conn-3")
    assert sync.push_at_connect(ch, UID, store) == 0
    assert store.encryption_status(cid)["state"] == "locked"


def test_push_ignores_conv_bound_to_other_key(tmp_path, relay):
    # A conv bound to a DIFFERENT relay key must not be unsealed by this relay.
    store = ConversationStore(store_dir=str(tmp_path / "c"))
    cid = store.generate_id()
    store.save(cid, [], user_id=UID)
    store.enable_encryption(cid, "pw", session_id="s")
    other_priv, other_pub = __import__(
        "core.relay_keywrap", fromlist=["generate_relay_keypair"]
    ).generate_relay_keypair()
    store.set_conv_relay(cid, base64.b64encode(other_pub).decode())
    store.lock_encryption(cid)

    ch = FakeChannel(relay, "relay-conn-4")
    assert sync.push_at_connect(ch, UID, store) == 0
    assert store.encryption_status(cid)["state"] == "locked"


def test_relay_service_request_dispatch(relay):
    # key_pubkey_get + key_unseal handler contract
    pk = relay.handle_request("key_pubkey_get", {})
    assert pk["ok"] and pk["key_id"] and pk["pubkey"]
    relay.lock()
    assert relay.handle_request("key_pubkey_get", {})["ok"] is False
    assert relay.handle_request("bogus", {})["ok"] is False
