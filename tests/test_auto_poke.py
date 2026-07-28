"""Auto-poke: hand the turn back when it ended on unfinished plan work."""

from pathlib import Path

from core import auto_poke
from core.auto_poke import PokeLedger, poke_text, stalled_step

ROOT = Path(__file__).resolve().parents[1]


def _plan(status="in_progress", steps=None, plan_id="p_1", created_by="claude"):
    return {"id": plan_id, "status": status, "created_by": created_by,
            "steps": steps if steps is not None else []}


def _step(index=1, status="in_progress", assigned_to="claude", **extra):
    step = {"index": index, "status": status, "assigned_to": assigned_to,
            "description": f"step {index}", "note": ""}
    step.update(extra)
    return step


# -- what counts as stalled --------------------------------------------


def test_a_step_left_in_progress_by_this_agent_is_stalled():
    plan = _plan(steps=[_step(1, "done"), _step(2, "in_progress")])

    found = stalled_step([plan], "claude")

    assert found is not None
    assert found[1]["index"] == 2


def test_finished_paused_and_verification_steps_are_left_alone():
    # pending_verification waits on somebody else; poking its author would
    # produce a duplicate report.
    for status in ("done", "pending", "skipped", "error", "pending_verification"):
        plan = _plan(steps=[_step(1, status)])
        assert stalled_step([plan], "claude") is None, status

    paused = _plan(steps=[_step(1, "in_progress", paused=True)])
    assert stalled_step([paused], "claude") is None


def test_plans_awaiting_approval_or_already_over_are_left_alone():
    for status in ("pending_approval", "completed", "cancelled", "draft"):
        plan = _plan(status=status, steps=[_step(1, "in_progress")])
        assert stalled_step([plan], "claude") is None, status


def test_a_step_owned_by_another_agent_is_not_ours_to_poke():
    plan = _plan(steps=[_step(1, "in_progress", assigned_to="grok")])

    assert stalled_step([plan], "claude") is None
    assert stalled_step([plan], "grok") is not None


def test_unassigned_step_falls_back_to_the_plan_author():
    plan = _plan(created_by="claude", steps=[_step(1, "in_progress", assigned_to="")])

    assert stalled_step([plan], "claude") is not None
    assert stalled_step([plan], "grok") is None


def test_malformed_plans_do_not_raise():
    assert stalled_step([None, "nonsense", {}, {"status": "in_progress"}], "a") is None
    assert stalled_step(None, "a") is None


# -- the message -------------------------------------------------------


def test_poke_names_both_acceptable_exits():
    plan = _plan(steps=[_step(1), _step(2)])
    text = poke_text(plan, plan["steps"][1])

    assert "step 2/2" in text
    assert 'update_plan(plan_id="p_1"' in text
    assert '"status": "done"' in text
    assert "error" in text          # reporting a blocker is the other exit
    assert "do not skip ahead" in text.lower()


# -- the budget --------------------------------------------------------


def test_the_same_step_is_poked_at_most_limit_times():
    ledger = PokeLedger()
    plan = _plan(steps=[_step(1)])
    step = plan["steps"][0]

    assert ledger.claim("c", plan, step, 2) is True
    assert ledger.claim("c", plan, step, 2) is True
    assert ledger.claim("c", plan, step, 2) is False   # budget spent


def test_progress_buys_a_fresh_budget():
    ledger = PokeLedger()
    plan = _plan(steps=[_step(1), _step(2)])

    assert ledger.claim("c", plan, plan["steps"][0], 1) is True
    assert ledger.claim("c", plan, plan["steps"][0], 1) is False
    # The agent moved on: patience resets.
    assert ledger.claim("c", plan, plan["steps"][1], 1) is True


def test_a_note_written_on_the_step_counts_as_progress():
    ledger = PokeLedger()
    plan = _plan(steps=[_step(1)])

    assert ledger.claim("c", plan, plan["steps"][0], 1) is True
    assert ledger.claim("c", plan, plan["steps"][0], 1) is False
    plan["steps"][0]["note"] = "halfway, blocked on the relay"
    assert ledger.claim("c", plan, plan["steps"][0], 1) is True


def test_conversations_have_separate_budgets():
    ledger = PokeLedger()
    plan = _plan(steps=[_step(1)])

    assert ledger.claim("conv-a", plan, plan["steps"][0], 1) is True
    assert ledger.claim("conv-b", plan, plan["steps"][0], 1) is True


def test_zero_limit_never_claims():
    ledger = PokeLedger()
    plan = _plan(steps=[_step(1)])

    assert ledger.claim("c", plan, plan["steps"][0], 0) is False


def test_ledger_is_bounded():
    ledger = PokeLedger()
    plan_steps = _step(1)
    for i in range(auto_poke.MAX_TRACKED + 20):
        ledger.claim("c", _plan(plan_id=f"p_{i}"), plan_steps, 2)

    assert len(ledger._counts) <= auto_poke.MAX_TRACKED


