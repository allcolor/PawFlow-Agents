"""Regression tests: concurrent OAuth refresh must never destroy a valid
codex credential. Two codex sessions sharing one pool slot must not both
POST the same single-use refresh_token (the loser would error and drop a
slot the winner just rotated). Mirror of test_oauth_credential_preservation
for the codex provider.
"""

import threading
import tempfile
import unittest
from unittest.mock import patch

from core.llm_client import LLMClient
from core.llm_providers import codex_session as cxs


class _PoolStore:
    """Thread-safe in-memory stand-in for the encrypted codex pool."""

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
                service_id="", pool_index=-1, id_token="", account="",
                user_id="", conv_id=""):
        with self._lock:
            if 0 <= pool_index < len(self._pool):
                self._pool[pool_index] = {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "expires_at": int(expires_at),
                    "id_token": id_token or self._pool[pool_index].get("id_token", ""),
                    "account": account or self._pool[pool_index].get("account", ""),
                }

    @property
    def pool(self):
        with self._lock:
            return [dict(c) for c in self._pool]


class _SingleUseRefreshServer:
    """Models a single-use refresh_token: a token rotates exactly once;
    re-POSTing a consumed token raises (as the OAuth endpoint would)."""

    def __init__(self):
        self._consumed = set()
        self._n = 0
        self._lock = threading.Lock()

    def refresh(self, refresh_token):
        with self._lock:
            if refresh_token in self._consumed:
                raise RuntimeError("invalid_grant: refresh_token already used")
            self._consumed.add(refresh_token)
            self._n += 1
            return {
                "access_token": f"AT{self._n}",
                "refresh_token": f"RT{self._n}",
                "id_token": f"ID{self._n}",
                "expires_at": 9_999_999_999_000,
            }


class TestCoordinatedCodexRefreshDedup(unittest.TestCase):
    def _client(self):
        client = LLMClient(provider="codex-app-server", config={})
        client._agent_service = "svc"
        return client

    def setUp(self):
        cxs.CodexSessionMixin._codex_refresh_locks.clear()

    def test_peer_rotated_slot_is_reused_without_network(self):
        live = [{"access_token": "AT1", "refresh_token": "RT1",
                 "id_token": "ID1", "expires_at": 9_999_999_999_000}]
        client = self._client()
        with patch.object(cxs, "_load_credentials_pool",
                          return_value=[dict(live[0])]), \
                patch.object(cxs, "refresh_oauth_token") as raw:
            out = client._codex_refresh_oauth_token_coordinated(
                "RT0", service_id="svc", pool_index=0, user_id="", conv_id="")
        raw.assert_not_called()
        self.assertEqual(out["access_token"], "AT1")
        self.assertEqual(out["refresh_token"], "RT1")
        self.assertEqual(out["id_token"], "ID1")

    def test_unrotated_slot_falls_through_to_network_refresh(self):
        live = [{"access_token": "AT0", "refresh_token": "RT0",
                 "id_token": "ID0", "expires_at": 1}]
        client = self._client()
        new = {"access_token": "AT1", "refresh_token": "RT1",
               "id_token": "ID1", "expires_at": 9_999_999_999_000}
        with patch.object(cxs, "_load_credentials_pool",
                          return_value=[dict(live[0])]), \
                patch.object(cxs, "refresh_oauth_token", return_value=new) as raw:
            out = client._codex_refresh_oauth_token_coordinated(
                "RT0", service_id="svc", pool_index=0, user_id="", conv_id="")
        raw.assert_called_once_with("RT0")
        self.assertEqual(out["refresh_token"], "RT1")


class TestConcurrentCodexRefreshKeepsCredential(unittest.TestCase):
    def _client(self):
        client = LLMClient(provider="codex-app-server", config={})
        client._agent_service = "svc"
        return client

    def test_two_sessions_sharing_slot_keep_credential(self):
        cxs.CodexSessionMixin._codex_refresh_locks.clear()
        store = _PoolStore([{"access_token": "AT0", "refresh_token": "RT0",
                             "id_token": "ID0", "expires_at": 1}])
        server = _SingleUseRefreshServer()
        barrier = threading.Barrier(2)
        results = []

        def worker():
            client = self._client()
            barrier.wait()
            results.append(client._codex_force_refresh_pool_entry(0))

        with patch.object(cxs, "_load_credentials_pool", side_effect=store.load), \
                patch.object(cxs, "_save_credentials_pool", side_effect=store.save), \
                patch.object(cxs, "_persist_tokens_to_service", side_effect=store.persist), \
                patch.object(cxs, "refresh_oauth_token", side_effect=server.refresh):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        self.assertEqual(sorted(results), [True, True])
        pool = store.pool
        self.assertEqual(len(pool), 1)
        self.assertTrue(pool[0]["access_token"])
        self.assertTrue(pool[0]["refresh_token"])

    def test_transient_force_refresh_failure_keeps_credential(self):
        store = _PoolStore([{"access_token": "AT0", "refresh_token": "RT0",
                             "id_token": "ID0", "expires_at": 1}])
        client = self._client()
        with patch.object(cxs, "_load_credentials_pool", side_effect=store.load), \
                patch.object(cxs, "_save_credentials_pool", side_effect=store.save), \
                patch.object(cxs, "refresh_oauth_token",
                             side_effect=RuntimeError("network down")):
            ok = client._codex_force_refresh_pool_entry(0)

        self.assertFalse(ok)
        self.assertEqual(store.pool[0]["refresh_token"], "RT0")


class TestCodexSetupCredentialsCompactsPoolSafely(unittest.TestCase):
    def test_refresh_survives_dead_slot_purge_and_reindexes_selected_slot(self):
        cxs.CodexSessionMixin._codex_refresh_locks.clear()
        store = _PoolStore([
            {"access_token": "", "refresh_token": "dead",
             "id_token": "", "expires_at": 0},
            {"access_token": "AT0", "refresh_token": "RT0",
             "id_token": "ID0", "expires_at": 1},
        ])
        client = LLMClient(provider="codex-app-server", config={})
        client._agent_service = "svc"

        with tempfile.TemporaryDirectory() as workdir, \
                patch.object(cxs, "_load_credentials_pool", side_effect=store.load), \
                patch.object(cxs, "_save_credentials_pool", side_effect=store.save), \
                patch.object(cxs, "_persist_tokens_to_service", side_effect=store.persist), \
                patch.object(cxs, "refresh_oauth_token", return_value={
                    "access_token": "AT1",
                    "refresh_token": "RT1",
                    "id_token": "ID1",
                    "expires_at": 9_999_999_999_000,
                }):
            client._codex_setup_credentials(workdir, user_id="u", conversation_id="c")

        self.assertEqual(client._current_pool_index, 0)
        pool = store.pool
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0]["access_token"], "AT1")
        self.assertEqual(pool[0]["refresh_token"], "RT1")
        self.assertEqual(pool[0]["id_token"], "ID1")
        self.assertEqual(pool[0]["expires_at"], 9_999_999_999_000)


if __name__ == "__main__":
    unittest.main()
