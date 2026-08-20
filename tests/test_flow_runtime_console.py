"""Flow Runtime Console: queue pause, connection identity, runtime API."""

import json
import time

import pytest

from tasks import register_all_tasks
register_all_tasks()

from core import FlowFile
from core.connection import Connection, ConnectionManager


# ── Connection identity + pause semantics ────────────────────────

def test_connection_has_a_stable_identity():
    conn = Connection("a", "b", relationship="failure")
    assert conn.connection_id == "conn_a__failure__b"
    # Two relationships between the same tasks are DIFFERENT queues.
    other = Connection("a", "b", relationship="success")
    assert other.connection_id != conn.connection_id
    manager = ConnectionManager()
    manager.add_connection(conn)
    manager.add_connection(other)
    assert manager.get_by_id(conn.connection_id) is conn
    assert manager.get_by_id("conn_nope") is None
    assert conn.get_stats()["connection_id"] == conn.connection_id


def test_paused_queue_accepts_upstream_but_blocks_downstream():
    conn = Connection("a", "b")
    conn.pause()
    assert conn.enqueue(FlowFile(content=b"x")) is True   # keeps growing
    assert conn.queue_size() == 1
    assert conn.dequeue() is None                          # no consumption
    assert conn.has_processable() is False                 # scheduler view
    assert conn.get_stats()["paused"] is True
    conn.resume()
    assert conn.has_processable() is True
    ff = conn.dequeue()
    assert ff is not None and ff.get_content() == b"x"


def test_flowfiles_are_addressed_by_process_id():
    conn = Connection("a", "b")
    first, second = FlowFile(content=b"1"), FlowFile(content=b"22")
    conn.enqueue(first)
    conn.enqueue(second)
    assert conn.get_flowfile(second.process_id) is second
    assert conn.get_flowfile("missing") is None
    assert conn.remove_by_process_id(second.process_id) is True
    assert conn.remove_by_process_id(second.process_id) is False
    assert conn.queue_size() == 1 and conn.queue_bytes() == 1


# ── Scheduler is pause-aware ─────────────────────────────────────

def test_scheduler_ignores_paused_queues_source_invariants():
    source = open("engine/_continuous_exec_run.py", encoding="utf-8").read()
    assert "any(c.has_processable() for c in incoming)" in source
    assert "if not c.is_paused()" in source
    # Queue-aware tasks must not bypass the pause via peek/remove.
    assert "task.select_processable(_consumable)" in source


def test_paused_queue_stalls_a_running_flow_until_resume():
    from engine.continuous_executor import ContinuousFlowExecutor
    from engine.parser import FlowParser
    flow = FlowParser().parse({
        "id": "pause_flow", "name": "p", "version": "1.0.0",
        "tasks": {"a": {"type": "log", "parameters": {"message": "in"}},
                  "b": {"type": "log", "parameters": {"message": "out"}}},
        "relations": [{"from": "a", "to": "b", "type": "success"}],
        "entries": ["a"], "exits": [], "parameters": {},
        "variables": {}, "groups": {},
    })
    executor = ContinuousFlowExecutor(flow, enable_checkpoints=False,
                                      schedule_interval=0.01)
    conn = executor.connections.get_connection("a", "b")
    conn.pause()
    executor.start()
    try:
        conn.enqueue(FlowFile(content=b"held"))
        time.sleep(0.4)
        assert conn.queue_size() == 1          # b never consumed it
        conn.resume()
        deadline = time.time() + 5
        while conn.queue_size() and time.time() < deadline:
            time.sleep(0.05)
        assert conn.queue_size() == 0          # resumed and drained
    finally:
        executor.stop()


# ── Runtime API actions ──────────────────────────────────────────

