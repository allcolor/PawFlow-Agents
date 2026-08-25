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


def test_connection_manager_builds_relation_queue_configuration():
    manager = ConnectionManager()
    manager.build_from_flow({"relations": [{
        "from": "a", "to": "b", "type": "success",
        "max_queue_size": 12, "max_queue_bytes": 3456,
        "flowfile_ttl_seconds": 78, "prioritizer": "fifo",
        "priority_attribute": "urgency",
    }]})
    conn = manager.get_by_id("conn_a__success__b")
    assert conn is not None
    assert conn.max_queue_size == 12
    assert conn.max_queue_bytes == 3456
    assert conn.flowfile_ttl_seconds == 78
    assert conn.get_stats()["prioritizer"] == "fifo"
    assert conn.get_stats()["priority_attribute"] == "urgency"


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


# ── Safe whole-flow hot-swap ─────────────────────────────────────

def _hot_swap_flow(message="old", relations=None):
    from engine.parser import FlowParser
    return FlowParser.parse({
        "id": "hot_swap", "name": "Hot swap", "version": "1.0.0",
        "tasks": {
            "a": {"type": "log", "parameters": {"message": message}},
            "b": {"type": "log", "parameters": {"message": "b"}},
        },
        "relations": relations if relations is not None else [
            {"from": "a", "to": "b", "type": "success"},
            {"from": "a", "to": "b", "type": "failure"},
        ],
        "entries": ["a"], "exits": [], "parameters": {},
        "variables": {}, "groups": {},
    })


def test_update_flow_preserves_each_queue_by_connection_id_and_pause_state():
    from engine.continuous_executor import ContinuousFlowExecutor

    executor = ContinuousFlowExecutor(_hot_swap_flow(), enable_checkpoints=False)
    executor.stop()
    success = executor.connections.get_by_id("conn_a__success__b")
    failure = executor.connections.get_by_id("conn_a__failure__b")
    success_ff = FlowFile(content=b"success")
    failure_ff = FlowFile(content=b"failure")
    success.enqueue(success_ff)
    failure.enqueue(failure_ff)
    failure.pause()

    assert executor.update_flow(_hot_swap_flow(message="new")) is True
    new_success = executor.connections.get_by_id("conn_a__success__b")
    new_failure = executor.connections.get_by_id("conn_a__failure__b")
    assert new_success.get_flowfile(success_ff.process_id) is success_ff
    assert new_success.get_flowfile(failure_ff.process_id) is None
    assert new_failure.get_flowfile(failure_ff.process_id) is failure_ff
    assert new_failure.get_flowfile(success_ff.process_id) is None
    assert new_success.is_paused() is False
    assert new_failure.is_paused() is True


def test_update_flow_requires_explicit_drop_for_nonempty_removed_queue():
    from engine.continuous_executor import ContinuousFlowExecutor

    executor = ContinuousFlowExecutor(_hot_swap_flow(), enable_checkpoints=False)
    removed = executor.connections.get_by_id("conn_a__failure__b")
    queued = FlowFile(content=b"must-not-disappear")
    removed.enqueue(queued)
    only_success = _hot_swap_flow(
        message="new",
        relations=[{"from": "a", "to": "b", "type": "success"}],
    )

    assert executor.update_flow(only_success) is False
    assert removed.get_flowfile(queued.process_id) is queued
    assert executor.update_flow(only_success, removed_queue_policy="drop") is True
    assert executor.connections.get_by_id("conn_a__failure__b") is None
    latest = executor.get_version_history()[-1]
    assert latest["dropped_flowfiles"] == 1


def test_update_flow_wait_timeout_resumes_scheduler_without_start():
    from engine.continuous_executor import ContinuousFlowExecutor

    executor = ContinuousFlowExecutor(_hot_swap_flow(), enable_checkpoints=False)
    executor._update_wait_timeout = 0.2
    executor.start()
    try:
        pool = executor._pool
        with executor._lock:
            executor._in_flight["a"] = 1          # a task that never returns
        assert executor.update_flow(_hot_swap_flow(message="new"),
                                    in_flight_policy="wait") is False
        # Aborted cleanly: same pool (no leak), scheduler back, no update.
        assert executor._pool is pool
        assert executor.is_running is True
        assert executor._scheduler_thread is not None
        assert executor._scheduler_thread.is_alive()
        assert executor.get_version_history() == []
    finally:
        with executor._lock:
            executor._in_flight.clear()
        executor.stop()


