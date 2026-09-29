"""Concurrent saves of one service scope must not share a temp file."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tasks import register_all_tasks
register_all_tasks()

SVC_TYPE = "cacheService"


class TestServiceSaveRace(unittest.TestCase):

    def setUp(self):
        import core.service_registry as mod
        from core.service_registry import ServiceRegistry
        self.mod = mod
        self._tmp = tempfile.TemporaryDirectory(prefix="pawflow_save_race_")
        tmp = Path(self._tmp.name)
        ServiceRegistry.reset()
        self._og = mod._global_services_dir
        self._ou = mod._user_services_dir
        mod._global_services_dir = lambda: tmp / "global_services"
        mod._user_services_dir = lambda: tmp / "user_services"
        self.svc_dir = tmp / "user_services" / "alice"
        self.reg = ServiceRegistry.get_instance()

    def tearDown(self):
        from core.service_registry import ServiceRegistry
        ServiceRegistry.reset()
        self.mod._global_services_dir = self._og
        self.mod._user_services_dir = self._ou
        self._tmp.cleanup()

    def test_interleaved_saves_both_land(self):
        from core.service_registry import SCOPE_USER
        self.reg.install(SCOPE_USER, "alice", "asvc", SVC_TYPE)

        real_write_text = Path.write_text
        state = {"nested": False}

        def write_then_interleave(path, *args, **kwargs):
            result = real_write_text(path, *args, **kwargs)
            # A second save runs between the first save's write and rename,
            # as two request threads saving the same scope do.
            if path.suffix == ".tmp" and not state["nested"]:
                state["nested"] = True
                self.reg._save(SCOPE_USER, "alice")
            return result

        with patch.object(Path, "write_text", write_then_interleave), \
                self.assertNoLogs("core._service_registry_io", level="ERROR"):
            self.reg._save(SCOPE_USER, "alice")

        self.assertTrue(state["nested"])
        self.assertTrue((self.svc_dir / "asvc.json").exists())
        self.assertEqual(list(self.svc_dir.glob("*.tmp")), [])
        self.assertEqual(list(self.svc_dir.glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
