"""File checkpoint system for /rewind support.

Before each file modification (write, edit, delete), captures a reverse diff
or deletion marker so changes can be undone. Checkpoints are grouped by
user turn (checkpoint_id) and stored in FileStore.

Storage format in FileStore:
  category="checkpoint"
  metadata={"checkpoint_id": "...", "path": "...", "action": "modified|created|deleted",
            "service": "...", "conversation_id": "...", "timestamp": ...}
  content=reverse diff (for modified), full content (for deleted), empty (for created)
"""

import difflib
import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class CheckpointManager:
    """Manages file checkpoints for a conversation."""

    # Files already backed up in the current checkpoint (avoid duplicate diffs)
    # Key: (conversation_id, checkpoint_id, service_id, path)
    _captured: set = set()

    @classmethod
    def capture_before_write(cls, svc, path: str, content_after: bytes,
                             conversation_id: str, checkpoint_id: str,
                             service_id: str = "") -> None:
        """Capture file state before a write/edit operation.

        If the file exists, stores a reverse diff (new→old).
        If the file doesn't exist (creation), stores a creation marker.
        """
        if not conversation_id or not checkpoint_id:
            return

        key = (conversation_id, checkpoint_id, service_id, path)
        if key in cls._captured:
            return  # Already captured for this checkpoint
        cls._captured.add(key)

        try:
            from core.file_store import FileStore
            fs = FileStore.instance()

            # Read current content (before modification)
            try:
                old_content = svc.read_file(path)
                if isinstance(old_content, str):
                    old_content = old_content.encode("utf-8")
            except (FileNotFoundError, OSError):
                old_content = None

            if old_content is None:
                # File doesn't exist → this is a creation
                metadata = {
                    "checkpoint_id": checkpoint_id,
                    "path": path,
                    "action": "created",
                    "service": service_id,
                    "conversation_id": conversation_id,
                    "timestamp": time.time(),
                }
                fs.store(
                    f"checkpoint_{checkpoint_id}_{path.replace('/', '_')}",
                    json.dumps(metadata).encode("utf-8"),
                    "application/json",
                    category="checkpoint",
                    conversation_id=conversation_id,
                )
                return

            # File exists → generate reverse diff (new→old)
            try:
                old_text = old_content.decode("utf-8")
                if isinstance(content_after, bytes):
                    new_text = content_after.decode("utf-8")
                else:
                    new_text = str(content_after)
                # Unified diff: to restore, apply this patch
                diff_lines = list(difflib.unified_diff(
                    new_text.splitlines(keepends=True),
                    old_text.splitlines(keepends=True),
                    fromfile=f"a/{path}",
                    tofile=f"b/{path}",
                ))
                diff_text = "".join(diff_lines)
                if not diff_text:
                    # No actual change — skip
                    cls._captured.discard(key)
                    return
            except UnicodeDecodeError:
                # Binary file — store full old content
                diff_text = None

            metadata = {
                "checkpoint_id": checkpoint_id,
                "path": path,
                "action": "modified",
                "service": service_id,
                "conversation_id": conversation_id,
                "timestamp": time.time(),
                "binary": diff_text is None,
            }

            if diff_text is not None:
                # Text diff — compact
                payload = json.dumps({**metadata, "diff": diff_text}).encode("utf-8")
            else:
                # Binary — store full old content
                payload = json.dumps({**metadata, "has_binary": True}).encode("utf-8")
                # Store binary content separately
                fs.store(
                    f"checkpoint_bin_{checkpoint_id}_{path.replace('/', '_')}",
                    old_content,
                    "application/octet-stream",
                    category="checkpoint_bin",
                    conversation_id=conversation_id,
                )

            fs.store(
                f"checkpoint_{checkpoint_id}_{path.replace('/', '_')}",
                payload,
                "application/json",
                category="checkpoint",
                conversation_id=conversation_id,
            )
            logger.debug(f"[checkpoint] captured {path} for {checkpoint_id[:8]} "
                         f"({len(diff_text or '')} chars diff)")

        except Exception as e:
            logger.warning(f"[checkpoint] failed to capture {path}: {e}")

    @classmethod
    def capture_before_delete(cls, svc, path: str,
                              conversation_id: str, checkpoint_id: str,
                              service_id: str = "") -> None:
        """Capture file content before deletion."""
        if not conversation_id or not checkpoint_id:
            return

        key = (conversation_id, checkpoint_id, service_id, path)
        if key in cls._captured:
            return
        cls._captured.add(key)

        try:
            from core.file_store import FileStore
            fs = FileStore.instance()

            # Read content before deletion
            try:
                content = svc.read_file(path)
                if isinstance(content, str):
                    content = content.encode("utf-8")
            except (FileNotFoundError, OSError):
                return  # File doesn't exist, nothing to checkpoint

            metadata = {
                "checkpoint_id": checkpoint_id,
                "path": path,
                "action": "deleted",
                "service": service_id,
                "conversation_id": conversation_id,
                "timestamp": time.time(),
            }
            # Store full content (needed to restore)
            fs.store(
                f"checkpoint_{checkpoint_id}_{path.replace('/', '_')}",
                json.dumps({**metadata, "content": content.decode("utf-8", errors="replace")}).encode("utf-8"),
                "application/json",
                category="checkpoint",
                conversation_id=conversation_id,
            )
        except Exception as e:
            logger.warning(f"[checkpoint] failed to capture delete {path}: {e}")

    @classmethod
    def start_checkpoint(cls, conversation_id: str) -> str:
        """Start a new checkpoint for a user turn. Returns checkpoint_id."""
        import hashlib
        cp_id = hashlib.md5(
            f"{conversation_id}:{time.time()}".encode()
        ).hexdigest()[:12]
        # Register checkpoint in conversation store
        try:
            from core.conversation_store import ConversationStore
            store = ConversationStore.instance()
            checkpoints = store.get_extra(conversation_id, "checkpoints") or []
            checkpoints.append({
                "id": cp_id,
                "timestamp": time.time(),
                "message_count": store.message_count(conversation_id),
            })
            store.set_extra(conversation_id, "checkpoints", checkpoints)
        except Exception as e:
            logger.warning(f"[checkpoint] failed to register: {e}")
        return cp_id

    @classmethod
    def list_checkpoints(cls, conversation_id: str) -> list:
        """List all checkpoints for a conversation."""
        try:
            from core.conversation_store import ConversationStore
            return ConversationStore.instance().get_extra(
                conversation_id, "checkpoints") or []
        except Exception:
            return []

    @classmethod
    def rewind_files(cls, conversation_id: str, checkpoint_id: str,
                     service_resolver=None) -> dict:
        """Rewind file changes back to a checkpoint.

        Applies reverse diffs and restores deleted files for all checkpoints
        AFTER the target checkpoint_id (in reverse order).

        Returns {"restored": N, "deleted": N, "errors": [...]}
        """
        from core.file_store import FileStore
        fs = FileStore.instance()

        # Get all checkpoints
        checkpoints = cls.list_checkpoints(conversation_id)
        if not checkpoints:
            return {"restored": 0, "deleted": 0, "errors": ["No checkpoints"]}

        # Find target index
        target_idx = None
        for i, cp in enumerate(checkpoints):
            if cp["id"] == checkpoint_id:
                target_idx = i
                break
        if target_idx is None:
            return {"restored": 0, "deleted": 0,
                    "errors": [f"Checkpoint {checkpoint_id} not found"]}

        # Get checkpoint IDs to rewind (everything AFTER target, newest first)
        to_rewind = [cp["id"] for cp in reversed(checkpoints[target_idx + 1:])]
        if not to_rewind:
            return {"restored": 0, "deleted": 0, "errors": ["Nothing to rewind"]}

        restored = 0
        deleted = 0
        errors = []

        for cp_id in to_rewind:
            # Find all checkpoint entries for this cp_id
            entries = fs.list_by_category("checkpoint",
                                          conversation_id=conversation_id)
            for entry in entries:
                try:
                    data = json.loads(fs.get(entry["id"])[1].decode("utf-8"))
                except Exception:
                    continue
                if data.get("checkpoint_id") != cp_id:
                    continue

                path = data.get("path", "")
                action = data.get("action", "")
                svc_id = data.get("service", "")

                # Resolve filesystem service
                svc = None
                if service_resolver and svc_id:
                    svc = service_resolver(svc_id)
                if not svc and service_resolver:
                    svc = service_resolver(None)  # default

                if not svc:
                    errors.append(f"No service for {path}")
                    continue

                try:
                    if action == "created":
                        # File was created → delete it
                        try:
                            svc.delete_file(path)
                            deleted += 1
                        except (FileNotFoundError, OSError):
                            pass  # Already gone
                    elif action == "deleted":
                        # File was deleted → restore it
                        content = data.get("content", "")
                        svc.write_file(path, content.encode("utf-8"))
                        restored += 1
                    elif action == "modified":
                        if data.get("binary"):
                            # Restore binary from checkpoint_bin
                            bin_entries = fs.list_by_category(
                                "checkpoint_bin",
                                conversation_id=conversation_id)
                            for be in bin_entries:
                                _, bin_data, _ = fs.get(be["id"])
                                svc.write_file(path, bin_data)
                                restored += 1
                                break
                        else:
                            # Apply reverse diff
                            diff = data.get("diff", "")
                            if diff:
                                _apply_reverse_diff(svc, path, diff)
                                restored += 1
                except Exception as e:
                    errors.append(f"{path}: {e}")

            # Clean up checkpoint entries for this cp_id
            for entry in entries:
                try:
                    data = json.loads(fs.get(entry["id"])[1].decode("utf-8"))
                    if data.get("checkpoint_id") == cp_id:
                        fs.delete(entry["id"])
                except Exception:
                    pass
            # Also clean up binary entries
            try:
                bin_entries = fs.list_by_category("checkpoint_bin",
                                                  conversation_id=conversation_id)
                for be in bin_entries:
                    try:
                        fs.delete(be["id"])
                    except Exception:
                        pass
            except Exception:
                pass

        # Remove rewound checkpoints from the list
        checkpoints = checkpoints[:target_idx + 1]
        try:
            from core.conversation_store import ConversationStore
            ConversationStore.instance().set_extra(
                conversation_id, "checkpoints", checkpoints)
        except Exception:
            pass

        # Clear captured cache for this conversation
        cls._captured = {k for k in cls._captured
                         if k[0] != conversation_id}

        return {"restored": restored, "deleted": deleted, "errors": errors}

    @classmethod
    def cleanup_old(cls, max_age_days: int = 30) -> int:
        """Delete checkpoints older than max_age_days. Returns count deleted."""
        from core.file_store import FileStore
        fs = FileStore.instance()
        cutoff = time.time() - (max_age_days * 86400)
        count = 0
        for category in ("checkpoint", "checkpoint_bin"):
            try:
                entries = fs.list_by_category(category)
                for entry in entries:
                    try:
                        data = json.loads(fs.get(entry["id"])[1].decode("utf-8"))
                        if data.get("timestamp", 0) < cutoff:
                            fs.delete(entry["id"])
                            count += 1
                    except Exception:
                        pass
            except Exception:
                pass
        return count


