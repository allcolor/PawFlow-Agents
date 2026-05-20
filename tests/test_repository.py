"""Tests for ScopedRepository."""

import json
import tempfile
import unittest
from pathlib import Path

from core.paths import repo_dir, repo_file, parse_flow_fqn
from core.repository import ScopedRepository


class TestPaths(unittest.TestCase):

    def test_repo_dir_global(self):
        import core.paths as _p
        p = repo_dir("agents", "global")
        self.assertEqual(p, _p.REPOSITORY_DIR / "agents" / "global")

    def test_repo_dir_user(self):
        import core.paths as _p
        p = repo_dir("agents", "user", "u1")
        self.assertEqual(p, _p.REPOSITORY_DIR / "agents" / "users" / "u1")

    def test_repo_dir_conv(self):
        import core.paths as _p
        p = repo_dir("agents", "conv", "u1", "c1")
        self.assertEqual(p, _p.REPOSITORY_DIR / "agents" / "users" / "u1" / "c1")

    def test_repo_file_md(self):
        import core.paths as _p
        p = repo_file("skills", "summarize", "global")
        self.assertEqual(p, _p.REPOSITORY_DIR / "skills" / "global" / "summarize.md")

    def test_repo_file_json(self):
        import core.paths as _p
        p = repo_file("mcps", "myserver", "global")
        self.assertEqual(p, _p.REPOSITORY_DIR / "mcps" / "global" / "myserver.json")

    def test_parse_flow_fqn_with_version(self):
        pkg, name, ver = parse_flow_fqn("pawflow.demo.ingest:2.3.1")
        self.assertEqual(pkg, "pawflow.demo")
        self.assertEqual(name, "ingest")
        self.assertEqual(ver, "2.3.1")

    def test_parse_flow_fqn_without_version(self):
        pkg, name, ver = parse_flow_fqn("myco.etl.daily")
        self.assertEqual(pkg, "myco.etl")
        self.assertEqual(name, "daily")
        self.assertEqual(ver, "")


