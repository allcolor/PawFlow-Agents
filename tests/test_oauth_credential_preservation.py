"""Regression tests: a transient OAuth refresh failure must never destroy
a valid credential in the Claude Code pool.

A force stop that kills the CC container mid-refresh, a network blip, or
an Anthropic 5xx all surface as exceptions from `_refresh_oauth_token`.
Only a genuine grant rejection (OAuthRejectedError) may drop the pool
slot — everything else keeps the refresh_token intact.
"""

import threading
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


class TestCoordinatedRefreshDedup(unittest.TestCase):
    """_refresh_oauth_token_coordinated serializes + dedupes refreshes of a
    single-use refresh_token shared by two sessions on the same pool slot."""

    def _client(self):
        client = LLMClient(provider="claude-code", config={})
        client._agent_service = "svc"
        return client

    def setUp(self):
        ccs.ClaudeCodeSessionMixin._refresh_locks.clear()

    def test_peer_rotated_slot_is_reused_without_network(self):
        # A peer already rotated the slot to RT1/AT1; we still hold the
        # stale RT0. The coordinated path must adopt RT1 and NOT POST the
        # consumed RT0 (which would 400 invalid_grant and drop the slot).
        live = [{"access_token": "AT1", "refresh_token": "RT1",
                 "expires_at": 9_999_999_999_000}]
        client = self._client()
        with patch.object(ccs, "_load_credentials_pool",
                          return_value=[dict(live[0])]), \
                patch.object(client, "_refresh_oauth_token") as raw:
            out = client._refresh_oauth_token_coordinated(
                "RT0", service_id="svc", pool_index=0, user_id="", conv_id="")
        raw.assert_not_called()
        self.assertEqual(out["access_token"], "AT1")
        self.assertEqual(out["refresh_token"], "RT1")

    def test_unrotated_slot_falls_through_to_network_refresh(self):
        live = [{"access_token": "AT0", "refresh_token": "RT0", "expires_at": 1}]
        client = self._client()
        new = {"access_token": "AT1", "refresh_token": "RT1",
               "expires_at": 9_999_999_999_000}
        with patch.object(ccs, "_load_credentials_pool",
                          return_value=[dict(live[0])]), \
                patch.object(client, "_refresh_oauth_token",
                             return_value=new) as raw:
            out = client._refresh_oauth_token_coordinated(
                "RT0", service_id="svc", pool_index=0, user_id="", conv_id="")
        raw.assert_called_once_with("RT0")
        self.assertEqual(out["refresh_token"], "RT1")


class _PoolStore:
    """Thread-safe in-memory stand-in for the encrypted credentials pool."""

    def __init__(self, pool):
        self._pool = [dict(c) for c in pool]
        self._lock = threading.Lock()

    def load(self, *a, **k):
        with self._lock:
            return [dict(c) for c in self._pool]

    def save(self, pool, *a, **k):
        with self._lock:
            self._pool = [dict(c) for c in pool]

    def persist(self, access_token, refresh_token, expires_at, *,
                service_id="", pool_index=-1, user_id="", conv_id=""):
        with self._lock:
            if 0 <= pool_index < len(self._pool):
                self._pool[pool_index] = {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "expires_at": int(expires_at),
                    "account": self._pool[pool_index].get("account", ""),
                }

    @property
    def pool(self):
        with self._lock:
            return [dict(c) for c in self._pool]


class _SingleUseRefreshServer:
    """Models Anthropic's single-use refresh_token: a token may be rotated
    exactly once; re-POSTing a consumed token is an invalid_grant."""

    def __init__(self):
        self._consumed = set()
        self._n = 0
        self._lock = threading.Lock()

    def refresh(self, refresh_token):
        with self._lock:
            if refresh_token in self._consumed:
                raise OAuthRejectedError("invalid_grant: refresh_token already used")
            self._consumed.add(refresh_token)
            self._n += 1
            return {
                "access_token": f"AT{self._n}",
                "refresh_token": f"RT{self._n}",
                "expires_at": 9_999_999_999_000,
            }


class TestConcurrentRefreshKeepsCredential(unittest.TestCase):
    """Two sessions sharing one pool slot must not lose the credential when
    both try to refresh the same single-use token at once."""

    def _client(self):
        client = LLMClient(provider="claude-code", config={})
        client._agent_service = "svc"
        return client

    def test_two_sessions_sharing_slot_keep_credential(self):
        ccs.ClaudeCodeSessionMixin._refresh_locks.clear()
        store = _PoolStore([{"access_token": "AT0", "refresh_token": "RT0",
                             "expires_at": 1}])
        server = _SingleUseRefreshServer()
        barrier = threading.Barrier(2)
        results = []

        def worker():
            client = self._client()
            client._refresh_oauth_token = server.refresh
            barrier.wait()
            results.append(client._force_refresh_pool_entry(0))

        with patch.object(ccs, "_load_credentials_pool", side_effect=store.load), \
                patch.object(ccs, "_save_credentials_pool", side_effect=store.save), \
                patch.object(ccs, "_persist_tokens_to_service", side_effect=store.persist):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        # Neither session may drop the slot; both must succeed.
        self.assertEqual(sorted(results), [True, True])
        pool = store.pool
        self.assertEqual(len(pool), 1)
        self.assertTrue(pool[0]["access_token"])
        self.assertTrue(pool[0]["refresh_token"])


if __name__ == "__main__":
    unittest.main()
