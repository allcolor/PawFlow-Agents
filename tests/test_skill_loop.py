"""Tests for the skill learning loop: drafts, stats, footer, curator."""

import json
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import core.skill_stats as skill_stats
from core.memory_store import MemoryStore
from core.skill_loop import (
    SKILL_IMPROVE_FOOTER,
    SKILL_LOOP_HINT,
    propose_skill_draft_from_summary,
)


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def clone_for_call(self):
        return self

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return MagicMock(content=json.dumps(self.payload))


@pytest.fixture()
def mem_store():
    tmpdir = tempfile.mkdtemp()
    MemoryStore.reset()
    store = MemoryStore(store_dir=tmpdir)
    MemoryStore._instance = store
    yield store
    MemoryStore.reset()
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture()
def stats_file(tmp_path, monkeypatch):
    path = tmp_path / "skill_stats.json"
    monkeypatch.setattr(skill_stats, "_STATS_FILE", path)
    skill_stats.reset_for_tests()
    yield path
    skill_stats.reset_for_tests()


class TestSkillDraftProposal:
    def test_stores_draft_memory(self, mem_store):
        client = _FakeClient({"skill": {
            "name": "Restart Foo Daemon!",
            "description": "Restart the foo daemon after config edits",
            "steps": ["edit config", "run foo --reload", "check status"],
            "trigger": "foo config changed",
        }})
        ok = propose_skill_draft_from_summary(
            "u1", "long summary", llm_client=client, conversation_id="c1")
        assert ok is True
        entries = mem_store.list_all("u1")
        assert len(entries) == 1
        e = entries[0]
        assert e.text.startswith("Skill draft: `restart-foo-daemon`")
        assert "skill-draft" in e.tags
        assert e.category == "discoveries"
        assert e.conversation_id == "c1"
        assert e.expires_at > time.time()

    def test_null_skill_stores_nothing(self, mem_store):
        client = _FakeClient({"skill": None})
        ok = propose_skill_draft_from_summary(
            "u1", "summary", llm_client=client, conversation_id="c1")
        assert ok is False
        assert mem_store.list_all("u1") == []

    def test_duplicate_draft_not_stored_twice(self, mem_store):
        payload = {"skill": {
            "name": "one-trick",
            "description": "desc",
            "steps": ["a"],
        }}
        assert propose_skill_draft_from_summary(
            "u1", "s", llm_client=_FakeClient(payload),
            conversation_id="c1")
        assert not propose_skill_draft_from_summary(
            "u1", "s2", llm_client=_FakeClient(payload),
            conversation_id="c2")
        assert len(mem_store.list_all("u1")) == 1

    def test_no_client_is_noop(self, mem_store):
        assert propose_skill_draft_from_summary("u1", "s") is False

    def test_invalid_payload_is_noop(self, mem_store):
        client = _FakeClient({"skill": {"name": "x"}})  # missing fields
        assert not propose_skill_draft_from_summary(
            "u1", "s", llm_client=client)
        assert mem_store.list_all("u1") == []

    def test_hint_mentions_manage_resource(self):
        assert "manage_resource" in SKILL_LOOP_HINT
        assert "skill-draft" in SKILL_LOOP_HINT


class TestSkillStats:
    def test_record_and_get(self, stats_file):
        s1 = skill_stats.record_load("u1", "deploy", "c1", "coder")
        assert s1["loads"] == 1
        s2 = skill_stats.record_load("u1", "deploy", "c2", "coder")
        assert s2["loads"] == 2
        assert set(s2["conversations"]) == {"c1", "c2"}
        got = skill_stats.get_stats("u1", "deploy")
        assert got["loads"] == 2
        assert got["last_used_at"] > 0

    def test_persisted_across_reload(self, stats_file):
        skill_stats.record_load("u1", "deploy", "c1")
        skill_stats.reset_for_tests()
        assert skill_stats.get_stats("u1", "deploy")["loads"] == 1
        assert Path(stats_file).exists()

    def test_stats_for_user_scoping(self, stats_file):
        skill_stats.record_load("u1", "a")
        skill_stats.record_load("u2", "b")
        per_user = skill_stats.stats_for_user("u1")
        assert list(per_user) == ["a"]

    def test_conversation_list_bounded(self, stats_file):
        for i in range(12):
            skill_stats.record_load("u1", "s", f"c{i}")
        convs = skill_stats.get_stats("u1", "s")["conversations"]
        assert len(convs) == 8
        assert convs[-1] == "c11"