def test_update_flow_rechecks_removed_queues_after_scheduler_stop(monkeypatch):
    from engine.continuous_executor import ContinuousFlowExecutor

    executor = ContinuousFlowExecutor(_hot_swap_flow(), enable_checkpoints=False)
    removed = executor.connections.get_by_id("conn_a__failure__b")
    removed.pause()                         # nobody consumes the late arrival
    executor.start()
    try:
        late = FlowFile(content=b"arrived-after-preflight")
        real_set = executor._stop_event.set

        def _enqueue_then_stop():
            removed.enqueue(late)           # the race window, made deterministic
            real_set()
        monkeypatch.setattr(executor._stop_event, "set", _enqueue_then_stop)

        only_success = _hot_swap_flow(
            message="new",
            relations=[{"from": "a", "to": "b", "type": "success"}],
        )
        assert executor.update_flow(only_success) is False
        assert removed.get_flowfile(late.process_id) is late
        assert executor.connections.get_by_id("conn_a__failure__b") is removed
        assert executor.is_running is True
        assert executor.get_version_history() == []
    finally:
        executor.stop()


def test_update_flow_rebuild_failure_raises_and_leaves_executor_stopped(monkeypatch):
    from engine.continuous_executor import ContinuousFlowExecutor
    from engine._continuous_exec_control import FlowUpdateError

    executor = ContinuousFlowExecutor(_hot_swap_flow(), enable_checkpoints=False)
    executor.stop()

    def _boom(flow):
        raise RuntimeError("boom")
    monkeypatch.setattr(executor, "_build", _boom)

    with pytest.raises(FlowUpdateError):
        executor.update_flow(_hot_swap_flow(message="new"))
    assert executor.is_running is False


def test_checkpoint_recovery_restores_each_relationship_queue(tmp_path):
    from engine.checkpoint import CheckpointManager
    from engine.continuous_executor import ContinuousFlowExecutor

    executor = ContinuousFlowExecutor(_hot_swap_flow(), enable_checkpoints=False)
    executor._checkpoint_mgr = CheckpointManager("hot_swap", checkpoint_dir=str(tmp_path))
    executor.connections.get_by_id("conn_a__failure__b").enqueue(
        FlowFile(content=b"failure-only"))
    assert executor.save_checkpoint_now()

    fresh = ContinuousFlowExecutor(_hot_swap_flow(), enable_checkpoints=False)
    fresh._checkpoint_mgr = CheckpointManager("hot_swap", checkpoint_dir=str(tmp_path))
    fresh._recover_from_checkpoint()
    assert fresh.connections.get_by_id("conn_a__success__b").queue_size() == 0
    failure = fresh.connections.get_by_id("conn_a__failure__b")
    assert failure.queue_size() == 1
    assert failure.peek().get_content() == b"failure-only"


def test_manual_checkpoint_refuses_an_in_flight_snapshot(tmp_path):
    from engine.checkpoint import CheckpointManager
    from engine.continuous_executor import ContinuousFlowExecutor

    executor = ContinuousFlowExecutor(_hot_swap_flow(), enable_checkpoints=False)
    executor._checkpoint_mgr = CheckpointManager(
        "hot_swap", checkpoint_dir=str(tmp_path))
    executor._in_flight["a"] = 1
    assert executor.save_checkpoint_now() is None
    assert executor._checkpoint_mgr.load_latest_checkpoint() is None


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
        _flow = type("Flow", (), {"id": "hot"})()
        flow_version = 3
        checkpoints = []
        controls = []
        updates = []
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
        def update_flow(self, flow, **policies):
            self.updates.append((flow, policies)); return True

    executor = _Executor()
    from core.executor_registry import ExecutorRegistry
    from core.deployment_registry import DeploymentRegistry

    class _Inst:
        owner = "u1"
        flow_fqn = "default.hot:1.0.0"
        flow_scope = "user"
        conversation_id = ""
        parameters = {}
        service_configs = {}
        service_overrides = {}
        flow_id = "hot"
        flow_name = "Hot"
        layout = {}

    class _DReg:
        updates = []
        def get(self, iid):
            return _Inst() if iid == "inst1" else None
        def update_flow_version(self, iid, fqn, **metadata):
            self.updates.append((iid, fqn, metadata))
    executor.registry_updates = _DReg.updates
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


