"""Website Creator fixed-script Chromium extraction contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.agent_contracts import CapabilityEffect
from core.handlers.browser_console_extract import BrowserConsoleExtractHandler
from pawflow_relay.website_browser import (
    MAX_EXTRACTION_BYTES,
    BrowserSession,
    build_chromium_command,
    cleanup_sessions,
    extract_session,
)
from tasks.ai.workflow.website_creator_tasks import WebsiteCreatorToolTask


_WORKSPACE = "/workspace/pawflow-sites/run-1"
_TARGET = "target-1"
_SESSION = "session-1"


class _HandlerRelay:
    _service_id = "relay-browser"

    def __init__(self):
        self.calls = []
        self.files = {}

    def _request(self, action, path, **kwargs):
        self.calls.append((action, path, kwargs))
        return {
            "path": kwargs["write_to"],
            "bytes": 123,
            "sha256": "a" * 64,
            "schema_version": "rendered_inventory.v1",
            "counts": {"items": 3},
            "preview": "preview",
            "extraction_mode": "cdp_pipe",
        }

    def exists(self, path, local=False):
        assert local is False
        return path in self.files

    def read_file(self, path, local=False):
        assert local is False
        return self.files[path]

    def atomic_write_file(self, path, content, local=False):
        assert local is False
        self.files[path] = bytes(content)


class _FakeTransport:
    def __init__(self, payload: dict, *, origin: str = "https://example.com", indices=None):
        self.serialized = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        self.origin = origin
        self.calls = []
        self.indices = list(indices or [])
        self.chunk_index = 0

    def request(self, method, params, *, session_id="", timeout=10):
        self.calls.append((method, params, session_id, timeout))
        assert method == "Runtime.evaluate"
        expression = params["expression"]
        if "__PAWFLOW_ORIGIN__" in expression:
            value = self.origin
        elif "__PAWFLOW_EXTRACT_INIT__" in expression:
            value = {
                "chars": len(self.serialized),
                "bytes": len(self.serialized.encode("utf-8")),
                "preview": self.serialized[:100],
                "counts": {"items": len(json.loads(self.serialized).get("items", []))},
            }
        elif "__PAWFLOW_EXTRACT_CHUNK__" in expression:
            start = self.chunk_index * 7
            text = self.serialized[start:start + 7]
            index = (
                self.indices[self.chunk_index]
                if self.chunk_index < len(self.indices)
                else self.chunk_index
            )
            value = {"index": index, "text": text}
            self.chunk_index += 1
        else:
            raise AssertionError(f"unexpected fixed expression: {expression[:80]}")
        return {"result": {"result": {"value": value}}}


def _session(transport, tmp_path) -> BrowserSession:
    return BrowserSession(
        session_id=_SESSION,
        target_id=_TARGET,
        cdp_session_id="cdp-1",
        approved_origin="https://example.com",
        profile_path=tmp_path / "profiles" / "run-1",
        transport=transport,
        process=SimpleNamespace(poll=lambda: None),
    )


def test_browser_extract_handler_has_fixed_schema_and_declared_effects():
    handler = BrowserConsoleExtractHandler(
        _WORKSPACE,
        session_id=_SESSION,
        target_id=_TARGET,
        approved_origin="https://example.com",
        inventory_budget_bytes=10_000,
    )
    schema = handler.parameters_schema
    assert schema["required"] == ["script_id", "target_id"]
    assert set(schema["properties"]["script_id"]["enum"]) == {
        "rendered_inventory_v1", "dom_outline_v1", "computed_assets_v1",
    }
    assert schema["properties"]["timeout"]["maximum"] == 30
    assert "expression" not in schema["properties"]
    assert "url_prefix" not in schema["properties"]
    assert handler.EFFECTS == (
        CapabilityEffect.BROWSER_CONTROL,
        CapabilityEffect.FILESYSTEM_WRITE,
    )


def test_browser_extract_is_exposed_only_in_website_creator_phases():
    for phase in ("explore", "build", "review", "correct"):
        assert "browser_console_extract" in WebsiteCreatorToolTask.allowed_tool_names(phase)
    with pytest.raises(ValueError, match="unsupported"):
        WebsiteCreatorToolTask.allowed_tool_names("default")


def test_browser_extract_handler_binds_target_origin_budget_and_workspace_path():
    relay = _HandlerRelay()
    handler = BrowserConsoleExtractHandler(
        _WORKSPACE,
        session_id=_SESSION,
        target_id=_TARGET,
        approved_origin="https://example.com",
        inventory_budget_bytes=10_000,
    )
    handler.set_service(relay)

    result = json.loads(handler.execute({
        "script_id": "rendered_inventory_v1",
        "target_id": _TARGET,
        "options": {"max_items": 25},
        "write_to": "inventory/rendered.json",
        "timeout": 12,
    }))

    assert result["extraction_mode"] == "cdp_pipe"
    assert relay.calls == [(
        "browser_console_extract",
        ".",
        {
            "session_id": _SESSION,
            "target_id": _TARGET,
            "approved_origin": "https://example.com",
            "script_id": "rendered_inventory_v1",
            "options": {"max_items": 25},
            "write_to": f"{_WORKSPACE}/inventory/rendered.json",
            "timeout": 12,
            "max_bytes": 10_000,
            "local": False,
        },
    )]
    with pytest.raises(ValueError, match="target_id"):
        handler.execute({"script_id": "dom_outline_v1", "target_id": "other"})
    with pytest.raises(ValueError, match="workspace"):
        handler.execute({
            "script_id": "dom_outline_v1",
            "target_id": _TARGET,
            "write_to": "../escape.json",
        })


def test_fake_cdp_extract_validates_origin_streams_ordered_chunks_and_hashes(tmp_path):
    payload = {
        "schema_version": "rendered_inventory.v1",
        "script_id": "rendered_inventory_v1",
        "items": [{"url": "https://example.com/a"}, {"url": "https://example.com/b"}],
    }
    transport = _FakeTransport(payload)
    result = extract_session(
        _session(transport, tmp_path),
        {
            "target_id": _TARGET,
            "approved_origin": "https://example.com",
            "script_id": "rendered_inventory_v1",
            "options": {"max_items": 25},
            "write_to": "inventory/rendered.json",
            "timeout": 10,
            "max_bytes": MAX_EXTRACTION_BYTES,
        },
        root_dir=str(tmp_path),
        chunk_chars=7,
    )
    raw = transport.serialized.encode("utf-8")
    target = tmp_path / "inventory" / "rendered.json"
    assert target.read_bytes() == raw
    assert result["sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["bytes"] == len(raw)
    assert result["counts"] == {"items": 2}
    assert result["extraction_mode"] == "cdp_pipe"
    assert all(call[2] == "cdp-1" for call in transport.calls)


def test_fake_cdp_extract_rejects_origin_unknown_script_timeout_size_and_chunk_order(tmp_path):
    payload = {
        "schema_version": "dom_outline.v1",
        "script_id": "dom_outline_v1",
        "items": [],
    }
    with pytest.raises(ValueError, match="origin"):
        extract_session(
            _session(_FakeTransport(payload, origin="https://other.example"), tmp_path),
            {
                "target_id": _TARGET,
                "approved_origin": "https://example.com",
                "script_id": "dom_outline_v1",
            },
            root_dir=str(tmp_path),
        )
    with pytest.raises(ValueError, match="script_id"):
        extract_session(
            _session(_FakeTransport(payload), tmp_path),
            {"target_id": _TARGET, "script_id": "model_authored_js"},
            root_dir=str(tmp_path),
        )
    with pytest.raises(ValueError, match="timeout"):
        extract_session(
            _session(_FakeTransport(payload), tmp_path),
            {"target_id": _TARGET, "script_id": "dom_outline_v1", "timeout": 31},
            root_dir=str(tmp_path),
        )

    oversized_payload = {**payload, "padding": "x" * 200}
    oversized = _FakeTransport(oversized_payload)
    with pytest.raises(ValueError, match="maximum size"):
        extract_session(
            _session(oversized, tmp_path),
            {"target_id": _TARGET, "script_id": "dom_outline_v1", "max_bytes": 100},
            root_dir=str(tmp_path),
        )
    assert not (tmp_path / "inventory" / "dom_outline_v1.json").exists()

    reordered = _FakeTransport(payload, indices=[1])
    with pytest.raises(ValueError, match="chunk order"):
        extract_session(
            _session(reordered, tmp_path),
            {"target_id": _TARGET, "script_id": "dom_outline_v1"},
            root_dir=str(tmp_path),
            chunk_chars=7,
        )
    assert not (tmp_path / "inventory" / "dom_outline_v1.json").exists()


def test_chromium_command_uses_pipe_and_run_scoped_profile(tmp_path):
    profile = tmp_path / "profiles" / "run-1"
    command = build_chromium_command(
        "/usr/bin/chromium", profile, "https://example.com/",
    )
    assert "--remote-debugging-pipe" in command
    assert not any(arg.startswith("--remote-debugging-port") for arg in command)
    assert f"--user-data-dir={profile}" in command
    assert str(Path.home() / ".config" / "chromium") not in " ".join(command)


def test_cleanup_sessions_terminates_process_closes_transport_and_removes_profile(tmp_path):
    profile = tmp_path / "profiles" / "run-1"
    profile.mkdir(parents=True)
    (profile / "state").write_text("isolated", encoding="utf-8")

    class Process:
        terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=0):
            assert timeout == 5

    class Transport:
        closed = False

        def close(self):
            self.closed = True

    process = Process()
    transport = Transport()
    state = SimpleNamespace(website_browser_sessions={
        _SESSION: BrowserSession(
            session_id=_SESSION,
            target_id=_TARGET,
            cdp_session_id="cdp-1",
            approved_origin="https://example.com",
            profile_path=profile,
            transport=transport,
            process=process,
        ),
    })
    cleanup_sessions(state)
    assert process.terminated is True
    assert transport.closed is True
    assert not profile.exists()
    assert state.website_browser_sessions == {}
