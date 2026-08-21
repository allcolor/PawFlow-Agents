"""Server coordinator for relay-backed ScratchDir roots."""

from __future__ import annotations

import hashlib
import mimetypes
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath

from core.scratchdir_models import (
    DEFAULT_QUOTA_BYTES,
    DEFAULT_QUOTA_FILES,
    SCRATCHDIR_FORMAT,
    ScratchDirError,
    ScratchDirState,
    validate_ttl,
)
from core.scratchdir_store import ScratchDirStore


class ScratchDirManager:
    """Coordinate relay receipts with durable server metadata."""

    def __init__(self, relay, store: ScratchDirStore | None = None) -> None:
        self._relay = relay
        self._store = store or ScratchDirStore.instance()

    @property
    def relay_id(self) -> str:
        return str(
            getattr(self._relay, "_service_id", "")
            or getattr(self._relay, "service_id", "")
            or "").strip()

    def _require_relay(self) -> str:
        relay_id = self.relay_id
        if not relay_id:
            raise ScratchDirError(
                "scratchdir_relay_required",
                "ScratchDir requires the conversation's default relay")
        supports = getattr(self._relay, "supports_capability", None)
        if not callable(supports) or not supports("scratchdir_v1"):
            raise ScratchDirError(
                "scratchdir_capability_missing",
                f"relay '{relay_id}' does not advertise scratchdir_v1")
        return relay_id

    @staticmethod
    def _scope_hash(user_id: str, conversation_id: str,
                    agent_name: str, relay_id: str) -> str:
        payload = (
            f"{user_id}\0{conversation_id}\0{agent_name}\0{relay_id}"
        ).encode()
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _operation_id() -> str:
        return uuid.uuid4().hex

    @staticmethod
    def _relay_error(exc: Exception) -> ScratchDirError:
        text = str(exc)
        if text.startswith("[") and "]" in text:
            code, message = text[1:].split("]", 1)
            if code.startswith("scratchdir_"):
                return ScratchDirError(code, message.lstrip(": "))
        return ScratchDirError("scratchdir_unavailable", text)

    def _public(self, record) -> dict:
        value = record.public_dict()
        value["status"] = value.pop("state")
        return value

    def execute(self, *, action: str, user_id: str, conversation_id: str,
                agent_name: str, ttl_hours=None) -> dict:
        relay_id = self._require_relay()
        if action == "ensure":
            return self.ensure(
                user_id, conversation_id, agent_name, relay_id,
                ttl_hours=ttl_hours)
        if action == "status":
            return self.status(user_id, conversation_id, agent_name, relay_id)
        if action == "renew":
            return self.renew(
                user_id, conversation_id, agent_name, relay_id,
                ttl_hours=ttl_hours)
        if action == "clear":
            return self.clear(user_id, conversation_id, agent_name, relay_id)
        raise ScratchDirError(
            "scratchdir_invalid_request",
            "action must be status, ensure, renew, or clear")

    def bind(self, user_id: str, conversation_id: str, agent_name: str):
        """Ensure the scoped root and return its filesystem facade."""

        relay_id = self._require_relay()
        self.ensure(user_id, conversation_id, agent_name, relay_id)
        record = self._store.get(
            user_id, conversation_id, agent_name, relay_id)
        if record is None or record.state != ScratchDirState.ACTIVE.value:
            raise ScratchDirError(
                "scratchdir_unavailable",
                "ScratchDir could not be activated on the default relay")
        from services.scratchdir_service import ScratchDirService
        return ScratchDirService(
            self._relay,
            scratch_id=record.id,
            scope_hash=self._scope_hash(
                user_id, conversation_id, agent_name, relay_id),
            epoch=record.epoch,
        )

    def ensure(self, user_id: str, conversation_id: str, agent_name: str,
               relay_id: str, *, ttl_hours=None) -> dict:
        ttl = validate_ttl(ttl_hours)
        existing = self._store.get(
            user_id, conversation_id, agent_name, relay_id)
        if existing is not None and existing.state == ScratchDirState.ACTIVE.value:
            return self.status(user_id, conversation_id, agent_name, relay_id)
        scratch_id = existing.id if existing is not None else f"sd_{uuid.uuid4().hex}"
        epoch = (existing.epoch + 1) if existing is not None else 1
        operation_id = self._operation_id()
        expires_at = time.time() + ttl * 3600
        kwargs = {
            "scratch_id": scratch_id,
            "scope_hash": self._scope_hash(
                user_id, conversation_id, agent_name, relay_id),
            "operation_id": operation_id,
            "epoch": epoch,
            "expires_at": expires_at,
            "quota_bytes": DEFAULT_QUOTA_BYTES,
            "quota_files": DEFAULT_QUOTA_FILES,
        }
        try:
            receipt = self._relay.scratchdir_ensure(**kwargs)
        except Exception as exc:
            raise self._relay_error(exc) from exc
        record = self._store.activate(
            user_id, conversation_id, agent_name, relay_id,
            locator=str(receipt.get("locator") or ""),
            ttl_hours=ttl,
            quota_bytes=receipt.get("quota_bytes"),
            quota_files=receipt.get("quota_files"),
            operation_id=operation_id,
            scratch_id=scratch_id,
        )
        record = self._store.update_usage(
            user_id, conversation_id, agent_name, relay_id,
            observed_bytes=int(receipt.get("observed_bytes") or 0),
            observed_files=int(receipt.get("observed_files") or 0))
        return self._public(record)

    def status(self, user_id: str, conversation_id: str, agent_name: str,
               relay_id: str) -> dict:
        record = self._store.get(
            user_id, conversation_id, agent_name, relay_id)
        if record is None or record.state == ScratchDirState.CLEARED.value:
            return {
                "format": SCRATCHDIR_FORMAT,
                "status": "absent",
                "url": "fs://scratchdir/",
                "mount_path": "/scratch",
                "relay_id": relay_id,
            }
        try:
            receipt = self._relay.scratchdir_status(
                scratch_id=record.id,
                scope_hash=self._scope_hash(
                    user_id, conversation_id, agent_name, relay_id),
                epoch=record.epoch,
            )
        except Exception as exc:
            raise self._relay_error(exc) from exc
        record = self._store.update_usage(
            user_id, conversation_id, agent_name, relay_id,
            observed_bytes=int(receipt.get("observed_bytes") or 0),
            observed_files=int(receipt.get("observed_files") or 0))
        return self._public(record)

    def renew(self, user_id: str, conversation_id: str, agent_name: str,
              relay_id: str, *, ttl_hours=None) -> dict:
        ttl = validate_ttl(ttl_hours)
        record = self._store.get(
            user_id, conversation_id, agent_name, relay_id)
        if record is None or record.state != ScratchDirState.ACTIVE.value:
            raise ScratchDirError(
                "scratchdir_not_active",
                "ScratchDir is not active; call ensure first")
        operation_id = self._operation_id()
        try:
            self._relay.scratchdir_renew(
                scratch_id=record.id,
                scope_hash=self._scope_hash(
                    user_id, conversation_id, agent_name, relay_id),
                operation_id=operation_id,
                epoch=record.epoch,
                expires_at=time.time() + ttl * 3600,
            )
        except Exception as exc:
            raise self._relay_error(exc) from exc
        return self._public(self._store.renew(
            user_id, conversation_id, agent_name, relay_id,
            ttl_hours=ttl))

    def clear(self, user_id: str, conversation_id: str, agent_name: str,
              relay_id: str) -> dict:
        record = self._store.get(
            user_id, conversation_id, agent_name, relay_id)
        if record is None:
            return {
                "format": SCRATCHDIR_FORMAT,
                "status": "absent",
                "url": "fs://scratchdir/",
                "mount_path": "/scratch",
                "relay_id": relay_id,
            }
        operation_id = self._operation_id()
        clearing = self._store.begin_clear(
            user_id, conversation_id, agent_name, relay_id,
            operation_id=operation_id)
        try:
            self._relay.scratchdir_clear(
                scratch_id=clearing.id,
                scope_hash=self._scope_hash(
                    user_id, conversation_id, agent_name, relay_id),
                operation_id=operation_id,
                epoch=clearing.epoch,
            )
        except Exception as exc:
            self._store.mark_orphaned(
                user_id, conversation_id, agent_name, relay_id,
                operation_id=operation_id)
            raise self._relay_error(exc) from exc
        return self._public(self._store.finish_clear(
            user_id, conversation_id, agent_name, relay_id,
            operation_id=operation_id))

    def tree(self, user_id: str, conversation_id: str, agent_name: str,
             relay_id: str, *, max_entries: int = 200) -> dict:
        """Return a bounded logical tree without exposing relay paths."""

        limit = min(500, max(1, int(max_entries)))
        status = self.status(user_id, conversation_id, agent_name, relay_id)
        if status["status"] != ScratchDirState.ACTIVE.value:
            return {**status, "entries": [], "truncated": False}
        service = self.bind(user_id, conversation_id, agent_name)
        entries = service.list_dir(".", recursive=True, max_entries=limit + 1)
        public = [{
            "path": str(entry.name).replace("\\", "/"),
            "kind": entry.kind,
            "size": int(entry.size or 0),
            "modified": entry.modified,
        } for entry in entries[:limit]]
        return {**status, "entries": public,
                "truncated": len(entries) > limit}

    def promote(self, user_id: str, conversation_id: str, agent_name: str,
                relay_id: str, *, path: str) -> dict:
        """Stream one scoped file into conversation FileStore."""

        from services.scratchdir_service import normalize_scratchdir_path

        logical = normalize_scratchdir_path(path)
        if logical == ".":
            raise ScratchDirError(
                "scratchdir_request_invalid", "A file path is required")
        service = self.bind(user_id, conversation_id, agent_name)
        with tempfile.TemporaryDirectory(prefix="pawflow-scratchdir-promote-") as tmp:
            staged = Path(tmp) / "payload"
            result = service.copy_file_to_local(logical, str(staged))
            if not staged.is_file():
                raise ScratchDirError(
                    "scratchdir_not_found", "ScratchDir file was not found")
            from core.file_store import FileStore
            filename = PurePosixPath(logical).name
            content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            file_id = FileStore.instance().store_file(
                filename, str(staged), content_type,
                conversation_id=conversation_id, user_id=user_id,
                agent_name=agent_name, category="scratchdir")
        return {
            "format": SCRATCHDIR_FORMAT,
            "file_id": file_id,
            "filename": filename,
            "size": int((result or {}).get("written", 0)),
            "url": f"fs://filestore/{file_id}/{filename}",
        }

    def cleanup_expired(self, user_id: str, conversation_id: str,
                        agent_name: str, relay_id: str) -> bool:
        """Clear one expired root through the normal fenced lifecycle."""

        record = self._store.get(
            user_id, conversation_id, agent_name, relay_id)
        if record is None or record.state != ScratchDirState.EXPIRED.value:
            return False
        self.clear(user_id, conversation_id, agent_name, relay_id)
        return True
