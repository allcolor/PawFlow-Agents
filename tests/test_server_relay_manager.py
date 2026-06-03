import json

from core import server_relay_manager as srm


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


def test_prepare_relay_code_dir_stages_runtime_from_server_image(monkeypatch, tmp_path):
    root = tmp_path / "app"
    tools = root / "tools"
    relay_pkg = root / "pawflow_relay"
    sdk = root / "docker" / "pawflow_sdk"
    core = root / "core"
    tools.mkdir(parents=True)
    relay_pkg.mkdir()
    sdk.mkdir(parents=True)
    core.mkdir()
    (tools / "pawflow_relay_launcher.py").write_text("launcher", encoding="utf-8")
    (relay_pkg / "__init__.py").write_text("pkg", encoding="utf-8")
    (sdk / "pawflow.py").write_text("sdk", encoding="utf-8")
    monkeypatch.setattr(srm, "__file__", str(core / "server_relay_manager.py"))

    code_dir = srm._prepare_relay_code_dir(tmp_path / "runtime")

    assert (code_dir / "pawflow_relay_launcher.py").read_text(encoding="utf-8") == "launcher"
    assert (code_dir / "pawflow_relay" / "__init__.py").read_text(encoding="utf-8") == "pkg"
    assert (code_dir / "pawflow.py").read_text(encoding="utf-8") == "sdk"
    marker = json.loads((code_dir / ".pawflow-runtime-source.json").read_text(encoding="utf-8"))
    assert marker["source"] == str(root)
    assert len(marker["source_hash"]) == 64


def test_prepare_relay_code_dir_ignores_persistent_synced_runtime(monkeypatch, tmp_path):
    root = tmp_path / "app"
    (root / "tools").mkdir(parents=True)
    (root / "pawflow_relay").mkdir()
    (root / "docker" / "pawflow_sdk").mkdir(parents=True)
    (root / "tools" / "pawflow_relay_launcher.py").write_text("image-launcher", encoding="utf-8")
    (root / "pawflow_relay" / "__init__.py").write_text("image-pkg", encoding="utf-8")
    (root / "docker" / "pawflow_sdk" / "pawflow.py").write_text("image-sdk", encoding="utf-8")
    (root / "core").mkdir()
    monkeypatch.setattr(srm, "__file__", str(root / "core" / "server_relay_manager.py"))

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
    (root / "docker" / "pawflow_sdk").mkdir(parents=True)
    (root / "tools" / "pawflow_relay_launcher.py").write_text("image-launcher", encoding="utf-8")
    (root / "pawflow_relay" / "__init__.py").write_text("image-pkg", encoding="utf-8")
    (root / "docker" / "pawflow_sdk" / "pawflow.py").write_text("image-sdk", encoding="utf-8")
    (root / "core").mkdir()
    monkeypatch.setattr(srm, "__file__", str(root / "core" / "server_relay_manager.py"))

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
