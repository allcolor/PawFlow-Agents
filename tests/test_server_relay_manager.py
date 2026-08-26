import json
import inspect

from core import server_relay_manager as srm
from core import _relay_naming as _rn


def test_managed_relay_uses_private_plain_ws_bridge():
    assert srm._managed_relay_ws_url(
        "host.docker.internal", 19990, "/ws/relay/MyWorkspace",
    ) == "ws://host.docker.internal:19990/ws/relay/MyWorkspace"


def test_managed_relay_spawn_log_does_not_include_secret_command():
    source = inspect.getsource(srm.ServerRelayManager.spawn_service_relay)
    assert 'cmd=%s' not in source
    assert 'logger.info("Spawning managed server relay service: %s", container_name)' in source


def test_managed_server_relay_passes_opt_in_tunnel_capability_to_launcher():
    source = inspect.getsource(srm.ServerRelayManager.spawn_service_relay)

    assert "allow_service_tunnels: bool = False" in source
    assert 'docker_run_args.append("--allow-service-tunnels")' in source


def test_server_minimal_relay_has_distinct_stable_identity():
    conv_id = "abcdef1234567890fedcba"

    assert srm._relay_id_for_conv(conv_id) == "srv_ws_abcdef1234567890"
    assert srm._relay_id_for_conv(conv_id, "minimal") == "srv_min_abcdef1234567890"
    assert srm._container_name(conv_id, "minimal") == "pawflow-relay-min-abcdef1234567890"
    assert srm._volume_name(conv_id, "minimal") == "pawflow_exec_abcdef1234567890fedcba"


def test_server_minimal_relay_config_is_protected_execution_target(monkeypatch):
    values = {
        "server_relay_minimal_image": "pawflow-relay-minimal:latest",
        "server_relay_minimal_cpus": "1",
        "server_relay_minimal_memory": "512m",
    }
    monkeypatch.setattr(srm, "_cfg", lambda key: values[key])

    cfg = srm._relay_kind_config("minimal")

    assert cfg["kind"] == "minimal"
    assert cfg["image"] == "pawflow-relay-minimal:latest"
    assert cfg["publish_desktop"] is False
    assert "minimal execution" in cfg["description"]


def test_server_relay_image_settings_can_be_overridden_by_environment(monkeypatch):
    monkeypatch.setenv("PAWFLOW_SERVER_RELAY_IMAGE", "ghcr.io/allcolor/pawflow-relay-dev:test")
    monkeypatch.setenv("PAWFLOW_SERVER_RELAY_MINIMAL_IMAGE", "ghcr.io/allcolor/pawflow-relay-minimal:test")

    assert srm._cfg("server_relay_image") == "ghcr.io/allcolor/pawflow-relay-dev:test"
    assert srm._cfg("server_relay_minimal_image") == "ghcr.io/allcolor/pawflow-relay-minimal:test"


def test_server_workspace_relay_keeps_existing_identity_and_desktop():
    conv_id = "abcdef1234567890fedcba"
    cfg = srm._relay_kind_config("workspace")

    assert srm._relay_id_for_conv(conv_id, "workspace") == "srv_ws_abcdef1234567890"
    assert srm._container_name(conv_id, "workspace") == "pawflow-relay-srv-abcdef1234567890"
    assert srm._volume_name(conv_id, "workspace") == "pawflow_ws_abcdef1234567890fedcba"
    assert cfg["publish_desktop"] is True


def test_server_relay_desktop_is_not_published_on_host():
    src = srm.Path(srm.__file__).read_text(encoding="utf-8")

    assert '"--publish"' not in src
    assert '"PAWFLOW_DESKTOP_NOVNC_PORT=6080"' in src
    assert "desktop_host_port" not in src
    assert "audio_host_port" not in src


