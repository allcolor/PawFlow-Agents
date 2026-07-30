"""Passive memory recall: relevant memories without a tool call."""

import threading
import time
from pathlib import Path

from core import passive_recall
from core.passive_recall import PassiveRecall

ROOT = Path(__file__).resolve().parents[1]


class _Entry:
    def __init__(self, text, category="facts"):
        self.text = text
        self.category = category


def _fresh():
    """A detached instance — the singleton is shared across tests."""
    return PassiveRecall()


# -- rendering ---------------------------------------------------------


def test_render_trims_and_labels_entries():
    long_text = "x" * (passive_recall.MAX_ENTRY_CHARS + 50)
    block = passive_recall.render([(_Entry("Quentin uses compose"), 0.87),
                                   (_Entry(long_text, category=""), 0.51)])

    assert "- [facts] Quentin uses compose (0.87)" in block
    lines = block.splitlines()
    assert len(lines[1]) <= passive_recall.MAX_ENTRY_CHARS + 12
    assert lines[1].startswith("- x")


def test_render_skips_memories_already_in_the_static_digest():
    # The digest and the passive block share one prompt: quoting the same
    # memory twice wastes context and reads as emphasis.
    digest = "Key facts: Quentin runs PawFlow under docker compose"
    block = passive_recall.render(
        [(_Entry("Quentin runs PawFlow under docker compose"), 0.9),
         (_Entry("Chromium profile must be preserved"), 0.8)],
        exclude=digest)

    assert "docker compose" not in block
    assert "Chromium profile" in block


# -- scheduling --------------------------------------------------------


def test_take_returns_the_previous_turn_result_once():
    pr = _fresh()
    pr._ready[pr._key("u", "c", "a")] = "- something"

    assert pr.take("u", "c", "a") == "- something"
    # Cleared: a stale block must not outlive the topic that produced it.
    assert pr.take("u", "c", "a") == ""


