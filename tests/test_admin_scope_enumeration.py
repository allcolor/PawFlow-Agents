"""Cross-user enumeration primitives for the admin view-all feature."""

import tempfile
import unittest
from pathlib import Path

from tasks import register_all_tasks
register_all_tasks()

SVC_TYPE = "cacheService"


class TestIterAllScopes(unittest.TestCase):
    """ServiceRegistry.iter_all_scopes enumerates global + every user."""

    def setUp(self):
        import core.service_registry as mod
        from core.service_registry import ServiceRegistry
        self.mod = mod
        self._tmp = tempfile.TemporaryDirectory(prefix="pawflow_iter_")
        tmp = Path(self._tmp.name)
        ServiceRegistry.reset()
        self._og = mod._global_services_dir
        self._ou = mod._user_services_dir
        mod._global_services_dir = lambda: tmp / "global_services"
        mod._user_services_dir = lambda: tmp / "user_services"
        self.reg = ServiceRegistry.get_instance()

    def tearDown(self):
        from core.service_registry import ServiceRegistry
        ServiceRegistry.reset()
        self.mod._global_services_dir = self._og
        self.mod._user_services_dir = self._ou
        self._tmp.cleanup()

    def test_enumerates_global_and_every_user(self):
        from core.service_registry import SCOPE_USER, SCOPE_GLOBAL
        self.reg.install(SCOPE_GLOBAL, "", "gsvc", SVC_TYPE)
        self.reg.install(SCOPE_USER, "alice", "asvc", SVC_TYPE)
        self.reg.install(SCOPE_USER, "bob", "bsvc", SVC_TYPE)

        scopes = self.reg.iter_all_scopes()
        kinds = {(s, owner) for (s, sid, owner, c) in scopes}
        self.assertIn(("global", ""), kinds)
        self.assertIn(("user", "alice"), kinds)
        self.assertIn(("user", "bob"), kinds)
        self.assertIn("asvc", self.reg.get_all("user", "alice"))
        self.assertIn("bsvc", self.reg.get_all("user", "bob"))

    def test_conv_pairs_without_services_excluded(self):
        from core.service_registry import SCOPE_USER
        self.reg.install(SCOPE_USER, "alice", "asvc", SVC_TYPE)
        scopes = self.reg.iter_all_scopes(conv_pairs=[("alice", "conv-empty")])
        self.assertFalse(any(s == "conv" for (s, sid, owner, c) in scopes))


class TestResourceStoreListAllGlobal(unittest.TestCase):
    """ResourceStore.list_all_global catalogs every owner, owner-tagged."""

    def setUp(self):
        import core.paths as paths
        from core.repository import ScopedRepository
        self._tmp = tempfile.TemporaryDirectory(prefix="pawflow_lag_")
        tmp = Path(self._tmp.name)
        self._paths = paths
        self._orig_data = paths.DATA_DIR
        self._orig_repo = paths.REPOSITORY_DIR
        paths.DATA_DIR = tmp
        paths.REPOSITORY_DIR = tmp / "repository"
        ScopedRepository.reset()

    def tearDown(self):
        from core.repository import ScopedRepository
        self._paths.DATA_DIR = self._orig_data
        self._paths.REPOSITORY_DIR = self._orig_repo
        ScopedRepository.reset()
        self._tmp.cleanup()

    def test_catalog_across_owners(self):
        from core.repository import ScopedRepository
        from core.resource_store import ResourceStore
        repo = ScopedRepository.instance()
        repo.create("prompts", "g", "global", {"title": "G"})
        repo.create("prompts", "pa", "user", {"title": "A"}, user_id="alice")
        repo.create("prompts", "pb", "user", {"title": "B"}, user_id="bob")

        rs = ResourceStore.instance()
        rows = rs.list_all_global("prompt")
        by_name = {r["name"]: r for r in rows}
        self.assertEqual(by_name["g"]["_owner_id"], "")
        self.assertEqual(by_name["g"]["_scope"], "global")
        self.assertEqual(by_name["pa"]["_owner_id"], "alice")
        self.assertEqual(by_name["pa"]["_scope"], "user")
        self.assertEqual(by_name["pb"]["_owner_id"], "bob")


if __name__ == "__main__":
    unittest.main()
