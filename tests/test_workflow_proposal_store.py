import pytest

from core.workflow_proposal_store import (
    ACCEPTED,
    CANCELLED,
    PLANNER_REVIEW,
    USER_REVIEW,
    ProposalConflict,
    WorkflowProposalStore,
    definition_digest,
)


@pytest.fixture
def store(tmp_path):
    return WorkflowProposalStore(tmp_path / "proposals.sqlite3")


def _create(store):
    digest = definition_digest({"tasks": {"a": {"type": "x"}}})
    return store.create(
        user_id="alice", conversation_id="conv", title="Release",
        summary="Review then publish", draft_id="d_abc",
        draft_revision=1, digest=digest, created_by="planner",
    )


def test_create_is_immediately_user_reviewable(store):
    proposal = _create(store)
    assert proposal["status"] == USER_REVIEW
    assert proposal["planner_reviewed_revision"] == 1
    assert proposal["planner_reviewed_digest"] == proposal["definition_digest"]
    assert proposal["state_revision"] == 1
    assert proposal["review_history"][0]["event_id"]
    assert proposal["review_history"][0]["created_at"]


def test_unchanged_planner_revision_can_be_accepted(store):
    proposal = _create(store)
    accepted = store.accept(
        proposal["proposal_id"],
        expected_state_revision=proposal["state_revision"],
        actor_id="alice",
    )
    assert accepted["status"] == ACCEPTED
    assert accepted["accepted_by"] == "alice"


def test_user_edit_requires_planner_review_before_acceptance(store):
    proposal = _create(store)
    digest = definition_digest({"tasks": {"a": {"type": "edited"}}})
    edited = store.note_draft_changed(
        draft_id="d_abc", draft_revision=2, digest=digest,
        actor_id="alice",
    )
    with pytest.raises(ProposalConflict, match="not been reviewed"):
        store.accept(
            proposal["proposal_id"],
            expected_state_revision=edited["state_revision"],
            actor_id="alice",
        )
    submitted = store.submit_to_planner(
        proposal["proposal_id"],
        expected_state_revision=edited["state_revision"],
        draft_revision=2, digest=digest, actor_id="alice",
        comment="I added a failure branch.",
    )
    assert submitted["status"] == PLANNER_REVIEW
    reviewed = store.planner_review(
        proposal["proposal_id"],
        expected_state_revision=submitted["state_revision"],
        draft_revision=2, digest=digest, actor_id="planner",
        decision="accept", comment="The branch is bounded.",
    )
    assert reviewed["status"] == USER_REVIEW
    accepted = store.accept(
        proposal["proposal_id"],
        expected_state_revision=reviewed["state_revision"],
        actor_id="alice",
    )
    assert accepted["status"] == ACCEPTED
    assert accepted["review_round"] == 1
    assert [event["action"] for event in accepted["review_history"]] == [
        "submitted_for_user_review", "edited_draft",
        "submitted_to_planner", "planner_accept", "accepted",
    ]


def test_stale_review_cannot_overwrite_newer_state(store):
    proposal = _create(store)
    digest = definition_digest({"tasks": {"a": {"type": "edited"}}})
    edited = store.note_draft_changed(
        draft_id="d_abc", draft_revision=2, digest=digest,
        actor_id="alice",
    )
    store.submit_to_planner(
        proposal["proposal_id"],
        expected_state_revision=edited["state_revision"],
        draft_revision=2, digest=digest, actor_id="alice",
    )
    with pytest.raises(ProposalConflict, match="state revision"):
        store.submit_to_planner(
            proposal["proposal_id"],
            expected_state_revision=edited["state_revision"],
            draft_revision=2, digest=digest, actor_id="alice",
        )


def test_edit_during_planner_review_invalidates_exact_review_turn(store):
    proposal = _create(store)
    submitted = store.submit_to_planner(
        proposal["proposal_id"],
        expected_state_revision=proposal["state_revision"],
        draft_revision=proposal["draft_revision"],
        digest=proposal["definition_digest"], actor_id="alice")
    digest = definition_digest({"tasks": {"a": {"type": "late-edit"}}})
    invalidated = store.note_draft_changed(
        draft_id="d_abc", draft_revision=2, digest=digest,
        actor_id="alice")
    assert invalidated["status"] == USER_REVIEW
    assert invalidated["definition_digest"] == digest
    assert invalidated["state_revision"] == submitted["state_revision"] + 1
    assert invalidated["review_history"][-1]["action"] == (
        "planner_review_invalidated")


def test_either_actor_can_cancel_without_accepting(store):
    proposal = _create(store)
    cancelled = store.cancel(
        proposal["proposal_id"],
        expected_state_revision=proposal["state_revision"],
        actor_type="user", actor_id="alice", comment="Not needed.",
    )
    assert cancelled["status"] == CANCELLED
    with pytest.raises(ProposalConflict, match="cancelled"):
        store.note_draft_changed(
            draft_id="d_abc", draft_revision=2,
            digest=definition_digest({}), actor_id="alice",
        )


def test_scope_filtered_get_and_list(store):
    proposal = _create(store)
    assert store.get(
        proposal["proposal_id"], user_id="mallory",
        conversation_id="conv",
    ) is None
    assert store.list(user_id="alice", conversation_id="conv")[0][
        "proposal_id"] == proposal["proposal_id"]


def test_import_terminal_proposal_is_idempotent_and_keeps_provenance(store):
    arguments = {
        "proposal_id": "wp_legacy_abc",
        "user_id": "alice",
        "conversation_id": "conv",
        "title": "Imported plan",
        "summary": "Legacy terminal history",
        "draft_id": "d_legacy_abc",
        "digest": "a" * 64,
        "created_by": "legacy-plan-migration",
        "published_flow_ref": {
            "schema_version": 1,
            "resource_type": "flow",
            "name": "legacy.plan:1.0.0",
        },
        "run_id": "fr_legacy_abc",
        "status": "completed",
        "import_metadata": {
            "schema_version": 1,
            "source_type": "legacy_plan",
            "source_id": "p_1234",
            "source_digest": "b" * 64,
        },
        "created_at": "2026-01-01T00:00:00+00:00",
        "terminal_at": "2026-01-02T00:00:00+00:00",
    }

    imported = store.import_terminal(**arguments)
    retried = store.import_terminal(**arguments)

    assert imported == retried
    assert imported["status"] == "completed"
    assert imported["run_ids"] == ["fr_legacy_abc"]
    assert imported["import_metadata"]["source_id"] == "p_1234"
    assert imported["review_history"][-1]["action"] == "imported_terminal"
    with pytest.raises(ProposalConflict, match="different imported proposal"):
        store.import_terminal(**{**arguments, "title": "Changed"})