class TestLoadSkillFooter:
    def _handler(self):
        from core.handlers.skills import LoadSkillHandler
        h = LoadSkillHandler()
        h.set_user_id("u1")
        h.set_conversation_id("c1")
        h.set_agent_name("coder")
        return h

    def test_footer_appended(self, stats_file, monkeypatch):
        monkeypatch.setattr(
            "core.skill_resolver.resolve_assigned_skill_prompt",
            lambda *a: "SKILL BODY")
        out = self._handler().execute({"name": "deploy"})
        assert out.startswith("SKILL BODY")
        assert SKILL_IMPROVE_FOOTER in out
        assert skill_stats.get_stats("u1", "deploy")["loads"] == 1

    def test_promotion_suggested_for_conv_scope(self, stats_file, monkeypatch):
        monkeypatch.setattr(
            "core.skill_resolver.resolve_assigned_skill_prompt",
            lambda *a: "SKILL BODY")
        fake_rs = MagicMock()
        fake_rs.get_any.return_value = {"_scope": "conversation"}
        monkeypatch.setattr(
            "core.resource_store.ResourceStore.instance",
            staticmethod(lambda: fake_rs))
        h = self._handler()
        h.execute({"name": "deploy"})
        h.execute({"name": "deploy"})
        out = h.execute({"name": "deploy"})
        assert "promote it to user scope" in out

    def test_no_promotion_for_user_scope(self, stats_file, monkeypatch):
        monkeypatch.setattr(
            "core.skill_resolver.resolve_assigned_skill_prompt",
            lambda *a: "SKILL BODY")
        fake_rs = MagicMock()
        fake_rs.get_any.return_value = {"_scope": "user"}
        monkeypatch.setattr(
            "core.resource_store.ResourceStore.instance",
            staticmethod(lambda: fake_rs))
        h = self._handler()
        for _ in range(3):
            out = h.execute({"name": "deploy"})
        assert "promote it to user scope" not in out

    def test_error_returns_bare_block(self, stats_file, monkeypatch):
        monkeypatch.setattr(
            "core.skill_resolver.resolve_assigned_skill_prompt",
            lambda *a: "SKILL BODY")
        monkeypatch.setattr(
            "core.skill_stats.record_load",
            MagicMock(side_effect=RuntimeError("boom")))
        out = self._handler().execute({"name": "deploy"})
        assert out == "SKILL BODY"


class TestSkillCuratorTask:
    def _run(self, monkeypatch, stats, skills, config=None):
        from core import FlowFile
        from tasks.system.skill_curator import SkillCuratorTask
        fake_rs = MagicMock()
        fake_rs.list_all.return_value = skills
        monkeypatch.setattr(
            "core.resource_store.ResourceStore.instance",
            staticmethod(lambda: fake_rs))
        monkeypatch.setattr(
            "core.skill_stats.stats_for_user", lambda uid: stats)
        task = SkillCuratorTask(dict({"user_id": "u1"}, **(config or {})))
        ff = FlowFile()
        results = task.execute(ff)
        assert len(results) == 1
        return json.loads(results[0].get_content().decode("utf-8"))

    def test_flags_never_loaded_and_stale(self, monkeypatch):
        now = time.time()
        skills = [
            {"name": "fresh", "_scope": "user", "description": "d"},
            {"name": "old", "_scope": "user", "description": "d"},
            {"name": "never", "_scope": "user", "description": "d"},
        ]
        stats = {
            "fresh": {"loads": 5, "last_used_at": now - 3600},
            "old": {"loads": 2, "last_used_at": now - 200 * 86400},
        }
        report = self._run(monkeypatch, stats, skills)
        by_name = {s["name"]: s["status"] for s in report["skills"]}
        assert by_name == {"fresh": "active", "old": "stale",
                           "never": "never_loaded"}
        assert report["flagged"] == 2
        assert {p["name"] for p in report["proposed_actions"]} == {
            "old", "never"}
        # Report-only contract: no LLM configured, actions default to review
        assert all(p["action"] == "review"
                   for p in report["proposed_actions"])

    def test_global_scope_excluded_by_default(self, monkeypatch):
        skills = [{"name": "g", "_scope": "global", "description": ""}]
        report = self._run(monkeypatch, {}, skills)
        assert report["total_skills"] == 0

    def test_missing_user_id_errors(self):
        from core import FlowFile
        from tasks.system.skill_curator import SkillCuratorTask
        with pytest.raises(ValueError):
            SkillCuratorTask({})
        # Empty user_id passes required-param validation but is refused
        task = SkillCuratorTask({"user_id": "  "})
        out = task.execute(FlowFile())
        assert out[0].get_attribute("skill.curator.error")

    def test_registered(self):
        from tasks import register_all_tasks
        register_all_tasks()
        from core import TaskFactory
        assert TaskFactory.get("skillCurator") is not None