def test_runtime_flow_update_requires_preview_and_explicit_risk_policies(
        runtime, monkeypatch):
    from tasks.ai.actions import flow_runtime as actions
    executor, removed = runtime
    removed.enqueue(FlowFile(content=b"queued"))
    candidate_flow = object()
    common = {
        "id": "hot", "name": "Hot", "version": "1.1.0",
        "parameters": {}, "services": {}, "groups": {}, "layout": {},
        "entries": ["gen"], "exits": ["save"],
        "tasks": {
            "gen": {"type": "log", "parameters": {"message": "gen"}},
            "save": {"type": "log", "parameters": {"message": "save"}},
        },
    }
    base = {**common, "version": "1.0.0", "relations": [
        {"from": "gen", "to": "save", "type": "success"},
    ]}
    candidate = {**common, "relations": []}
    monkeypatch.setattr(actions, "_prepare_runtime_update",
                        lambda inst, fqn: (base, candidate, candidate_flow))

    preview, _ = _call("flow_runtime_update_preview", {
        "instance_id": "inst1", "fqn": "default.hot:1.1.0",
    })
    assert preview["runtime_impact"] is True
    assert preview["removed_flowfiles"] == 1
    assert preview["in_flight_tasks"] == ["gen"]
    assert preview["preview_token"]

    refused, refused_ff = _call("flow_runtime_update_apply", {
        "instance_id": "inst1", "fqn": "default.hot:1.1.0",
        "preview_token": preview["preview_token"],
    })
    assert refused["error"] == "removed_queues_require_explicit_drop"
    assert refused_ff.get_attribute("http.response.status") == "409"

    applied, _ = _call("flow_runtime_update_apply", {
        "instance_id": "inst1", "fqn": "default.hot:1.1.0",
        "preview_token": preview["preview_token"],
        "removed_queue_policy": "drop", "in_flight_policy": "wait",
    })
    assert applied["ok"] is True
    assert executor.updates == [(candidate_flow, {
        "removed_queue_policy": "drop", "in_flight_policy": "wait",
    })]
    assert executor.registry_updates == [("inst1", "default.hot:1.1.0", {
        "flow_id": "hot", "flow_name": "Hot", "layout": {},
    })]


def test_runtime_update_rebuild_failure_is_a_500_not_a_stale_preview(
        runtime, monkeypatch):
    from tasks.ai.actions import flow_runtime as actions
    from engine._continuous_exec_control import FlowUpdateError
    executor, _ = runtime
    definition = {
        "id": "hot", "name": "Hot", "version": "1.1.0",
        "parameters": {}, "services": {}, "groups": {}, "layout": {},
        "entries": ["gen"], "exits": ["save"],
        "tasks": {
            "gen": {"type": "log", "parameters": {"message": "gen"}},
            "save": {"type": "log", "parameters": {"message": "save"}},
        },
        "relations": [{"from": "gen", "to": "save", "type": "success"}],
    }
    monkeypatch.setattr(actions, "_prepare_runtime_update",
                        lambda inst, fqn: (definition, definition, object()))

    def _broken(flow, **policies):
        raise FlowUpdateError("rebuild exploded; executor stopped")
    monkeypatch.setattr(executor, "update_flow", _broken)

    preview, _ = _call("flow_runtime_update_preview", {
        "instance_id": "inst1", "fqn": "default.hot:1.1.0"})
    failed, ff = _call("flow_runtime_update_apply", {
        "instance_id": "inst1", "fqn": "default.hot:1.1.0",
        "preview_token": preview["preview_token"],
        "in_flight_policy": "wait"})
    assert failed["error"] == "runtime_update_failed"
    assert "rebuild exploded" in failed["detail"]
    assert ff.get_attribute("http.response.status") == "500"
    assert executor.registry_updates == []     # nothing persisted on failure


