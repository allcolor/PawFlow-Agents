"""Managed realtime stack: credentials, provisioning, staging, proxy gate.

No Docker: every docker CLI call is intercepted at _run_docker. The
HTTPListenerService lookup is stubbed where the worker container env is
built.
"""

import json
import threading
from types import SimpleNamespace

import pytest

import core.realtime_stack_manager as rsm
from core.realtime_stack_manager import RealtimeStackManager


@pytest.fixture()
def manager(monkeypatch, tmp_path):
    monkeypatch.setenv("PAWFLOW_DATA_DIR", str(tmp_path / "data"))
    mgr = RealtimeStackManager()  # fresh instance, not the singleton
    return mgr


class _FakeDocker:
    """Scripted docker CLI: records calls, answers per-subcommand."""

    def __init__(self):
        self.calls = []
        self.container_states = {}   # name -> state string
        self.images = set()

    def __call__(self, args, timeout=0):
        self.calls.append(list(args))
        ok = SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[0] == "inspect":
            name = args[-1]
            state = self.container_states.get(name, "")
            if not state:
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            return SimpleNamespace(returncode=0, stdout=state + "\n",
                                   stderr="")
        if args[:2] == ["image", "inspect"]:
            present = args[2] in self.images
            return SimpleNamespace(returncode=0 if present else 1,
                                   stdout="", stderr="")
        if args[0] == "pull":
            self.images.add(args[1])
            return ok
        if args[0] == "build":
            self.images.add(args[args.index("-t") + 1])
            return ok
        if args[0] == "run":
            name = args[args.index("--name") + 1]
            self.container_states[name] = "running"
            return ok
        if args[0] == "start":
            self.container_states[args[1]] = "running"
            return ok
        if args[0] == "rm":
            self.container_states.pop(args[-1], None)
            return ok
        return ok


@pytest.fixture()
def fake_docker(monkeypatch):
    fake = _FakeDocker()
    monkeypatch.setattr(rsm, "_run_docker", fake)
    return fake


@pytest.fixture()
def fake_listener(monkeypatch):
    listener = SimpleNamespace(is_ssl=True, _port=8443)

    class _Stub:
        @staticmethod
        def all_instances():
            return {8443: listener}

    import services.http_listener_service as hls
    monkeypatch.setattr(hls, "HTTPListenerService", _Stub)
    return listener


class TestCredentials:
    def test_generated_persisted_and_encrypted_at_rest(self, manager,
                                                       monkeypatch):
        creds = manager.credentials()
        assert creds["api_key"].startswith("pflk")
        assert len(creds["worker_secret"]) == 48   # token_hex(24)
        raw = manager._state_file().read_text(encoding="utf-8")
        assert creds["api_secret"] not in raw
        assert creds["worker_secret"] not in raw
        # Stable on re-read.
        assert manager.credentials() == creds

    def test_has_state_tracks_provisioning(self, manager):
        assert manager.has_state() is False
        manager.credentials()
        assert manager.has_state() is True

    def test_engine_credentials_shape(self, manager):
        eng = manager.engine_credentials()
        assert eng["livekit_url"] == f"ws://127.0.0.1:{rsm.SIGNAL_PORT}"
        assert eng["livekit_api_key"]
        assert eng["livekit_api_secret"]


