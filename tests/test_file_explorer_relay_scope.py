"""Conversation-scoped explorer service resolution and authorization."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core import FlowFile
from core.conversation_store import ConversationStore
from core.service_registry import ServiceRegistry
from tasks.ai.actions.files_fs import _handle_files_fs


FS_ACTIONS = [
    "fs_list_dir", "fs_read_file", "fs_write_file", "fs_delete", "fs_mkdir",
    "fs_rename", "fs_search", "fs_copy", "fs_copy_to_store", "fs_exec",
    "fs_zip_dir",
]


@pytest.fixture
def explorer(tmp_path, monkeypatch):
    import core.conversation_access as access
    import core.relay_bindings as bindings
    import core.remote_fs_bindings as remote

    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    store.save("law", [], user_id="alice")
    store.save("other", [], user_id="bob")
    store.set_extra("law", "relay_bindings", {
        "linked": {"*": ["permisWS", "shared-relay"]},
        "default": {"*": "permisWS"},
    })
    store.set_extra("law", "collaborators", [
        {"user_id": "reader", "role": "read", "status": "accepted"},
        {"user_id": "writer", "role": "write", "status": "accepted"},
        {"user_id": "pending", "role": "write", "status": "pending"},
    ])
    monkeypatch.setattr(bindings, "_get_store", lambda: store)
    monkeypatch.setattr(remote, "list_tool_filesystems", lambda *args: [])
    monkeypatch.setattr(access, "user_exists", lambda uid: True)

    relay = Mock()
    relay.list_dir.return_value = [
        SimpleNamespace(name="plans", kind="directory", size=0, modified=""),
    ]
    relay.stat.return_value = SimpleNamespace(size=5)
    relay.read_file.return_value = b"plans"
    relay.copy_file.return_value = {"size": 5}
    relay.search.return_value = ["plans"]
    relay.exec.return_value = {"stdout": "plans", "returncode": 0}
    definitions = {
        ("conv", "law", "permisWS"): SimpleNamespace(
            service_id="permisWS", service_type="relay", scope="conv"),
        ("user", "alice", "shared-relay"): SimpleNamespace(
            service_id="shared-relay", service_type="relay", scope="user"),
        ("user", "alice", "unlinked"): SimpleNamespace(
            service_id="unlinked", service_type="relay", scope="user"),
        ("conv", "other", "foreign"): SimpleNamespace(
            service_id="foreign", service_type="relay", scope="conv"),
    }
    # Keep the real registry scope chain and filesystem binding checks.
    registry = ServiceRegistry()
    registry.get_definition = Mock(side_effect=lambda scope, sid, name:
                                   definitions.get((scope, sid, name)))
    registry.get_live_instance = Mock(side_effect=lambda scope, sid, name:
                                     relay if (scope, sid, name) in definitions else None)
    monkeypatch.setattr(ServiceRegistry, "get_instance", lambda: registry)
    return store, relay, registry


def call(explorer, action, *, user="alice", **body):
    ff = FlowFile(content=b"")
    _handle_files_fs(None, action, body, explorer[0], user, ff)
    return json.loads(ff.get_content()), ff.get_attribute("http.response.status")


def test_dropdown_conversation_relay_can_be_listed(explorer):
    services, _ = call(explorer, "fs_list_services", conversation_id="law")
    assert services["services"][0] == {
        "id": "permisWS", "type": "relay", "scope": "conv",
    }
    payload, _ = call(explorer, "fs_list_dir", conversation_id="law",
                      service=services["services"][0]["id"], path=".")
    assert payload == {"entries": [
        {"name": "plans", "kind": "directory", "size": 0, "modified": ""},
    ]}
    explorer[1].list_dir.assert_called_once_with(".")
    explorer[2].get_live_instance.assert_called_once_with("conv", "law", "permisWS")


@pytest.mark.parametrize("service", ["permisWS", "PERMISWS", "shared-relay"])
def test_linked_conversation_and_user_relays_resolve(explorer, service):
    payload, _ = call(explorer, "fs_read_file", conversation_id="law",
                      service=service, path="plan.txt")
    assert payload == {"content": "plans", "encoding": "utf-8", "size": 5}
    explorer[1].set_user_id.assert_called_once_with("alice")


@pytest.mark.parametrize("action", FS_ACTIONS)
def test_all_operations_pass_conversation_to_resolver(explorer, monkeypatch, action):
    import core.handlers._fs_base as fs_base

    resolver = Mock(return_value=None)
    monkeypatch.setattr(fs_base, "find_fs_service", resolver)
    call(explorer, action, conversation_id="law", service="permisWS",
         source_service="permisWS", dest_service="shared-relay")
    expected = ["permisWS", "shared-relay"] if action == "fs_copy" else ["permisWS"]
    assert [c.args for c in resolver.call_args_list] == [
        ("alice", name, "law") for name in expected
    ]


@pytest.mark.parametrize("action", FS_ACTIONS)
def test_operations_require_conversation(explorer, action):
    payload, status = call(explorer, action, service="permisWS",
                           source_service="permisWS", dest_service="shared-relay")
    assert payload == {"error": "conversation_id is required"}
    assert status == "400"
    explorer[2].get_live_instance.assert_not_called()


@pytest.mark.parametrize("action", ["fs_list_services", *FS_ACTIONS])
@pytest.mark.parametrize("user", ["stranger", "pending", ""])
def test_conversation_access_is_checked_before_resolution(explorer, action, user):
    payload, status = call(explorer, action, user=user, conversation_id="law",
                           service="permisWS", source_service="permisWS",
                           dest_service="shared-relay")
    assert (payload, status) == ({"error": "Conversation not found"}, "404")
    explorer[2].get_definition.assert_not_called()
    explorer[2].get_live_instance.assert_not_called()


@pytest.mark.parametrize("action", [
    "fs_list_dir", "fs_read_file", "fs_search",
    "fs_write_file", "fs_delete", "fs_mkdir", "fs_rename", "fs_copy",
    "fs_copy_to_store", "fs_exec", "fs_zip_dir",
])
def test_read_only_collaborator_cannot_drive_relay(explorer, action):
    payload, status = call(explorer, action, user="reader", conversation_id="law",
                           service="permisWS", source_service="permisWS",
                           dest_service="shared-relay")
    assert (payload, status) == ({"error": "Conversation not found"}, "404")
    explorer[2].get_live_instance.assert_not_called()


def test_writer_can_read_and_write_live_relay(explorer):
    payload, _ = call(explorer, "fs_read_file", user="reader",
                      conversation_id="law", service="permisWS", path="plan.txt")
    assert payload == {"error": "Conversation not found"}
    payload, _ = call(explorer, "fs_read_file", user="writer",
                      conversation_id="law", service="permisWS", path="plan.txt")
    assert payload["content"] == "plans"
    payload, _ = call(explorer, "fs_write_file", user="writer",
                      conversation_id="law", service="permisWS",
                      path="plan.txt", content="updated")
    assert payload == {"ok": True, "size": 7}
    explorer[1].write_file.assert_called_once_with("plan.txt", b"updated")


@pytest.mark.parametrize("service", ["unlinked", "foreign", "missing"])
def test_unlinked_or_foreign_relay_never_reaches_registry(explorer, service):
    payload, status = call(explorer, "fs_list_dir", conversation_id="law",
                           service=service, path=".")
    assert (payload, status) == ({"error": "Filesystem service not found"}, "400")
    explorer[2].get_live_instance.assert_not_called()
    explorer[1].list_dir.assert_not_called()


@pytest.mark.parametrize("blocked_side", ["source_service", "dest_service"])
def test_copy_rejects_unlinked_source_or_destination(explorer, blocked_side):
    body = {"source_service": "permisWS", "dest_service": "shared-relay"}
    body[blocked_side] = "unlinked"
    payload, status = call(explorer, "fs_copy", conversation_id="law",
                           source_path="plan.txt", dest_path="copy.txt", **body)
    assert (payload, status) == ({"error": "Source or dest service not found"}, "400")
    explorer[1].copy_file.assert_not_called()


@pytest.mark.parametrize("action", ["fs_list_services", *FS_ACTIONS])
def test_unknown_conversation_is_denied_before_resolution(explorer, action):
    payload, status = call(explorer, action, conversation_id="missing",
                           service="permisWS", source_service="permisWS",
                           dest_service="shared-relay")
    assert (payload, status) == ({"error": "Conversation not found"}, "404")
    explorer[2].get_definition.assert_not_called()


@pytest.mark.parametrize("user,conversation_id,status", [
    ("stranger", "law", 404), ("reader", "law", 404),
    ("pending", "law", 404), ("alice", "other", 404),
    ("alice", "missing", 404), ("alice", "", 400),
])
def test_stream_upload_denied_before_lookup_or_body(explorer, monkeypatch,
                                                   user, conversation_id, status):
    from services import _http_upload_stream as upload
    from tests.test_streaming_upload import _UploadHandler

    monkeypatch.setattr(ConversationStore, "instance", lambda: explorer[0])
    handler = _UploadHandler(b"private upload")
    upload.handle_upload_stream(
        handler, 14, SimpleNamespace(username=user),
        f"service=permisWS&path=plan.txt&conversation_id={conversation_id}")
    assert handler.status == status
    assert handler.rfile.tell() == 0
    explorer[2].get_definition.assert_not_called()
    explorer[1].write_file_stream.assert_not_called()
    payload = json.loads(handler.wfile.getvalue())
    assert payload["error"] == (
        "conversation_id is required" if status == 400 else "Conversation not found")


@pytest.mark.parametrize("user", ["alice", "writer"])
def test_stream_upload_uses_authorized_tile_relay(explorer, monkeypatch, user):
    from services import _http_upload_stream as upload
    from tests.test_streaming_upload import _UploadHandler

    monkeypatch.setattr(ConversationStore, "instance", lambda: explorer[0])
    received = []

    def write_stream(path, chunks, expected_size):
        received.extend(chunks)
        return sum(map(len, received))

    explorer[1].write_file_stream.side_effect = write_stream
    handler = _UploadHandler(b"plans")
    upload.handle_upload_stream(
        handler, 5, SimpleNamespace(username=user),
        "service=permisWS&path=plan.txt&conversation_id=law")
    assert handler.status == 200
    assert b"".join(received) == b"plans"
    explorer[2].get_live_instance.assert_called_once_with("conv", "law", "permisWS")
    explorer[1].set_user_id.assert_called_once_with(user)


def test_stream_upload_rejects_unlinked_relay_without_reading(explorer, monkeypatch):
    from services import _http_upload_stream as upload
    from tests.test_streaming_upload import _UploadHandler

    monkeypatch.setattr(ConversationStore, "instance", lambda: explorer[0])
    handler = _UploadHandler(b"plans")
    upload.handle_upload_stream(
        handler, 5, SimpleNamespace(username="alice"),
        "service=unlinked&path=plan.txt&conversation_id=law")
    assert handler.status == 400
    assert handler.rfile.tell() == 0
    explorer[2].get_live_instance.assert_not_called()


def test_foreign_conversation_id_cannot_select_its_relay(explorer):
    payload, status = call(explorer, "fs_list_dir", conversation_id="other",
                           service="foreign", path=".")
    assert (payload, status) == ({"error": "Conversation not found"}, "404")
    explorer[2].get_live_instance.assert_not_called()