def test_managed_desktop_relays_enable_container_automation_not_host_access():
    spawn = inspect.getsource(srm.ServerRelayManager.spawn)
    service_spawn = inspect.getsource(
        srm.ServerRelayManager.spawn_service_relay
    )

    for source in (spawn, service_spawn):
        assert 'if kind_cfg["publish_desktop"]:' in source
        assert 'docker_run_args.append("--allow-automation")' in source
        assert '"--allow-local"' not in source
        assert '"--allow-local-screen"' not in source


def test_server_workspace_allocates_runtime_path(monkeypatch, tmp_path):
    monkeypatch.setenv("PAWFLOW_DATA_DIR", str(tmp_path / "data"))
    path = srm._relay_runtime_dir("alice@example.com", "conv/one", "workspace")

    assert path == tmp_path / "data" / "runtime" / "relay" / "alice_example.com" / "conv_one"


def test_server_relay_scope_runtime_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("PAWFLOW_DATA_DIR", str(tmp_path / "data"))

    assert srm._relay_runtime_dir_for_scope("conv", "alice@example.com", "conv/one") == (
        tmp_path / "data" / "runtime" / "relay" / "alice_example.com" / "conv_one")
    assert srm._relay_runtime_dir_for_scope("user", "alice@example.com", "alice@example.com") == (
        tmp_path / "data" / "runtime" / "relay" / "alice_example.com")
    assert srm._relay_runtime_dir_for_scope("global", "alice@example.com", "") == (
        tmp_path / "data" / "runtime" / "relay" / "global")


def test_server_minimal_relay_uses_separate_runtime_subdir(monkeypatch, tmp_path):
    monkeypatch.setenv("PAWFLOW_DATA_DIR", str(tmp_path / "data"))

    assert srm._relay_runtime_dir("", "conv1", "minimal") == (
        tmp_path / "data" / "runtime" / "relay" / "global" / "conv1" / "minimal")


def test_server_relay_host_path_maps_container_data_dir(monkeypatch, tmp_path):
    container_data = tmp_path / "container-data"
    host_data = tmp_path / "host-data"
    runtime_dir = container_data / "runtime" / "relay" / "alice" / "conv1"
    monkeypatch.setenv("PAWFLOW_DATA_DIR", str(container_data))
    monkeypatch.setenv("PAWFLOW_HOST_DATA_DIR", str(host_data))

    assert srm._relay_runtime_host_dir(runtime_dir) == str(
        host_data / "runtime" / "relay" / "alice" / "conv1")


def test_server_relay_runtime_chown_uses_host_runner_uid_gid(monkeypatch, tmp_path):
    calls = []
    root = tmp_path / "runtime"
    (root / "child").mkdir(parents=True)
    (root / "child" / "file.txt").write_text("x", encoding="utf-8")
    monkeypatch.setenv("PAWFLOW_RUN_UID", "1234")
    monkeypatch.setenv("PAWFLOW_RUN_GID", "5678")
    monkeypatch.setattr(
        srm.os,
        "chown",
        lambda path, uid, gid, *, follow_symlinks=True:
            calls.append((str(path), uid, gid)),
    )

    srm._chown_for_host_runner(root)

    assert (str(root), 1234, 5678) in calls
    assert (str(root / "child"), 1234, 5678) in calls
    assert (str(root / "child" / "file.txt"), 1234, 5678) in calls


def test_server_relay_runtime_chown_does_not_follow_dangling_symlinks(
        monkeypatch, tmp_path):
    calls = []
    root = tmp_path / "runtime"
    root.mkdir()
    dangling = root / "python"
    dangling.symlink_to(tmp_path / "missing-python")
    monkeypatch.setenv("PAWFLOW_RUN_UID", "1234")
    monkeypatch.setenv("PAWFLOW_RUN_GID", "5678")

    def fake_chown(path, uid, gid, *, follow_symlinks=True):
        if str(path) == str(dangling) and follow_symlinks:
            raise FileNotFoundError(path)
        calls.append((str(path), uid, gid, follow_symlinks))

    monkeypatch.setattr(srm.os, "chown", fake_chown)

    srm._chown_for_host_runner(root)

    assert (str(dangling), 1234, 5678, False) in calls


