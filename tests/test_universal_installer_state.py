
import pytest

from pawflow_installer.state import InstallerStateStore
from tests.universal_installer_fixtures import install_request


def test_state_is_atomic_loadable_and_request_bound(tmp_path):
    store = InstallerStateStore(tmp_path / "operations")
    request = install_request()
    state = store.create(request)

    path = store.root / f"{state.operation_id}.json"
    assert path.is_file()
    assert not list(store.root.glob("*.tmp"))
    loaded = store.load(state.operation_id)
    store.assert_matches(loaded, request)
    assert loaded.request_digest == request.digest()

    other = request.model_copy(deep=True)
    other.install.port = 9555
    with pytest.raises(ValueError, match="does not match"):
        store.assert_matches(loaded, other)


def test_state_cancel_and_cleanup_are_scoped_to_one_uuid(tmp_path):
    store = InstallerStateStore(tmp_path / "operations")
    first = store.create(install_request())
    second = store.create(install_request())
    assert store.mark_cancelled(first.operation_id).cancelled is True

    store.cleanup(first.operation_id)
    assert not (store.root / f"{first.operation_id}.json").exists()
    assert (store.root / f"{second.operation_id}.json").exists()


def test_state_file_contains_no_secret_shaped_values(tmp_path):
    store = InstallerStateStore(tmp_path / "operations")
    state = store.create(install_request())
    state.request["unexpected"] = {
        "gateway_key": "do-not-store",
        "message": "Bearer abc.def",
    }
    store.save(state)
    raw = (store.root / f"{state.operation_id}.json").read_text(encoding="utf-8")
    assert "do-not-store" not in raw
    assert "abc.def" not in raw
    assert "[REDACTED]" in raw
