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


def test_server_workspace_relay_keeps_existing_identity_and_desktop():
    conv_id = "abcdef1234567890fedcba"
    cfg = srm._relay_kind_config("workspace")

    assert srm._relay_id_for_conv(conv_id, "workspace") == "srv_ws_abcdef1234567890"
    assert srm._container_name(conv_id, "workspace") == "pawflow-relay-srv-abcdef1234567890"
    assert srm._volume_name(conv_id, "workspace") == "pawflow_ws_abcdef1234567890fedcba"
    assert cfg["publish_desktop"] is True


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