def test_server_relay_runtime_chown_tolerates_disappearing_entries(
        monkeypatch, tmp_path):
    calls = []
    root = tmp_path / "runtime"
    root.mkdir()
    vanished = root / "vanished.txt"
    vanished.write_text("gone", encoding="utf-8")
    monkeypatch.setenv("PAWFLOW_RUN_UID", "1234")
    monkeypatch.setenv("PAWFLOW_RUN_GID", "5678")

    def fake_chown(path, uid, gid, *, follow_symlinks=True):
        if str(path) == str(vanished):
            raise FileNotFoundError(path)
        calls.append((str(path), uid, gid, follow_symlinks))

    monkeypatch.setattr(srm.os, "chown", fake_chown)

    srm._chown_for_host_runner(root)

    assert (str(root), 1234, 5678, False) in calls


def test_prepare_relay_code_dir_stages_runtime_from_server_image(monkeypatch, tmp_path):
    root = tmp_path / "app"
    tools = root / "tools"
    relay_pkg = root / "pawflow_relay"
    sdk = root / "docker" / "pawflow_sdk"
    core = root / "core"
    graphify_pkg = core / "graphify"
    tools.mkdir(parents=True)
    relay_pkg.mkdir()
    sdk.mkdir(parents=True)
    graphify_pkg.mkdir(parents=True)
    (tools / "pawflow_relay_launcher.py").write_text("launcher", encoding="utf-8")
    (tools / "__pycache__").mkdir()
    (tools / "__pycache__" / "stale.pyc").write_bytes(b"stale tools bytecode")
    (tools / "orphan.pyc").write_bytes(b"orphan bytecode")
    (relay_pkg / "__init__.py").write_text("pkg", encoding="utf-8")
    (relay_pkg / "__pycache__").mkdir()
    (relay_pkg / "__pycache__" / "stale.pyo").write_bytes(b"stale package bytecode")
    (graphify_pkg / "__init__.py").write_text("graphify", encoding="utf-8")
    (sdk / "pawflow.py").write_text("sdk", encoding="utf-8")
    monkeypatch.setattr(_rn, "__file__", str(core / "server_relay_manager.py"))

    code_dir = srm._prepare_relay_code_dir(tmp_path / "runtime")

    assert (code_dir / "pawflow_relay_launcher.py").read_text(encoding="utf-8") == "launcher"
    assert (code_dir / "pawflow_relay" / "__init__.py").read_text(encoding="utf-8") == "pkg"
    assert (code_dir / "graphify" / "__init__.py").read_text(encoding="utf-8") == "graphify"
    assert (code_dir / "pawflow.py").read_text(encoding="utf-8") == "sdk"
    assert not (code_dir / "__pycache__").exists()
    assert not (code_dir / "orphan.pyc").exists()
    assert not (code_dir / "pawflow_relay" / "__pycache__").exists()
    marker = json.loads((code_dir / ".pawflow-runtime-source.json").read_text(encoding="utf-8"))
    assert marker["source"] == str(root)
    assert len(marker["source_hash"]) == 64


