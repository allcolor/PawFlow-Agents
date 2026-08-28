import json

import pytest
from pydantic import ValidationError

from pawflow_installer.events import InstallEvent
from pawflow_installer.models import InstallRequest
from tests.universal_installer_fixtures import request_payload


def test_install_request_requires_explicit_contract_fields():
    payload = request_payload()
    del payload["install"]["source"]
    with pytest.raises(ValidationError):
        InstallRequest.model_validate(payload)


def test_ssh_target_requires_explicit_host_key_policy():
    payload = request_payload(target="ssh")
    del payload["target"]["host_key_policy"]
    with pytest.raises(ValidationError, match="host-key policy"):
        InstallRequest.model_validate(payload)


def test_remote_target_cannot_use_local_reachability():
    payload = request_payload(target="ssh")
    payload["reachability"] = {
        "mode": "local", "hostname": None, "certificate_sha256": None
    }
    with pytest.raises(ValidationError, match="remote installation"):
        InstallRequest.model_validate(payload)


def test_relay_paths_and_capabilities_are_explicit():
    payload = request_payload(relay=True)
    payload["relay_desktop"]["capabilities"] = ["filesystem.write"]
    with pytest.raises(ValidationError, match="filesystem.read"):
        InstallRequest.model_validate(payload)


def test_semantic_digest_ignores_request_identity_but_not_choices():
    first = InstallRequest.model_validate(request_payload())
    second = InstallRequest.model_validate(request_payload())
    assert first.request_id != second.request_id
    assert first.digest() == second.digest()
    changed = second.model_copy(deep=True)
    changed.install.port = 9555
    assert changed.digest() != first.digest()


def test_event_redacts_nested_secrets_and_bearer_tokens():
    event = InstallEvent(
        operation_id="op",
        step_id="step",
        kind="log",
        message="Authorization: Bearer abc.def",
        data={"session_token": "secret", "nested": {"password": "hidden"}},
    ).as_dict()
    encoded = json.dumps(event)
    assert "abc.def" not in encoded
    assert "secret" not in encoded
    assert "hidden" not in encoded
    assert event["event_id"]
    assert event["created_at"]


def test_relay_artifact_requires_matching_checksum():
    payload = request_payload(relay=True)
    payload["relay_desktop"]["artifact_path"] = "/downloads/relay.AppImage"
    with pytest.raises(ValidationError, match="provided together"):
        InstallRequest.model_validate(payload)