@pytest.fixture
def runtime(monkeypatch):
    """A fake running instance with one real Connection."""
    conn = Connection("gen", "save", relationship="success")
    manager = ConnectionManager()
    manager.add_connection(conn)

    class _Task:
        TYPE = "inferLLM"
        NAME = "Infer"
        _original_config = {"service": "video", "api_key": "${my_secret}",
                            "_user_id": "internal"}
        config = _original_config
        def get_parameter_schema(self):
            return {"service": {"type": "string"}}

    class _Executor:
        is_running = True
        connections = manager
        _provenance = None
        _flow = None
        checkpoints = []
        controls = []
        def get_task(self, tid):
            return _Task() if tid == "gen" else None
        def get_all_task_states(self):
            return {"gen": {"state": "running", "in_flight": 1}}
        def stop_task(self, tid):
            self.controls.append(("stop", tid)); return True
        def start_task(self, tid):
            self.controls.append(("start", tid)); return True
        def restart_task(self, tid):
            self.controls.append(("restart", tid)); return True
        def _save_checkpoint(self):
            self.checkpoints.append(time.time())

    executor = _Executor()
    from core.executor_registry import ExecutorRegistry
    from core.deployment_registry import DeploymentRegistry

    class _Inst:
        owner = "u1"

    class _DReg:
        def get(self, iid):
            return _Inst() if iid == "inst1" else None
    monkeypatch.setattr(ExecutorRegistry, "get_instance", classmethod(
        lambda cls: type("R", (), {"get": lambda s, i: executor if i == "inst1" else None})()))
    monkeypatch.setattr(DeploymentRegistry, "get_instance",
                        classmethod(lambda cls: _DReg()))
    return executor, conn


def _call(action, body, user_id="u1"):
    from tasks.ai.actions.flow_runtime import _handle_flow_runtime
    ff = FlowFile()
    result = _handle_flow_runtime(None, action, body, None, user_id, ff)
    assert result is not None
    return json.loads(ff.get_content().decode()), ff


def test_task_control_and_ownership(runtime):
    executor, _ = runtime
    data, _ff = _call("flow_runtime_task_control",
                      {"instance_id": "inst1", "task_id": "gen",
                       "operation": "restart"})
    assert data["ok"] and executor.controls == [("restart", "gen")]
    data, ff = _call("flow_runtime_task_control",
                     {"instance_id": "inst1", "task_id": "gen",
                      "operation": "restart"}, user_id="intruder")
    assert data["error"] == "Permission denied"
    assert ff.get_attribute("http.response.status") == "403"


def test_task_details_never_resolve_secrets(runtime):
    data, _ = _call("flow_runtime_task_details",
                    {"instance_id": "inst1", "task_id": "gen"})
    # Raw reference intact, internal runtime keys elided.
    assert data["config"]["api_key"] == "${my_secret}"
    assert "_user_id" not in data["config"]
    assert data["state"]["in_flight"] == 1


def test_queue_list_paginates_server_side(runtime):
    _, conn = runtime
    for i in range(7):
        conn.enqueue(FlowFile(content=b"x" * (i + 1)))
    data, _ = _call("flow_runtime_queue_list",
                    {"instance_id": "inst1",
                     "connection_id": conn.connection_id,
                     "offset": 5, "limit": 50})
    assert data["stats"]["queue_size"] == 7
    assert len(data["flowfiles"]) == 2          # page, not the whole queue
    assert data["flowfiles"][0]["process_id"]


def test_queue_control_pause_resume_clear_without_task_reset(runtime):
    executor, conn = runtime
    conn.enqueue(FlowFile(content=b"x"))
    data, _ = _call("flow_runtime_queue_control",
                    {"instance_id": "inst1",
                     "connection_id": conn.connection_id,
                     "operation": "pause"})
    assert data["stats"]["paused"] is True
    data, _ = _call("flow_runtime_queue_control",
                    {"instance_id": "inst1",
                     "connection_id": conn.connection_id,
                     "operation": "clear"})
    assert data["stats"]["queue_size"] == 0
    # Empty queue means EMPTY QUEUE: no implicit task reset...
    assert executor.controls == []
    # ...and the checkpoint is written immediately so a crash cannot
    # resurrect the dropped FlowFiles.
    assert len(executor.checkpoints) == 1


def test_queue_item_race_is_a_normal_answer(runtime):
    _, conn = runtime
    ff = FlowFile(content=b"data")
    conn.enqueue(ff)
    data, _ = _call("flow_runtime_queue_item",
                    {"instance_id": "inst1",
                     "connection_id": conn.connection_id,
                     "process_id": ff.process_id})
    assert data["content"]["available"] is True
    assert data["content"]["text"] == "data"
    conn.dequeue()   # consumed between listing and click
    data, ff2 = _call("flow_runtime_queue_item",
                      {"instance_id": "inst1",
                       "connection_id": conn.connection_id,
                       "process_id": ff.process_id})
    assert data == {"no_longer_queued": True}
    assert not ff2.get_attribute("http.response.status")   # not an error