def test_prepare_relay_code_dir_ignores_persistent_synced_runtime(monkeypatch, tmp_path):
    root = tmp_path / "app"
    (root / "tools").mkdir(parents=True)
    (root / "pawflow_relay").mkdir()
    (root / "core" / "graphify").mkdir(parents=True)
    (root / "docker" / "pawflow_sdk").mkdir(parents=True)
    (root / "tools" / "pawflow_relay_launcher.py").write_text("image-launcher", encoding="utf-8")
    (root / "pawflow_relay" / "__init__.py").write_text("image-pkg", encoding="utf-8")
    (root / "core" / "graphify" / "__init__.py").write_text(
        "image-graphify", encoding="utf-8")
    (root / "docker" / "pawflow_sdk" / "pawflow.py").write_text("image-sdk", encoding="utf-8")
    monkeypatch.setattr(_rn, "__file__", str(root / "core" / "server_relay_manager.py"))

    data_dir = tmp_path / "data"
    persistent = data_dir / "runtime" / "relay_runtime" / "current"
    (persistent / "pawflow_relay").mkdir(parents=True)
    (persistent / "pawflow_relay" / "__init__.py").write_text("synced-pkg", encoding="utf-8")
    (persistent / "pawflow_relay_launcher.py").write_text("synced-launcher", encoding="utf-8")
    (persistent / "pawflow.py").write_text("synced-sdk", encoding="utf-8")
    monkeypatch.setenv("PAWFLOW_DATA_DIR", str(data_dir))

    code_dir = srm._prepare_relay_code_dir(tmp_path / "runtime")

    assert (code_dir / "pawflow_relay_launcher.py").read_text(encoding="utf-8") == "image-launcher"
    assert (code_dir / "pawflow_relay" / "__init__.py").read_text(encoding="utf-8") == "image-pkg"
    assert (code_dir / "pawflow.py").read_text(encoding="utf-8") == "image-sdk"


def test_prepare_relay_code_dir_replaces_stale_staging(monkeypatch, tmp_path):
    root = tmp_path / "app"
    (root / "tools").mkdir(parents=True)
    (root / "pawflow_relay").mkdir()
    (root / "core" / "graphify").mkdir(parents=True)
    (root / "docker" / "pawflow_sdk").mkdir(parents=True)
    (root / "tools" / "pawflow_relay_launcher.py").write_text("image-launcher", encoding="utf-8")
    (root / "pawflow_relay" / "__init__.py").write_text("image-pkg", encoding="utf-8")
    (root / "core" / "graphify" / "__init__.py").write_text(
        "image-graphify", encoding="utf-8")
    (root / "docker" / "pawflow_sdk" / "pawflow.py").write_text("image-sdk", encoding="utf-8")
    monkeypatch.setattr(_rn, "__file__", str(root / "core" / "server_relay_manager.py"))

    stale = tmp_path / "runtime" / ".pawflow-runtime"
    (stale / "pawflow_relay").mkdir(parents=True)
    (stale / "pawflow_relay_launcher.py").write_text("old-launcher", encoding="utf-8")
    (stale / "pawflow.py").write_text("old-sdk", encoding="utf-8")
    (stale / ".pawflow-runtime-source.json").write_text(
        json.dumps({"source": str(root), "source_hash": "old"}) + "\n",
        encoding="utf-8",
    )

    code_dir = srm._prepare_relay_code_dir(tmp_path / "runtime")

    assert (code_dir / "pawflow_relay_launcher.py").read_text(encoding="utf-8") == "image-launcher"
    marker = json.loads((code_dir / ".pawflow-runtime-source.json").read_text(encoding="utf-8"))
    assert marker["source_hash"] != "old"


class _FakeStore:
    """Minimal ConversationStore stand-in for the extra-metadata calls."""

    def __init__(self, extras):
        self.extras = dict(extras)

    def get_extra(self, conv_id, key):
        return self.extras.get((conv_id, key))

    def set_extra(self, conv_id, key, value):
        self.extras[(conv_id, key)] = value


def _patch_store(monkeypatch, store):
    import core.conversation_store as cs
    monkeypatch.setattr(cs.ConversationStore, "instance", classmethod(lambda cls: store))