class TestScopedRepository(unittest.TestCase):

    def setUp(self):
        # Per-test tmpdir — never touch the real data/ tree.
        # conftest.py redirects paths.* session-wide, but ScopedRepository
        # wants a fresh per-test root so tests don't see each other's state.
        import core.paths as paths
        self._tmp = tempfile.TemporaryDirectory(prefix="pawflow_repo_")
        self._orig_data = paths.DATA_DIR
        self._orig_repo = paths.REPOSITORY_DIR
        tmp_root = Path(self._tmp.name)
        paths.DATA_DIR = tmp_root
        paths.REPOSITORY_DIR = tmp_root / "repository"
        ScopedRepository.reset()
        self.repo = ScopedRepository.instance()

    def tearDown(self):
        import core.paths as paths
        paths.DATA_DIR = self._orig_data
        paths.REPOSITORY_DIR = self._orig_repo
        ScopedRepository.reset()
        self._tmp.cleanup()

    # ── CRUD ───────────────────────────────────────────

    def test_create_and_get_global(self):
        self.repo.create("agents", "test-agent", "global",
                         {"prompt": "You are a test agent"})
        result = self.repo.get("agents", "test-agent", "global")
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "test-agent")
        self.assertEqual(result["prompt"], "You are a test agent")

    def test_create_and_get_user(self):
        self.repo.create("skills", "my-skill", "user",
                         {
                             "description": "Summarize text",
                             "instructions": "Summarize",
                         }, user_id="u1")
        result = self.repo.get("skills", "my-skill", "user", user_id="u1")
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "my-skill")
        self.assertEqual(result["description"], "Summarize text")
        self.assertEqual(result["instructions"], "Summarize")

    def test_create_and_get_conv(self):
        self.repo.create("agents", "draft", "conv",
                         {"prompt": "Draft"}, user_id="u1", conv_id="c1")
        result = self.repo.get("agents", "draft", "conv",
                               user_id="u1", conv_id="c1")
        self.assertIsNotNone(result)

    def test_create_duplicate_raises(self):
        self.repo.create("agents", "x", "global", {"prompt": "a"})
        with self.assertRaises(ValueError):
            self.repo.create("agents", "x", "global", {"prompt": "b"})

    def test_same_name_different_scopes(self):
        self.repo.create("agents", "researcher", "global",
                         {"prompt": "global version"})
        self.repo.create("agents", "researcher", "user",
                         {"prompt": "user version"}, user_id="u1")
        g = self.repo.get("agents", "researcher", "global")
        u = self.repo.get("agents", "researcher", "user", user_id="u1")
        self.assertEqual(g["prompt"], "global version")
        self.assertEqual(u["prompt"], "user version")

    def test_update(self):
        self.repo.create("agents", "a", "global", {"prompt": "v1"})
        self.repo.update("agents", "a", "global", {"prompt": "v2"})
        result = self.repo.get("agents", "a", "global")
        self.assertEqual(result["prompt"], "v2")

    def test_update_not_found_raises(self):
        with self.assertRaises(KeyError):
            self.repo.update("agents", "nope", "global", {"prompt": "x"})

    def test_delete(self):
        self.repo.create("agents", "del-me", "global", {"prompt": "x"})
        self.assertTrue(self.repo.delete("agents", "del-me", "global"))
        self.assertIsNone(self.repo.get("agents", "del-me", "global"))

    def test_delete_not_found(self):
        self.assertFalse(self.repo.delete("agents", "nope", "global"))

    # ── List ───────────────────────────────────────────

    def test_list_scope(self):
        self.repo.create("agents", "a1", "global", {"prompt": "x"})
        self.repo.create("agents", "a2", "global", {"prompt": "y"})
        result = self.repo.list("agents", "global")
        names = [r["name"] for r in result]
        self.assertIn("a1", names)
        self.assertIn("a2", names)
        self.assertEqual(len(result), 2)

    def test_list_available(self):
        self.repo.create("agents", "g1", "global", {"prompt": "x"})
        self.repo.create("agents", "u1", "user",
                         {"prompt": "y"}, user_id="alice")
        self.repo.create("agents", "c1", "conv",
                         {"prompt": "z"}, user_id="alice", conv_id="conv1")
        result = self.repo.list_available("agents", "alice", "conv1")
        names = [r["name"] for r in result]
        self.assertIn("g1", names)
        self.assertIn("u1", names)
        self.assertIn("c1", names)
        # Each has _scope
        scopes = [r["_scope"] for r in result]
        self.assertIn("global", scopes)
        self.assertIn("user:alice", scopes)
        self.assertIn("conv:alice/conv1", scopes)

    # ── Promote / Demote ─────────────────────────────────

    def test_promote_conv_to_user(self):
        self.repo.create("agents", "draft", "conv",
                         {"prompt": "test"}, user_id="u1", conv_id="c1")
        self.repo.promote("agents", "draft", "conv", "user",
                          user_id="u1", conv_id="c1")
        # Both exist now
        self.assertIsNotNone(
            self.repo.get("agents", "draft", "conv",
                          user_id="u1", conv_id="c1"))
        self.assertIsNotNone(
            self.repo.get("agents", "draft", "user", user_id="u1"))

    def test_promote_with_move(self):
        self.repo.create("agents", "draft", "conv",
                         {"prompt": "test"}, user_id="u1", conv_id="c1")
        self.repo.promote("agents", "draft", "conv", "user",
                          user_id="u1", conv_id="c1", move=True)
        # Source gone
        self.assertIsNone(
            self.repo.get("agents", "draft", "conv",
                          user_id="u1", conv_id="c1"))
        # Target exists
        self.assertIsNotNone(
            self.repo.get("agents", "draft", "user", user_id="u1"))

    def test_promote_wrong_direction_raises(self):
        self.repo.create("agents", "a", "global", {"prompt": "x"})
        with self.assertRaises(ValueError):
            self.repo.promote("agents", "a", "global", "user",
                              user_id="u1")

    def test_demote_global_to_user(self):
        self.repo.create("agents", "base", "global", {"prompt": "x"})
        self.repo.demote("agents", "base", "global", "user",
                         user_id="u1")
        self.assertIsNotNone(
            self.repo.get("agents", "base", "user", user_id="u1"))

    def test_promote_target_exists_raises(self):
        self.repo.create("agents", "a", "conv",
                         {"prompt": "x"}, user_id="u1", conv_id="c1")
        self.repo.create("agents", "a", "user",
                         {"prompt": "y"}, user_id="u1")
        with self.assertRaises(ValueError):
            self.repo.promote("agents", "a", "conv", "user",
                              user_id="u1", conv_id="c1")

    # ── Flow operations ───────────────────────────────────

    def test_create_flow(self):
        self.repo.create_flow(
            "myco.etl.daily:1.0.0", "global",
            {"tasks": {}, "relations": []})
        result = self.repo.get_flow("myco.etl.daily:1.0.0", "global")
        self.assertIsNotNone(result)
        self.assertEqual(result["fqn"], "myco.etl.daily:1.0.0")
        self.assertEqual(result["package"], "myco.etl")
        self.assertEqual(result["name"], "daily")
        self.assertEqual(result["version"], "1.0.0")

    def test_get_flow_latest(self):
        self.repo.create_flow(
            "pkg.flow1:1.0.0", "global", {"tasks": {}})
        result = self.repo.get_flow("pkg.flow1", "global")
        self.assertIsNotNone(result)
        self.assertEqual(result["version"], "1.0.0")

    def test_publish_new_version(self):
        self.repo.create_flow(
            "pkg.flow1:1.0.0", "global", {"tasks": {"a": {}}})
        self.repo.publish_flow_version(
            "pkg.flow1:2.0.0", "global", {"tasks": {"a": {}, "b": {}}})
        latest = self.repo.get_flow("pkg.flow1", "global")
        self.assertEqual(latest["version"], "2.0.0")
        # Old version still exists
        old = self.repo.get_flow("pkg.flow1:1.0.0", "global")
        self.assertIsNotNone(old)

    def test_list_flow_versions(self):
        self.repo.create_flow("pkg.f:1.0.0", "global", {})
        self.repo.publish_flow_version("pkg.f:1.1.0", "global", {})
        self.repo.publish_flow_version("pkg.f:2.0.0", "global", {})
        versions = self.repo.list_flow_versions("pkg.f", "global")
        self.assertEqual(versions, ["1.0.0", "1.1.0", "2.0.0"])

    def test_rollback_flow(self):
        self.repo.create_flow("pkg.f:1.0.0", "global", {})
        self.repo.publish_flow_version("pkg.f:2.0.0", "global", {})
        self.repo.rollback_flow("pkg.f", "1.0.0", "global")
        latest = self.repo.get_flow("pkg.f", "global")
        self.assertEqual(latest["version"], "1.0.0")

    def test_promote_flow(self):
        self.repo.create_flow(
            "pkg.f:1.0.0", "conv",
            {"tasks": {}}, user_id="u1", conv_id="c1")
        self.repo.promote("flows", "pkg.f", "conv", "user",
                          user_id="u1", conv_id="c1")
        result = self.repo.get_flow("pkg.f:1.0.0", "user", user_id="u1")
        self.assertIsNotNone(result)

    def test_create_flow_no_version_raises(self):
        with self.assertRaises(ValueError):
            self.repo.create_flow("pkg.f", "global", {})


if __name__ == "__main__":
    unittest.main()