def test_prepare_runtime_update_loads_real_versions(tmp_path, monkeypatch):
    import core.paths as paths
    from core.repository import ScopedRepository
    from core.flow_authoring import FlowAuthoringService
    from core.deployment_registry import DeployedInstance
    from tasks.ai.actions.flow_runtime import _prepare_runtime_update

    monkeypatch.setattr(paths, "REPOSITORY_DIR", tmp_path / "repository")
    monkeypatch.setattr(paths, "RUNTIME_DIR", tmp_path / "runtime")
    ScopedRepository.reset()
    FlowAuthoringService.reset()
    try:
        definition = {
            "id": "hot", "name": "Hot", "version": "1.0.0",
            "parameters": {"greeting": {"type": "string", "default": "hi"}},
            "tasks": {
                "gen": {"type": "log", "parameters": {"message": "${greeting}"}},
                "save": {"type": "log", "parameters": {"message": "save"}},
            },
            "relations": [{"from": "gen", "to": "save", "type": "success"}],
            "entries": ["gen"], "exits": ["save"],
        }
        repo = ScopedRepository.instance()
        repo.create_flow("my_flows.hot:1.0.0", "user", definition, user_id="alice")
        repo.create_flow("my_flows.hot:1.1.0", "user",
                         {**definition, "version": "1.1.0", "relations": []},
                         user_id="alice")
        inst = DeployedInstance(
            instance_id="i1", flow_id="hot", flow_name="Hot",
            flow_fqn="my_flows.hot:1.0.0", flow_scope="user", owner="alice",
            parameters={"greeting": "hello"})

        base, candidate, parsed = _prepare_runtime_update(inst, "my_flows.hot:1.1.0")
        assert base["version"] == "1.0.0"
        assert candidate["version"] == "1.1.0"
        assert candidate["relations"] == []            # stored document, untouched
        assert set(parsed.tasks) == {"gen", "save"}
        assert parsed.parameters["_instance_id"] == "i1"
        assert parsed.parameters["greeting"] == "hello"  # deployment override wins
        with pytest.raises(ValueError):
            _prepare_runtime_update(inst, "my_flows.hot")            # unpinned
        with pytest.raises(ValueError):
            _prepare_runtime_update(inst, "my_flows.other:1.1.0")    # other flow
    finally:
        ScopedRepository.reset()
        FlowAuthoringService.reset()


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


# ── Repository scope of a deployed instance ──────────────────────

class _ScopedInst:
    instance_id = "inst9"
    owner = "u1"
    conversation_id = "conv1"
    flow_fqn = "default.hot:1.0.0"
    flow_scope = "independent"


def _fake_repo(monkeypatch, published):
    from core.repository import ScopedRepository

    class _Repo:
        def get_flow(self, fqn, scope, user_id="", conv_id=""):
            return published.get(scope)
    monkeypatch.setattr(ScopedRepository, "instance",
                        classmethod(lambda cls: _Repo()))


def test_repo_scope_ignores_a_dependency_scope_stored_in_flow_scope(monkeypatch):
    """The UI deployment path used to store the template's runtime dependency
    scope (independent|user|conversation) in flow_scope. Only a scope that
    really publishes the FQN is trusted."""
    from tasks.ai.actions.flow_runtime import _repo_scope
    _fake_repo(monkeypatch, {"user": {"id": "hot"}})
    assert _repo_scope(_ScopedInst()) == "user"


