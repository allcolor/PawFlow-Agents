"""Transactional append-only Media Studio project lineage and provider jobs."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

import core.paths as _paths
from core.media_studio import canonical_digest, utc_now


PROJECT_STATUSES = frozenset({"active", "archived"})
REVISION_STATUSES = frozenset({"active", "completed", "failed", "superseded"})
JOB_STATUSES = frozenset({
    "created", "submitted", "completed", "failed", "superseded",
})


class MediaProjectConflict(RuntimeError):
    """A project changed or an idempotency key was reused for other input."""


def _required(value: Any, name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _object(value: Any, name: str, *, allow_empty: bool = True) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    if not allow_empty and not value:
        raise ValueError(f"{name} is required")
    return value


def _array(value: Any, name: str) -> list:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be an array")
    return list(value)


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for key in tuple(result):
        if key.endswith("_json"):
            result[key[:-5]] = json.loads(result.pop(key) or "null")
    return result


class MediaProjectStore:
    """SQLite store for immutable revisions and durable provider correlation."""

    _instance: "MediaProjectStore | None" = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "MediaProjectStore":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def __init__(self, database_path: Path | None = None) -> None:
        self._database_path_override = (
            Path(database_path) if database_path is not None else None)
        self._lock = threading.RLock()
        self._initialize()

    @property
    def database_path(self) -> Path:
        return self._database_path_override or (
            _paths.RUNTIME_DIR / "media_projects.sqlite3")

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(self.database_path), timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS media_projects (
                    project_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    state_revision INTEGER NOT NULL,
                    current_revision_id TEXT NOT NULL DEFAULT '',
                    idempotency_key TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, conversation_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS idx_media_projects_scope
                    ON media_projects(user_id, conversation_id, updated_at);

                CREATE TABLE IF NOT EXISTS media_revisions (
                    revision_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES media_projects(project_id)
                        ON DELETE CASCADE,
                    parent_revision_id TEXT,
                    run_id TEXT NOT NULL,
                    root_turn_id TEXT NOT NULL,
                    user_request TEXT NOT NULL,
                    intent_json TEXT NOT NULL,
                    brief_json TEXT NOT NULL,
                    proposal_json TEXT NOT NULL,
                    selection_json TEXT NOT NULL,
                    references_json TEXT NOT NULL,
                    provider_jobs_json TEXT NOT NULL,
                    ffmpeg_recipe_json TEXT NOT NULL,
                    artifacts_json TEXT NOT NULL,
                    qa_report_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    supersession_reason TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(project_id, idempotency_key),
                    FOREIGN KEY(parent_revision_id)
                        REFERENCES media_revisions(revision_id)
                );
                CREATE INDEX IF NOT EXISTS idx_media_revisions_project
                    ON media_revisions(project_id, created_at);

                CREATE TABLE IF NOT EXISTS media_provider_jobs (
                    job_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES media_projects(project_id)
                        ON DELETE CASCADE,
                    run_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    engine TEXT NOT NULL,
                    service_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    provider_job_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    output_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    idempotency_key TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, idempotency_key)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_media_provider_identity
                    ON media_provider_jobs(service_id, provider_job_id)
                    WHERE provider_job_id <> '';
                CREATE INDEX IF NOT EXISTS idx_media_provider_jobs_run
                    ON media_provider_jobs(run_id, task_id);
                """)

    def create_project(
        self, *, user_id: str, conversation_id: str, title: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        user_id = _required(user_id, "user_id")
        conversation_id = _required(conversation_id, "conversation_id")
        title = _required(title, "title")
        idempotency_key = _required(idempotency_key, "idempotency_key")
        digest = canonical_digest({
            "user_id": user_id,
            "conversation_id": conversation_id,
            "title": title,
        })
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """SELECT * FROM media_projects
                   WHERE user_id=? AND conversation_id=? AND idempotency_key=?""",
                (user_id, conversation_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["payload_digest"] != digest:
                    raise MediaProjectConflict(
                        "project idempotency key was reused for different input")
                connection.commit()
                return _decode(existing)
            project_id = f"media_project_{uuid.uuid4()}"
            now = utc_now()
            connection.execute(
                """INSERT INTO media_projects (
                       project_id, user_id, conversation_id, title, status,
                       state_revision, idempotency_key, payload_digest,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, 'active', 1, ?, ?, ?, ?)""",
                (project_id, user_id, conversation_id, title, idempotency_key,
                 digest, now, now),
            )
            connection.commit()
        return self.get_project(
            project_id, user_id=user_id, conversation_id=conversation_id)

    def get_project(
        self, project_id: str, *, user_id: str, conversation_id: str,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM media_projects
                   WHERE project_id=? AND user_id=? AND conversation_id=?""",
                (
                    _required(project_id, "project_id"),
                    _required(user_id, "user_id"),
                    _required(conversation_id, "conversation_id"),
                ),
            ).fetchone()
        if row is None:
            raise KeyError(project_id)
        return _decode(row)

    def list_projects(
        self, *, user_id: str, conversation_id: str, limit: int = 100,
    ) -> list[dict[str, Any]]:
        user_id = _required(user_id, "user_id")
        conversation_id = _required(conversation_id, "conversation_id")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM media_projects
                   WHERE user_id=? AND conversation_id=?
                   ORDER BY updated_at DESC LIMIT ?""",
                (user_id, conversation_id, limit),
            ).fetchall()
        return [_decode(row) for row in rows]

    def append_revision(
        self, *, project_id: str, user_id: str, conversation_id: str,
        expected_state_revision: int, idempotency_key: str, run_id: str,
        root_turn_id: str, user_request: str, intent: dict, brief: dict,
        proposal: dict | None = None, selection: dict | None = None,
        references: list | tuple = (), provider_jobs: list | tuple = (),
        ffmpeg_recipe: dict | None = None, artifacts: list | tuple = (),
        qa_report: dict | None = None, status: str = "completed",
        parent_revision_id: str = "", supersession_reason: str = "",
    ) -> dict[str, Any]:
        project_id = _required(project_id, "project_id")
        user_id = _required(user_id, "user_id")
        conversation_id = _required(conversation_id, "conversation_id")
        idempotency_key = _required(idempotency_key, "idempotency_key")
        run_id = _required(run_id, "run_id")
        root_turn_id = _required(root_turn_id, "root_turn_id")
        user_request = _required(user_request, "user_request")
        if status not in REVISION_STATUSES:
            raise ValueError("revision status is invalid")
        if status == "superseded":
            _required(supersession_reason, "supersession_reason")
        payload = {
            "run_id": run_id,
            "root_turn_id": root_turn_id,
            "user_request": user_request,
            "intent": _object(intent, "intent", allow_empty=False),
            "brief": _object(brief, "brief", allow_empty=False),
            "proposal": _object(proposal or {}, "proposal"),
            "selection": _object(selection or {}, "selection"),
            "references": _array(references, "references"),
            "provider_jobs": _array(provider_jobs, "provider_jobs"),
            "ffmpeg_recipe": _object(ffmpeg_recipe or {}, "ffmpeg_recipe"),
            "artifacts": _array(artifacts, "artifacts"),
            "qa_report": _object(qa_report or {}, "qa_report"),
            "status": status,
            "parent_revision_id": str(parent_revision_id or ""),
            "supersession_reason": str(supersession_reason or ""),
        }
        self._validate_artifacts(payload["artifacts"])
        digest = canonical_digest(payload)

        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """SELECT * FROM media_revisions
                   WHERE project_id=? AND idempotency_key=?""",
                (project_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["payload_digest"] != digest:
                    raise MediaProjectConflict(
                        "revision idempotency key was reused for different input")
                connection.commit()
                return _decode(existing)

            project = self._locked_project(
                connection, project_id, user_id, conversation_id)
            if (
                isinstance(expected_state_revision, bool)
                or project["state_revision"] != expected_state_revision
            ):
                raise MediaProjectConflict("project state revision changed")

            parent_id = str(parent_revision_id or project["current_revision_id"])
            if parent_id:
                parent = connection.execute(
                    """SELECT revision_id FROM media_revisions
                       WHERE revision_id=? AND project_id=?""",
                    (parent_id, project_id),
                ).fetchone()
                if parent is None:
                    raise MediaProjectConflict(
                        "parent revision does not belong to project")
            revision_id = f"media_revision_{uuid.uuid4()}"
            now = utc_now()
            connection.execute(
                """INSERT INTO media_revisions (
                       revision_id, project_id, parent_revision_id, run_id,
                       root_turn_id, user_request, intent_json, brief_json,
                       proposal_json, selection_json, references_json,
                       provider_jobs_json, ffmpeg_recipe_json, artifacts_json,
                       qa_report_json, status, supersession_reason,
                       idempotency_key, payload_digest, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                             ?, ?, ?, ?)""",
                (
                    revision_id, project_id, parent_id or None, run_id,
                    root_turn_id, user_request, _json(payload["intent"]),
                    _json(payload["brief"]), _json(payload["proposal"]),
                    _json(payload["selection"]), _json(payload["references"]),
                    _json(payload["provider_jobs"]),
                    _json(payload["ffmpeg_recipe"]),
                    _json(payload["artifacts"]), _json(payload["qa_report"]),
                    status, payload["supersession_reason"], idempotency_key,
                    digest, now,
                ),
            )
            cursor = connection.execute(
                """UPDATE media_projects
                   SET current_revision_id=?, state_revision=state_revision+1,
                       updated_at=?
                   WHERE project_id=? AND state_revision=?""",
                (revision_id, now, project_id, expected_state_revision),
            )
            if cursor.rowcount != 1:
                raise MediaProjectConflict("project state revision changed")
            connection.commit()
        return self.get_revision(
            revision_id, user_id=user_id, conversation_id=conversation_id)

    def get_revision(
        self, revision_id: str, *, user_id: str, conversation_id: str,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT revision.*
                   FROM media_revisions AS revision
                   JOIN media_projects AS project
                     ON project.project_id=revision.project_id
                   WHERE revision.revision_id=? AND project.user_id=?
                     AND project.conversation_id=?""",
                (
                    _required(revision_id, "revision_id"),
                    _required(user_id, "user_id"),
                    _required(conversation_id, "conversation_id"),
                ),
            ).fetchone()
        if row is None:
            raise KeyError(revision_id)
        return _decode(row)

    def list_revisions(
        self, project_id: str, *, user_id: str, conversation_id: str,
    ) -> list[dict[str, Any]]:
        self.get_project(
            project_id, user_id=user_id, conversation_id=conversation_id)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM media_revisions
                   WHERE project_id=? ORDER BY created_at, revision_id""",
                (project_id,),
            ).fetchall()
        return [_decode(row) for row in rows]

    def start_provider_job(
        self, *, project_id: str, user_id: str, conversation_id: str,
        run_id: str, task_id: str, engine: str, service_id: str,
        operation: str, idempotency_key: str,
    ) -> dict[str, Any]:
        values = {
            "project_id": _required(project_id, "project_id"),
            "run_id": _required(run_id, "run_id"),
            "task_id": _required(task_id, "task_id"),
            "engine": _required(engine, "engine"),
            "service_id": _required(service_id, "service_id"),
            "operation": _required(operation, "operation"),
        }
        idempotency_key = _required(idempotency_key, "idempotency_key")
        digest = canonical_digest(values)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._locked_project(
                connection, project_id, user_id, conversation_id)
            existing = connection.execute(
                """SELECT * FROM media_provider_jobs
                   WHERE project_id=? AND idempotency_key=?""",
                (project_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["payload_digest"] != digest:
                    raise MediaProjectConflict(
                        "job idempotency key was reused for different input")
                connection.commit()
                return _decode(existing)
            job_id = f"media_job_{uuid.uuid4()}"
            now = utc_now()
            connection.execute(
                """INSERT INTO media_provider_jobs (
                       job_id, project_id, run_id, task_id, engine, service_id,
                       operation, status, idempotency_key, payload_digest,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 'created', ?, ?, ?, ?)""",
                (
                    job_id, project_id, values["run_id"], values["task_id"],
                    values["engine"], values["service_id"], values["operation"],
                    idempotency_key, digest, now, now,
                ),
            )
            connection.commit()
        return self.get_provider_job(
            job_id, user_id=user_id, conversation_id=conversation_id)

    def record_provider_submission(
        self, job_id: str, *, user_id: str, conversation_id: str,
        provider_job_id: str,
    ) -> dict[str, Any]:
        provider_job_id = _required(provider_job_id, "provider_job_id")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = self._locked_job(
                connection, job_id, user_id, conversation_id)
            if job["provider_job_id"]:
                if job["provider_job_id"] != provider_job_id:
                    raise MediaProjectConflict(
                        "provider job identity cannot be replaced")
                connection.commit()
                return _decode(job)
            if job["status"] != "created":
                raise MediaProjectConflict("provider job is not awaiting submission")
            connection.execute(
                """UPDATE media_provider_jobs
                   SET provider_job_id=?, status='submitted', updated_at=?
                   WHERE job_id=?""",
                (provider_job_id, utc_now(), job_id),
            )
            connection.commit()
        return self.get_provider_job(
            job_id, user_id=user_id, conversation_id=conversation_id)

    def finish_provider_job(
        self, job_id: str, *, user_id: str, conversation_id: str,
        status: str, output: dict | None = None, error: str = "",
    ) -> dict[str, Any]:
        if status not in {"completed", "failed", "superseded"}:
            raise ValueError("terminal provider job status is invalid")
        output = _object(output or {}, "output")
        if status == "completed" and not output:
            raise ValueError("completed provider jobs require output")
        if status == "failed":
            _required(error, "error")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = self._locked_job(
                connection, job_id, user_id, conversation_id)
            if job["status"] == status:
                existing_output = json.loads(job["output_json"])
                if existing_output != output or job["error"] != str(error or ""):
                    raise MediaProjectConflict(
                        "terminal provider result cannot be replaced")
                connection.commit()
                return _decode(job)
            if job["status"] != "submitted":
                raise MediaProjectConflict(
                    "only a submitted provider job can become terminal")
            connection.execute(
                """UPDATE media_provider_jobs
                   SET status=?, output_json=?, error=?, updated_at=?
                   WHERE job_id=?""",
                (status, _json(output), str(error or ""), utc_now(), job_id),
            )
            connection.commit()
        return self.get_provider_job(
            job_id, user_id=user_id, conversation_id=conversation_id)

    def get_provider_job(
        self, job_id: str, *, user_id: str, conversation_id: str,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT job.*
                   FROM media_provider_jobs AS job
                   JOIN media_projects AS project
                     ON project.project_id=job.project_id
                   WHERE job.job_id=? AND project.user_id=?
                     AND project.conversation_id=?""",
                (
                    _required(job_id, "job_id"),
                    _required(user_id, "user_id"),
                    _required(conversation_id, "conversation_id"),
                ),
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _decode(row)

    def delete_conversation(self, *, user_id: str, conversation_id: str) -> int:
        user_id = _required(user_id, "user_id")
        conversation_id = _required(conversation_id, "conversation_id")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM media_projects WHERE user_id=? AND conversation_id=?",
                (user_id, conversation_id),
            )
            connection.commit()
            return int(cursor.rowcount)

    @staticmethod
    def _validate_artifacts(artifacts: list) -> None:
        for index, artifact in enumerate(artifacts):
            if not isinstance(artifact, dict):
                raise ValueError(f"artifacts[{index}] must be an object")
            has_file = bool(str(artifact.get("file_id") or ""))
            has_url = str(artifact.get("url") or "").startswith(
                "fs://filestore/")
            has_relay_path = bool(
                str(artifact.get("service") or "")
                and str(artifact.get("path") or ""))
            if not (has_file or has_url or has_relay_path):
                raise ValueError(
                    f"artifacts[{index}] requires a FileStore or relay reference")

    @staticmethod
    def _locked_project(
        connection: sqlite3.Connection, project_id: str,
        user_id: str, conversation_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """SELECT * FROM media_projects
               WHERE project_id=? AND user_id=? AND conversation_id=?""",
            (
                _required(project_id, "project_id"),
                _required(user_id, "user_id"),
                _required(conversation_id, "conversation_id"),
            ),
        ).fetchone()
        if row is None:
            raise KeyError(project_id)
        return row

    @staticmethod
    def _locked_job(
        connection: sqlite3.Connection, job_id: str,
        user_id: str, conversation_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """SELECT job.*
               FROM media_provider_jobs AS job
               JOIN media_projects AS project
                 ON project.project_id=job.project_id
               WHERE job.job_id=? AND project.user_id=?
                 AND project.conversation_id=?""",
            (
                _required(job_id, "job_id"),
                _required(user_id, "user_id"),
                _required(conversation_id, "conversation_id"),
            ),
        ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return row
