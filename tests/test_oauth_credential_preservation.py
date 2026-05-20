"""Regression tests: a transient OAuth refresh failure must never destroy
a valid credential in the Claude Code pool.

A force stop that kills the CC container mid-refresh, a network blip, or
an Anthropic 5xx all surface as exceptions from `_refresh_oauth_token`.
Only a genuine grant rejection (OAuthRejectedError) may drop the pool
slot — everything else keeps the refresh_token intact.
"""

import unittest
from unittest.mock import patch

from core.llm_client import LLMClient
from core.llm_providers import claude_code_session as ccs
from core.llm_providers.claude_code_session import OAuthRejectedError


class _FakeResp:
    def __init__(self, status, body):
        self.status = status
        self._body = body.encode("utf-8")

    def read(self):
        return self._body


class _FakeConn:
    def __init__(self, resp=None, request_exc=None):
        self._resp = resp
        self._request_exc = request_exc

    def request(self, *a, **k):
        if self._request_exc is not None:
            raise self._request_exc

    def getresponse(self):
        return self._resp

    def close(self):
        pass


def _patch_conn(conn):
    return patch("http.client.HTTPSConnection", return_value=conn)


class TestRefreshErrorClassification(unittest.TestCase):
    """_refresh_oauth_token classifies rejection vs transient failure."""

    def test_invalid_grant_400_is_rejection(self):
        conn = _FakeConn(_FakeResp(400, '{"error": "invalid_grant"}'))
        with _patch_conn(conn):
            with self.assertRaises(OAuthRejectedError):
                LLMClient._refresh_oauth_token("rt")

    def test_401_is_rejection(self):
        conn = _FakeConn(_FakeResp(401, "Unauthorized"))
        with _patch_conn(conn):
            with self.assertRaises(OAuthRejectedError):
                LLMClient._refresh_oauth_token("rt")

    def test_500_is_transient(self):
        conn = _FakeConn(_FakeResp(500, "Internal Server Error"))
        with _patch_conn(conn):
            with self.assertRaises(RuntimeError) as ctx:
                LLMClient._refresh_oauth_token("rt")
        self.assertNotIsInstance(ctx.exception, OAuthRejectedError)

    def test_400_without_invalid_grant_is_transient(self):
        # A 400 that is not a grant rejection (e.g. our own malformed
        # request) must not cost the user their credential.
        conn = _FakeConn(_FakeResp(400, '{"error": "invalid_request"}'))
        with _patch_conn(conn):
            with self.assertRaises(RuntimeError) as ctx:
                LLMClient._refresh_oauth_token("rt")
        self.assertNotIsInstance(ctx.exception, OAuthRejectedError)

    def test_network_error_is_transient(self):
        conn = _FakeConn(request_exc=ConnectionResetError("killed mid-refresh"))
        with _patch_conn(conn):
            with self.assertRaises(ConnectionResetError):
                LLMClient._refresh_oauth_token("rt")


class TestForceRefreshPreservesPool(unittest.TestCase):
    """_force_refresh_pool_entry keeps the slot on transient failure."""

    def _client(self):
        client = LLMClient(provider="claude-code", config={})
        client._agent_service = ""
        return client

    def test_transient_failure_keeps_credential(self):
        pool = [{"access_token": "at", "refresh_token": "rt", "expires_at": 1}]
        with patch.object(ccs, "_load_credentials_pool", return_value=list(pool)), \
                patch.object(ccs, "_save_credentials_pool") as save:
            client = self._client()
            with patch.object(client, "_refresh_oauth_token",
                              side_effect=RuntimeError("network down")):
                ok = client._force_refresh_pool_entry(0)
        self.assertFalse(ok)
        save.assert_not_called()  # the valid refresh_token survives

    def test_rejection_drops_credential(self):
        pool = [{"access_token": "at", "refresh_token": "rt", "expires_at": 1}]
        with patch.object(ccs, "_load_credentials_pool", return_value=list(pool)), \
                patch.object(ccs, "_save_credentials_pool") as save:
            client = self._client()
            with patch.object(client, "_refresh_oauth_token",
                              side_effect=OAuthRejectedError("revoked")):
                ok = client._force_refresh_pool_entry(0)
        self.assertFalse(ok)
        save.assert_called_once()
        self.assertEqual(save.call_args[0][0], [])  # slot removed


if __name__ == "__main__":
    unittest.main()
