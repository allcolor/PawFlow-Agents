import json

from core import FlowFile
from core.conversation_store import ConversationStore
from core.file_store import FileStore
from tasks.ai.actions.files_fs import _handle_files_fs


def _payload(flowfile):
    return json.loads(flowfile.get_content().decode("utf-8"))


def test_delete_file_uses_filestore_metadata_not_transcript_text(tmp_path, monkeypatch):
    conv_store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    conv_store.save("conv1", [
        {"role": "user", "content": "no file reference here", "msg_id": "m1"},
    ], user_id="alice")

    file_store = FileStore(base_dir=str(tmp_path / "files"))
    monkeypatch.setattr(FileStore, "_instance", file_store)
    file_id = file_store.store(
        "report.txt", b"content", "text/plain",
        conversation_id="conv1", user_id="alice")

    ff = FlowFile(content=b"")
    result = _handle_files_fs(
        None, "delete_file",
        {"conversation_id": "conv1", "file_id": file_id},
        conv_store, "alice", ff)

    assert result == [ff]
    assert _payload(ff) == {"ok": True, "file_id": file_id}
    assert not file_store.exists(file_id)


def test_delete_file_rejects_other_conversation_file(tmp_path, monkeypatch):
    conv_store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    conv_store.save("conv1", [], user_id="alice")
    conv_store.save("conv2", [], user_id="alice")

    file_store = FileStore(base_dir=str(tmp_path / "files"))
    monkeypatch.setattr(FileStore, "_instance", file_store)
    file_id = file_store.store(
        "report.txt", b"content", "text/plain",
        conversation_id="conv2", user_id="alice")

    ff = FlowFile(content=b"")
    _handle_files_fs(
        None, "delete_file",
        {"conversation_id": "conv1", "file_id": file_id},
        conv_store, "alice", ff)

    assert ff.get_attribute("http.response.status") == "403"
    assert _payload(ff)["error"] == "File not in this conversation"
    assert file_store.exists(file_id)


def test_delete_files_deletes_only_accessible_conversation_files(tmp_path, monkeypatch):
    conv_store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    conv_store.save("conv1", [], user_id="alice")
    conv_store.save("conv2", [], user_id="alice")

    file_store = FileStore(base_dir=str(tmp_path / "files"))
    monkeypatch.setattr(FileStore, "_instance", file_store)
    keep_id = file_store.store(
        "keep.txt", b"keep", "text/plain",
        conversation_id="conv2", user_id="alice")
    delete_a = file_store.store(
        "a.txt", b"a", "text/plain",
        conversation_id="conv1", user_id="alice")
    delete_b = file_store.store(
        "b.txt", b"b", "text/plain",
        conversation_id="conv1", user_id="alice")

    ff = FlowFile(content=b"")
    _handle_files_fs(
        None, "delete_files",
        {"conversation_id": "conv1", "file_ids": [delete_a, keep_id, delete_b]},
        conv_store, "alice", ff)

    payload = _payload(ff)
    assert payload["ok"] is True
    assert payload["deleted"] == 2
    assert set(payload["file_ids"]) == {delete_a, delete_b}
    assert payload["skipped"] == [{"file_id": keep_id, "error": "File not in this conversation"}]
    assert not file_store.exists(delete_a)
    assert not file_store.exists(delete_b)
    assert file_store.exists(keep_id)


def test_filestore_list_hides_transient_stt_and_tts(tmp_path):
    file_store = FileStore(base_dir=str(tmp_path / "files"))
    visible_id = file_store.store(
        "visible.txt", b"visible", "text/plain",
        conversation_id="conv1", user_id="alice")
    file_store.store(
        "speech.wav", b"wav", "audio/wav",
        conversation_id="conv1", user_id="alice", ttl=300,
        category="webchat_stt")
    file_store.store(
        "speech.mp3", b"mp3", "audio/mpeg",
        conversation_id="conv1", user_id="alice", ttl=300,
        category="voice_clone_tts")

    rows = file_store.list_files(user_id="alice", conversation_id="conv1")

    assert [row["file_id"] for row in rows] == [visible_id]


