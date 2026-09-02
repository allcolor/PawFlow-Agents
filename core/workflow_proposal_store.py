"""Transactional workflow proposal and planner/user review state."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import core.paths as _paths
from core.sqlite_store_guard import SqliteStoreGuard, SqliteStoreUnavailableError

PLANNER_DRAFTING = "planner_drafting"
USER_REVIEW = "user_review"
PLANNER_REVIEW = "planner_review"
ACCEPTED = "accepted"
APPROVED = "approved"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"


class ProposalConflict(RuntimeError):
    """The proposal state or exact draft revision changed."""


def definition_digest(definition: Any) -> str:
    """Return the canonical digest used at every proposal review boundary."""
    payload = json.dumps(
        definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _utc(timestamp: float | None = None) -> str:
    return datetime.fromtimestamp(
        time.time() if timestamp is None else timestamp,
        tz=timezone.utc,
    ).isoformat()


def _required(value: Any, name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{name} is required")
    return result


class WorkflowProposalStore:
    """SQLite store fencing every co-editing transition by state revision."""

    _instance: WorkflowProposalStore | None = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> WorkflowProposalStore:
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def __init__(
        self,
        database_path: Path | None = None,
        *,
        before_live_write: Callable[[], Any] | None = None,
    ) -> None:
        self.database_path = Path(
            database_path
            or (_paths.RUNTIME_DIR / "workflow_proposals.sqlite3"))
        if before_live_write is None:
            from core.plan_migration_runtime import mark_active_plan_migration_write

            before_live_write = mark_active_plan_migration_write
        if not callable(before_live_write):
            raise TypeError("before_live_write must be callable")
        self._before_live_write = before_live_write
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._guard = SqliteStoreGuard("Workflow proposal")
        try:
            self._guard.initialize(self.database_path, self._initialize)
        except SqliteStoreUnavailableError:
            pass

    @property
    def available(self) -> bool:
        """Return whether the store is safe to read or write."""
        return self._guard.available

    def _connect(self) -> sqlite3.Connection:
        self._guard.require_available()
        connection = sqlite3.connect(
            str(self.database_path), timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflow_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    status TEXT NOT NULL,
                    draft_id TEXT NOT NULL UNIQUE,
                    draft_revision INTEGER NOT NULL,
                    definition_digest TEXT NOT NULL,
                    review_round INTEGER NOT NULL DEFAULT 0,
                    planner_reviewed_revision INTEGER,
                    planner_reviewed_digest TEXT NOT NULL DEFAULT '',
                    state_revision INTEGER NOT NULL DEFAULT 1,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    accepted_at TEXT,
                    accepted_by TEXT NOT NULL DEFAULT '',
                    approved_at TEXT,
                    approved_by TEXT NOT NULL DEFAULT '',
                    published_flow_ref_json TEXT,
                    run_ids_json TEXT NOT NULL DEFAULT '[]',
                    cancelled_at TEXT,
                    cancelled_by TEXT NOT NULL DEFAULT '',
                    import_metadata_json TEXT,
                    terminal_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_workflow_proposals_scope
                    ON workflow_proposals(user_id, conversation_id, updated_at);
                CREATE TABLE IF NOT EXISTS workflow_proposal_reviews (
                    event_id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    actor_type TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    draft_revision INTEGER NOT NULL,
                    definition_digest TEXT NOT NULL,
                    comment TEXT NOT NULL,
                    FOREIGN KEY(proposal_id)
                        REFERENCES workflow_proposals(proposal_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_workflow_proposal_reviews
                    ON workflow_proposal_reviews(proposal_id, created_at);
                """)
            columns = {
                str(row["name"]) for row in connection.execute(
                    "PRAGMA table_info(workflow_proposals)").fetchall()}
            for name, ddl in (
                ("approved_at", "TEXT"),
                ("approved_by", "TEXT NOT NULL DEFAULT ''"),
                ("published_flow_ref_json", "TEXT"),
                ("run_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("import_metadata_json", "TEXT"),
                ("terminal_at", "TEXT"),
            ):
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE workflow_proposals ADD COLUMN {name} {ddl}")

    def create(
        self, *, user_id: str, conversation_id: str, title: str,
        summary: str, draft_id: str, draft_revision: int,
        digest: str, created_by: str,
    ) -> dict[str, Any]:
        user_id = _required(user_id, "user_id")
        conversation_id = _required(conversation_id, "conversation_id")
        title = _required(title, "title")
        draft_id = _required(draft_id, "draft_id")
        created_by = _required(created_by, "created_by")
        digest = _required(digest, "definition_digest")
        if (
            isinstance(draft_revision, bool)
            or not isinstance(draft_revision, int)
            or draft_revision < 0
        ):
            raise ValueError("draft_revision must be an integer >= 0")
        proposal_id = f"wp_{uuid.uuid4()}"
        now = _utc()
        self._before_live_write()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO workflow_proposals (
                    proposal_id, user_id, conversation_id, title, summary,
                    status, draft_id, draft_revision, definition_digest,
                    planner_reviewed_revision, planner_reviewed_digest,
                    created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id, user_id, conversation_id, title,
                    str(summary or ""), USER_REVIEW, draft_id, draft_revision,
                    digest, draft_revision, digest, created_by, now, now,
                ),
            )
            self._append_review(
                connection, proposal_id=proposal_id, actor_type="planner",
                actor_id=created_by, action="submitted_for_user_review",
                draft_revision=draft_revision, digest=digest, comment="",
            )
            connection.commit()
        return self.get(proposal_id, user_id=user_id,
                        conversation_id=conversation_id)

    def import_terminal(
        self, *, proposal_id: str, user_id: str, conversation_id: str,
        title: str, summary: str, draft_id: str, digest: str,
        created_by: str, published_flow_ref: dict[str, Any], run_id: str,
        status: str, import_metadata: dict[str, Any], created_at: str,
        terminal_at: str,
    ) -> dict[str, Any]:
        """Import one terminal legacy proposal without replaying review actions."""

        proposal_id = _required(proposal_id, "proposal_id")
        user_id = _required(user_id, "user_id")
        conversation_id = _required(conversation_id, "conversation_id")
        title = _required(title, "title")
        draft_id = _required(draft_id, "draft_id")
        digest = _required(digest, "definition_digest")
        created_by = _required(created_by, "created_by")
        run_id = _required(run_id, "run_id")
        created_at = _required(created_at, "created_at")
        terminal_at = _required(terminal_at, "terminal_at")
        if status not in {COMPLETED, FAILED, CANCELLED}:
            raise ValueError("imported workflow proposal status must be terminal")
        if (
            not isinstance(published_flow_ref, dict) or not published_flow_ref
            or not isinstance(import_metadata, dict) or not import_metadata
        ):
            raise ValueError(
                "published_flow_ref and import_metadata are required objects")
        expected = {
            "proposal_id": proposal_id,
            "user_id": user_id,
            "conversation_id": conversation_id,
            "title": title,
            "summary": str(summary or ""),
            "status": status,
            "draft_id": draft_id,
            "definition_digest": digest,
            "created_by": created_by,
            "created_at": created_at,
            "updated_at": terminal_at,
            "terminal_at": terminal_at,
            "published_flow_ref": published_flow_ref,
            "run_ids": [run_id],
            "import_metadata": import_metadata,
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT proposal_id FROM workflow_proposals "
                "WHERE proposal_id = ?", (proposal_id,)).fetchone()
            if row is not None:
                connection.commit()
                existing = self.get(proposal_id)
                if all(existing.get(key) == value for key, value in expected.items()):
                    return existing
                raise ProposalConflict("different imported proposal already exists")
            connection.execute(
                """
                INSERT INTO workflow_proposals (
                    proposal_id, user_id, conversation_id, title, summary,
                    status, draft_id, draft_revision, definition_digest,
                    review_round, planner_reviewed_revision,
                    planner_reviewed_digest, state_revision, created_by,
                    created_at, updated_at, accepted_at, accepted_by,
                    approved_at, approved_by, published_flow_ref_json,
                    run_ids_json, cancelled_at, cancelled_by,
                    import_metadata_json, terminal_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    proposal_id, user_id, conversation_id, title,
                    str(summary or ""), status, draft_id, 0, digest, 0, 0,
                    digest, 1, created_by, created_at, terminal_at,
                    created_at, created_by, created_at, created_by,
                    json.dumps(published_flow_ref, sort_keys=True),
                    json.dumps([run_id]),
                    terminal_at if status == CANCELLED else None,
                    created_by if status == CANCELLED else "",
                    json.dumps(import_metadata, sort_keys=True), terminal_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO workflow_proposal_reviews (
                    event_id, proposal_id, created_at, actor_type, actor_id,
                    action, draft_revision, definition_digest, comment
                ) VALUES (?, ?, ?, 'system', ?, 'imported_terminal', 0, ?, '')
                """,
                (
                    f"wpr_legacy_{proposal_id}", proposal_id, terminal_at,
                    created_by, digest,
                ),
            )
            connection.commit()
        return self.get(proposal_id, user_id=user_id,
                        conversation_id=conversation_id)

    def import_active(
        self, *, proposal_id: str, user_id: str, conversation_id: str,
        title: str, summary: str, draft_id: str, digest: str,
        created_by: str, published_flow_ref: dict[str, Any], run_id: str,
        status: str, import_metadata: dict[str, Any], created_at: str,
    ) -> dict[str, Any]:
        """Import one running proposal projection for a waiting legacy run."""

        proposal_id = _required(proposal_id, "proposal_id")
        user_id = _required(user_id, "user_id")
        conversation_id = _required(conversation_id, "conversation_id")
        title = _required(title, "title")
        draft_id = _required(draft_id, "draft_id")
        digest = _required(digest, "definition_digest")
        created_by = _required(created_by, "created_by")
        run_id = _required(run_id, "run_id")
        created_at = _required(created_at, "created_at")
        if status != RUNNING:
            raise ValueError("active imported proposal must be running")
        if (
            not isinstance(published_flow_ref, dict) or not published_flow_ref
            or not isinstance(import_metadata, dict) or not import_metadata
        ):
            raise ValueError(
                "published_flow_ref and import_metadata are required objects")
        expected = {
            "proposal_id": proposal_id,
            "user_id": user_id,
            "conversation_id": conversation_id,
            "title": title,
            "summary": str(summary or ""),
            "status": status,
            "draft_id": draft_id,
            "definition_digest": digest,
            "created_by": created_by,
            "created_at": created_at,
            "updated_at": created_at,
            "terminal_at": None,
            "published_flow_ref": published_flow_ref,
            "run_ids": [run_id],
            "import_metadata": import_metadata,
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT proposal_id FROM workflow_proposals "
                "WHERE proposal_id = ?", (proposal_id,),
            ).fetchone()
            if row is not None:
                connection.commit()
                existing = self.get(proposal_id)
                if all(existing.get(key) == value for key, value in expected.items()):
                    return existing
                raise ProposalConflict("different imported proposal already exists")
            connection.execute(
                """
                INSERT INTO workflow_proposals (
                    proposal_id, user_id, conversation_id, title, summary,
                    status, draft_id, draft_revision, definition_digest,
                    review_round, planner_reviewed_revision,
                    planner_reviewed_digest, state_revision, created_by,
                    created_at, updated_at, accepted_at, accepted_by,
                    approved_at, approved_by, published_flow_ref_json,
                    run_ids_json, cancelled_at, cancelled_by,
                    import_metadata_json, terminal_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    proposal_id, user_id, conversation_id, title,
                    str(summary or ""), status, draft_id, 0, digest, 0, 0,
                    digest, 1, created_by, created_at, created_at,
                    created_at, created_by, created_at, created_by,
                    json.dumps(published_flow_ref, sort_keys=True),
                    json.dumps([run_id]), None, "",
                    json.dumps(import_metadata, sort_keys=True), None,
                ),
            )
            connection.execute(
                """
                INSERT INTO workflow_proposal_reviews (
                    event_id, proposal_id, created_at, actor_type, actor_id,
                    action, draft_revision, definition_digest, comment
                ) VALUES (?, ?, ?, 'system', ?, 'imported_active', 0, ?, '')
                """,
                (
                    f"wpr_legacy_active_{proposal_id}", proposal_id,
                    created_at, created_by, digest,
                ),
            )
            connection.commit()
        return self.get(
            proposal_id, user_id=user_id, conversation_id=conversation_id)

    def import_inactive(
        self, *, proposal_id: str, user_id: str, conversation_id: str,
        title: str, summary: str, draft_id: str, digest: str,
        created_by: str, published_flow_ref: dict[str, Any], run_id: str,
        status: str, import_metadata: dict[str, Any], created_at: str,
    ) -> dict[str, Any]:
        """Import a reviewable or accepted proposal without starting a run."""

        proposal_id = _required(proposal_id, "proposal_id")
        user_id = _required(user_id, "user_id")
        conversation_id = _required(conversation_id, "conversation_id")
        title = _required(title, "title")
        draft_id = _required(draft_id, "draft_id")
        digest = _required(digest, "definition_digest")
        created_by = _required(created_by, "created_by")
        created_at = _required(created_at, "created_at")
        if status not in {USER_REVIEW, ACCEPTED}:
            raise ValueError(
                "inactive imported proposal must be user_review or accepted")
        run_id = str(run_id or "").strip()
        if (status == ACCEPTED) != bool(run_id):
            raise ValueError("accepted imported proposal requires exactly one run")
        if (
            not isinstance(published_flow_ref, dict) or not published_flow_ref
            or not isinstance(import_metadata, dict) or not import_metadata
        ):
            raise ValueError(
                "published_flow_ref and import_metadata are required objects")
        run_ids = [run_id] if run_id else []
        expected = {
            "proposal_id": proposal_id,
            "user_id": user_id,
            "conversation_id": conversation_id,
            "title": title,
            "summary": str(summary or ""),
            "status": status,
            "draft_id": draft_id,
            "definition_digest": digest,
            "created_by": created_by,
            "created_at": created_at,
            "updated_at": created_at,
            "terminal_at": None,
            "published_flow_ref": published_flow_ref,
            "run_ids": run_ids,
            "import_metadata": import_metadata,
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT proposal_id FROM workflow_proposals "
                "WHERE proposal_id = ?", (proposal_id,),
            ).fetchone()
            if row is not None:
                connection.commit()
                existing = self.get(proposal_id)
                if all(existing.get(key) == value for key, value in expected.items()):
                    return existing
                raise ProposalConflict("different imported proposal already exists")
            accepted_at = created_at if status == ACCEPTED else None
            accepted_by = created_by if status == ACCEPTED else ""
            connection.execute(
                """
                INSERT INTO workflow_proposals (
                    proposal_id, user_id, conversation_id, title, summary,
                    status, draft_id, draft_revision, definition_digest,
                    review_round, planner_reviewed_revision,
                    planner_reviewed_digest, state_revision, created_by,
                    created_at, updated_at, accepted_at, accepted_by,
                    approved_at, approved_by, published_flow_ref_json,
                    run_ids_json, cancelled_at, cancelled_by,
                    import_metadata_json, terminal_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    proposal_id, user_id, conversation_id, title,
                    str(summary or ""), status, draft_id, 0, digest, 0, 0,
                    digest, 1, created_by, created_at, created_at,
                    accepted_at, accepted_by, None, "",
                    json.dumps(published_flow_ref, sort_keys=True),
                    json.dumps(run_ids), None, "",
                    json.dumps(import_metadata, sort_keys=True), None,
                ),
            )
            connection.execute(
                """
                INSERT INTO workflow_proposal_reviews (
                    event_id, proposal_id, created_at, actor_type, actor_id,
                    action, draft_revision, definition_digest, comment
                ) VALUES (?, ?, ?, 'system', ?, ?, 0, ?, '')
                """,
                (
                    f"wpr_legacy_inactive_{proposal_id}", proposal_id,
                    created_at, created_by, f"imported_{status}", digest,
                ),
            )
            connection.commit()
        return self.get(
            proposal_id, user_id=user_id, conversation_id=conversation_id)

    def delete_imported(
        self, proposal_id: str, *, import_metadata: dict[str, Any],
    ) -> bool:
        """Delete only a proposal carrying the exact import provenance."""

        proposal_id = _required(proposal_id, "proposal_id")
        if not isinstance(import_metadata, dict) or not import_metadata:
            raise ValueError("import_metadata must be a non-empty object")
        expected = json.dumps(import_metadata, sort_keys=True)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT import_metadata_json FROM workflow_proposals "
                "WHERE proposal_id = ?", (proposal_id,),
            ).fetchone()
            if row is None:
                connection.commit()
                return False
            if row["import_metadata_json"] != expected:
                raise ProposalConflict(
                    "imported proposal provenance does not match")
            connection.execute(
                "DELETE FROM workflow_proposals WHERE proposal_id = ?",
                (proposal_id,),
            )
            connection.commit()
        return True

    def get(
        self, proposal_id: str, *, user_id: str = "",
        conversation_id: str = "",
    ) -> dict[str, Any] | None:
        proposal_id = _required(proposal_id, "proposal_id")
        if user_id and conversation_id:
            query = (
                "SELECT * FROM workflow_proposals WHERE proposal_id = ? "
                "AND user_id = ? AND conversation_id = ?")
            values = (proposal_id, user_id, conversation_id)
        elif user_id:
            query = (
                "SELECT * FROM workflow_proposals WHERE proposal_id = ? "
                "AND user_id = ?")
            values = (proposal_id, user_id)
        elif conversation_id:
            query = (
                "SELECT * FROM workflow_proposals WHERE proposal_id = ? "
                "AND conversation_id = ?")
            values = (proposal_id, conversation_id)
        else:
            query = "SELECT * FROM workflow_proposals WHERE proposal_id = ?"
            values = (proposal_id,)
        with self._connect() as connection:
            row = connection.execute(query, values).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["published_flow_ref"] = (
                json.loads(result.pop("published_flow_ref_json"))
                if result.get("published_flow_ref_json") else None)
            result["run_ids"] = json.loads(result.pop("run_ids_json") or "[]")
            raw_import_metadata = result.pop("import_metadata_json", None)
            result["import_metadata"] = (
                json.loads(raw_import_metadata)
                if raw_import_metadata else None)
            result["review_history"] = [
                dict(item) for item in connection.execute(
                    """
                    SELECT * FROM workflow_proposal_reviews
                    WHERE proposal_id = ?
                    ORDER BY created_at, event_id
                    """,
                    (proposal_id,),
                ).fetchall()
            ]
            return result

    def list(
        self, *, user_id: str, conversation_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        user_id = _required(user_id, "user_id")
        conversation_id = _required(conversation_id, "conversation_id")
        bounded_limit = max(1, min(int(limit), 500))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workflow_proposals
                WHERE user_id = ? AND conversation_id = ?
                ORDER BY updated_at DESC, proposal_id DESC LIMIT ?
                """,
                (user_id, conversation_id, bounded_limit),
            ).fetchall()
        return [
            proposal for row in rows
            if (proposal := self.get(
                str(row["proposal_id"]), user_id=user_id,
                conversation_id=conversation_id)) is not None
        ]

    def get_by_draft(
        self, draft_id: str, *, user_id: str = "",
    ) -> dict[str, Any] | None:
        """Return the proposal bound to one private authoring draft."""
        draft_id = _required(draft_id, "draft_id")
        if user_id:
            query = (
                "SELECT proposal_id FROM workflow_proposals "
                "WHERE draft_id = ? AND user_id = ?")
            values = (draft_id, user_id)
        else:
            query = (
                "SELECT proposal_id FROM workflow_proposals WHERE draft_id = ?")
            values = (draft_id,)
        with self._connect() as connection:
            row = connection.execute(query, values).fetchone()
        return self.get(row["proposal_id"]) if row is not None else None

    def note_draft_changed(
        self, *, draft_id: str, draft_revision: int, digest: str,
        actor_id: str, actor_type: str = "user",
    ) -> dict[str, Any] | None:
        """Invalidate stale review without transferring the actor turn."""
        actor_id = _required(actor_id, "actor_id")
        if actor_type not in {"user", "planner", "system"}:
            raise ValueError("actor_type must be user, planner, or system")
        digest = _required(digest, "definition_digest")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM workflow_proposals WHERE draft_id = ?",
                (_required(draft_id, "draft_id"),),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            if row["status"] == CANCELLED:
                raise ProposalConflict("cancelled proposal cannot be edited")
            self._before_live_write()
            status = USER_REVIEW
            action = (
                "planner_review_invalidated"
                if row["status"] == PLANNER_REVIEW else "edited_draft")
            now = _utc()
            connection.execute(
                """
                UPDATE workflow_proposals
                SET status = ?, draft_revision = ?, definition_digest = ?,
                    accepted_at = NULL, accepted_by = '',
                    state_revision = state_revision + 1, updated_at = ?
                WHERE proposal_id = ?
                """,
                (status, draft_revision, digest, now, row["proposal_id"]),
            )
            self._append_review(
                connection, proposal_id=row["proposal_id"],
                actor_type=actor_type, actor_id=actor_id, action=action,
                draft_revision=draft_revision, digest=digest, comment="",
            )
            connection.commit()
        return self.get(row["proposal_id"])

    def submit_to_planner(
        self, proposal_id: str, *, expected_state_revision: int,
        draft_revision: int, digest: str, actor_id: str,
        comment: str = "",
    ) -> dict[str, Any]:
        return self._transition(
            proposal_id, expected_state_revision=expected_state_revision,
            expected_status=USER_REVIEW, next_status=PLANNER_REVIEW,
            actor_type="user", actor_id=actor_id,
            action="submitted_to_planner", draft_revision=draft_revision,
            digest=digest, comment=comment, increment_round=True,
        )

    def planner_review(
        self, proposal_id: str, *, expected_state_revision: int,
        draft_revision: int, digest: str, actor_id: str,
        decision: str, comment: str = "",
    ) -> dict[str, Any]:
        if decision not in {"accept", "revised", "request_changes"}:
            raise ValueError(
                "decision must be accept, revised, or request_changes")
        return self._transition(
            proposal_id, expected_state_revision=expected_state_revision,
            expected_status=PLANNER_REVIEW, next_status=USER_REVIEW,
            actor_type="planner", actor_id=actor_id,
            action=f"planner_{decision}", draft_revision=draft_revision,
            digest=digest, comment=comment, planner_reviewed=True,
        )

    def accept(
        self, proposal_id: str, *, expected_state_revision: int,
        actor_id: str,
    ) -> dict[str, Any]:
        actor_id = _required(actor_id, "actor_id")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._locked_row(
                connection, proposal_id, expected_state_revision, USER_REVIEW)
            if (
                row["draft_revision"] != row["planner_reviewed_revision"]
                or row["definition_digest"] != row["planner_reviewed_digest"]
            ):
                raise ProposalConflict(
                    "current draft revision has not been reviewed by planner")
            self._before_live_write()
            now = _utc()
            connection.execute(
                """
                UPDATE workflow_proposals
                SET status = ?, accepted_at = ?, accepted_by = ?,
                    state_revision = state_revision + 1, updated_at = ?
                WHERE proposal_id = ?
                """,
                (ACCEPTED, now, actor_id, now, proposal_id),
            )
            self._append_review(
                connection, proposal_id=proposal_id, actor_type="user",
                actor_id=actor_id, action="accepted",
                draft_revision=row["draft_revision"],
                digest=row["definition_digest"], comment="",
            )
            connection.commit()
        return self.get(proposal_id)

    def cancel(
        self, proposal_id: str, *, expected_state_revision: int,
        actor_type: str, actor_id: str, comment: str = "",
    ) -> dict[str, Any]:
        if actor_type not in {"user", "planner"}:
            raise ValueError("actor_type must be user or planner")
        actor_id = _required(actor_id, "actor_id")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM workflow_proposals WHERE proposal_id = ?",
                (_required(proposal_id, "proposal_id"),),
            ).fetchone()
            if row is None:
                raise KeyError(proposal_id)
            if row["state_revision"] != expected_state_revision:
                raise ProposalConflict("proposal state revision changed")
            if row["status"] == CANCELLED:
                raise ProposalConflict("proposal is already cancelled")
            if row["status"] not in {
                PLANNER_DRAFTING, USER_REVIEW, PLANNER_REVIEW, ACCEPTED,
            }:
                raise ProposalConflict(
                    "a running or terminal proposal must be cancelled through "
                    "its linked flow run")
            self._before_live_write()
            now = _utc()
            connection.execute(
                """
                UPDATE workflow_proposals
                SET status = ?, cancelled_at = ?, cancelled_by = ?,
                    state_revision = state_revision + 1, updated_at = ?
                WHERE proposal_id = ?
                """,
                (CANCELLED, now, actor_id, now, proposal_id),
            )
            self._append_review(
                connection, proposal_id=proposal_id, actor_type=actor_type,
                actor_id=actor_id, action="cancelled",
                draft_revision=row["draft_revision"],
                digest=row["definition_digest"], comment=comment,
            )
            connection.commit()
        return self.get(proposal_id)

    def approve(
        self, proposal_id: str, *, expected_state_revision: int,
        actor_id: str, published_flow_ref: dict[str, Any], run_id: str,
    ) -> dict[str, Any]:
        actor_id = _required(actor_id, "actor_id")
        run_id = _required(run_id, "run_id")
        if not isinstance(published_flow_ref, dict) or not published_flow_ref:
            raise ValueError("published_flow_ref is required")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._locked_row(
                connection, proposal_id, expected_state_revision, ACCEPTED)
            self._before_live_write()
            now = _utc()
            connection.execute(
                """
                UPDATE workflow_proposals
                SET status = ?, approved_at = ?, approved_by = ?,
                    published_flow_ref_json = ?, run_ids_json = ?,
                    state_revision = state_revision + 1, updated_at = ?
                WHERE proposal_id = ?
                """,
                (APPROVED, now, actor_id,
                 json.dumps(published_flow_ref, sort_keys=True),
                 json.dumps([run_id]), now, proposal_id),
            )
            self._append_review(
                connection, proposal_id=proposal_id, actor_type="user",
                actor_id=actor_id, action="approved",
                draft_revision=row["draft_revision"],
                digest=row["definition_digest"], comment="",
            )
            connection.commit()
        return self.get(proposal_id)

    def mark_run_status(
        self, proposal_id: str, *, run_id: str, status: str,
    ) -> dict[str, Any]:
        allowed = {
            APPROVED: {RUNNING, FAILED, CANCELLED},
            RUNNING: {COMPLETED, FAILED, CANCELLED},
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM workflow_proposals WHERE proposal_id = ?",
                (_required(proposal_id, "proposal_id"),),
            ).fetchone()
            if row is None:
                raise KeyError(proposal_id)
            run_ids = json.loads(row["run_ids_json"] or "[]")
            if run_id not in run_ids:
                raise ProposalConflict("run_id is not linked to this proposal")
            if status == row["status"]:
                connection.commit()
                return self.get(proposal_id)
            if status not in allowed.get(row["status"], set()):
                raise ProposalConflict(
                    f"invalid proposal run transition {row['status']} -> {status}")
            self._before_live_write()
            connection.execute(
                "UPDATE workflow_proposals SET status = ?, "
                "state_revision = state_revision + 1, updated_at = ? "
                "WHERE proposal_id = ?", (status, _utc(), proposal_id),
            )
            connection.commit()
        return self.get(proposal_id)

    def start_replay(
        self, proposal_id: str, *, expected_state_revision: int,
        run_id: str, actor_id: str,
    ) -> dict[str, Any]:
        """Link a new replay identity and project its coarse running state."""
        actor_id = _required(actor_id, "actor_id")
        run_id = _required(run_id, "run_id")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM workflow_proposals WHERE proposal_id = ?",
                (_required(proposal_id, "proposal_id"),),
            ).fetchone()
            if row is None:
                raise KeyError(proposal_id)
            if row["state_revision"] != expected_state_revision:
                raise ProposalConflict("proposal state revision changed")
            if row["status"] not in {COMPLETED, FAILED, CANCELLED}:
                raise ProposalConflict("only a terminal proposal run can be replayed")
            run_ids = json.loads(row["run_ids_json"] or "[]")
            if run_id in run_ids:
                raise ProposalConflict("replay run is already linked")
            self._before_live_write()
            run_ids.append(run_id)
            now = _utc()
            connection.execute(
                "UPDATE workflow_proposals SET status = ?, run_ids_json = ?, "
                "state_revision = state_revision + 1, updated_at = ? "
                "WHERE proposal_id = ?",
                (RUNNING, json.dumps(run_ids), now, proposal_id),
            )
            self._append_review(
                connection, proposal_id=proposal_id, actor_type="user",
                actor_id=actor_id, action="replayed",
                draft_revision=row["draft_revision"],
                digest=row["definition_digest"], comment="",
            )
            connection.commit()
        return self.get(proposal_id)

    def _transition(
        self, proposal_id: str, *, expected_state_revision: int,
        expected_status: str, next_status: str, actor_type: str,
        actor_id: str, action: str, draft_revision: int, digest: str,
        comment: str, increment_round: bool = False,
        planner_reviewed: bool = False,
    ) -> dict[str, Any]:
        actor_id = _required(actor_id, "actor_id")
        digest = _required(digest, "definition_digest")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._locked_row(
                connection, proposal_id, expected_state_revision,
                expected_status)
            if (
                row["draft_revision"] != draft_revision
                or row["definition_digest"] != digest
            ):
                raise ProposalConflict("exact draft revision or digest changed")
            self._before_live_write()
            reviewed_revision = (
                draft_revision if planner_reviewed
                else row["planner_reviewed_revision"])
            reviewed_digest = (
                digest if planner_reviewed else row["planner_reviewed_digest"])
            now = _utc()
            connection.execute(
                """
                UPDATE workflow_proposals
                SET status = ?, review_round = review_round + ?,
                    planner_reviewed_revision = ?,
                    planner_reviewed_digest = ?,
                    state_revision = state_revision + 1, updated_at = ?
                WHERE proposal_id = ?
                """,
                (
                    next_status, 1 if increment_round else 0,
                    reviewed_revision, reviewed_digest, now, proposal_id,
                ),
            )
            self._append_review(
                connection, proposal_id=proposal_id,
                actor_type=actor_type, actor_id=actor_id, action=action,
                draft_revision=draft_revision, digest=digest,
                comment=str(comment or ""),
            )
            connection.commit()
        return self.get(proposal_id)

    @staticmethod
    def _locked_row(
        connection: sqlite3.Connection, proposal_id: str,
        expected_state_revision: int, expected_status: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM workflow_proposals WHERE proposal_id = ?",
            (_required(proposal_id, "proposal_id"),),
        ).fetchone()
        if row is None:
            raise KeyError(proposal_id)
        if row["state_revision"] != expected_state_revision:
            raise ProposalConflict("proposal state revision changed")
        if row["status"] != expected_status:
            raise ProposalConflict(
                f"proposal must be in {expected_status} state")
        return row

    @staticmethod
    def _append_review(
        connection: sqlite3.Connection, *, proposal_id: str,
        actor_type: str, actor_id: str, action: str,
        draft_revision: int, digest: str, comment: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO workflow_proposal_reviews (
                event_id, proposal_id, created_at, actor_type, actor_id,
                action, draft_revision, definition_digest, comment
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()), proposal_id, _utc(), actor_type, actor_id,
                action, draft_revision, digest, comment,
            ),
        )


__all__ = [
    "ACCEPTED",
    "APPROVED",
    "CANCELLED",
    "COMPLETED",
    "FAILED",
    "PLANNER_DRAFTING",
    "PLANNER_REVIEW",
    "RUNNING",
    "USER_REVIEW",
    "ProposalConflict",
    "WorkflowProposalStore",
    "definition_digest",
]
