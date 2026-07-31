"""The credential pool is a read-modify-write, and it has concurrent writers.

Every update loads the whole pool, edits one slot and writes the whole pool
back -- into a secrets file that is itself read and rewritten whole. The
writers are genuinely concurrent: the Claude, codex and gemini sweepers tick
independently, a refresh can land mid-tick, and a login writes the same file
from a third thread.

Interleaved, the loser's snapshot predates the winner's edit, so saving it
puts the OTHER slot's previous token back. For Anthropic that is permanent:
the refresh_token is single-use, so the resurrected one is already dead and
the account is logged out for good once the container is gone.
"""

import copy
import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from core.llm_providers import _cc_credentials as cc
from core.llm_providers import claude_code_session as ccs


def _future_ms():
    return int((time.time() + 3600) * 1000)


class _SlowPool:
    """An in-memory pool whose load is slow enough to interleave.

    The delay stands in for what really happens between load and save --
    decrypting, re-reading the secrets file, re-encrypting and writing it
    back. It only has to be long enough that a second writer entering
    unguarded would read the pre-edit snapshot.
    """

    def __init__(self, pool, delay=0.05):
        self.state = pool
        self.delay = delay
        self.saves = 0

    def load(self, service_id="", user_id="", conv_id=""):
        snapshot = copy.deepcopy(self.state)
        time.sleep(self.delay)
        return snapshot

    def save(self, pool, service_id="", user_id="", conv_id=""):
        self.state = copy.deepcopy(pool)
        self.saves += 1


class CredentialPoolConcurrency(unittest.TestCase):

    def _pool_of_two(self):
        return [
            {"access_token": "a0", "refresh_token": "r0",
             "expires_at": _future_ms(), "account": "one"},
            {"access_token": "a1", "refresh_token": "r1",
             "expires_at": _future_ms(), "account": "two"},
        ]

    def test_two_slots_rotating_at_once_both_survive(self):
        """The exact interleaving from the beta.62 review.

        Two sweepers each rescue a different slot. Unguarded, the writes are
        [['old0','new1'], ['new0','old1']] and the last one wins -- putting
        back a single-use refresh_token that is already dead.
        """
        slow = _SlowPool(self._pool_of_two())

        with patch.object(ccs, "_find_cc_service_id", lambda *a, **k: "svc"), \
                patch.object(ccs, "_load_credentials_pool", slow.load), \
                patch.object(ccs, "_save_credentials_pool", slow.save):
            threads = [
                threading.Thread(
                    target=cc._persist_tokens_to_service,
                    args=(f"new_a{i}", f"new_r{i}", _future_ms()),
                    kwargs={"service_id": "svc", "pool_index": i})
                for i in (0, 1)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

        self.assertEqual(
            [c["refresh_token"] for c in slow.state], ["new_r0", "new_r1"],
            "a concurrent write restored the other slot's old refresh_token")
        self.assertEqual(slow.saves, 2, "one of the two writes never happened")

    def test_an_add_racing_a_recovery_does_not_drop_either(self):
        """A login lands while a sweeper is rescuing a rotated token.

        Both are read-modify-writes of the same pool, so both must hold the
        same lock -- guarding only the recoveries leaves the window open.
        """
        slow = _SlowPool(self._pool_of_two())

        with patch.object(ccs, "_find_cc_service_id", lambda *a, **k: "svc"), \
                patch.object(ccs, "_load_credentials_pool", slow.load), \
                patch.object(ccs, "_save_credentials_pool", slow.save):
            adder = threading.Thread(
                target=cc.add_credential_to_pool,
                args=("a2", "r2", _future_ms()),
                kwargs={"account": "three", "service_id": "svc"})
            rescuer = threading.Thread(
                target=cc._persist_tokens_to_service,
                args=("new_a0", "new_r0", _future_ms()),
                kwargs={"service_id": "svc", "pool_index": 0})
            adder.start()
            rescuer.start()
            adder.join(timeout=10)
            rescuer.join(timeout=10)

        tokens = [c["refresh_token"] for c in slow.state]
        self.assertIn("r2", tokens, "the login was lost")
        self.assertIn("new_r0", tokens, "the rescued token was lost")

    def test_every_provider_shares_one_lock(self):
        """They collide on the secrets file, not on their own pool key.

        Each _save_credentials_pool reads GLOBAL_SECRETS_FILE whole, replaces
        its own key and writes the whole thing back. Two providers holding
        separate locks therefore still drop each other's keys entirely.
        """
        from core.llm_providers import codex_session, gemini_session
        from core.llm_providers.cli_shared import credentials_pool_lock

        self.assertIs(codex_session.credentials_pool_lock(),
                      credentials_pool_lock())
        self.assertIs(gemini_session.credentials_pool_lock(),
                      credentials_pool_lock())


class MemoOnlyAfterAConfirmedWrite(unittest.TestCase):
    """The skip-memo is a promise that the token already reached the pool.

    Recording an attempt instead of a result is how a rotation is lost for
    good: every later tick skips the slot, so the write that failed is never
    retried and the token dies with the container.
    """

    def _workdir_with_token(self, refresh):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, ".credentials.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"claudeAiOauth": {
                "accessToken": "acc", "refreshToken": refresh,
                "expiresAt": _future_ms()}}, f)
        return d

    def test_a_refused_persist_is_retried_on_the_next_tick(self):
        workdir = self._workdir_with_token("rotated")
        calls = []

        def persist(*a, **k):
            calls.append(1)
            # False is what every early return in the real one produces:
            # no service id, no matching credential, invalid token.
            return len(calls) > 1

        with patch.object(cc, "_persist_tokens_to_service", persist):
            first = cc.recover_tokens_from_workdir(workdir, "svc", 0)
            second = cc.recover_tokens_from_workdir(workdir, "svc", 0)

        self.assertFalse(first, "a failed write was reported as a recovery")
        self.assertTrue(second, "the retry was skipped by a premature memo")
        self.assertEqual(len(calls), 2)

    def test_a_successful_persist_is_memoised_once(self):
        workdir = self._workdir_with_token("rotated-ok")
        calls = []

        with patch.object(cc, "_persist_tokens_to_service",
                          lambda *a, **k: calls.append(1) or True):
            self.assertTrue(cc.recover_tokens_from_workdir(workdir, "svc", 0))
            self.assertFalse(cc.recover_tokens_from_workdir(workdir, "svc", 0))

        self.assertEqual(len(calls), 1, "the periodic call was not free")


if __name__ == "__main__":
    unittest.main()
