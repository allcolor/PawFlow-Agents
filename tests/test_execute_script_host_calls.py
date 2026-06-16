"""Parity / boundary / serialization tests for containerized executeScript.

These exercise the REAL container SDK proxies (get_service / pawflow / flowfile
from docker/pawflow_sdk/pawflow.py) wired to the REAL host dispatcher
(core.flow_script_host.FlowScriptHostDispatcher), with the stdin/stdout docker
hop replaced by an in-process shim. That covers the whole host-call contract
without needing Docker. A Docker-gated end-to-end smoke test is included but
skipped unless PAWFLOW_TEST_DOCKER_IMAGE points at a usable relay image.
"""

import importlib.util
import os
from pathlib import Path

import pytest

from core.flow_script_host import FlowScriptHostDispatcher


def _load_sdk():
    """Load the container SDK module from its on-disk path (not importable)."""
    path = (Path(__file__).resolve().parents[1]
            / "docker" / "pawflow_sdk" / "pawflow.py")
    spec = importlib.util.spec_from_file_location("pawflow_sdk_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeService:
    def execute_query(self, query, params=None):
        return [{"n": 1, "q": query, "p": list(params or [])}]

    def execute_update(self, query, params=None):
        return 7

    def secret_blob(self):
        return object()  # not JSON-serializable

    def connect(self):  # lifecycle op — drop-in: allowed like the raw object
        return "connected"

    def boom(self):
        raise ValueError("dsn=postgres://user:SECRET@host/db failed")


class _FakeFlowFile:
    def __init__(self):
        self._content = b""
        self._attrs = {}

    def get_content(self):
        return self._content  # bytes, like core.FlowFile

    def set_content(self, data):
        self._content = data  # preserves bytes vs str, like core.FlowFile

    def get_attribute(self, key, default=None):
        return self._attrs.get(key, default)

    def set_attribute(self, key, value):
        self._attrs[key] = value

    def get_attributes(self):
        return dict(self._attrs)


class _FakePawflow:
    def set_extra(self, conversation_id, key, value):
        self._store = getattr(self, "_store", {})
        self._store[(conversation_id, key)] = value
        return True

    def get_extra(self, conversation_id, key):
        return getattr(self, "_store", {}).get((conversation_id, key))

    def _private(self):  # underscore — proxy must refuse before any host hop
        return "nope"


@pytest.fixture
def wired(monkeypatch):
    """SDK proxies wired straight to the dispatcher (no Docker hop)."""
    sdk = _load_sdk()
    flowfile = _FakeFlowFile()
    dispatcher = FlowScriptHostDispatcher(
        services={"db": _FakeService()},
        pawflow_api=_FakePawflow(),
        flowfile=flowfile,
    )

    def fake_host_call(kind, target, *, operation="", args=None, arguments=None):
        env = {
            "format": sdk._PFP_HOST_CALL_FORMAT,
            "kind": kind, "target": target, "operation": operation,
            "args": list(args or []), "arguments": arguments or {},
        }
        resp = dispatcher.handle(env)
        assert resp.get("format") == sdk._PFP_RESULT_FORMAT
        if not resp.get("ok", True):
            raise RuntimeError(resp.get("error"))
        return resp.get("result")

    monkeypatch.setattr(sdk.pfp, "_host_call", fake_host_call)
    return sdk, flowfile


# ── service proxy: parity ────────────────────────────────────────────
def test_service_query_positional_and_kwargs(wired):
    sdk, _ = wired
    db = sdk.get_service("db")
    rows = db.execute_query("SELECT 1", [2, 3])
    assert rows == [{"n": 1, "q": "SELECT 1", "p": [2, 3]}]
    assert db.execute_update("UPDATE t SET x=1") == 7


def test_service_unknown_is_rejected_without_registry_leak(wired):
    sdk, _ = wired
    with pytest.raises(RuntimeError) as exc:
        sdk.get_service("not_declared").execute_query("x")
    assert "not declared in this flow's services" in str(exc.value)


def test_service_lifecycle_op_allowed_dropin(wired):
    # Drop-in parity: any non-dunder op the raw object exposes stays callable.
    sdk, _ = wired
    assert sdk.get_service("db").connect() == "connected"


def test_service_dunder_refused_in_proxy(wired):
    sdk, _ = wired
    with pytest.raises(AttributeError):
        _ = sdk.get_service("db").__totally_fake__


def test_service_non_json_result_errors_clearly(wired):
    sdk, _ = wired
    with pytest.raises(RuntimeError) as exc:
        sdk.get_service("db").secret_blob()
    assert "non-JSON" in str(exc.value)


def test_underlying_error_is_sanitized(wired):
    # C6: a raw exception (with a secret) must NOT cross back to the container.
    sdk, _ = wired
    with pytest.raises(RuntimeError) as exc:
        sdk.get_service("db").boom()
    msg = str(exc.value)
    assert "host operation failed" in msg
    assert "SECRET" not in msg and "postgres://" not in msg


# ── pawflow proxy ────────────────────────────────────────────────────
def test_pawflow_round_trip(wired):
    sdk, _ = wired
    assert sdk.script_pawflow.set_extra("c1", "k", "v") is True
    assert sdk.script_pawflow.get_extra("c1", "k") == "v"


def test_pawflow_single_underscore_allowed_dropin(wired):
    # Single underscore stays callable (parity with the raw facade); only
    # dunders are refused at the proxy.
    sdk, _ = wired
    assert sdk.script_pawflow._private() == "nope"
    with pytest.raises(AttributeError):
        _ = sdk.script_pawflow.__totally_fake__


# ── flowfile proxy: mutations land on the host flowfile ──────────────
def test_flowfile_mutations_apply_on_host(wired):
    sdk, flowfile = wired
    sdk.script_flowfile.set_content("hello world")
    sdk.script_flowfile.set_attribute("telegram.chat_id", "123")
    # set_content(str) preserves str on the host, like core.FlowFile.
    assert flowfile.get_content() == "hello world"
    assert flowfile.get_attributes()["telegram.chat_id"] == "123"
    # get_content() returns bytes (drop-in with core.FlowFile).
    assert sdk.script_flowfile.get_content() == b"hello world"
    assert sdk.script_flowfile.get_attribute("telegram.chat_id") == "123"
    assert sdk.script_flowfile.get_attribute("missing", "dflt") == "dflt"


def test_flowfile_binary_round_trip(wired):
    # C2: raw bytes must round-trip losslessly through the JSON boundary.
    sdk, flowfile = wired
    blob = bytes(range(256))
    sdk.script_flowfile.set_content(blob)
    assert flowfile.get_content() == blob
    assert sdk.script_flowfile.get_content() == blob


# ── dispatcher unit guards (host side, no SDK) ───────────────────────
def test_dispatcher_rejects_bad_envelope():
    disp = FlowScriptHostDispatcher(services={}, pawflow_api=None, flowfile=None)
    resp = disp.handle({"format": "wrong"})
    assert resp["ok"] is False


def test_dispatcher_unknown_kind():
    disp = FlowScriptHostDispatcher(services={}, pawflow_api=None, flowfile=None)
    from core.flow_script_host import HOST_CALL_FORMAT
    resp = disp.handle({"format": HOST_CALL_FORMAT, "kind": "mystery"})
    assert resp["ok"] is False and "unsupported host-call kind" in resp["error"]


def test_abort_cancels_inflight_run_agent():
    # C1: the EXPLICIT docker timeout (watchdog -> abort) must cancel a blocking
    # pawflow.run_agent the script launched, without any implicit per-call timeout.
    import threading
    from core.flow_script_host import HOST_CALL_FORMAT

    started = threading.Event()
    release = threading.Event()
    cancelled = {}

    class _P:
        def run_agent(self, conversation_id, agent, message, **kw):
            started.set()
            release.wait(3)  # unblocked by cancel_agent
            return {"response": "done"}

        def cancel_agent(self, conversation_id, agent="", runtime_port="",
                         reason=""):
            cancelled["args"] = (conversation_id, agent, runtime_port, reason)
            release.set()
            return True

    disp = FlowScriptHostDispatcher(services={}, pawflow_api=_P(), flowfile=None)
    env = {
        "format": HOST_CALL_FORMAT, "kind": "pawflow_api",
        "operation": "run_agent", "args": ["cid1", "agentX", "hi"],
        "arguments": {},
    }
    out = {}
    worker = threading.Thread(target=lambda: out.update(resp=disp.handle(env)))
    worker.start()
    assert started.wait(3)
    disp.abort()  # watchdog fires
    worker.join(3)
    assert cancelled["args"][0] == "cid1"
    assert cancelled["args"][1] == "agentX"
    assert cancelled["args"][3] == "container_timeout"
    assert out["resp"]["ok"] is True


# ── Docker-gated end-to-end smoke (opt-in) ───────────────────────────
@pytest.mark.skipif(
    not os.environ.get("PAWFLOW_TEST_DOCKER_IMAGE"),
    reason="set PAWFLOW_TEST_DOCKER_IMAGE to a relay image to run the e2e path")
def test_execute_docker_end_to_end():
    from core import FlowFile
    from tasks.system.execute_script import ExecuteScriptTask

    task = ExecuteScriptTask({
        "containerize": True,
        "docker_image": os.environ["PAWFLOW_TEST_DOCKER_IMAGE"],
        "docker_timeout": 60,
        "script": (
            "db = get_service('db')\n"
            "rows = db.execute_query('SELECT 1', [2, 3])\n"
            "flowfile.set_attribute('checked', 'yes')\n"
            "result = str(rows)\n"
        ),
    })
    task.set_services({"db": _FakeService()})
    ff = FlowFile(content=b"")
    out = task.execute(ff)[0]
    assert b"SELECT 1" in out.get_content()
    assert out.get_attribute("checked") == "yes"
