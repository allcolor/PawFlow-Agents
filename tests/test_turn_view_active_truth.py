"""The active-agents set is the single truth for a turn block's "working".

Regression for the block stuck "En cours" after the answer was delivered:
the turn-id mismatch guard in turnViewFinalize/turnViewFail refused every
terminal event when the block adopted a turn id the server never named
(interrupted/drained turns), and nothing else could ever close the block.

Invariant: en cours <=> the agent is in active agents.
- The mismatch guard may only refuse when a LIVE successor actually exists
  in activeInteractions (_turnLiveSuccessorExists).
- The authoritative list_active poll reconciles: no active agent => an open
  "working" block closes (turnViewSyncActive), as a reopenable guess so a
  racing live row can reopen it (the inverse bug: closed-while-running).
"""

from pathlib import Path

TURN_VIEW_JS = Path("tasks/io/chat_ui/turn_view.js").read_text(encoding="utf-8")
ACTIVE_AGENTS_JS = Path("tasks/io/chat_ui/active_agents.js").read_text(encoding="utf-8")


def test_finalize_mismatch_guard_requires_live_successor():
    finalize = TURN_VIEW_JS[TURN_VIEW_JS.index("function turnViewFinalize"):
                            TURN_VIEW_JS.index("function turnViewFail")]
    assert "const stateRuntimeId = _turnRuntimeId(state)" in finalize
    assert "incomingTurnId !== stateRuntimeId" in finalize
    assert "_turnLiveSuccessorExists(incomingTurnId)" in finalize


def test_fail_mismatch_guard_requires_live_successor():
    fail = TURN_VIEW_JS[TURN_VIEW_JS.index("function turnViewFail"):
                        TURN_VIEW_JS.index("function _turnLiveSuccessorExists")]
    assert "turnId !== _turnRuntimeId(state)" in fail
    assert "_turnLiveSuccessorExists(turnId)" in fail


def test_live_successor_reads_active_interactions_and_defaults_live():
    helper = TURN_VIEW_JS[TURN_VIEW_JS.index("function _turnLiveSuccessorExists"):
                          TURN_VIEW_JS.index("function turnViewSyncActive")]
    # No truth available => assume live (old conservative behavior).
    assert "typeof activeInteractions === 'undefined'" in helper
    assert "return true" in helper
    assert "Object.values(activeInteractions)" in helper


def test_sync_active_closes_working_block_as_reopenable_guess():
    sync = TURN_VIEW_JS[TURN_VIEW_JS.index("function turnViewSyncActive"):]
    sync = sync[:sync.index("\nfunction ", 1)] if "\nfunction " in sync[1:] else sync
    assert "state.status !== 'working'" in sync
    # Reopenable: a live row arriving right after must be able to reopen it.
    assert "state.closedByGuess = true" in sync
    assert "_turnUpdateStatus(state, 'completed')" in sync
    # The reopen path this relies on exists in ingest.
    assert "state.closedByGuess && state.status === 'completed'" in TURN_VIEW_JS


def test_poll_reconciles_turn_block_with_server_truth():
    sync_fn = ACTIVE_AGENTS_JS[ACTIVE_AGENTS_JS.index("function syncActiveFromServer"):]
    assert "turnViewSyncActive(hasActiveForCurrentConv)" in sync_fn
    # The poll stays periodic — the safety net must fire without user action.
    assert "setInterval(syncActiveFromServer" in ACTIVE_AGENTS_JS
