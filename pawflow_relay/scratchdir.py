"""Relay-owned lifecycle for scoped ScratchDir byte roots."""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

_CAPABILITY = "scratchdir_v1"
_ID_RE = re.compile(r"sd_[a-f0-9]{32}")
_HASH_RE = re.compile(r"[a-f0-9]{64}")
_LOCK = threading.RLock()
_SCOPED_ACTIONS = frozenset({
    "list_dir", "read_file", "read_file_stream", "read_file_chunked",
    "read_chunk", "copy_file",
    "write_file", "write_file_chunked", "delete_file", "mkdir", "stat",
    "exists", "search", "grep", "find_replace", "edit", "batch_edit",
    "apply_patch", "edit_notebook", "exec", "exec_stream",
})


class ScratchDirRelayError(RuntimeError):
    """Relay-local typed ScratchDir failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def capability() -> str:
    return _CAPABILITY


def _runtime_root() -> Path:
    configured = os.environ.get("PAWFLOW_SCRATCHDIR_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".pawflow" / "runtime" / "scratchdirs").resolve()


def _require_token(value: object, pattern: re.Pattern, code: str,
                   label: str) -> str:
    text = str(value or "").strip()
    if not pattern.fullmatch(text):
        raise ScratchDirRelayError(code, f"invalid {label}")
    return text


def _paths(scratch_id: str, workspace_root: str = "") -> tuple[Path, Path, Path]:
    base = _runtime_root()
    if workspace_root:
        workspace = Path(workspace_root).resolve()
        if base == workspace or base.is_relative_to(workspace):
            raise ScratchDirRelayError(
                "scratchdir_root_unsafe",
                "ScratchDir runtime root must be outside the workspace")
    root = (base / scratch_id).resolve()
    if root.parent != base:
        raise ScratchDirRelayError(
            "scratchdir_path_escape", "ScratchDir identifier escapes runtime root")
    return root, root / "files", root / "record.json"


def _read_record(record_path: Path) -> dict | None:
    try:
        value = json.loads(record_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScratchDirRelayError(
            "scratchdir_record_invalid",
            "ScratchDir relay record is unreadable") from exc
    if not isinstance(value, dict):
        raise ScratchDirRelayError(
            "scratchdir_record_invalid", "ScratchDir relay record is invalid")
    return value


def _write_record(record_path: Path, record: dict) -> None:
    record_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = record_path.with_name(
        f".record-{os.getpid()}-{threading.get_ident()}.tmp")
    data = json.dumps(
        record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    try:
        temporary.write_text(data, encoding="utf-8")
        os.replace(temporary, record_path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _require_owner(record: dict, scope_hash: str) -> None:
    if record.get("scope_hash") != scope_hash:
        raise ScratchDirRelayError(
            "scratchdir_owner_mismatch", "ScratchDir owner does not match")


def _usage(files_root: Path, quota_bytes: int, quota_files: int) -> tuple[int, int]:
    total_bytes = 0
    total_files = 0
    if files_root.is_symlink():
        raise ScratchDirRelayError(
            "scratchdir_unsafe_entry",
            "ScratchDir root must not be a symbolic link")
    if not files_root.exists():
        return 0, 0
    resolved_root = files_root.resolve()
    for path in files_root.rglob("*"):
        if path.is_symlink():
            try:
                target = path.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ScratchDirRelayError(
                    "scratchdir_unsafe_entry",
                    "ScratchDir contains an unsafe symbolic link") from exc
            if target != resolved_root and not target.is_relative_to(resolved_root):
                raise ScratchDirRelayError(
                    "scratchdir_unsafe_entry",
                    "ScratchDir contains an unsafe symbolic link")
            # The target is already visited and counted at its canonical path.
            # Skipping the alias supports standard in-tree links such as a
            # virtualenv lib64-to-lib link without double-counting its files.
            continue
        if not path.is_file():
            continue
        total_files += 1
        total_bytes += path.stat().st_size
        if total_files > quota_files:
            raise ScratchDirRelayError(
                "scratchdir_quota_files", "ScratchDir file quota exceeded")
        if total_bytes > quota_bytes:
            raise ScratchDirRelayError(
                "scratchdir_quota_bytes", "ScratchDir byte quota exceeded")
    return total_bytes, total_files


def _logical_path(value: object) -> str:
    path = str(value if value is not None else ".").strip()
    if path.startswith("fs://scratchdir/"):
        path = path[len("fs://scratchdir/"):] or "."
    if path == "/scratch":
        path = "."
    elif path.startswith("/scratch/"):
        path = path[len("/scratch/"):]
    elif path.startswith(("/", "\\")):
        raise ScratchDirRelayError(
            "scratchdir_path_escape", "ScratchDir paths must be relative")
    if "\\" in path:
        raise ScratchDirRelayError(
            "scratchdir_path_escape", "ScratchDir paths must use forward slashes")
    if path in ("", "."):
        return "."
    if any(part in ("", ".", "..") for part in path.split("/")):
        raise ScratchDirRelayError(
            "scratchdir_path_escape",
            "ScratchDir path contains an unsafe component")
    return path


def _contained_path(files_root: Path, value: object) -> Path:
    relative = _logical_path(value)
    target = (files_root / relative).resolve()
    root = files_root.resolve()
    if target != root and not target.is_relative_to(root):
        raise ScratchDirRelayError(
            "scratchdir_path_escape", "ScratchDir path escapes its scoped root")
    return target


def resolve_operation(action: str, message: dict, *,
                      workspace_root: str = "") -> tuple[str, str]:
    """Resolve one generic filesystem RPC against its authenticated root."""

    if action not in _SCOPED_ACTIONS:
        raise ScratchDirRelayError(
            "scratchdir_action_unknown",
            f"action is not available on ScratchDir: {action}")
    ticket = message.get("scratchdir")
    if not isinstance(ticket, dict):
        raise ScratchDirRelayError(
            "scratchdir_context_missing", "ScratchDir ticket is required")
    scratch_id = _require_token(
        ticket.get("scratch_id"), _ID_RE,
        "scratchdir_id_invalid", "scratch_id")
    scope_hash = _require_token(
        ticket.get("scope_hash"), _HASH_RE,
        "scratchdir_scope_invalid", "scope_hash")
    epoch = int(ticket.get("epoch") or 0)
    _root, files_root, record_path = _paths(scratch_id, workspace_root)
    with _LOCK:
        record = _read_record(record_path)
        if record is None:
            raise ScratchDirRelayError(
                "scratchdir_not_found", "ScratchDir is not registered on relay")
        _require_owner(record, scope_hash)
        if int(record.get("epoch") or 0) != epoch:
            raise ScratchDirRelayError(
                "scratchdir_epoch_stale", "ScratchDir epoch is stale")
        if (record.get("state") == "active"
                and float(record.get("expires_at") or 0) <= time.time()):
            record["state"] = "expired"
            record["updated_at"] = time.time()
            _write_record(record_path, record)
        if record.get("state") != "active":
            raise ScratchDirRelayError(
                "scratchdir_expired", "ScratchDir is not active")
        _usage(
            files_root,
            int(record["quota_bytes"]),
            int(record["quota_files"]),
        )
        target = _contained_path(files_root, message.get("path", "."))
        if action == "copy_file":
            _contained_path(files_root, message.get("dest_path", "."))
        elif action == "batch_edit":
            for edit in message.get("edits") or []:
                if not isinstance(edit, dict):
                    raise ScratchDirRelayError(
                        "scratchdir_request_invalid",
                        "ScratchDir edit entries must be objects")
                _contained_path(files_root, edit.get("path", ""))
    return str(files_root), str(target)


def validate_operation(message: dict, *, workspace_root: str = "") -> None:
    """Recount usage after a potentially mutating scoped operation."""

    ticket = message.get("scratchdir") or {}
    scratch_id = _require_token(
        ticket.get("scratch_id"), _ID_RE,
        "scratchdir_id_invalid", "scratch_id")
    scope_hash = _require_token(
        ticket.get("scope_hash"), _HASH_RE,
        "scratchdir_scope_invalid", "scope_hash")
    _root, files_root, record_path = _paths(scratch_id, workspace_root)
    with _LOCK:
        record = _read_record(record_path)
        if record is None:
            raise ScratchDirRelayError(
                "scratchdir_not_found", "ScratchDir is not registered on relay")
        _require_owner(record, scope_hash)
        _usage(
            files_root,
            int(record["quota_bytes"]),
            int(record["quota_files"]),
        )


def redact_result(value, files_root: str):
    """Replace implementation-private roots in a nested RPC result."""

    if isinstance(value, str):
        variants = {files_root, files_root.replace("\\", "/")}
        result = value
        for physical in variants:
            if physical:
                result = result.replace(physical, "/scratch")
        return result
    if isinstance(value, dict):
        return {key: redact_result(item, files_root)
                for key, item in value.items()}
    if isinstance(value, list):
        return [redact_result(item, files_root) for item in value]
    return value


def _public(record: dict, files_root: Path) -> dict:
    observed_bytes, observed_files = _usage(
        files_root, int(record["quota_bytes"]), int(record["quota_files"]))
    return {
        "format": "pawflow.scratchdir.relay.v1",
        "capability": _CAPABILITY,
        "scratch_id": record["scratch_id"],
        "state": record["state"],
        "epoch": int(record["epoch"]),
        "operation_id": record["operation_id"],
        "expires_at": float(record["expires_at"]),
        "quota_bytes": int(record["quota_bytes"]),
        "quota_files": int(record["quota_files"]),
        "observed_bytes": observed_bytes,
        "observed_files": observed_files,
        "locator": record["scratch_id"],
    }


def ensure(message: dict, *, workspace_root: str = "") -> dict:
    scratch_id = _require_token(
        message.get("scratch_id"), _ID_RE, "scratchdir_id_invalid", "scratch_id")
    scope_hash = _require_token(
        message.get("scope_hash"), _HASH_RE,
        "scratchdir_scope_invalid", "scope_hash")
    operation_id = str(message.get("operation_id") or "").strip()
    if not operation_id:
        raise ScratchDirRelayError(
            "scratchdir_operation_missing", "operation_id is required")
    epoch = int(message.get("epoch") or 0)
    quota_bytes = int(message.get("quota_bytes") or 0)
    quota_files = int(message.get("quota_files") or 0)
    expires_at = float(message.get("expires_at") or 0)
    if epoch < 1 or quota_bytes < 1 or quota_files < 1 or expires_at <= 0:
        raise ScratchDirRelayError(
            "scratchdir_request_invalid",
            "epoch, quotas and expires_at must be positive")
    root, files_root, record_path = _paths(scratch_id, workspace_root)
    with _LOCK:
        existing = _read_record(record_path)
        if existing is not None:
            _require_owner(existing, scope_hash)
            if existing.get("operation_id") == operation_id:
                return _public(existing, files_root)
            old_epoch = int(existing.get("epoch") or 0)
            if epoch < old_epoch:
                raise ScratchDirRelayError(
                    "scratchdir_epoch_stale", "ScratchDir epoch is stale")
            if existing.get("state") == "active" and epoch == old_epoch:
                return _public(existing, files_root)
            if epoch > old_epoch and files_root.exists():
                shutil.rmtree(files_root)
        root.mkdir(parents=True, exist_ok=True)
        files_root.mkdir(parents=True, exist_ok=True)
        record = {
            "format": "pawflow.scratchdir.relay-record.v1",
            "scratch_id": scratch_id,
            "scope_hash": scope_hash,
            "state": "active",
            "epoch": epoch,
            "operation_id": operation_id,
            "expires_at": expires_at,
            "quota_bytes": quota_bytes,
            "quota_files": quota_files,
            "created_at": (
                float(existing.get("created_at"))
                if existing else time.time()),
            "updated_at": time.time(),
            "cleared_at": 0.0,
        }
        _write_record(record_path, record)
        return _public(record, files_root)


def status(message: dict, *, workspace_root: str = "") -> dict:
    scratch_id = _require_token(
        message.get("scratch_id"), _ID_RE, "scratchdir_id_invalid", "scratch_id")
    scope_hash = _require_token(
        message.get("scope_hash"), _HASH_RE,
        "scratchdir_scope_invalid", "scope_hash")
    _root, files_root, record_path = _paths(scratch_id, workspace_root)
    with _LOCK:
        record = _read_record(record_path)
        if record is None:
            raise ScratchDirRelayError(
                "scratchdir_not_found", "ScratchDir is not registered on relay")
        _require_owner(record, scope_hash)
        if record.get("state") == "active" and float(
                record.get("expires_at") or 0) <= time.time():
            record["state"] = "expired"
            record["updated_at"] = time.time()
            _write_record(record_path, record)
        return _public(record, files_root)


def renew(message: dict, *, workspace_root: str = "") -> dict:
    scratch_id = _require_token(
        message.get("scratch_id"), _ID_RE, "scratchdir_id_invalid", "scratch_id")
    scope_hash = _require_token(
        message.get("scope_hash"), _HASH_RE,
        "scratchdir_scope_invalid", "scope_hash")
    operation_id = str(message.get("operation_id") or "").strip()
    epoch = int(message.get("epoch") or 0)
    expires_at = float(message.get("expires_at") or 0)
    if not operation_id or epoch < 1 or expires_at <= 0:
        raise ScratchDirRelayError(
            "scratchdir_request_invalid",
            "operation_id, epoch and expires_at are required")
    _root, files_root, record_path = _paths(scratch_id, workspace_root)
    with _LOCK:
        record = _read_record(record_path)
        if record is None:
            raise ScratchDirRelayError(
                "scratchdir_not_found", "ScratchDir is not registered on relay")
        _require_owner(record, scope_hash)
        if int(record.get("epoch") or 0) != epoch:
            raise ScratchDirRelayError(
                "scratchdir_epoch_stale", "ScratchDir epoch is stale")
        if record.get("state") != "active":
            raise ScratchDirRelayError(
                "scratchdir_not_active", "ScratchDir is not active")
        if record.get("operation_id") != operation_id:
            record["operation_id"] = operation_id
            record["expires_at"] = expires_at
            record["updated_at"] = time.time()
            _write_record(record_path, record)
        return _public(record, files_root)


def clear(message: dict, *, workspace_root: str = "") -> dict:
    scratch_id = _require_token(
        message.get("scratch_id"), _ID_RE, "scratchdir_id_invalid", "scratch_id")
    scope_hash = _require_token(
        message.get("scope_hash"), _HASH_RE,
        "scratchdir_scope_invalid", "scope_hash")
    operation_id = str(message.get("operation_id") or "").strip()
    epoch = int(message.get("epoch") or 0)
    if not operation_id or epoch < 1:
        raise ScratchDirRelayError(
            "scratchdir_request_invalid", "operation_id and epoch are required")
    _root, files_root, record_path = _paths(scratch_id, workspace_root)
    with _LOCK:
        record = _read_record(record_path)
        if record is None:
            raise ScratchDirRelayError(
                "scratchdir_not_found", "ScratchDir is not registered on relay")
        _require_owner(record, scope_hash)
        old_epoch = int(record.get("epoch") or 0)
        if record.get("state") == "cleared":
            if record.get("operation_id") == operation_id:
                return _public(record, files_root)
            raise ScratchDirRelayError(
                "scratchdir_state_conflict", "ScratchDir is already cleared")
        if epoch != old_epoch + 1:
            raise ScratchDirRelayError(
                "scratchdir_epoch_stale",
                "ScratchDir clear epoch must advance exactly once")
        if files_root.exists():
            shutil.rmtree(files_root)
        record.update({
            "state": "cleared",
            "epoch": epoch,
            "operation_id": operation_id,
            "updated_at": time.time(),
            "cleared_at": time.time(),
        })
        _write_record(record_path, record)
        return _public(record, files_root)


def reconcile(message: dict, *, workspace_root: str = "") -> dict:
    return status(message, workspace_root=workspace_root)


_HANDLERS = {
    "scratchdir_ensure": ensure,
    "scratchdir_status": status,
    "scratchdir_renew": renew,
    "scratchdir_clear": clear,
    "scratchdir_reconcile": reconcile,
}


def is_action(action: str) -> bool:
    return action in _HANDLERS


def handle(action: str, message: dict, *, workspace_root: str = "") -> dict:
    try:
        return _HANDLERS[action](message, workspace_root=workspace_root)
    except KeyError as exc:
        raise ScratchDirRelayError(
            "scratchdir_action_unknown", f"unknown ScratchDir action: {action}") from exc