def test_flowfile_drop_by_process_id_checkpoints(runtime):
    executor, conn = runtime
    keep, drop = FlowFile(content=b"keep"), FlowFile(content=b"drop")
    conn.enqueue(keep)
    conn.enqueue(drop)
    data, _ = _call("flow_runtime_flowfile_drop",
                    {"instance_id": "inst1",
                     "connection_id": conn.connection_id,
                     "process_id": drop.process_id})
    assert data["ok"] is True and data["stats"]["queue_size"] == 1
    assert conn.get_flowfile(keep.process_id) is keep
    assert executor.checkpoints


def test_binary_and_large_content_previews_are_metadata_only(runtime):
    _, conn = runtime
    binary = FlowFile(content=b"\x00\x01\x02")
    conn.enqueue(binary)
    data, _ = _call("flow_runtime_queue_item",
                    {"instance_id": "inst1",
                     "connection_id": conn.connection_id,
                     "process_id": binary.process_id})
    assert data["content"]["available"] is False
    assert data["content"]["reason"] == "binary"


def test_content_download_route_streams(runtime):
    from tasks.ai.actions.flow_runtime import handle_content_download
    _, conn = runtime
    ff = FlowFile(content=b"stream-me")
    ff.set_attribute("filename", "out.txt")
    conn.enqueue(ff)

    class _Req:
        path_params = {"instance_id": "inst1",
                       "connection_id": conn.connection_id,
                       "process_id": ff.process_id}
        auth_user_id = "u1"
        auth_role = ""
        result = None
        def complete(self, status, headers, body):
            self.result = ("json", status, body)
        def complete_stream(self, status, headers, stream):
            self.result = ("stream", status, headers, b"".join(stream))
    req = _Req()
    handle_content_download(req)
    kind, status, headers, body = req.result
    assert (kind, status) == ("stream", 200)
    assert body == b"stream-me"
    assert 'filename="out.txt"' in headers["Content-Disposition"]
    # Consumed → 404 with the normal message.
    conn.dequeue()
    req2 = _Req()
    handle_content_download(req2)
    assert req2.result[1] == 404


def test_route_registration_is_idempotent():
    from tasks.ai.actions.flow_runtime import register_flow_runtime_routes

    class _Listener:
        def __init__(self):
            self.routes = []
        def get_routes(self):
            return [{"method": m, "pattern": p} for m, p, *_ in self.routes]
        def register_route(self, method, pattern, owner, callback=None,
                           **kw):
            self.routes.append((method, pattern, owner, callback))
    listener = _Listener()
    register_flow_runtime_routes(listener)
    register_flow_runtime_routes(listener)
    assert len(listener.routes) == 1


# ── UI source invariants ─────────────────────────────────────────

def _text(relative):
    return open(relative, encoding="utf-8").read()


def test_viewer_edges_read_as_queues_and_pause_kills_the_current():
    source = _text("tasks/io/chat_ui/flow_graph.html")
    # Queue label always: relationship · count · bytes.
    assert "fmtBytes(e?.queue_bytes || 0)" in source
    # Paused: dashed grey, no animation; the whole queue state rides data.
    assert "const animated = queueSize > 0 && !paused;" in source
    assert "strokeDasharray: paused ? '4 4' : undefined" in source
    assert "connectionId: e?.connection_id || ''" in source


def test_viewer_has_runtime_console_surfaces():
    source = _text("tasks/io/chat_ui/flow_graph.html")
    # Right-click task menu with start/stop/restart + configuration drawer.
    assert "flow_runtime_task_control" in source
    assert "function TaskDrawer" in source
    # Edge click opens the Queue Inspector: pagination, pause, empty,
    # FlowFile inspection and streaming download.
    assert "function QueueDrawer" in source
    assert "flow_runtime_queue_list" in source
    assert "This operation cannot be undone." in source
    assert "/flowfiles/${encodeURIComponent(pid)}/content" in source
    assert "FlowFile is no longer queued." in source
    # Runtime ops only on a RUNNING root instance (never templates).
    assert "graphStack.length === 1 && !!rootGraph.instance_id && isRunning" in source


def test_openspace_stage_mirrors_pause_and_backpressure():
    source = _text("tasks/io/chat_ui/openspace.js")
    assert "rec.paused = !!e.paused;" in source
    assert "rec.active = !rec.paused" in source