def _apply_reverse_diff(svc, path: str, diff: str) -> None:
    """Apply a unified diff to a file (reverse direction)."""
    import subprocess
    import tempfile
    import os

    # Read current file
    try:
        current = svc.read_file(path)
        if isinstance(current, str):
            current = current.encode("utf-8")
    except (FileNotFoundError, OSError):
        current = b""

    # Try to apply with Python difflib (simple line-based apply)
    try:
        result = _apply_unified_diff(current.decode("utf-8"), diff)
        svc.write_file(path, result.encode("utf-8"))
        return
    except Exception:
        pass

    # Fallback: try system `patch` command
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.patch',
                                          delete=False) as pf:
            pf.write(diff)
            patch_path = pf.name
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.txt',
                                          delete=False) as tf:
            tf.write(current)
            tmp_path = tf.name
        subprocess.run(
            ["patch", tmp_path, patch_path],
            check=True, capture_output=True)
        with open(tmp_path, "rb") as f:
            svc.write_file(path, f.read())
        os.unlink(patch_path)
        os.unlink(tmp_path)
    except Exception as e:
        raise RuntimeError(f"Failed to apply diff to {path}: {e}")


def _apply_unified_diff(text: str, diff: str) -> str:
    """Apply a unified diff to text. Simple implementation."""
    lines = text.splitlines(keepends=True)
    result = []
    diff_lines = diff.splitlines(keepends=True)

    i = 0  # position in original text
    for dl in diff_lines:
        if dl.startswith("---") or dl.startswith("+++"):
            continue
        if dl.startswith("@@"):
            # Parse hunk header: @@ -start,count +start,count @@
            import re
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", dl)
            if m:
                target = int(m.group(1)) - 1  # 0-indexed
                # Add unchanged lines before this hunk
                while i < target and i < len(lines):
                    result.append(lines[i])
                    i += 1
            continue
        if dl.startswith("-"):
            # Remove line (skip it in original)
            i += 1
        elif dl.startswith("+"):
            # Add line
            result.append(dl[1:])
        elif dl.startswith(" "):
            # Context line
            if i < len(lines):
                result.append(lines[i])
            i += 1

    # Add remaining lines
    while i < len(lines):
        result.append(lines[i])
        i += 1

    return "".join(result)