class TestProvisioning:
    def _provision_sync(self, manager):
        """Run ensure_stack and wait for the background thread."""
        status = manager.ensure_stack()
        thread = manager._provision_thread
        if thread is not None:
            thread.join(timeout=10)
        return status

    def test_full_provision_from_scratch(self, manager, fake_docker,
                                         fake_listener):
        self._provision_sync(manager)
        assert manager.status()["state"] == "ready"
        assert fake_docker.container_states[rsm.SERVER_CONTAINER] == "running"
        assert fake_docker.container_states[rsm.WORKER_CONTAINER] == "running"
        assert rsm.SERVER_IMAGE in fake_docker.images
        assert rsm.WORKER_IMAGE in fake_docker.images

        server_run = next(c for c in fake_docker.calls
                          if c[0] == "run" and rsm.SERVER_CONTAINER in c)
        assert "--network" in server_run and "host" in server_run
        keys_env = next(a for a in server_run
                        if a.startswith("LIVEKIT_KEYS="))
        creds = manager.credentials()
        assert keys_env == (f"LIVEKIT_KEYS={creds['api_key']}: "
                            f"{creds['api_secret']}")

        worker_run = next(c for c in fake_docker.calls
                          if c[0] == "run" and rsm.WORKER_CONTAINER in c)
        assert "PAWFLOW_URL=https://127.0.0.1:8443" in worker_run
        assert "PAWFLOW_TLS_INSECURE=1" in worker_run
        assert (f"{rsm._WORKER_SECRET_ENV}={creds['worker_secret']}"
                in worker_run)
        # Worker code is bind-mounted read-only.
        assert any(a.endswith(":/app/pawflow_livekit_worker:ro")
                   for a in worker_run)

    def test_stopped_containers_are_restarted_not_recreated(
            self, manager, fake_docker, fake_listener):
        fake_docker.container_states[rsm.SERVER_CONTAINER] = "exited"
        fake_docker.container_states[rsm.WORKER_CONTAINER] = "exited"
        fake_docker.images.update({rsm.SERVER_IMAGE, rsm.WORKER_IMAGE})
        self._provision_sync(manager)
        assert manager.status()["state"] == "ready"
        assert not any(c[0] == "run" for c in fake_docker.calls)
        assert [c for c in fake_docker.calls if c[0] == "start"] == [
            ["start", rsm.SERVER_CONTAINER], ["start", rsm.WORKER_CONTAINER]]

    def test_provision_error_is_reported(self, manager, fake_docker,
                                         fake_listener, monkeypatch):
        def _fail_pull(args, timeout=0):
            if args[0] == "pull":
                return SimpleNamespace(returncode=1, stdout="",
                                       stderr="no network")
            return fake_docker(args, timeout=timeout)
        monkeypatch.setattr(rsm, "_run_docker", _fail_pull)
        self._provision_sync(manager)
        status = manager.status()
        assert status["state"] == "error"
        assert "no network" in status["detail"]

    def test_ensure_while_provisioning_does_not_double_start(
            self, manager, fake_docker, fake_listener, monkeypatch):
        release = threading.Event()
        original = manager._provision

        def _slow():
            release.wait(timeout=5)
            original()
        monkeypatch.setattr(manager, "_provision", _slow)
        first = manager.ensure_stack()
        second = manager.ensure_stack()
        assert first["state"] == "provisioning"
        assert second["state"] == "provisioning"
        thread = manager._provision_thread
        release.set()
        thread.join(timeout=10)
        assert manager.status()["state"] == "ready"


class TestWorkerCodeStaging:
    def test_staged_once_and_rehashed_on_change(self, manager, monkeypatch,
                                                tmp_path):
        src = tmp_path / "src" / "pawflow_livekit_worker"
        src.mkdir(parents=True)
        (src / "__init__.py").write_text("", encoding="utf-8")
        (src / "worker.py").write_text("A = 1\n", encoding="utf-8")

        import pathlib
        real_resolve = pathlib.Path.resolve

        def _fake_resolve(self):
            if self.name == "realtime_stack_manager.py":
                return tmp_path / "src" / "core" / "realtime_stack_manager.py"
            return real_resolve(self)
        monkeypatch.setattr(pathlib.Path, "resolve", _fake_resolve)

        code_dir = manager._stage_worker_code()
        assert (code_dir / "worker.py").read_text(
            encoding="utf-8") == "A = 1\n"
        marker = json.loads((code_dir / ".source.json").read_text(
            encoding="utf-8"))

        # Unchanged source: same staging, marker untouched.
        again = manager._stage_worker_code()
        assert again == code_dir
        assert json.loads((code_dir / ".source.json").read_text(
            encoding="utf-8")) == marker

        # Changed source: restaged with a new hash.
        (src / "worker.py").write_text("A = 2\n", encoding="utf-8")
        restaged = manager._stage_worker_code()
        assert (restaged / "worker.py").read_text(
            encoding="utf-8") == "A = 2\n"
        assert json.loads((restaged / ".source.json").read_text(
            encoding="utf-8")) != marker


class TestSignalProxyGate:
    def test_refuses_without_managed_state(self, manager, monkeypatch):
        monkeypatch.setattr(RealtimeStackManager, "_instance", manager)
        from services.livekit_signal_proxy import livekit_signal_ws_proxy

        sent = []

        class _Sock:
            def sendall(self, data):
                sent.append(bytes(data))

            def close(self):
                pass

        livekit_signal_ws_proxy(_Sock(), {"path": "rtc"}, {"query": ""})
        # One WS close frame, code 4404, no backend connection attempted.
        assert len(sent) == 1
        assert sent[0][0] == 0x88
        assert int.from_bytes(sent[0][2:4], "big") == 4404