def test_forget_drops_a_plan_budget():
    ledger = PokeLedger()
    plan = _plan(steps=[_step(1)])

    ledger.claim("c", plan, plan["steps"][0], 1)
    assert ledger.claim("c", plan, plan["steps"][0], 1) is False
    ledger.forget("c", "p_1")
    assert ledger.claim("c", plan, plan["steps"][0], 1) is True


# -- the decision ------------------------------------------------------


def test_poke_for_turn_returns_the_message_once_per_budget(monkeypatch):
    plan = _plan(steps=[_step(1, "in_progress")])
    monkeypatch.setenv("PAWFLOW_AUTO_POKE_LIMIT", "1")
    monkeypatch.setattr("core.plan_store.PlanStore.instance",
                        classmethod(lambda cls: type("S", (), {
                            "list_plans": lambda self, u, c: [plan]})()))
    monkeypatch.setattr(auto_poke.PokeLedger, "_instance", None)

    first = auto_poke.poke_for_turn("c", "u", "claude")
    second = auto_poke.poke_for_turn("c", "u", "claude")

    assert first is not None and "step 1/1" in first[2]
    assert second is None


def test_poke_for_turn_is_off_when_the_limit_is_zero(monkeypatch):
    # Disabled means no work at all — not "work, then discard the result".
    listed = []

    class _Store:
        def list_plans(self, user_id, conv_id):
            listed.append((user_id, conv_id))
            return [_plan(steps=[_step(1, "in_progress")])]

    monkeypatch.setenv("PAWFLOW_AUTO_POKE_LIMIT", "0")
    monkeypatch.setattr("core.plan_store.PlanStore.instance",
                        classmethod(lambda cls: _Store()))

    assert auto_poke.poke_for_turn("c", "u", "claude") is None
    assert listed == []


def test_poke_for_turn_requires_a_user_and_a_conversation(monkeypatch):
    monkeypatch.setenv("PAWFLOW_AUTO_POKE_LIMIT", "2")
    listed = []

    class _Store:
        def list_plans(self, user_id, conv_id):
            listed.append((user_id, conv_id))
            return []

    monkeypatch.setattr("core.plan_store.PlanStore.instance",
                        classmethod(lambda cls: _Store()))

    assert auto_poke.poke_for_turn("", "u", "claude") is None
    assert auto_poke.poke_for_turn("c", "", "claude") is None
    assert listed == []


def test_poke_for_turn_survives_an_unreadable_plan_store(monkeypatch):
    monkeypatch.setenv("PAWFLOW_AUTO_POKE_LIMIT", "2")

    def boom(*a, **k):
        raise RuntimeError("disk gone")

    monkeypatch.setattr("core.plan_store.PlanStore.instance", classmethod(boom))

    assert auto_poke.poke_for_turn("c", "u", "claude") is None


def test_limit_reads_environment_then_global_parameters(monkeypatch):
    monkeypatch.setenv("PAWFLOW_AUTO_POKE_LIMIT", "5")
    assert auto_poke.poke_limit() == 5

    monkeypatch.delenv("PAWFLOW_AUTO_POKE_LIMIT", raising=False)
    monkeypatch.setattr("core.expression._load_global_parameters",
                        lambda: {"auto_poke_limit": 0})
    assert auto_poke.poke_limit() == 0

    monkeypatch.setattr("core.expression._load_global_parameters", lambda: {})
    assert auto_poke.poke_limit() == auto_poke.DEFAULT_LIMIT


# -- wiring ------------------------------------------------------------


def test_poke_is_gated_on_a_clean_uninterrupted_turn():
    src = (ROOT / "tasks" / "ai" / "_agent_streaming_loop.py").read_text(encoding="utf-8")
    call = "self._maybe_poke_stalled_plan(ctx, conversation_id)"
    guard = src[src.index("# ── Auto-poke"):src.index(call)]

    # An error deserves the user's attention, and a force stop is a decision:
    # neither may be turned into another agent turn.
    assert "not _had_error" in guard
    assert "_is_current_generation(gen_key, my_generation)" in guard


def test_poke_and_orchestrator_share_one_delivery_path():
    # Two implementations of "hand text to an agent as if the user typed it"
    # would drift; the poke must reuse the orchestrator's.
    plans = (ROOT / "tasks" / "ai" / "actions" / "plans.py").read_text(encoding="utf-8")
    loop = (ROOT / "tasks" / "ai" / "_agent_streaming_loop.py").read_text(encoding="utf-8")

    assert "def deliver_agent_message(" in plans
    assert plans.count("skip_pre_persist") == 1
    assert "deliver_agent_message(" in loop


def test_queued_messages_take_precedence_over_a_poke():
    src = (ROOT / "tasks" / "ai" / "_agent_streaming_loop.py").read_text(encoding="utf-8")
    body = src[src.index("def _maybe_poke_stalled_plan"):src.index("def _maybe_generate_title")]

    assert "peek_count()" in body
    assert body.index("peek_count()") < body.index("poke_for_turn")