def test_recreate_replaces_the_container_without_touching_user_data(monkeypatch):
    # The point of the primitive: destroy() deletes the Docker volume and the
    # workspace directory, recreate() must delete neither.
    mgr = srm.ServerRelayManager()
    meta = {"relay_id": "srv_ws_abcdef1234567890", "container_id": "old-cid",
            "user_id": "alice", "kind": "workspace",
            "volume": "pawflow_ws_conv1",
            "workspace_dir": "/data/runtime/relay/alice/conv1"}
    store = _FakeStore({("conv1", "server_relay"): meta})
    _patch_store(monkeypatch, store)

    cleaned = []
    monkeypatch.setattr(mgr, "_cleanup_container",
                        lambda cid, remove=True: cleaned.append((cid, remove)))
    # Any volume removal or workspace deletion would show up here.
    runs = []
    monkeypatch.setattr(srm.subprocess, "run", lambda *a, **k: runs.append(a))
    removed_trees = []
    monkeypatch.setattr(srm.shutil, "rmtree",
                        lambda path, **k: removed_trees.append(path))
    spawned = {}

    def fake_spawn(conv_id, user_id, *, kind="workspace"):
        # spawn() refuses while live metadata is present — it must be cleared.
        assert store.get_extra(conv_id, "server_relay") is None
        spawned.update(conv_id=conv_id, user_id=user_id, kind=kind)
        new_meta = dict(meta, container_id="new-cid")
        store.set_extra(conv_id, "server_relay", new_meta)
        return new_meta

    monkeypatch.setattr(mgr, "spawn", fake_spawn)

    result = mgr.recreate("conv1", kind="workspace")

    assert cleaned == [("old-cid", True)]
    assert spawned == {"conv_id": "conv1", "user_id": "alice", "kind": "workspace"}
    assert runs == []
    assert removed_trees == []
    # Same identity, same volume, same workspace: the registered relay service
    # and the conversation bindings stay valid, the files are still there.
    assert result["relay_id"] == meta["relay_id"]
    assert result["volume"] == "pawflow_ws_conv1"
    assert result["workspace_dir"] == "/data/runtime/relay/alice/conv1"
    assert result["container_id"] == "new-cid"


def test_recreate_restores_metadata_when_the_respawn_fails(monkeypatch):
    mgr = srm.ServerRelayManager()
    meta = {"relay_id": "srv_min_abcdef1234567890", "container_id": "old-cid",
            "user_id": "alice", "kind": "minimal"}
    store = _FakeStore({("conv1", "server_minimal_relay"): meta})
    _patch_store(monkeypatch, store)
    monkeypatch.setattr(mgr, "_cleanup_container", lambda cid, remove=True: None)

    def boom(conv_id, user_id, *, kind="workspace"):
        raise RuntimeError("no docker")

    monkeypatch.setattr(mgr, "spawn", boom)

    try:
        mgr.recreate("conv1", kind="minimal")
    except RuntimeError as exc:
        assert "no docker" in str(exc)
    else:
        raise AssertionError("recreate should propagate the spawn failure")

    # A failed respawn must not erase the relay from the store: the workspace is
    # still on disk and restart_orphans() can pick it up at the next startup.
    assert store.get_extra("conv1", "server_minimal_relay") == meta


def test_recreate_refuses_without_a_relay_or_without_a_user(monkeypatch):
    mgr = srm.ServerRelayManager()
    store = _FakeStore({("conv2", "server_relay"): {"relay_id": "srv_ws_x"}})
    _patch_store(monkeypatch, store)

    def no_spawn(*a, **k):
        raise AssertionError("spawn should not run")

    monkeypatch.setattr(mgr, "spawn", no_spawn)
    monkeypatch.setattr(mgr, "_cleanup_container", lambda cid, remove=True: None)

    for conv_id in ("conv-missing", "conv2"):
        try:
            mgr.recreate(conv_id)
        except ValueError:
            pass
        else:
            raise AssertionError(f"recreate should refuse for {conv_id}")


def test_ensure_minimal_reuses_running_server_execution_relay(monkeypatch):
    mgr = srm.ServerRelayManager()
    existing = {"relay_id": "srv_min_abcdef1234567890", "container_id": "cid"}

    monkeypatch.setattr(mgr, "get_metadata", lambda conv_id, *, kind="workspace": existing)
    monkeypatch.setattr(mgr, "_is_container_running", lambda container_id: container_id == "cid")
    monkeypatch.setattr(
        mgr,
        "spawn",
        lambda conv_id, user_id, *, kind="workspace": (_ for _ in ()).throw(AssertionError("spawn should not run")),
    )

    assert mgr.ensure_minimal("conv1", "alice") is existing
