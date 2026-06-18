"""Regression tests: concurrent OAuth refresh must never destroy a valid
gemini credential. Two gemini sessions sharing one pool slot must not both
POST the same single-use refresh_token (the loser would error and drop a
slot the winner just rotated). Mirror of test_oauth_credential_preservation
for the gemini provider.
"""

import threading
import unittest
from unittest.mock import patch

from core.llm_client import LLMClient
from core.llm_providers import gemini_session as gms


class _PoolStore:
    """Thread-safe in-memory stand-in for the encrypted gemini pool."""

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
                service_id="", pool_index=-1, account="",
                user_id="", conv_id=""):
        with self._lock:
            if 0 <= pool_index < len(self._pool):
                self._pool[pool_index] = {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "expires_at": int(expires_at),
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
                "expires_at": 9_999_999_999_000,
            }


class TestCoordinatedGeminiRefreshDedup(unittest.TestCase):
    def _client(self):
        client = LLMClient(provider="gemini", config={})
        client._agent_service = "svc"
        return client

    def setUp(self):
        gms.GeminiSessionMixin._gemini_refresh_locks.clear()

    def test_peer_rotated_slot_is_reused_without_network(self):
        live = [{"access_token": "AT1", "refresh_token": "RT1",
                 "expires_at": 9_999_999_999_000}]
        client = self._client()
        with patch.object(gms, "_load_credentials_pool",
                          return_value=[dict(live[0])]), \
                patch.object(gms, "refresh_oauth_token") as raw:
            out = client._gemini_refresh_oauth_token_coordinated(
                "RT0", service_id="svc", pool_index=0, user_id="", conv_id="")
        raw.assert_not_called()
        self.assertEqual(out["access_token"], "AT1")
        self.assertEqual(out["refresh_token"], "RT1")

    def test_unrotated_slot_falls_through_to_network_refresh(self):
        live = [{"access_token": "AT0", "refresh_token": "RT0", "expires_at": 1}]
        client = self._client()
        new = {"access_token": "AT1", "refresh_token": "RT1",
               "expires_at": 9_999_999_999_000}
        with patch.object(gms, "_load_credentials_pool",
                          return_value=[dict(live[0])]), \
                patch.object(gms, "refresh_oauth_token", return_value=new) as raw:
            out = client._gemini_refresh_oauth_token_coordinated(
                "RT0", service_id="svc", pool_index=0, user_id="", conv_id="")
        raw.assert_called_once_with("RT0")
        self.assertEqual(out["refresh_token"], "RT1")


class TestConcurrentGeminiRefreshKeepsCredential(unittest.TestCase):
    def _client(self):
        client = LLMClient(provider="gemini", config={})
        client._agent_service = "svc"
        return client

    def test_two_sessions_sharing_slot_keep_credential(self):
        gms.GeminiSessionMixin._gemini_refresh_locks.clear()
        store = _PoolStore([{"access_token": "AT0", "refresh_token": "RT0",
                             "expires_at": 1}])
        server = _SingleUseRefreshServer()
        barrier = threading.Barrier(2)
        results = []

        def worker():
            client = self._client()
            barrier.wait()
            results.append(client._gemini_force_refresh_pool_entry(0))

        with patch.object(gms, "_load_credentials_pool", side_effect=store.load), \
                patch.object(gms, "_save_credentials_pool", side_effect=store.save), \
                patch.object(gms, "_persist_tokens_to_service", side_effect=store.persist), \
                patch.object(gms, "refresh_oauth_token", side_effect=server.refresh):
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


if __name__ == "__main__":
    unittest.main()