def test_schedule_does_not_block_the_turn(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def slow_compute(*a, **k):
        started.set()
        release.wait(5)
        return "- late memory"

    pr = _fresh()
    monkeypatch.setattr(pr, "_compute", staticmethod(slow_compute))

    t0 = time.monotonic()
    assert pr.schedule("u", "c", "a", "what about the relay?") is True
    elapsed = time.monotonic() - t0

    assert elapsed < 0.5          # the caller returned while the embed runs
    assert started.wait(5)
    assert pr.take("u", "c", "a") == ""   # nothing ready yet — no stall, no block
    release.set()


def test_result_lands_for_the_next_turn(monkeypatch):
    pr = _fresh()
    monkeypatch.setattr(pr, "_compute", staticmethod(lambda *a, **k: "- a memory"))

    pr.schedule("u", "c", "a", "question")
    for _ in range(50):
        if pr.take("u", "c", "a") == "- a memory":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("background recall never produced a block")


def test_a_failing_embedder_never_breaks_the_turn(monkeypatch):
    pr = _fresh()

    def boom(*a, **k):
        raise RuntimeError("embedding provider down")

    monkeypatch.setattr(pr, "_compute", staticmethod(boom))

    # Called directly rather than through the thread: a raise inside a daemon
    # thread would be invisible here, and the property under test is that the
    # failure is swallowed at the source, with the in-flight slot released.
    key = pr._key("u", "c", "a")
    pr._in_flight.add(key)
    pr._generation[key] = 1
    pr._run(key, 1, "u", "c", "a", "question", "", 5)

    assert pr.take("u", "c", "a") == ""
    assert pr._in_flight == set()

    assert pr.schedule("u", "c", "a", "question") is True
    time.sleep(0.2)
    assert pr.take("u", "c", "a") == ""
    assert pr._in_flight == set()


def test_concurrent_schedules_do_not_pile_up(monkeypatch):
    pr = _fresh()
    release = threading.Event()
    calls = []

    def slow(*a, **k):
        calls.append(1)
        release.wait(5)
        return ""

    monkeypatch.setattr(pr, "_compute", staticmethod(slow))

    assert pr.schedule("u", "c", "a", "first") is True
    assert pr.schedule("u", "c", "a", "second") is False   # one in flight already
    release.set()
    time.sleep(0.1)
    assert len(calls) == 1


def test_a_recall_overtaken_by_a_newer_turn_is_dropped(monkeypatch):
    """A slow recall must not surface once its question is gone.

    The next turn is skipped while one is in flight -- that part is by design.
    What was not: the slow answer still landed in _ready, and the turn after
    it, on another subject entirely, was handed memories fetched for a
    question nobody was asking anymore.
    """
    pr = _fresh()
    release = threading.Event()

    def slow(*a, **k):
        release.wait(5)
        return "- memories about the FIRST question"

    monkeypatch.setattr(pr, "_compute", staticmethod(slow))

    assert pr.schedule("u", "c", "a", "first question") is True
    # The user moved on; this turn is skipped because one is still running.
    assert pr.schedule("u", "c", "a", "a completely different one") is False
    release.set()
    time.sleep(0.2)

    assert pr.take("u", "c", "a") == "", (
        "a block computed for a question two turns old was served anyway")


def test_a_recall_nobody_overtook_is_still_delivered(monkeypatch):
    pr = _fresh()
    monkeypatch.setattr(pr, "_compute",
                        staticmethod(lambda *a, **k: "- on topic"))

    assert pr.schedule("u", "c", "a", "the question") is True
    time.sleep(0.2)
    assert pr.take("u", "c", "a") == "- on topic"


def test_empty_turn_and_disabled_limit_are_skipped(monkeypatch):
    pr = _fresh()

    def never(*a, **k):
        raise AssertionError("should not run")

    monkeypatch.setattr(pr, "_compute", staticmethod(never))

    assert pr.schedule("u", "c", "a", "   ") is False
    assert pr.schedule("", "c", "a", "text") is False
    monkeypatch.setenv("PAWFLOW_PASSIVE_RECALL_LIMIT", "0")
    assert pr.schedule("u", "c", "a", "text") is False


def test_pending_results_are_bounded(monkeypatch):
    pr = _fresh()
    monkeypatch.setattr(pr, "_compute", staticmethod(lambda *a, **k: "- x"))
    for i in range(passive_recall.MAX_TRACKED + 10):
        pr._ready[pr._key("u", f"c{i}", "a")] = "- x"
    _k = pr._key("u", "cN", "a")
    pr._generation[_k] = 1
    pr._run(_k, 1, "u", "cN", "a", "q", "", 5)

    assert len(pr._ready) <= passive_recall.MAX_TRACKED


def test_limit_reads_environment_then_global_parameters(monkeypatch):
    monkeypatch.setenv("PAWFLOW_PASSIVE_RECALL_LIMIT", "3")
    assert passive_recall.recall_limit() == 3

    monkeypatch.delenv("PAWFLOW_PASSIVE_RECALL_LIMIT", raising=False)
    monkeypatch.setattr("core.expression._load_global_parameters",
                        lambda: {"passive_recall_limit": 2})
    assert passive_recall.recall_limit() == 2

    monkeypatch.setattr("core.expression._load_global_parameters", lambda: {})
    assert passive_recall.recall_limit() == passive_recall.DEFAULT_LIMIT


# -- search behaviour --------------------------------------------------


def test_compute_drops_weak_matches_and_caps_the_result(monkeypatch):
    hits = [(_Entry("strong"), 0.90), (_Entry("ok"), 0.55),
            (_Entry("weak"), 0.20), (_Entry("noise"), 0.05)]

    class _Store:
        def semantic_recall(self, user_id, vector, limit=10, agent_name="",
                            conversation_id=""):
            assert vector == [0.1, 0.2]
            return hits

    monkeypatch.setattr("core.embeddings.build_memory_embed_fn",
                        lambda **k: (lambda text: [0.1, 0.2]))
    monkeypatch.setattr("core.memory_store.MemoryStore.instance",
                        classmethod(lambda cls: _Store()))

    block = PassiveRecall._compute("u", "c", "a", "query", "", 5)

    assert "strong" in block and "ok" in block
    assert "weak" not in block and "noise" not in block


def test_no_embedding_provider_yields_no_block(monkeypatch):
    monkeypatch.setattr("core.embeddings.build_memory_embed_fn",
                        lambda **k: (lambda text: []))

    assert PassiveRecall._compute("u", "c", "a", "query", "", 5) == ""


# -- wiring ------------------------------------------------------------


def test_context_builder_takes_then_schedules():
    src = (ROOT / "tasks" / "ai" / "_agentctx_p3.py").read_text(encoding="utf-8")
    take_at = src.index("passive_recall.pending_block(")
    schedule_at = src.index("passive_recall.schedule_for_turn(")

    # Take before schedule: the block served this turn is the previous turn's
    # result, not the one still being computed.
    assert take_at < schedule_at
    assert "_add_digest(passive_recall.BLOCK_TITLE" in src