def test_fs_read_file_resolves_filesystem_service(monkeypatch):
    class DummyFs:
        def read_file(self, path):
            assert path == "image.png"
            return b"png-bytes"

    import core.handlers._fs_base as fs_base

    monkeypatch.setattr(
        fs_base, "find_fs_service",
        lambda user_id, service: DummyFs() if (user_id, service) == ("alice", "relay") else None,
    )

    ff = FlowFile(content=b"")

    result = _handle_files_fs(
        None, "fs_read_file",
        {"service": "relay", "path": "image.png"},
        None, "alice", ff)

    assert result == [ff]
    assert _payload(ff) == {"content": "png-bytes", "encoding": "utf-8", "size": 9}


def test_fs_list_services_returns_only_conversation_linked_relays(monkeypatch):
    class ServiceDef:
        def __init__(self, service_id, service_type="relay", scope="user"):
            self.service_id = service_id
            self.service_type = service_type
            self.scope = scope

    class Registry:
        def resolve_definition(self, service_id, *, user_id="", conv_id=""):
            assert user_id == "alice"
            assert conv_id == "conv1"
            if service_id == "linked-relay":
                return ServiceDef("linked-relay")
            if service_id == "linked-tool-relay":
                return ServiceDef("linked-tool-relay", "toolRelay")
            if service_id == "unlinked-relay":
                return ServiceDef("unlinked-relay")
            return None

    import core.relay_bindings as relay_bindings
    import core.service_registry as service_registry

    monkeypatch.setattr(service_registry.ServiceRegistry, "get_instance", lambda: Registry())
    monkeypatch.setattr(relay_bindings, "get_linked", lambda cid, agent="": ["linked-relay", "linked-tool-relay"])

    ff = FlowFile(content=b"")
    result = _handle_files_fs(
        None, "fs_list_services",
        {"conversation_id": "conv1"},
        None, "alice", ff)

    assert result == [ff]
    assert _payload(ff) == {"services": [{"id": "linked-relay", "type": "relay", "scope": "user"}]}


def test_fs_list_services_without_conversation_returns_empty():
    ff = FlowFile(content=b"")
    result = _handle_files_fs(
        None, "fs_list_services", {}, None, "alice", ff)

    assert result == [ff]
    assert _payload(ff) == {"services": []}


def test_flow_runtime_graph_loads_repository_template(tmp_path, monkeypatch):
    import core.paths as paths
    from core.repository import ScopedRepository

    monkeypatch.setattr(paths, "REPOSITORY_DIR", tmp_path / "repository")
    ScopedRepository.reset()
    ScopedRepository.instance().create_flow(
        "default.demo_flow:1.0.0", "global", {
            "id": "demo-flow",
            "name": "demo_flow",
            "tasks": {
                "start": {"type": "generateFlowFile", "parameters": {}},
                "done": {"type": "logMessage", "parameters": {}},
            },
            "relations": [{"from": "start", "to": "done", "type": "success"}],
        })
    ff = FlowFile(content=b"")

    result = _handle_files_fs(
        None, "flow_runtime_graph", {"template_id": "default.demo_flow:1.0.0"},
        None, "alice", ff)

    payload = _payload(ff)
    assert result == [ff]
    assert payload["flow_name"] == "demo_flow"
    assert set(payload["nodes"]) == {"start", "done"}
    assert payload["edges"] == [{
        "source": "start",
        "target": "done",
        "relationship": "success",
        "queue_size": 0,
        "max_queue": 10000,
        "backpressured": False,
    }]


def test_flow_runtime_graph_includes_subflow_groups(tmp_path):
    from tasks.ai.actions.files_fs import _static_flow_graph

    child = tmp_path / "child.json"
    child.write_text(json.dumps({
        "id": "child",
        "version": "1.0.0",
        "tasks": {},
        "relations": [],
    }), encoding="utf-8")
    nodes, edges = _static_flow_graph({
        "tasks": {"trigger": {"type": "cronTrigger", "parameters": {}}},
        "groups": {
            "child_group": {
                "name": "Child group",
                "flow_ref": {"path": str(child), "version": "1.0.0"},
                "port_mapping": {"input": {"port_task_id": "in_port"}},
            },
        },
        "relations": [{"from": "trigger", "to": "child_group", "type": "success"}],
    })

    assert nodes["child_group"]["type"] == "subflow"
    assert nodes["child_group"]["subflow_ref"] == {"path": str(child), "version": "1.0.0"}
    assert edges == [{
        "source": "trigger",
        "target": "child_group",
        "relationship": "success",
        "queue_size": 0,
        "max_queue": 10000,
        "backpressured": False,
    }]
