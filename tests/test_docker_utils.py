from core.docker_utils import to_host_path


def test_to_host_path_translates_pawflow_data_dir(monkeypatch):
    monkeypatch.setenv("PAWFLOW_DATA_DIR", "/app/data")
    monkeypatch.setenv("PAWFLOW_HOST_DATA_DIR", "/srv/pawflow/data")

    assert to_host_path(
        "/app/data/runtime/sessions/codex/allcolor"
    ) == "/srv/pawflow/data/runtime/sessions/codex/allcolor"


def test_to_host_path_data_dir_takes_precedence_over_workspace(monkeypatch):
    monkeypatch.setenv("PAWFLOW_DATA_DIR", "/app/data")
    monkeypatch.setenv("PAWFLOW_HOST_DATA_DIR", "/srv/pawflow/data")
    monkeypatch.setenv("PAWFLOW_WORKDIR", "/app")
    monkeypatch.setenv("PAWFLOW_HOST_WORKDIR", "/srv/pawflow/app")

    assert to_host_path("/app/data/runtime") == "/srv/pawflow/data/runtime"


def test_to_host_path_preserves_existing_workspace_translation(monkeypatch):
    monkeypatch.delenv("PAWFLOW_HOST_DATA_DIR", raising=False)
    monkeypatch.setenv("PAWFLOW_WORKDIR", "/workspace")
    monkeypatch.setenv("PAWFLOW_HOST_WORKDIR", "/home/me/project")

    assert to_host_path("/workspace/pkg/file.py") == "/home/me/project/pkg/file.py"

