"""The generic OAuth credential pool needs a working login pair.

A service action declared without a handler is a dead button: the UI shows it,
the click reaches nothing. These tests exercise the two actions the `generic`
pool declares, including the naming convention the UI's oauth_code flow relies
on (it posts to the same action name with `_url` swapped for `_code`).
"""

import json

import pytest

from core.flowfile import FlowFile
from tasks.ai.actions._sf_k4 import _handle_sf_k4

_HELPERS = (None, None, None, None, None, None)


def _call(action, body):
    flowfile = FlowFile(content=b"")
    result = _handle_sf_k4(
        None, action, body, None, "allcolor", flowfile, _HELPERS)
    assert result, f"action {action} was not handled"
    return json.loads(result[0].get_content().decode())


class TestActionsAreReachable:

    def test_login_url_is_handled(self):
        payload = _call("generic_oauth_login_url", {"service_id": "pool"})
        assert payload["flow"] == "paste_credentials"
        assert "access_token" in payload["message"]

    def test_the_declared_action_matches_the_ui_convention(self):
        """The UI posts to serverAction.replace('_url', '_code')."""
        from services.llm_credential_oauth import (
            GENERIC, LLMCredentialOAuthProviderService)

        service = LLMCredentialOAuthProviderService({"provider": GENERIC})
        action = next(
            entry for entry in service.get_service_actions()
            if entry["id"] == "generic_oauth_login")
        assert action["flow"] == "oauth_code"
        assert action["server_action"] == "generic_oauth_login_url"
        assert action["when"] == {"provider": [GENERIC]}
        # The handler for the name the UI will derive must exist.
        derived = action["server_action"].replace("_url", "_code")
        assert derived == "generic_oauth_login_code"
        assert _call(derived, {})["error"]


class TestLoginCodeValidation:
    """Refuse bad input loudly instead of storing an unusable credential."""

    def test_missing_arguments_are_refused(self):
        assert "Missing" in _call("generic_oauth_login_code", {})["error"]
        assert "Missing" in _call(
            "generic_oauth_login_code", {"service_id": "pool"})["error"]

    def test_a_non_generic_pool_is_refused(self, monkeypatch):
        monkeypatch.setattr(
            "tasks.ai.actions._sf_k4._credential_provider_for_service",
            lambda service_id, user_id: "claude-code")
        problem = _call("generic_oauth_login_code", {
            "service_id": "pool", "credentials": '{"access_token": "t"}'})
        assert "not a generic credential provider" in problem["error"]

    def test_invalid_json_is_refused(self, monkeypatch):
        monkeypatch.setattr(
            "tasks.ai.actions._sf_k4._credential_provider_for_service",
            lambda service_id, user_id: "generic")
        problem = _call("generic_oauth_login_code", {
            "service_id": "pool", "credentials": "not json"})
        assert "must be JSON" in problem["error"]

    def test_a_document_without_an_access_token_is_refused(self, monkeypatch):
        monkeypatch.setattr(
            "tasks.ai.actions._sf_k4._credential_provider_for_service",
            lambda service_id, user_id: "generic")
        problem = _call("generic_oauth_login_code", {
            "service_id": "pool", "credentials": '{"refresh_token": "r"}'})
        assert "no access_token" in problem["error"]


class TestLoginCodeStores:

    @pytest.fixture
    def pool(self, monkeypatch):
        stored = {}
        monkeypatch.setattr(
            "tasks.ai.actions._sf_k4._credential_provider_for_service",
            lambda service_id, user_id: "generic")
        monkeypatch.setattr(
            "core.llm_oauth_credential.load_pool",
            lambda service_id: list(stored.get(service_id, [])))
        monkeypatch.setattr(
            "core.llm_oauth_credential.save_pool",
            lambda service_id, entries: stored.__setitem__(
                service_id, list(entries)))
        return stored

    def test_a_valid_document_lands_in_the_pool(self, pool):
        result = _call("generic_oauth_login_code", {
            "service_id": "pool",
            "credentials": json.dumps({
                "access_token": "at", "refresh_token": "rt",
                "expires_at": 1234567890}),
        })
        assert result["ok"] is True
        entry = pool["pool"][0]
        assert entry["access_token"] == "at"
        assert entry["refresh_token"] == "rt"
        assert entry["expires_at"] == 1234567890
        assert entry["added_at"] > 0

    def test_refresh_token_and_expiry_are_optional(self, pool):
        """A bare access token is usable; it just never auto-refreshes."""
        result = _call("generic_oauth_login_code", {
            "service_id": "pool",
            "credentials": json.dumps({"access_token": "at"}),
        })
        assert result["ok"] is True
        assert pool["pool"][0]["expires_at"] == 0.0

    def test_an_unparseable_expiry_does_not_reject_the_token(self, pool):
        _call("generic_oauth_login_code", {
            "service_id": "pool",
            "credentials": json.dumps({
                "access_token": "at", "expires_at": "tomorrow"}),
        })
        assert pool["pool"][0]["expires_at"] == 0.0

    def test_credentials_accumulate_rather_than_replace(self, pool):
        for token in ("a", "b"):
            _call("generic_oauth_login_code", {
                "service_id": "pool",
                "credentials": json.dumps({"access_token": token}),
            })
        assert [e["access_token"] for e in pool["pool"]] == ["a", "b"]