def test_repo_scope_prefers_the_stored_scope_when_it_publishes_the_flow(monkeypatch):
    from tasks.ai.actions.flow_runtime import _repo_scope
    _fake_repo(monkeypatch, {"conv": {"id": "hot"}, "user": {"id": "hot"}})

    class _Inst(_ScopedInst):
        flow_scope = "conv"
    assert _repo_scope(_Inst()) == "conv"


def test_repo_scope_refuses_a_flow_no_reachable_scope_publishes(monkeypatch):
    from tasks.ai.actions.flow_runtime import _repo_scope
    _fake_repo(monkeypatch, {})
    with pytest.raises(KeyError):
        _repo_scope(_ScopedInst())


def test_create_draft_opens_the_published_version_of_the_running_flow(monkeypatch):
    """Regression: 'Edit running flow...' answered 400 Invalid scope
    'independent' for every instance deployed from the UI."""
    from core.deployment_registry import DeploymentRegistry
    from core.executor_registry import ExecutorRegistry
    from core.flow_authoring import FlowAuthoringService

    _fake_repo(monkeypatch, {"user": {"id": "hot"}})
    monkeypatch.setattr(ExecutorRegistry, "get_instance", classmethod(
        lambda cls: type("R", (), {
            "get": lambda s, i: type("E", (), {"is_running": True})()})()))
    monkeypatch.setattr(DeploymentRegistry, "get_instance", classmethod(
        lambda cls: type("D", (), {"get": lambda s, i: _ScopedInst()})()))
    seen = {}

    class _Authoring:
        def create_draft(self, fqn, scope, user_id, conv_id="",
                         reuse_existing=True):
            seen.update(fqn=fqn, scope=scope, user_id=user_id,
                        conv_id=conv_id, reuse_existing=reuse_existing)
            return {"draft_id": "d_0123456789ab"}
    monkeypatch.setattr(FlowAuthoringService, "instance",
                        classmethod(lambda cls: _Authoring()))

    data, ff = _call("flow_runtime_create_draft", {"instance_id": "inst9"})
    assert data["draft"]["draft_id"] == "d_0123456789ab"
    assert not ff.get_attribute("http.response.status")
    # A user-scoped flow is NOT stored under the conversation: passing the
    # deployment's conversation would open a second draft of the same flow.
    assert seen == {"fqn": "default.hot:1.0.0", "scope": "user",
                    "user_id": "u1", "conv_id": "", "reuse_existing": True}


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
    # They have no button of their own, so the status bar says where they are.
    assert "Right-click a task: Start / Stop / Configuration" in source
    assert "click a queue to inspect, pause or empty it" in source


def test_published_runtime_update_is_previewed_and_explicit_in_the_same_canvas():
    source = _text("tasks/io/chat_ui/flow_graph.html")
    services = _text("tasks/io/chat_ui/services.js")
    resources = _text("tasks/io/chat_ui/resources_render.js")
    backend = _text("tasks/ai/actions/_agentres_k3.py")
    assert "function RuntimeImpactDrawer" in source
    assert "flow_runtime_update_preview" in source
    assert "flow_runtime_update_apply" in source
    assert "preview_token: impact.preview_token" in source
    assert "removed_queue_policy: removed ? 'drop' : 'reject'" in source
    assert "in_flight_policy: inFlight.length ? 'wait' : 'reject'" in source
    assert "keep_draft: !!INSTANCE_ID" in source
    assert "function _editRunningFlow(instanceId)" in services
    assert "flow_runtime_create_draft" in services
    assert "window.__PAWFLOW_FLOW_INSTANCE_ID" in services
    assert "f.flow_fqn || ''" in resources
    assert '"flow_fqn": getattr(inst, "flow_fqn", "") or ""' in backend
    for lang in ("en", "fr", "es"):
        assert "flowEditRuntime" in json.load(open(
            f"tasks/io/chat_ui/i18n/{lang}.json", encoding="utf-8"))


def test_openspace_stage_mirrors_pause_and_backpressure():
    source = _text("tasks/io/chat_ui/openspace_flow.js")
    assert "rec.paused = !!e.paused;" in source
    assert "rec.active = !rec.paused" in source
