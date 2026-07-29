"""Periodic self-reflection nudge (learning loop P5).

The trigger has to be silent most of the time. A block that appears every
turn is one the agent learns to skip, which costs the tokens without buying
the reflection -- so the tests pin when it stays quiet as hard as when it
fires.
"""

import time
from pathlib import Path

import pytest

import core.paths as _paths
from core import reflection_trigger as rt
from core.agent_diary import AgentDiary

USER = "alice"
AGENT = "claude"


@pytest.fixture
def diary(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "MEMORIES_DIR", tmp_path / "memories")
    AgentDiary._instance = None
    d = AgentDiary(store_dir=str(tmp_path / "memories"))
    yield d
    AgentDiary._instance = None


def _write(diary, n, entry_type="observation", agent=AGENT):
    for i in range(n):
        diary.write(USER, agent, f"{entry_type} number {i}", entry_type=entry_type)


class TestWhenItStaysQuiet:

    def test_an_empty_diary_triggers_nothing(self, diary):
        assert rt.pending_block(USER, AGENT, diary=diary) == ""

    def test_below_the_entry_threshold_triggers_nothing(self, diary):
        _write(diary, rt.MIN_ENTRIES_SINCE - 1)
        assert rt.reflection_due(USER, AGENT, diary=diary) == 0

    def test_a_recent_reflection_holds_it_off(self, diary):
        diary.write(USER, AGENT, "synthesis", entry_type="reflection")
        _write(diary, rt.MIN_ENTRIES_SINCE + 3)

        # Enough entries, but the last reflection is minutes old.
        assert rt.reflection_due(USER, AGENT, diary=diary) == 0

    def test_a_missing_user_or_agent_is_not_an_error(self, diary):
        assert rt.pending_block("", AGENT, diary=diary) == ""
        assert rt.pending_block(USER, "", diary=diary) == ""

    def test_an_unreadable_diary_stays_silent(self, diary):
        class _Broken:
            def read(self, *a, **k):
                raise OSError("disk gone")

        assert rt.pending_block(USER, AGENT, diary=_Broken()) == ""


class TestWhenItFires:

    def test_enough_entries_and_no_reflection_yet(self, diary):
        _write(diary, rt.MIN_ENTRIES_SINCE)

        block = rt.pending_block(USER, AGENT, diary=diary)

        assert "diary_write" in block and "reflection" in block
        assert str(rt.MIN_ENTRIES_SINCE) in block

    def test_an_old_reflection_no_longer_holds_it_off(self, diary):
        old = time.time() - rt.MIN_INTERVAL_S - 60
        record = diary.write(USER, AGENT, "synthesis", entry_type="reflection")
        path = diary._diary_path(USER, AGENT)
        path.write_text(
            path.read_text().replace(str(record["ts"]), str(old)), "utf-8")
        _write(diary, rt.MIN_ENTRIES_SINCE)

        assert rt.reflection_due(USER, AGENT, diary=diary) == rt.MIN_ENTRIES_SINCE

    def test_only_entries_after_the_last_reflection_count(self, diary):
        _write(diary, 10)
        diary.write(USER, AGENT, "synthesis", entry_type="reflection")
        _write(diary, 2)

        count, last_ts = rt.entries_since_last_reflection(USER, AGENT,
                                                          diary=diary)

        assert count == 2 and last_ts > 0

    def test_the_nudge_asks_for_kg_and_skill_follow_up(self, diary):
        _write(diary, rt.MIN_ENTRIES_SINCE)

        block = rt.pending_block(USER, AGENT, diary=diary)

        assert "kg_add" in block and "manage_resource" in block

    def test_each_agent_is_counted_on_its_own_diary(self, diary):
        _write(diary, rt.MIN_ENTRIES_SINCE, agent="other")

        assert rt.pending_block(USER, AGENT, diary=diary) == ""
        assert rt.pending_block(USER, "other", diary=diary) != ""


class TestPromptWiring:

    SRC = Path(__file__).resolve().parent.parent / "tasks" / "ai" / "_agentctx_p3.py"

    def test_the_block_is_injected_next_to_the_diary_digest(self):
        # The nudge is worthless if it never reaches the prompt; this pins the
        # call site rather than the rendering.
        src = self.SRC.read_text("utf-8")
        assert "reflection_trigger.pending_block" in src
        assert "reflection_trigger.BLOCK_TITLE" in src

    def test_conversation_search_is_advertised_to_the_agent(self):
        src = self.SRC.read_text("utf-8")
        assert "conversation_search" in src
