"""ConversationStore — directory-based conversation storage.

Each conversation is a directory:
  data/conversations/{user}/{conv_id}/
    transcript.jsonl              — all messages (faithful replay)
    shared.jsonl                  — shared context (public messages for all agents)
    {agent}/context.jsonl         — per-agent LLM context
    extras.json                   — atomic JSON metadata (no duplication)

Transcript line invariants (EVERY record, no exception):
  - msg_id  : own UUID, unique per line (not shared across records)
  - ts      : wall-clock epoch seconds when the line was written
  - seq     : per-conversation strictly-increasing integer. If line B
              follows line A in the file, seq_B > seq_A — always.

Records that reference another line (e.g. msg_patch → msg) carry the
target via a dedicated field (target_msg_id), never by squatting msg_id.

Line types:
  {"t":"meta", "msg_id":..., "ts":..., "seq":..., "user_id":"...", "status":"idle", "created_at":N, "expires_at":N}
  {"t":"msg", "msg_id":..., "ts":..., "seq":..., "role":"...", "content":"...", "source":{}, ...}
  {"t":"msg", ..., "private":true}  (tool calls/results — agent context only)
  {"t":"msg_patch", "msg_id":..., "ts":..., "seq":..., "target_msg_id":"...", ...fields}
  {"t":"trace_update", "msg_id":..., "ts":..., "seq":..., "trace_id":"...", ...}
  {"t":"status", "msg_id":..., "ts":..., "seq":..., "status":"active"}

Context files ({agent}/context.jsonl, shared.jsonl):
  One message dict per line (no "t" prefix — raw messages, same
  (msg_id, ts, seq) invariants).

Per-conversation locks ensure atomicity of logical operations.
"""

import json
import logging
import os
import shutil
import subprocess  # nosec B404
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.segmented_jsonl import SegmentedJsonl

logger = logging.getLogger(__name__)

_CTX_CACHE_MAX_MESSAGES = int(os.getenv("PAWFLOW_CTX_CACHE_MAX_MESSAGES", "500") or "500")
_CTX_CACHE_MAX_CHARS = int(os.getenv("PAWFLOW_CTX_CACHE_MAX_CHARS", "1000000") or "1000000")
_CTX_CACHE_MAX_CONVS = int(os.getenv("PAWFLOW_CTX_CACHE_MAX_CONVS", "20") or "20")
_CONV_LOCK_DIAG_MS = float(os.getenv("PAWFLOW_CONV_LOCK_DIAG_MS", "100") or "100")
_GIT_RETENTION_DAYS = int(os.getenv("PAWFLOW_CONV_GIT_RETENTION_DAYS", "7") or "7")
_GIT_RETENTION_COMMITS = int(os.getenv("PAWFLOW_CONV_GIT_RETENTION_COMMITS", "250") or "250")
_GIT_RETENTION_INTERVAL_SEC = int(os.getenv("PAWFLOW_CONV_GIT_RETENTION_INTERVAL_SEC", "86400") or "86400")

import core.paths as _paths


class _ConversationTimedRLock:
    """RLock wrapper that logs slow holders with the acquiring call-site."""

    def __init__(self, cid: str):
        self._cid = cid
        self._lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._owner_ident: Optional[int] = None
        self._owner_label = ""
        self._owner_started = 0.0
        self._depth = 0

    @staticmethod
    def _caller_label() -> str:
        try:
            frame = sys._getframe(3)
        except ValueError:
            frame = sys._getframe(2)
        return f"{frame.f_code.co_name}:{frame.f_lineno}"

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        label = self._caller_label()
        started = time.monotonic()
        ident = threading.get_ident()
        with self._state_lock:
            blocked_by = self._owner_label
            reentrant = self._owner_ident == ident
        ok = self._lock.acquire(blocking, timeout)
        if not ok:
            return False
        waited_ms = (time.monotonic() - started) * 1000.0
        with self._state_lock:
            if reentrant and self._owner_ident == ident:
                self._depth += 1
            else:
                self._owner_ident = ident
                self._owner_label = label
                self._owner_started = time.monotonic()
                self._depth = 1
        if waited_ms >= _CONV_LOCK_DIAG_MS and not reentrant:
            logger.warning(
                "[conv-lock:%s] waited %.1fms at %s blocked_by=%s",
                self._cid[:8], waited_ms, label, blocked_by or "?")
        return True

    def release(self) -> None:
        ident = threading.get_ident()
        log_label = ""
        held_ms = 0.0
        with self._state_lock:
            if self._owner_ident == ident and self._depth > 0:
                self._depth -= 1
                if self._depth == 0:
                    log_label = self._owner_label
                    held_ms = (time.monotonic() - self._owner_started) * 1000.0
                    self._owner_ident = None
                    self._owner_label = ""
                    self._owner_started = 0.0
        try:
            self._lock.release()
        finally:
            if held_ms >= _CONV_LOCK_DIAG_MS:
                logger.warning(
                    "[conv-lock:%s] held %.1fms by %s",
                    self._cid[:8], held_ms, log_label or "?")

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


class ConversationStore:
    """Singleton JSONL conversation store. Thread-safe, append-only."""

    _instance: Optional["ConversationStore"] = None
    _lock = threading.Lock()

    def __init__(self, store_dir: str = ""):
        self._store_dir = Path(store_dir or str(_paths.CONVERSATIONS_DIR))
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._conv_locks: Dict[str, _ConversationTimedRLock] = {}
        self._conv_locks_lock = threading.Lock()
        self._extras_locks: Dict[str, threading.RLock] = {}
        self._extras_locks_lock = threading.Lock()
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_lock = threading.Lock()
        self._ctx_cache: Dict[str, Dict[str, List[Dict]]] = {}  # cid -> {agent -> messages}
        self._ctx_cache_lock = threading.Lock()
        self._context_usage_repair_mtime: Dict[str, float] = {}
        self._cid_user: Dict[str, str] = {}  # cid -> user_id (fast lookup, no scan)
        self._loaded = False

    @classmethod
    def instance(cls) -> "ConversationStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        with cls._lock:
            cls._instance = None

    def _get_conv_lock(self, cid: str) -> _ConversationTimedRLock:
        with self._conv_locks_lock:
            if cid not in self._conv_locks:
                self._conv_locks[cid] = _ConversationTimedRLock(cid)
            return self._conv_locks[cid]

    def _get_extras_lock(self, cid: str) -> threading.RLock:
        with self._extras_locks_lock:
            if cid not in self._extras_locks:
                self._extras_locks[cid] = threading.RLock()
            return self._extras_locks[cid]

    def _stamp_line(self, cid: str, line: Dict[str, Any]) -> Dict[str, Any]:
        """Stamp the five-field invariant on every persisted record:
        ``(msg_id, ts, seq, conversation_id, user_id)``.

        Seq is the on-disk line index: assigned at WRITE time as
        ``last_persisted + 1``. Any pre-stamped seq on the incoming
        line is overwritten — producers cannot reserve a seq in
        advance because disk order is the sole source of truth.
        Callers MUST hold the per-conv lock while invoking this method
        and performing the subsequent write; the lock is what
        serializes mint + write into an atomic step per conv.
        """
        if not cid:
            raise ValueError(
                "_stamp_line requires a non-empty conversation_id — "
                "every persisted record lives inside a conversation")
        from core.llm_client import (
            _peek_persisted_seq, _record_persisted_seq)
        if not line.get("msg_id"):
            line["msg_id"] = uuid.uuid4().hex[:12]
        if "ts" not in line and "timestamp" not in line:
            line["ts"] = time.time()
        line["seq"] = _peek_persisted_seq(cid) + 1
        _record_persisted_seq(cid, line["seq"])
        if not line.get("conversation_id"):
            line["conversation_id"] = cid
        if not line.get("user_id"):
            line["user_id"] = self._cid_user.get(cid, "")
        return line

    @staticmethod
    def _safe_name(name: str) -> str:
        safe = "".join(c for c in name if c.isalnum() or c in "-_.:@")
        return safe.replace(":", "__")

    @staticmethod
    def _canon_agent(name: str) -> str:
        """Canonical form of an agent name — lowercase + stripped.

        Agent identity is case-insensitive: 'Claude', 'claude', 'ClAuDe'
        all refer to the same agent. Apply this at every storage/lookup
        boundary so file paths, extras keys, and context caches never
        end up with two entries for the same agent.
        """
        return (name or "").strip().lower()

    @classmethod
    def _canon_extra_key(cls, key: str) -> str:
        """Lowercase the agent-name suffix on per-agent extras keys.

        Keys like 'claude_session:<agent>' encode an agent name in the
        suffix. Normalize the suffix only — leave other keys untouched.
        """
        for _prefix in (
                "claude_session:", "cc_session:", "codex_session:",
                "gemini_acp_session:", "gemini_acp_pool_idx:",
                "gemini_acp_session_version:"):
            if key.startswith(_prefix):
                return _prefix + cls._canon_agent(key[len(_prefix):])
        return key

    def _conv_dir(self, cid: str, user_id: str = "") -> Path:
        """Directory for a conversation: {store_dir}/{user}/{conv_id}/"""
        if user_id:
            self._cid_user[cid] = user_id  # cache for future lookups
            return self._store_dir / self._safe_name(user_id) / self._safe_name(cid)
        # Fast lookup from cid→user mapping (populated by _ensure_loaded + save)
        uid = self._cid_user.get(cid)
        if uid:
            return self._store_dir / self._safe_name(uid) / self._safe_name(cid)
        # Fallback: scan user dirs on disk
        if self._store_dir.is_dir():
            for user_dir in self._store_dir.iterdir():
                if user_dir.is_dir():
                    conv_dir = user_dir / self._safe_name(cid)
                    if conv_dir.is_dir():
                        self._cid_user[cid] = user_dir.name  # remember for next time
                        return conv_dir
        raise ValueError(f"Conversation {cid[:16]} not found and no user_id provided")

    def _conv_path(self, cid: str) -> Path:
        """Legacy compat: points to transcript.jsonl for exists() checks."""
        return self._conv_dir(cid) / "transcript.jsonl"

    def _transcript_path(self, cid: str) -> Path:
        return self._conv_dir(cid) / "transcript.jsonl"

    def _transcript_log(self, cid: str) -> SegmentedJsonl:
        return SegmentedJsonl(self._transcript_path(cid))

    def _shared_ctx_path(self, cid: str) -> Path:
        return self._conv_dir(cid) / "shared.jsonl"

    def _shared_ctx_log(self, cid: str) -> SegmentedJsonl:
        return SegmentedJsonl(self._shared_ctx_path(cid))

    def _agent_ctx_path(self, cid: str, agent: str) -> Path:
        safe_agent = self._safe_name(self._canon_agent(agent)) if agent else "_shared"
        return self._conv_dir(cid) / safe_agent / "context.jsonl"

    def _agent_ctx_log(self, cid: str, agent: str) -> SegmentedJsonl:
        return SegmentedJsonl(self._agent_ctx_path(cid, agent))

    @staticmethod
    def _jsonl_exists(path: Path) -> bool:
        return SegmentedJsonl(path).exists()

    def _extras_path(self, cid: str) -> Path:
        return self._conv_dir(cid) / "extras.json"

    # ── Git per conversation ──────────────────────────────────────────

    def _git(self, cid: str, *args: str, check: bool = True,
             timeout: int = 10) -> subprocess.CompletedProcess:
        """Run a git command in the conversation directory.

        Passes `-c safe.directory=*` so git doesn't reject repos that live on
        a filesystem owned by a different uid (happens when the server runs on
        Windows against a \\\\wsl$\\... path, or inside Docker against a host
        bind-mount). Conversation snapshots also disable automatic Git
        maintenance/GC: on Windows/WSL, geometric repack can fail on pack or
        multi-pack-index locks and should never block the chat turn snapshot.
        """
        conv_dir = self._conv_dir(cid)
        git_cfg = [
            "-c", "safe.directory=*",
            "-c", "gc.auto=0",
            "-c", "maintenance.auto=false",
        ]
        return subprocess.run(  # nosec B603
            ["git", *git_cfg] + list(args),
            cwd=str(conv_dir), capture_output=True, text=True,
            check=check, timeout=timeout,
        )

    @staticmethod
    def _dir_size_bytes(path: Path) -> int:
        total = 0
        if not path.is_dir():
            return 0
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    pass
        return total

    def prune_git_history_now(self, cid: str,
                              progress: Optional[Callable[[str, Dict[str, Any]], None]] = None) -> dict:
        """Run conversation Git retention immediately and return size stats."""
        return self._maybe_prune_git_history(
            cid, force=True, progress=progress, raise_errors=True)

    def _maybe_prune_git_history(self, cid: str, force: bool = False,
                                 progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
                                 raise_errors: bool = False) -> dict:
        """Bound per-conversation Git history and reclaim unreachable objects."""
        def _progress(stage: str, **payload) -> None:
            if progress:
                try:
                    progress(stage, payload)
                except Exception:
                    logger.debug("git retention progress callback failed", exc_info=True)

        if _GIT_RETENTION_DAYS <= 0 and _GIT_RETENTION_COMMITS <= 0:
            return {"status": "disabled"}
        conv_dir = self._conv_dir(cid)
        git_dir = conv_dir / ".git"
        if not git_dir.exists():
            return {"status": "missing"}
        marker = git_dir / "pawflow-retention-last-run"
        now = time.time()
        size_before = self._dir_size_bytes(git_dir)
        try:
            if (not force and marker.exists()
                    and _GIT_RETENTION_INTERVAL_SEC > 0):
                age = now - marker.stat().st_mtime
                if age < _GIT_RETENTION_INTERVAL_SEC:
                    return {"status": "skipped", "reason": "interval",
                            "size_before": size_before,
                            "size_after": size_before}
        except OSError:
            pass
        try:
            _progress("scan", size_before=size_before)
            out = self._git(
                cid, "log", "--first-parent", "--reverse",
                "--format=%H%x00%ct", "live", timeout=30).stdout
            commits = []
            for raw in out.splitlines():
                if "\x00" not in raw:
                    continue
                h, ts = raw.split("\x00", 1)
                try:
                    commits.append((h, int(ts)))
                except ValueError:
                    continue
            if len(commits) <= 1:
                marker.touch(exist_ok=True)
                return {"status": "unchanged", "reason": "too_few_commits",
                        "commits_before": len(commits),
                        "commits_after": len(commits),
                        "size_before": size_before,
                        "size_after": self._dir_size_bytes(git_dir)}
            keep_start = len(commits) - 1
            if _GIT_RETENTION_DAYS > 0:
                cutoff = int(now - _GIT_RETENTION_DAYS * 86400)
                for idx, (_h, ts) in enumerate(commits):
                    if ts >= cutoff:
                        keep_start = min(keep_start, idx)
                        break
            if _GIT_RETENTION_COMMITS > 0:
                keep_start = min(keep_start, max(0, len(commits) - _GIT_RETENTION_COMMITS))
            if keep_start <= 0:
                marker.touch(exist_ok=True)
                size_after = self._dir_size_bytes(git_dir)
                return {"status": "unchanged", "reason": "within_retention",
                        "commits_before": len(commits),
                        "commits_after": len(commits),
                        "size_before": size_before, "size_after": size_after}

            kept = commits[keep_start:]
            _progress("rewrite", commits_before=len(commits), commits_after=len(kept))
            first = kept[0][0]
            tree = self._git(cid, "rev-parse", f"{first}^{{tree}}", timeout=30).stdout.strip()
            new_head = self._git(
                cid, "commit-tree", tree,
                "-m", f"PawFlow retention base for {first[:12]}",
                timeout=30).stdout.strip()
            for commit, _ts in kept[1:]:
                tree = self._git(cid, "rev-parse", f"{commit}^{{tree}}", timeout=30).stdout.strip()
                msg = self._git(cid, "log", "-1", "--format=%B", commit, timeout=30).stdout
                new_head = self._git(
                    cid, "commit-tree", tree, "-p", new_head,
                    "-m", msg.strip() or "snapshot",
                    timeout=30).stdout.strip()
            self._git(cid, "update-ref", "refs/heads/live", new_head, timeout=30)
            self._git(cid, "symbolic-ref", "HEAD", "refs/heads/live", timeout=30)
            _progress("gc", commits_before=len(commits), commits_after=len(kept))
            self._git(cid, "reflog", "expire", "--expire=now", "--expire-unreachable=now", "--all", timeout=60)
            self._git(cid, "gc", "--prune=now", timeout=1800)
            marker.touch(exist_ok=True)
            size_after = self._dir_size_bytes(git_dir)
            logger.info("[convstore] pruned git history for %s: kept %d/%d commits",
                        cid[:8], len(kept), len(commits))
            return {"status": "pruned", "commits_before": len(commits),
                    "commits_after": len(kept), "size_before": size_before,
                    "size_after": size_after}
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
            if raise_errors:
                raise
            detail = getattr(e, "stderr", None) or getattr(e, "stdout", None) or ""
            logger.warning("[convstore] git retention failed for %s: %s | git stderr: %s",
                           cid[:8], e, (detail.strip() if isinstance(detail, str) else detail))
            return {"status": "error", "error": str(e),
                    "size_before": size_before,
                    "size_after": self._dir_size_bytes(git_dir)}

    def _git_init(self, cid: str):
        """Initialize a git repo in the conversation directory (idempotent)."""
        conv_dir = self._conv_dir(cid)
        git_dir = conv_dir / ".git"
        if git_dir.exists() and (git_dir / "HEAD").exists():
            return
        # Remove incomplete .git dir if present
        if git_dir.exists():
            import shutil
            shutil.rmtree(git_dir, ignore_errors=True)
        try:
            self._git(cid, "init", "-q", "-b", "live")
            # Configure for this repo only (no user-level config needed)
            self._git(cid, "config", "user.email", "pawflow@local")
            self._git(cid, "config", "user.name", "PawFlow")
            # Initial commit with durable conversation state only. Agent
            # contexts and bg buckets are derived caches and are intentionally
            # left outside Git.
            self._git_untrack_derived_state(cid)
            existing = self._git_snapshot_files(cid)
            if existing:
                self._git(cid, "add", "--", *existing, check=False)
            self._git(cid, "commit", "-m", "init", "--allow-empty", "-q")
            logger.debug("[convstore] git init for %s", cid[:8])
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
            detail = getattr(e, "stderr", None) or getattr(e, "stdout", None) or ""
            logger.warning("[convstore] git init failed for %s: %s | git stderr: %s",
                           cid[:8], e, (detail.strip() if isinstance(detail, str) else detail))

    def _git_snapshot_files(self, cid: str) -> List[str]:
        """Files that form durable Git history for a conversation."""
        conv_dir = self._conv_dir(cid)
        files = [
            "transcript.jsonl", "transcript",
            "shared.jsonl", "shared",
            "extras.json", "bindings.json",
        ]
        return [f for f in files if (conv_dir / f).exists()]

    def _derived_state_paths(self, cid: str) -> List[str]:
        """Return replaceable per-agent context and bucket paths."""
        conv_dir = self._conv_dir(cid)
        paths: set[str] = set()
        summaries = conv_dir / "summaries"
        if summaries.exists():
            paths.add("summaries")
        for entry in conv_dir.iterdir():
            if not entry.is_dir():
                continue
            if entry.name in (".git", "transcript", "shared", "summaries"):
                continue
            if (entry / "context.jsonl").exists() or (entry / "context").exists():
                paths.add(entry.name)
        try:
            tracked = self._git(cid, "ls-files", "-z", check=False, timeout=30).stdout
            for rel in tracked.split("\0"):
                if not rel:
                    continue
                if rel == "summaries" or rel.startswith("summaries/"):
                    paths.add("summaries")
                    continue
                top = rel.split("/", 1)[0]
                if top in (".git", "transcript", "shared", "summaries"):
                    continue
                if rel.endswith("/context.jsonl") or "/context/" in rel:
                    paths.add(top)
        except Exception:
            logger.debug("git tracked derived-state scan failed for %s",
                         cid[:8], exc_info=True)
        return sorted(paths)

    def _git_untrack_derived_state(self, cid: str) -> None:
        """Stage removal of derived state from Git without deleting files."""
        paths = self._derived_state_paths(cid)
        if paths:
            self._git(cid, "rm", "-r", "--cached", "--ignore-unmatch",
                      "--", *paths, check=False, timeout=60)

    def _purge_derived_state_after_history_change(self, cid: str) -> None:
        """Drop contexts/buckets after rollback or branch switch.

        Git restores transcript/shared/extras. Agent contexts and bucket
        summaries are rebuilt from that durable state, so keeping old copies
        would make agents resume from the wrong branch/rollback point.
        """
        conv_dir = self._conv_dir(cid)
        paths = self._derived_state_paths(cid)
        if paths:
            self._git(cid, "rm", "-r", "--cached", "--ignore-unmatch",
                      "--", *paths, check=False, timeout=60)
        for rel in paths:
            path = conv_dir / rel
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass
        self._invalidate_ctx_cache(cid)
        self._invalidate_pyramid_cache(cid)

    def git_snapshot(self, cid: str, message: str = ""):
        """Commit current state as a snapshot (called after agent turn end).

        Uses selective git add (known files only) instead of git add -A
        to avoid scanning the entire working tree on large repos.

        Snapshot runs outside the per-conversation lock. It is best-effort
        history; holding the hot write lock across git add/diff/commit blocks
        live tool_call/tool_result publication for seconds on Windows/WSL.
        """
        conv_dir = self._conv_dir(cid)
        if not (conv_dir / ".git").exists():
            return
        try:
            # Selective add: durable transcript/shared/extras only. Agent
            # contexts and summaries are derived; untrack any legacy entries
            # so the next snapshot frees them from future history.
            self._git_untrack_derived_state(cid)
            existing = self._git_snapshot_files(cid)
            if not existing:
                return
            self._git(cid, "add", "--", *existing, check=False)
            # Commit only if something staged
            diff = self._git(cid, "diff", "--cached", "--quiet", check=False)
            if diff.returncode == 0:
                return  # nothing staged
            msg = message or f"snapshot {time.strftime('%H:%M:%S')}"
            self._git(cid, "commit", "-m", msg, "-q")
            logger.debug("[convstore] git snapshot for %s: %s", cid[:8], msg)
            self._maybe_prune_git_history(cid)
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
            detail = getattr(e, "stderr", None) or getattr(e, "stdout", None) or ""
            logger.warning("[convstore] git snapshot failed for %s: %s | git stderr: %s",
                           cid[:8], e, (detail.strip() if isinstance(detail, str) else detail))

    def git_log(self, cid: str, limit: int = 20) -> List[Dict]:
        """List recent git commits for a conversation."""
        conv_dir = self._conv_dir(cid)
        if not (conv_dir / ".git").exists():
            return []
        try:
            result = self._git(cid, "log", f"--max-count={limit}",
                               "--format=%H\t%at\t%s")
            entries = []
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split("\t", 2)
                if len(parts) >= 3:
                    entries.append({
                        "hash": parts[0],
                        "timestamp": int(parts[1]),
                        "message": parts[2],
                    })
            return entries
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            return []

    def git_rollback(self, cid: str, commit_hash: str) -> bool:
        """Rollback conversation to a previous commit."""
        conv_dir = self._conv_dir(cid)
        if not (conv_dir / ".git").exists():
            return False
        try:
            self._git(cid, "checkout", commit_hash, "--", ".")
            self._purge_derived_state_after_history_change(cid)
            # Reload cache from rolled-back state
            with self._cache_lock:
                self._cache.pop(cid, None)
            self._invalidate_ctx_cache(cid)
            self._reload_cache(cid)
            # Commit the rollback as a new snapshot
            self.git_snapshot(cid, f"rollback to {commit_hash[:8]}")
            logger.info("[convstore] rolled back %s to %s", cid[:8], commit_hash[:8])
            return True
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning("[convstore] rollback failed for %s: %s", cid[:8], e)
            return False

    @staticmethod
    def _invalidate_pyramid_cache(cid: str) -> None:
        """Drop the bg bucket builder's in-memory seq caches for a cid.
        Called whenever the on-disk pyramid state shifts non-
        monotonically (rollback, branch switch, shared edits) so the
        caches don't report stale seqs on the next maybe_trigger."""
        try:
            from core.bg_bucket_builder import BgBucketBuilder
            _bb = BgBucketBuilder.instance()
            with _bb._seq_cache_lock:
                _bb._shared_seq_cache.pop(cid, None)
                _bb._pyramid_seq_cache.pop(cid, None)
        except Exception:
            logger.debug("pyramid cache invalidation failed for %s",
                          cid[:8], exc_info=True)

    def git_diff(self, cid: str, commit_a: str = "HEAD~1", commit_b: str = "HEAD") -> str:
        """Get diff between two commits."""
        conv_dir = self._conv_dir(cid)
        if not (conv_dir / ".git").exists():
            return ""
        try:
            result = self._git(cid, "diff", commit_a, commit_b, check=False)
            return result.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return ""

    def _require_idle(self, cid: str) -> None:
        """Raise if conversation has active agents."""
        c = self._load_cache(cid)
        if c.get("status") not in ("idle", ""):
            raise RuntimeError(
                f"Conversation is {c.get('status')} — wait for agents to finish")

    def git_current_branch(self, cid: str) -> str:
        conv_dir = self._conv_dir(cid)
        git_dir = conv_dir / ".git"
        if not git_dir.exists() or not (git_dir / "HEAD").exists():
            return ""
        try:
            result = self._git(cid, "branch", "--show-current")
            return result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError,
                subprocess.TimeoutExpired):
            return "main"

    def git_list_branches(self, cid: str) -> List[Dict]:
        conv_dir = self._conv_dir(cid)
        if not (conv_dir / ".git").exists():
            return []
        try:
            result = self._git(cid, "branch", "--format=%(refname:short)\t%(objectname:short)\t%(committerdate:unix)")
            current = self.git_current_branch(cid)
            branches = []
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split("\t")
                name = parts[0]
                branches.append({
                    "name": name,
                    "commit": parts[1] if len(parts) > 1 else "",
                    "timestamp": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0,
                    "current": name == current,
                })
            return branches
        except (subprocess.CalledProcessError, FileNotFoundError,
                subprocess.TimeoutExpired):
            return []

    def git_branch(self, cid: str, branch_name: str) -> bool:
        """Create a new branch and switch to it."""
        self._require_idle(cid)
        conv_dir = self._conv_dir(cid)
        if not (conv_dir / ".git").exists():
            return False
        try:
            self.git_snapshot(cid, f"before branch {branch_name}")
            self._git(cid, "checkout", "-b", branch_name)
            logger.info("[convstore] branched %s → %s", cid[:8], branch_name)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError,
                subprocess.TimeoutExpired) as e:
            logger.warning("[convstore] branch failed for %s: %s", cid[:8], e)
            return False

    def git_switch(self, cid: str, branch_name: str) -> bool:
        """Switch to an existing branch. Reloads caches."""
        self._require_idle(cid)
        conv_dir = self._conv_dir(cid)
        if not (conv_dir / ".git").exists():
            return False
        try:
            self.git_snapshot(cid, f"before switch to {branch_name}")
            self._git(cid, "checkout", branch_name)
            self._purge_derived_state_after_history_change(cid)
            with self._cache_lock:
                self._cache.pop(cid, None)
            self._invalidate_ctx_cache(cid)
            self._reload_cache(cid)
            self.invalidate_claude_sessions(cid)
            logger.info("[convstore] switched %s → %s", cid[:8], branch_name)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError,
                subprocess.TimeoutExpired) as e:
            logger.warning("[convstore] switch failed for %s: %s", cid[:8], e)
            return False

    def git_delete_branch(self, cid: str, branch_name: str) -> bool:
        """Delete a branch (cannot delete current branch)."""
        current = self.git_current_branch(cid)
        if branch_name == current:
            raise ValueError("Cannot delete the current branch")
        conv_dir = self._conv_dir(cid)
        if not (conv_dir / ".git").exists():
            return False
        try:
            self._git(cid, "branch", "-D", branch_name)
            logger.info("[convstore] deleted branch %s on %s", branch_name, cid[:8])
            return True
        except (subprocess.CalledProcessError, FileNotFoundError,
                subprocess.TimeoutExpired) as e:
            logger.warning("[convstore] delete branch failed: %s", e)
            return False

    def git_tag(self, cid: str, tag_name: str, message: str = "") -> bool:
        conv_dir = self._conv_dir(cid)
        if not (conv_dir / ".git").exists():
            return False
        try:
            self.git_snapshot(cid, f"tag {tag_name}")
            if message:
                self._git(cid, "tag", "-a", tag_name, "-m", message)
            else:
                self._git(cid, "tag", tag_name)
            logger.info("[convstore] tagged %s: %s", cid[:8], tag_name)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError,
                subprocess.TimeoutExpired) as e:
            logger.warning("[convstore] tag failed: %s", e)
            return False

    def git_list_tags(self, cid: str) -> List[Dict]:
        conv_dir = self._conv_dir(cid)
        if not (conv_dir / ".git").exists():
            return []
        try:
            result = self._git(cid, "tag", "-l", "--format=%(refname:short)\t%(objectname:short)\t%(creatordate:unix)",
                               check=False)
            tags = []
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split("\t")
                tags.append({
                    "name": parts[0],
                    "commit": parts[1] if len(parts) > 1 else "",
                    "timestamp": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0,
                })
            return tags
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []

    def git_delete_tag(self, cid: str, tag_name: str) -> bool:
        conv_dir = self._conv_dir(cid)
        if not (conv_dir / ".git").exists():
            return False
        try:
            self._git(cid, "tag", "-d", tag_name)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError,
                subprocess.TimeoutExpired):
            return False

    def git_compare_branches(self, cid: str, branch_a: str, branch_b: str) -> Dict:
        """Compare two branches: commit counts and message counts."""
        conv_dir = self._conv_dir(cid)
        if not (conv_dir / ".git").exists():
            return {}
        try:
            # Commits ahead/behind
            result = self._git(cid, "rev-list", "--left-right", "--count",
                               f"{branch_a}...{branch_b}", check=False)
            parts = result.stdout.strip().split("\t")
            ahead = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 0
            behind = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            # Message count per branch via git show
            def _msg_count(branch: str) -> int:
                r = self._git(cid, "show", f"{branch}:transcript.jsonl", check=False)
                if r.returncode != 0:
                    return 0
                return sum(1 for l in r.stdout.strip().split("\n")
                           if l.strip() and '"t":"msg"' in l)
            return {
                "branch_a": branch_a, "branch_b": branch_b,
                "commits_ahead": ahead, "commits_behind": behind,
                "messages_a": _msg_count(branch_a),
                "messages_b": _msg_count(branch_b),
            }
        except (subprocess.CalledProcessError, FileNotFoundError,
                subprocess.TimeoutExpired):
            return {}

    def fork(self, cid: str, user_id: str) -> str:
        """Fork a conversation into a new independent copy (git clone)."""
        self._require_idle(cid)
        source_dir = self._conv_dir(cid)
        if not source_dir.is_dir():
            raise ValueError(f"Conversation {cid[:16]} not found")
        self.git_snapshot(cid, "before fork")
        new_cid = self.generate_id()
        dest_dir = self._store_dir / self._safe_name(user_id) / self._safe_name(new_cid)
        try:
            subprocess.run(  # nosec B603, B607
                ["git", "clone", str(source_dir), str(dest_dir)],
                capture_output=True, text=True, check=True, timeout=30,
            )
            # Remove the remote origin (it points to the source conv)
            subprocess.run(  # nosec B603, B607
                ["git", "-C", str(dest_dir), "remote", "remove", "origin"],
                capture_output=True, text=True, check=False, timeout=10,
            )
        except (subprocess.CalledProcessError, FileNotFoundError,
                subprocess.TimeoutExpired) as e:
            logger.warning("[convstore] fork clone failed: %s", e)
            raise RuntimeError(f"Fork failed: {e}")
        # Store fork metadata
        self._cid_user[new_cid] = user_id
        extras = self._read_extras(new_cid)
        extras["forked_from"] = cid
        extras["_meta_user_id"] = user_id
        extras["_meta_created_at"] = time.time()
        self._write_extras(new_cid, extras)
        # Set title
        source_title = self.get_extra(cid, "title") or "Conversation"
        self.set_extra(new_cid, "title", f"{source_title} (fork)")
        self._reload_cache(new_cid)
        self.git_snapshot(new_cid, "forked")
        logger.info("[convstore] forked %s → %s", cid[:8], new_cid[:8])
        return new_cid

    # ── Cross-file UUID invariant ────────────────────────────────────
    #
    # msg_id IS a UUID — universally unique by construction. A given
    # logical message is created ONCE (LLMMessage.__post_init__ mints
    # its uuid) and the same object flows through every write path via
    # dict(msg) transforms that never touch msg_id. So the invariant
    # is preserved by construction: no runtime heuristic needed.
    #
    # If the same logical content appears with two different msg_ids,
    # that's a caller bug (someone rebuilt the LLMMessage instead of
    # reusing it). Fix the caller. Do NOT try to "realign" here by
    # guessing which row is the canonical one from ts/content — the
    # msg_id IS the identity.

    # ── Context file helpers ──────────────────────────────────────────

    @staticmethod
    def _row_payload_chars(row: Dict) -> int:
        """Char weight of a transcript row's payload — content + tool I/O.

        Used to feed bg_bucket_builder's transcript-token cache. Rough
        estimate (raw chars, no tokenizer); /3.5 gives the bg-side
        token-budget metric. Counts:
          - row['content'] (str or list of {type,text} blocks)
          - row['tool_calls'] (list of dicts → arguments json size)
          - row['trace'] (list of dicts → string repr)
          - row['content_update'] (str)

        Anything else (metadata, ids, timestamps) is constant overhead
        and ignored — we want growth to track real LLM-visible payload.
        """
        total = 0
        c = row.get("content")
        if isinstance(c, str):
            total += len(c)
        elif isinstance(c, list):
            for p in c:
                if isinstance(p, dict):
                    t = p.get("text") or ""
                    if isinstance(t, str):
                        total += len(t)
        for tc in (row.get("tool_calls") or []):
            if isinstance(tc, dict):
                args = tc.get("arguments") or tc.get("function", {}).get("arguments") or ""
                if isinstance(args, str):
                    total += len(args)
                elif isinstance(args, dict):
                    try:
                        total += len(json.dumps(args, ensure_ascii=False))
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        cu = row.get("content_update")
        if isinstance(cu, str):
            total += len(cu)
        return total

    @staticmethod
    def _notify_bg_transcript_chars(cid: str, n_chars: int):
        """Best-effort hook to feed bg_bucket_builder. Failures swallowed:
        the trigger logic falls back to seq-gap if the cache stays cold.
        """
        if n_chars <= 0:
            return
        try:
            from core.bg_bucket_builder import BgBucketBuilder
            BgBucketBuilder.instance().note_transcript_bytes_appended(
                cid, n_chars)
        except Exception:
            logger.debug("bg transcript-chars hint failed", exc_info=True)

    def _append_ctx_file(self, cid: str, agent: str, messages: List[Dict]):
        """Append messages to an agent's context file.

        No dedup: msg_id is minted at message creation (uuid4) and the
        unified append_message router is the sole write path, so a
        duplicate msg_id on disk is a caller bug -- fix it at the root
        rather than silently dropping the second write here.
        """
        rows = []
        for m in messages:
            self._validate_message(m)
            rows.append(self._stamp_line(cid, dict(m)))
        self._agent_ctx_log(cid, agent).append_dicts(rows)

    def _seed_agent_context_from_shared_if_missing(self, cid: str, agent: str) -> int:
        """Initialize a new agent context from shared before its first row.

        The first user message routed to an agent must not create a private
        context containing only that message. If no private context exists yet,
        copy the current shared context personalized for this agent, then let
        the caller append the new row.
        """
        agent = self._canon_agent(agent) if agent else ""
        if not agent:
            return 0
        log = self._agent_ctx_log(cid, agent)
        if log.exists():
            return 0
        seed = self.load_shared_for_agent(cid, agent) or []
        if not seed:
            return 0
        self._write_ctx_file(self._agent_ctx_path(cid, agent), seed)
        logger.info(
            "[context:%s] seeded %s context from shared before first append: %d messages",
            cid[:8], agent, len(seed))
        return len(seed)

    @staticmethod
    def _prefix_content(content, prefix: str):
        """Prefix content with a tag. Handles both string and multipart (list)."""
        if isinstance(content, str):
            if content.startswith(prefix + "\n") or content.startswith(prefix + " "):
                return content
            return f"{prefix}\n{content}"
        if isinstance(content, list):
            if content:
                first = content[0]
                if isinstance(first, dict) and first.get("type") == "text" and first.get("text") == prefix:
                    return list(content)
            return [{"type": "text", "text": prefix}] + list(content)
        text = str(content)
        if text.startswith(prefix + "\n") or text.startswith(prefix + " "):
            return text
        return f"{prefix}\n{text}"

    @staticmethod
    def _strip_prefix(content, prefix: str):
        """Strip a prefix tag from content. Handles both string and multipart (list)."""
        if isinstance(content, str):
            full = prefix + "\n"
            return content[len(full):] if content.startswith(full) else content
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and first.get("type") == "text" and first.get("text") == prefix:
                return content[1:]
        return content

    @staticmethod
    def _agent_prefix(agent_name: str, source: Dict) -> str:
        """Build the [Agent X]: or [Agent X in Task Y]: or [Agent X in /btw]: prefix."""
        task_id = source.get("task_id", "")
        if task_id:
            return f"[Agent {agent_name} in Task {task_id}]:"
        if source.get("btw"):
            return f"[Agent {agent_name} in /btw]:"
        return f"[Agent {agent_name}]:"

    @staticmethod
    def _user_prefix(target: str, source: Dict) -> str:
        """Build the [User to agent X]: or [User to agent X in /btw]: prefix."""
        if source.get("btw"):
            return f"[User to agent {target} in /btw]:"
        return f"[User to agent {target}]:"

    @staticmethod
    def _transform_for_shared(msg: Dict) -> Dict:
        """Transform a message for the shared (agent-neutral) context.

        ALL messages are prefixed — shared belongs to no agent.
        - Agent messages: role→user, content prefixed [Agent X]: or [Agent X in Task Y]:
        - User messages: content prefixed [User to agent X]:
        - Agent_delegate messages: SHOULD NEVER REACH HERE (filtered upstream
          in append_message). If we're called on one, return as-is rather
          than mislabel it.
        """
        m = dict(msg)
        src = m.get("source") or {}
        src_type = src.get("type", "")

        if src_type == "agent_delegate":
            return m  # private channel — caller must not broadcast

        if src_type == "agent":
            agent_name = src.get("name")
            if not agent_name:
                raise ValueError(f"Agent message without source.name — msg_id={m.get('msg_id', '?')}")
            m["role"] = "user"
            m["content"] = ConversationStore._prefix_content(
                m.get("content", ""), ConversationStore._agent_prefix(agent_name, src))

        elif src_type == "user":
            target = src.get("target_agent", "")
            if target:
                m["content"] = ConversationStore._prefix_content(
                    m.get("content", ""), ConversationStore._user_prefix(target, src))

        return m

    @staticmethod
    def _transform_for_other_agent(msg: Dict, receiving_agent: str) -> Dict:
        """Transform a message for injection into a specific agent's context.

        - Own agent messages WITHOUT task: unchanged (role=assistant)
        - Own agent messages FROM task: prefixed [Agent X in Task Y]: (task is a sub-context)
        - Other agent messages: role→user, content prefixed [Agent X]:
        - User messages to receiving_agent: unchanged
        - User messages to other agent: content prefixed [User to agent X]:
        - Agent_delegate messages: SHOULD NEVER REACH HERE — private A↔B
          channel, filtered upstream. Returned as-is as a safety net.
        """
        m = dict(msg)
        src = m.get("source") or {}
        src_type = src.get("type", "")

        if src_type == "agent_delegate":
            return m  # private channel — should not be broadcast

        if src_type == "agent":
            agent_name = src.get("name")
            if not agent_name:
                raise ValueError(f"Agent message without source.name — msg_id={m.get('msg_id', '?')}")
            is_own = (agent_name == receiving_agent)
            is_sub_context = bool(src.get("task_id")) or bool(src.get("btw"))
            # Own messages from sub-contexts (task, btw) are prefixed
            # Own messages from normal conv are NOT prefixed (role=assistant)
            if not is_own or is_sub_context:
                m["role"] = "user"
                m["content"] = ConversationStore._prefix_content(
                    m.get("content", ""), ConversationStore._agent_prefix(agent_name, src))

        elif src_type == "user":
            target = src.get("target_agent", "")
            is_btw_msg = bool(src.get("btw"))
            # btw user messages are ALWAYS prefixed (sub-context, even for target agent)
            # Normal user messages only prefixed for non-target agents
            if target and (target != receiving_agent or is_btw_msg):
                m["content"] = ConversationStore._prefix_content(
                    m.get("content", ""), ConversationStore._user_prefix(target, src))

        return m

    @staticmethod
    def _personalize_from_shared(msg: Dict, agent_name: str) -> Dict:
        """Personalize a shared-context message for a specific agent.

        Reverses _transform_for_shared for this agent's own NON-TASK messages:
        - [Agent {me}]: (no task) → strip prefix, role=assistant
        - [Agent {me} in Task Y]: → keep prefix (task = sub-context, not own response)
        - [User to agent {me}]: → strip prefix
        - Everything else: keep as-is (already prefixed for "others")
        """
        m = dict(msg)
        src = m.get("source") or {}
        src_type = src.get("type", "")

        if src_type == "agent" and src.get("name") == agent_name:
            # Only un-prefix own messages that are NOT from a sub-context
            if not src.get("task_id") and not src.get("btw"):
                m["content"] = ConversationStore._strip_prefix(
                    m.get("content", ""), f"[Agent {agent_name}]:")
                m["role"] = "assistant"
            # Sub-context messages (task, btw) stay prefixed

        elif src_type == "user" and src.get("target_agent") == agent_name:
            m["content"] = ConversationStore._strip_prefix(
                m.get("content", ""), f"[User to agent {agent_name}]:")

        return m

    @staticmethod
    def filter_for_shared(messages: List[Dict]) -> List[Dict]:
        """Pick messages eligible for shared.jsonl.

        Shared context = conversation only: no tool results, no context
        injections. Assistant messages with tool_calls keep their text
        (if any) but drop the tool_calls/tool_call_id fields. This is
        the single source of truth for what goes into shared — used
        both by append_message and by the CC import path so imported convs
        render the same "Shared" view as native ones.
        """
        out = []
        for m in messages:
            if m.get("role") == "tool":
                continue
            if (m.get("source") or {}).get("type") == "context":
                continue
            if m.get("tool_calls"):
                m_copy = dict(m)
                m_copy.pop("tool_calls", None)
                m_copy.pop("tool_call_id", None)
                content = m_copy.get("content", "")
                if content and str(content).strip():
                    out.append(m_copy)
            else:
                out.append(m)
        return out

    def _append_shared_ctx(self, cid: str, messages: List[Dict],
                           timings: Optional[Dict[str, float]] = None):
        """Append already-shared-normalized messages to shared context.

        No dedup: see _append_ctx_file for rationale.

        After the write, updates core.bg_bucket_builder's in-memory counters
        and queues the trigger decision outside the foreground writer path.
        """
        def _add_timing(name: str, started: float) -> None:
            if timings is not None:
                timings[name] = timings.get(name, 0.0) + (
                    (time.monotonic() - started) * 1000.0)

        _max_seq = 0
        _shared_chars = 0
        rows = []
        _t0 = time.monotonic()
        for m in messages:
            self._validate_message(m)
            xf = self._stamp_line(cid, m)
            rows.append(xf)
            _shared_chars += self._row_payload_chars(xf)
            _s = int(xf.get("seq") or 0)
            if _s > _max_seq:
                _max_seq = _s
        self._shared_ctx_log(cid).append_dicts(rows)
        _add_timing("shared_write", _t0)

        # Keep cache hints synchronous so the queued trigger decision sees
        # this row, but run the decision itself in the bg executor. Resolving
        # expression config and logging state transitions are too expensive
        # for the writer hot path.
        try:
            _t0 = time.monotonic()
            from core.bg_bucket_builder import BgBucketBuilder
            _bb = BgBucketBuilder.instance()
            if _max_seq:
                _bb.note_shared_seq(cid, _max_seq)
            if messages:
                _bb.note_shared_rows_appended(cid, len(messages))
            if _shared_chars:
                _bb.note_shared_chars_appended(cid, _shared_chars)
            _uid = self._cid_user.get(cid, "") or ""
            if _uid:
                trigger = getattr(_bb, "maybe_trigger_async",
                                  _bb.maybe_trigger)
                trigger(cid, _uid)
            _add_timing("shared_bg_trigger", _t0)
        except Exception:
            logger.debug("bg bucket trigger failed", exc_info=True)

    def _read_ctx_file(self, path: Path) -> List[Dict]:
        """Read all messages from a context JSONL file, sorted by (ts, seq).

        File order is producer-FIFO but multi-producer races (different
        agents writing to the same conv, late tool_results arriving after
        newer turns) can put messages on disk in non-creation order.
        We sort by (ts, seq) here so the order reflects when each
        message was MINTED, not when the writer happened to flush it —
        matching what the user saw in the live SSE stream.
        """
        log = SegmentedJsonl(path)
        if not log.exists():
            return []
        result = list(log.iter_rows())
        result.sort(key=lambda m: (
            m.get("ts") or m.get("timestamp") or 0.0,
            m.get("seq") or 0,
        ))
        return result

    def _write_ctx_file(self, path: Path, messages: List[Dict]):
        """Overwrite a context file with messages (atomic: tmp + rename)."""
        for m in messages:
            self._validate_message(m)
        SegmentedJsonl(path).replace_dicts(messages)

    def _read_extras(self, cid: str) -> dict:
        """Read extras from the atomic JSON file."""
        path = self._extras_path(cid)
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
        return {}

    def _write_extras(self, cid: str, data: dict):
        """Atomically write extras JSON (tmp + rename).

        Callers mutating shared extras MUST hold `_get_extras_lock(cid)`.
        Readers intentionally do not take the hot conversation lock:
        this file is replaced atomically from a complete tmp file, so a
        reader sees either the old or the new JSON document. The retry loop
        covers Windows cases where anti-virus / Windows Defender / OneDrive
        or a concurrent reader briefly holds a handle on the destination and
        `os.replace` raises WinError 5. A handful of short retries lets the
        handle close and the rename succeed.
        """
        import time as _t
        path = self._extras_path(cid)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(
            f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        _last_err: Optional[Exception] = None
        try:
            for _attempt in range(8):
                try:
                    tmp.replace(path)
                    return
                except PermissionError as _pe:
                    _last_err = _pe
                    _t.sleep(0.025 * (1 + _attempt))  # 25, 50, 75, ... up to 200ms
            raise _last_err if _last_err else RuntimeError(
                "_write_extras: replace failed without an exception")
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    # ══════════════════════════════════════════════════════════════════
    #  SINGLE READ POINT
    # ══════════════════════════════════════════════════════════════════

    def _read(self, cid: str, read_fn: Callable):
        """THE ONLY transcript read method.

        Do not hold the conversation write lock while scanning the full
        transcript. The file is append-only; a concurrent partial final row is
        ignored by the JSON decoder and will be visible on the next read.
        """
        log = self._transcript_log(cid)
        if not log.exists():
            return read_fn(iter([]))
        try:
            return read_fn(log.iter_rows())
        except OSError as e:
            logger.error(f"[convstore] read failed {cid}: {e}")
            return read_fn(iter([]))

    @staticmethod
    def _iter_jsonl_reverse(path: Path, chunk_size: int = 1024 * 1024):
        """Yield JSONL rows from the end of a file without loading it all."""
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                pos = f.tell()
                buf = b""
                while pos > 0:
                    n = min(chunk_size, pos)
                    pos -= n
                    f.seek(pos)
                    buf = f.read(n) + buf
                    lines = buf.split(b"\n")
                    buf = lines[0]
                    for raw in reversed(lines[1:]):
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            yield json.loads(raw.decode("utf-8", errors="replace"))
                        except json.JSONDecodeError:
                            continue
                raw = buf.strip()
                if raw:
                    try:
                        yield json.loads(raw.decode("utf-8", errors="replace"))
                    except json.JSONDecodeError:
                        return
        except FileNotFoundError:
            return
        except OSError as e:
            logger.error("[convstore] reverse read failed %s: %s", path, e)
            return

    def load_transcript_seq_range(self, cid: str, first_seq: int,
                                  last_seq: int) -> List[Dict]:
        """Load transcript rows in seq range without a full transcript scan.

        Seq is monotonic in transcript file order, so reverse scanning can stop
        as soon as it sees a row before ``first_seq``. This is used by bg bucket
        trace extraction, where ranges are normally near the tail.
        """
        if not self.exists(cid):
            return []
        first_seq = int(first_seq or 0)
        last_seq = int(last_seq or 0)
        if first_seq <= 0 or last_seq < first_seq:
            return []
        rows: List[Dict] = []
        for row in self._transcript_log(cid).iter_rows_reverse():
            seq = int(row.get("seq") or 0)
            if seq > last_seq:
                continue
            if seq < first_seq:
                break
            rows.append(row)
        rows.reverse()
        return rows



    # ══════════════════════════════════════════════════════════════════
    #  CACHE
    # ══════════════════════════════════════════════════════════════════

    @staticmethod
    def _scan_cache(lines):
        c = {"user_id": "", "status": "idle", "created_at": 0,
             "updated_at": 0, "expires_at": 0, "msg_count": 0,
             "agents": set(), "extra_keys": set(), "extras": {}, "preview": "",
             "_max_seq": 0}
        for line in lines:
            seq = line.get("seq")
            if isinstance(seq, int) and seq > c["_max_seq"]:
                c["_max_seq"] = seq
            t = line.get("t", "")
            if t == "meta":
                c["user_id"] = line.get("user_id", "")
                c["status"] = line.get("status", "idle")
                c["created_at"] = line.get("created_at", 0)
                c["expires_at"] = line.get("expires_at", 0)
            elif t == "msg":
                c["msg_count"] += 1
                if not c["preview"] and line.get("role") == "user":
                    content = line.get("content", "")
                    if isinstance(content, str) and content.strip():
                        c["preview"] = content[:80]
            elif t == "extra":
                _ekey = line.get("key", "")
                c["extra_keys"].add(_ekey)
                c["extras"][_ekey] = line.get("value")
                if _ekey == "title":
                    c["title"] = line.get("value", "")
            elif t == "status":
                c["status"] = line.get("status", c["status"])
            c["updated_at"] = max(c["updated_at"], line.get("ts", 0))
        return c

    def _load_cache(self, cid: str) -> dict:
        with self._cache_lock:
            if cid in self._cache:
                return self._cache[cid]
        return self._reload_cache(cid)

    def _reload_cache(self, cid: str) -> dict:
        """Read file from disk and atomically swap cache entry.

        Extras are loaded from the separate extras.json file (not from JSONL).
        No gap where the entry is absent — list_conversations always
        sees either the old or new value, never missing.
        """
        c = self._read(cid, self._scan_cache)
        try:
            from core.llm_client import _seed_persisted_seq
            _seed_persisted_seq(cid, int(c.get("_max_seq") or 0))
        except Exception:
            logger.debug("persisted seq seed failed for %s", cid[:8], exc_info=True)
        c.pop("_max_seq", None)
        # Merge extras from extras.json file (source of truth)
        extras_data = self._read_extras(cid)
        if extras_data:
            c["extras"] = extras_data
            c["extra_keys"] = set(extras_data.keys())
            if "title" in extras_data:
                c["title"] = extras_data["title"]
            # Use meta from extras for cache fields
            c["user_id"] = extras_data.get("_meta_user_id", c.get("user_id", ""))
            if extras_data.get("_meta_created_at"):
                c["created_at"] = max(c["created_at"], extras_data["_meta_created_at"])
                c["updated_at"] = max(c["updated_at"], extras_data["_meta_created_at"])
        # Only declared conversation agents are routable agent contexts.
        # Arbitrary context directories can exist from older bugs/backups;
        # never let their folder names create pseudo-agents such as
        # "background" or a user id.
        conv_agents = c.get("extras", {}).get("conv_agents") or {}
        declared_agents = set()
        if isinstance(conv_agents, dict) and conv_agents:
            declared_agents.update(self._canon_agent(a) for a in conv_agents if a)
            c["agents"].update(declared_agents)
            self._prune_invalid_agent_context_dirs(cid, declared_agents)
        with self._cache_lock:
            self._cache[cid] = c
        return c

    @staticmethod
    def _cache_ts(line: Dict[str, Any]) -> float:
        try:
            return float(line.get("ts") or line.get("timestamp") or 0)
        except (TypeError, ValueError):
            return 0.0

    def _latest_transcript_line(self, cid: str) -> Dict[str, Any]:
        try:
            for line in self._transcript_log(cid).iter_rows_reverse():
                return line
        except Exception:
            logger.debug("latest transcript row read failed for %s", cid[:8], exc_info=True)
        return {}

    def _load_cache_metadata(self, cid: str, user_id: str = "") -> dict:
        """Warm list/ownership cache without scanning the transcript body."""
        extras_data = self._read_extras(cid)
        log = self._transcript_log(cid)
        latest = self._latest_transcript_line(cid) if log.exists() else {}
        c = {"user_id": user_id or extras_data.get("_meta_user_id", ""),
             "status": extras_data.get("_meta_status", "idle"),
             "created_at": extras_data.get("_meta_created_at", 0),
             "updated_at": extras_data.get("_meta_updated_at", 0),
             "expires_at": extras_data.get("_meta_expires_at", 0),
             "msg_count": int(extras_data.get("_meta_msg_count") or 0),
             "agents": set(), "extra_keys": set(extras_data.keys()),
             "extras": extras_data, "preview": extras_data.get("_meta_preview", "")}
        if not c["msg_count"] and log.exists():
            c["msg_count"] = max(0, log.total_rows() - 1)
        if latest:
            c["updated_at"] = max(float(c.get("updated_at") or 0), self._cache_ts(latest))
            if latest.get("t") == "status":
                c["status"] = latest.get("status", c["status"])
        if "title" in extras_data:
            c["title"] = extras_data["title"]
        max_seq = int(extras_data.get("_meta_max_seq") or 0)
        try:
            max_seq = max(max_seq, int(latest.get("seq") or 0))
        except (TypeError, ValueError):
            pass
        try:
            from core.llm_client import _seed_persisted_seq
            _seed_persisted_seq(cid, max_seq)
        except Exception:
            logger.debug("persisted seq seed failed for %s", cid[:8], exc_info=True)
        conv_agents = extras_data.get("conv_agents") or {}
        declared_agents = set()
        if isinstance(conv_agents, dict) and conv_agents:
            declared_agents.update(self._canon_agent(a) for a in conv_agents if a)
            c["agents"].update(declared_agents)
            self._prune_invalid_agent_context_dirs(cid, declared_agents)
        with self._cache_lock:
            self._cache[cid] = c
        return c

    def _prune_invalid_agent_context_dirs(self, cid: str,
                                          declared_agents: set) -> None:
        """Delete private context dirs that do not belong to declared agents."""
        conv_dir = self._conv_dir(cid)
        if not conv_dir.is_dir():
            return
        skip = {".git", "transcript", "shared", "summaries",
                "_jsonl_migration_backup"}
        for entry in conv_dir.iterdir():
            if not entry.is_dir() or entry.name in skip:
                continue
            agent = self._canon_agent(entry.name.replace("__", ":"))
            if agent in declared_agents:
                continue
            if not (self._jsonl_exists(entry / "context.jsonl")
                    or (entry / "context").is_dir()):
                continue
            try:
                shutil.rmtree(entry)
                logger.warning(
                    "[convstore] pruned invalid agent context dir %s/%s",
                    cid[:8], agent)
            except Exception:
                logger.warning(
                    "[convstore] failed to prune invalid context dir %s/%s",
                    cid[:8], agent, exc_info=True)

    def _cache_agents_for_append(self, cid: str) -> set:
        """Return known agents without rescanning the transcript hot path."""
        with self._cache_lock:
            cached = self._cache.get(cid)
            if cached is not None:
                return set(cached.get("agents", set()))
        return set(self._reload_cache(cid).get("agents", set()))

    def _note_cache_append(self, cid: str, transcript_line: Optional[Dict],
                           agents: set) -> None:
        """Apply append_message side effects to the in-memory cache.

        append_message is the hot path for every streamed assistant block,
        tool_call, and tool_result. Rescanning transcript.jsonl after each
        append makes long conversations slower on every write; the fields
        affected by an append are trivial to update in memory.
        """
        with self._cache_lock:
            cached = self._cache.get(cid)
            if cached is None:
                return
            if transcript_line is not None:
                cached["msg_count"] = int(cached.get("msg_count") or 0) + 1
                if not cached.get("preview") and transcript_line.get("role") == "user":
                    content = transcript_line.get("content", "")
                    if isinstance(content, str) and content.strip():
                        cached["preview"] = content[:80]
                cached["updated_at"] = max(
                    cached.get("updated_at", 0), transcript_line.get("ts", 0))
            if agents:
                cached.setdefault("agents", set()).update(
                    self._canon_agent(a) for a in agents if a)
        if transcript_line is not None:
            self._persist_hot_metadata(cid, transcript_line)

    def _persist_hot_metadata(self, cid: str, transcript_line: Dict[str, Any]) -> None:
        lock = self._get_extras_lock(cid)
        with lock:
            data = self._read_extras(cid)
            if transcript_line.get("t") == "msg":
                if "_meta_msg_count" in data:
                    base_count = int(data.get("_meta_msg_count") or 0)
                else:
                    with self._cache_lock:
                        cached_count = int((self._cache.get(cid) or {}).get("msg_count") or 0)
                    base_count = max(0, cached_count - 1)
                    if not base_count:
                        try:
                            base_count = max(0, self._transcript_log(cid).total_rows() - 2)
                        except Exception:
                            base_count = 0
                data["_meta_msg_count"] = base_count + 1
                if not data.get("_meta_preview") and transcript_line.get("role") == "user":
                    content = transcript_line.get("content", "")
                    if isinstance(content, str) and content.strip():
                        data["_meta_preview"] = content[:80]
            ts = self._cache_ts(transcript_line)
            if ts:
                data["_meta_updated_at"] = max(float(data.get("_meta_updated_at") or 0), ts)
            try:
                from core.llm_client import _peek_persisted_seq
                data["_meta_max_seq"] = max(
                    int(data.get("_meta_max_seq") or 0),
                    int(_peek_persisted_seq(cid) or 0),
                    int(transcript_line.get("seq") or 0),
                )
            except Exception:
                try:
                    data["_meta_max_seq"] = max(
                        int(data.get("_meta_max_seq") or 0),
                        int(transcript_line.get("seq") or 0),
                    )
                except (TypeError, ValueError):
                    pass
            self._write_extras(cid, data)

    def _ensure_loaded(self):
        if self._loaded:
            return
        with self._lock:  # class-level lock (also used for singleton)
            if self._loaded:
                return
            # Hold the lock across the scan so concurrent callers (boot-time
            # cleanup_orphan_claude_sessions) wait for the cache to be fully
            # populated. Previously we set _loaded=True BEFORE the scan,
            # which let those callers observe a half-empty cache and treat
            # live convs as orphans (safety net caught it, but it logged
            # a "cache race" warning for every live conv).
            count = 0
            for user_dir in self._store_dir.iterdir():
                if not user_dir.is_dir():
                    continue
                uid = user_dir.name
                for conv_dir in user_dir.iterdir():
                    if not conv_dir.is_dir():
                        continue
                    if (not SegmentedJsonl(conv_dir / "transcript.jsonl").exists()
                            and not (conv_dir / "extras.json").exists()):
                        continue
                    cid = conv_dir.name.replace("__", ":")
                    self._cid_user[cid] = uid
                    self._load_cache_metadata(cid, uid)
                    count += 1
            self._loaded = True
        if count:
            logger.info(f"ConversationStore: loaded {count} conversations from disk")

    @staticmethod
    def _validate_message(m: Dict):
        """Every message MUST have msg_id and timestamp at CREATION.

        msg_id and ts are minted at message CREATION (producer side —
        stable through transit, timestamp reflects the moment the
        message existed). seq is the on-disk line index, assigned at
        WRITE time by _stamp_line under the conv lock — producers must
        NOT stamp it in advance (disk order is the sole source of truth).
        """
        role = m.get("role", "")
        if role in ("system",):
            return  # system prompts are ephemeral, no msg_id needed
        if not m.get("msg_id"):
            raise ValueError(
                f"BUG: message without msg_id — role={role}, "
                f"content={str(m.get('content', ''))[:80]}")
        if not m.get("ts") and not m.get("timestamp"):
            raise ValueError(
                f"BUG: message without timestamp — role={role}, "
                f"msg_id={m.get('msg_id')}")

    # ══════════════════════════════════════════════════════════════════
    #  PUBLIC API
    # ══════════════════════════════════════════════════════════════════

    def generate_id(self) -> str:
        return uuid.uuid4().hex[:16]

    def exists(self, cid: str) -> bool:
        try:
            return self._conv_dir(cid).is_dir()
        except ValueError:
            return False

    # ── Create / Save ─────────────────────────────────────────────────

    def save(self, cid: str, messages: List[Dict], ttl: int = 0,
             user_id: str = "", status: str = ""):
        _now = time.time()
        if not user_id:
            raise ValueError("user_id is required to create a conversation")
        self._conv_dir(cid, user_id=user_id).mkdir(parents=True, exist_ok=True)

        # Write transcript
        meta_line = self._stamp_line(cid, {
            "t": "meta", "user_id": user_id, "status": status or "idle",
            "created_at": _now, "ts": _now,
            "expires_at": _now + ttl if ttl > 0 else 0,
        })
        rows = [meta_line]
        for m in messages:
            self._validate_message(m)
            rows.append(self._stamp_line(cid, {"t": "msg", **m}))
        self._transcript_log(cid).replace_dicts(rows)

        # Write extras with metadata
        extras = {
            "_meta_user_id": user_id,
            "_meta_created_at": _now,
            "_meta_expires_at": _now + ttl if ttl > 0 else 0,
            "_meta_status": status or "idle",
            "_meta_updated_at": _now,
            "_meta_msg_count": len(messages),
            "_meta_max_seq": max((int(r.get("seq") or 0) for r in rows), default=0),
        }
        for row in rows:
            if row.get("t") == "msg" and row.get("role") == "user":
                content = row.get("content", "")
                if isinstance(content, str) and content.strip():
                    extras["_meta_preview"] = content[:80]
                    break
        self._write_extras(cid, extras)

        # Initialize git repo
        self._git_init(cid)

        # Update cache
        self._reload_cache(cid)

    # ════════════════════════════════════════════════════════════════════
    # UNIFIED PERSISTENCE ROUTER — append_message()
    # ════════════════════════════════════════════════════════════════════
    #
    # Single write path. Every message (assistant block, tool call,
    # tool result, user input, delegate request/reply, context
    # injection, display_only) goes through here exactly ONCE.
    #
    # The router decides per-message which files to write based on
    # (role, source.type, display_only, tool_calls). Atomic under the
    # per-conv lock. SSE publication is the ConversationWriter's job
    # AFTER this returns successfully — visible ⇒ persisted.
    #
    # Does NOT git_snapshot. Git commits are per-turn and called
    # explicitly by the agent loop via core.conversation_git.commit_turn().
    #
    # NO DEDUP LOGIC — each call must carry a unique msg_id. A duplicate
    # indicates an upstream bug and is the caller's responsibility.
    # ════════════════════════════════════════════════════════════════════

    def append_message(self, cid: str, msg: Dict, agent_name: str = "",
                       user_id: str = "", ttl: int = 0) -> None:
        """Persist one message to every target file it belongs in.

        Sole write path. Callers feed one message at a time; writes are
        atomic under the conv lock.

        Routing rules:
          - transcript.jsonl: everything except source.type=='context'.
          - {agent}/context.jsonl (own): everything except source.type!=
            'context' and agent_name=='' (broadcast-less orphan).
          - shared.jsonl + other agents' contexts: user/assistant
            without tool_calls, not display_only, not context, not
            delegate-reply; assistant+tool_calls contributes a
            stripped-of-tool_calls copy (filter_for_shared).
          - agent_delegate: private A↔B routing via
            _route_delegate_message(); requests also project into shared.
          - display_only: transcript only.

        Raises on any I/O error (no silent swallow).
        """
        _append_started = time.monotonic()
        _timings: Dict[str, float] = {}

        def _mark_timing(name: str, started: float) -> None:
            _timings[name] = _timings.get(name, 0.0) + (
                (time.monotonic() - started) * 1000.0)

        self._validate_message(msg)
        agent_name = self._canon_agent(agent_name) if agent_name else ""
        role = msg.get("role", "")
        source = msg.get("source") or {}
        src_type = source.get("type", "")
        target_agent = self._canon_agent(source.get("target_agent", "")) if source.get("target_agent") else ""
        if role == "user" and src_type != "context" and not target_agent:
            raise ValueError("user messages require source.target_agent")
        route_agent = agent_name or (target_agent if target_agent not in ("ALL", "all") else "")
        display_only = bool(msg.get("display_only"))
        has_tool_calls = bool(msg.get("tool_calls"))
        now = time.time()
        transcript_line = None
        touched_agents = set()

        _mark_timing("pre_lock", _append_started)
        lock = self._get_conv_lock(cid)
        _t0 = time.monotonic()
        with lock:
            _mark_timing("lock_wait", _t0)
            # Create conv if missing
            _t0 = time.monotonic()
            if not self.exists(cid):
                if not user_id:
                    raise ValueError(
                        "user_id required for new conversation")
                self.save(cid, [], user_id=user_id, ttl=ttl)
            _mark_timing("create_or_exists", _t0)

            # 1. Transcript — everything except synthetic context injections.
            if src_type != "context":
                _transcript_t0 = time.monotonic()
                is_private = has_tool_calls or role == "tool"
                line = self._stamp_line(cid, {"t": "msg", **msg})
                if is_private:
                    line["private"] = True
                transcript_line = line
                _mark_timing("transcript_prepare", _transcript_t0)
                _t0 = time.monotonic()
                self._transcript_log(cid).append_dicts([line])
                _mark_timing("transcript_write", _t0)
                # Feed bg_bucket_builder's token-budget trigger so it
                # can fire a partial bucket BEFORE the agent's hot-path
                # /compact has to digest-tail at runtime.
                _t0 = time.monotonic()
                self._notify_bg_transcript_chars(
                    cid, self._row_payload_chars(line))
                _mark_timing("bg_notify", _t0)
                _mark_timing("transcript", _transcript_t0)

            if display_only:
                pass  # transcript only, no context files
            elif src_type == "agent_delegate":
                # Private A↔B routing (requests project to shared,
                # replies stay private between from/to).
                _t0 = time.monotonic()
                touched_agents.update(
                    self._route_delegate_message(cid, msg, agent_name))
                _mark_timing("delegate_route", _t0)
            else:
                # 2. Author's own context (brut, keeps tool_calls /
                #    tool results; also target of context injections).
                if route_agent:
                    _t0 = time.monotonic()
                    if src_type != "context":
                        self._seed_agent_context_from_shared_if_missing(
                            cid, route_agent)
                    self._append_ctx_file(cid, route_agent, [msg])
                    touched_agents.add(route_agent)
                    _mark_timing("own_ctx", _t0)

                # 3. Shared + broadcast to other agents — only for
                #    conversation messages (filter_for_shared drops
                #    tool results and context injections; strips
                #    tool_calls but keeps text).
                if src_type != "context" and role != "tool":
                    _t0 = time.monotonic()
                    shared_candidates = self.filter_for_shared([msg])
                    _mark_timing("shared_filter", _t0)
                    if shared_candidates:
                        _t0 = time.monotonic()
                        shared_msgs = [self._transform_for_shared(m)
                                       for m in shared_candidates]
                        self._append_shared_ctx(cid, shared_msgs, _timings)
                        _mark_timing("shared_append", _t0)
                        _t0 = time.monotonic()
                        known_agents = self._cache_agents_for_append(cid)
                        if route_agent:
                            known_agents.add(route_agent)
                        _mark_timing("known_agents", _t0)
                        _broadcast_count = 0
                        _t0 = time.monotonic()
                        for other in known_agents:
                            if not other or other == agent_name:
                                continue
                            transformed = [
                                self._transform_for_other_agent(m, other)
                                for m in shared_candidates]
                            self._append_ctx_file(cid, other, transformed)
                            touched_agents.add(other)
                            _broadcast_count += 1
                        _mark_timing("broadcast", _t0)
                        _timings["broadcast_count"] = float(_broadcast_count)

        # Refresh cache (msg_count, agents set) after any write.
        _t0 = time.monotonic()
        self._invalidate_ctx_cache(cid)
        _mark_timing("ctx_cache_invalidate", _t0)
        _t0 = time.monotonic()
        self._note_cache_append(cid, transcript_line, touched_agents)
        _mark_timing("cache_note", _t0)
        _total_ms = (time.monotonic() - _append_started) * 1000.0
        if _total_ms >= 100.0:
            logger.warning(
                "[convstore:%s] append slow role=%s msg_id=%s total_ms=%.1f "
                "pre_lock=%.1f lock_wait=%.1f create_or_exists=%.1f "
                "transcript=%.1f transcript_prepare=%.1f "
                "transcript_write=%.1f bg_notify=%.1f own_ctx=%.1f "
                "shared_filter=%.1f "
                "shared_append=%.1f shared_write=%.1f "
                "shared_bg_trigger=%.1f known_agents=%.1f broadcast=%.1f "
                "broadcast_count=%d delegate_route=%.1f "
                "ctx_cache_invalidate=%.1f cache_note=%.1f touched_agents=%d",
                cid[:8], role, msg.get("msg_id", "?"), _total_ms,
                _timings.get("pre_lock", 0.0),
                _timings.get("lock_wait", 0.0),
                _timings.get("create_or_exists", 0.0),
                _timings.get("transcript", 0.0),
                _timings.get("transcript_prepare", 0.0),
                _timings.get("transcript_write", 0.0),
                _timings.get("bg_notify", 0.0),
                _timings.get("own_ctx", 0.0),
                _timings.get("shared_filter", 0.0),
                _timings.get("shared_append", 0.0),
                _timings.get("shared_write", 0.0),
                _timings.get("shared_bg_trigger", 0.0),
                _timings.get("known_agents", 0.0),
                _timings.get("broadcast", 0.0),
                int(_timings.get("broadcast_count", 0.0)),
                _timings.get("delegate_route", 0.0),
                _timings.get("ctx_cache_invalidate", 0.0),
                _timings.get("cache_note", 0.0),
                len(touched_agents),
            )

    def _route_delegate_message(self, cid: str, msg: Dict,
                                agent_name: str) -> set:
        """Route an agent_delegate message to from's ctx, to's ctx, and
        (for requests only) to shared + other agents.

        Called under the conv lock by append_message.
        """
        src = msg.get("source") or {}
        _from = src.get("from", "") or agent_name
        _to = src.get("to", "")
        if not _to:
            return set()
        _kind = src.get("kind")
        touched_agents = {self._canon_agent(_from), self._canon_agent(_to)}

        # FROM's own ctx — [delegate <from> → <to>]:
        _for_from = dict(msg)
        _for_from["content"] = self._prefix_content(
            _for_from.get("content", ""),
            f"[delegate {_from} → {_to}]:")
        self._append_ctx_file(cid, _from, [_for_from])

        # TO's ctx — role coerced to user with explicit attribution.
        _for_to = dict(msg)
        if _for_to.get("role") == "assistant":
            _for_to["role"] = "user"
        if _kind == "reply":
            _attr = (f"Here is agent '{_from}''s reply to your "
                     f"delegate:")
        else:
            _attr = f"Here is a message from agent '{_from}':"
        _for_to["content"] = self._prefix_content(
            _for_to.get("content", ""), _attr)
        self._append_ctx_file(cid, _to, [_for_to])

        # Replies stay private between from/to — don't leak to shared.
        if _kind == "reply":
            return touched_agents

        # Request broadcasts to shared + other agents (not from/to,
        # they already got their tailored copy above).
        _for_shared = dict(msg)
        if _for_shared.get("role") == "assistant":
            _for_shared["role"] = "user"
        _for_shared["content"] = self._prefix_content(
            _for_shared.get("content", ""),
            f"[{_from} to agent {_to}]:")
        self._append_shared_ctx(cid, [_for_shared])

        known_agents = self._cache_agents_for_append(cid)
        known_agents.update(touched_agents)
        _delegate_parties = {self._canon_agent(_from), self._canon_agent(_to)}
        for other in known_agents:
            if not other or other in _delegate_parties:
                continue
            transformed = self._transform_for_other_agent(
                _for_shared, other)
            self._append_ctx_file(cid, other, [transformed])
            touched_agents.add(other)
        return touched_agents

    # ── Context ops ───────────────────────────────────────────────────

    @staticmethod
    def _context_cache_chars(messages: Optional[List[Dict]]) -> int:
        if not messages:
            return 0
        total = 0
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, str):
                total += len(content)
            elif content is not None:
                total += len(str(content))
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                total += len(str(tool_calls))
        return total

    @staticmethod
    def _should_cache_context(messages: Optional[List[Dict]]) -> bool:
        if messages is None:
            return True
        return (len(messages) <= _CTX_CACHE_MAX_MESSAGES and
                ConversationStore._context_cache_chars(messages) <= _CTX_CACHE_MAX_CHARS)

    def _trim_ctx_cache_locked(self) -> None:
        while len(self._ctx_cache) > _CTX_CACHE_MAX_CONVS:
            oldest = next(iter(self._ctx_cache), None)
            if oldest is None:
                return
            self._ctx_cache.pop(oldest, None)

    def load_agent_context(self, cid: str, agent_name: str) -> Optional[List[Dict]]:
        """Load agent context from {agent}/context.jsonl file.

        If agent_name is set but no context file exists, returns None
        (caller falls back to shared via load_context).
        If agent_name is empty, loads from shared.jsonl directly.
        """
        agent_name = self._canon_agent(agent_name) if agent_name else ""
        with self._ctx_cache_lock:
            if cid in self._ctx_cache and agent_name in self._ctx_cache[cid]:
                cached = self._ctx_cache[cid][agent_name]
                return list(cached) if cached is not None else None

        if agent_name:
            path = self._agent_ctx_path(cid, agent_name)
        else:
            path = self._shared_ctx_path(cid)
        # Hold the per-conv lock so reads serialize with any writer
        # atomically replacing this file (save_agent_context etc.) —
        # otherwise an open read handle can block MoveFileEx on Windows.
        lock = self._get_conv_lock(cid)
        with lock:
            result = self._read_ctx_file(path) or None
        with self._ctx_cache_lock:
            if self._should_cache_context(result):
                self._ctx_cache.setdefault(cid, {})[agent_name] = result
                self._trim_ctx_cache_locked()
            else:
                self._ctx_cache.get(cid, {}).pop(agent_name, None)
                logger.debug("ConversationStore: skipped ctx cache for %s/%s (%s messages, %s chars)",
                             cid[:8], agent_name or "shared", len(result or []),
                             self._context_cache_chars(result))
        return result

    def load_agent_context_page(self, cid: str, agent_name: str,
                                limit: int = 50, offset: int = 0) -> Optional[Dict]:
        """Load a newest-first page from an agent/shared context file.

        Returns None when the context file does not exist. Messages stay
        chronological inside the page, matching load_page().
        """
        agent_name = self._canon_agent(agent_name) if agent_name else ""
        path = self._agent_ctx_path(cid, agent_name) if agent_name else self._shared_ctx_path(cid)
        log = SegmentedJsonl(path)
        if not log.exists():
            return None
        try:
            limit_i = max(1, int(limit or 50))
        except (TypeError, ValueError):
            limit_i = 50
        try:
            offset_i = max(0, int(offset or 0))
        except (TypeError, ValueError):
            offset_i = 0
        total = log.total_rows()
        selected = []
        stop = offset_i + limit_i
        for idx, row in enumerate(log.iter_rows_reverse()):
            if idx < offset_i:
                continue
            if idx >= stop:
                break
            selected.append(row)
        selected.reverse()
        return {
            "messages": selected,
            "total_count": total,
            "has_more": stop < total,
            "offset": offset_i,
            "limit": limit_i,
        }

    def load_transcript_for_agent(self, cid: str, agent_name: str
                                   ) -> Optional[List[Dict]]:
        """Return the full transcript personalized for one agent.

        This is the right source for compaction: it contains everything
        (user messages, every agent's assistant turns, tool_calls,
        tool_results) and — unlike agent context — never includes a
        previously-injected compaction summary. Compacting from here
        can never layer stale summaries on top of each other.

        Personalization:
        - Own assistant messages: role=assistant, keep tool_calls, no prefix
        - Own tool messages: kept as-is (they belong to the agent's turn)
        - Other agents' messages: role=user, content prefixed "[Agent X]:"
        - User messages: role=user, prefixed "[User to agent X]:" when targeted
        - Other agents' tool_calls/tool_results: dropped (private to them)
        """
        if not self.exists(cid):
            return None
        canon = self._canon_agent(agent_name) if agent_name else ""
        raw = self.load(cid)
        if not raw:
            return None
        return self._personalize_transcript_for_agent(raw, canon)

    def load_transcript_tail_for_agent(self, cid: str, agent_name: str,
                                       limit: int = 1000) -> Optional[List[Dict]]:
        """Return a recent transcript tail personalized for one agent.

        Provider-triggered compaction only needs recent raw fidelity; old
        history comes from the shared bucket header. Read from the tail so a
        compact boundary never has to materialize a giant transcript just to
        walk back to the last few dozen useful messages.
        """
        if not self.exists(cid):
            return None
        try:
            limit_i = max(1, int(limit or 1000))
        except (TypeError, ValueError):
            limit_i = 1000
        page = self.load_page(cid, limit=limit_i, offset=0) or {}
        raw = page.get("messages") or []
        if not raw:
            return None
        canon = self._canon_agent(agent_name) if agent_name else ""
        return self._personalize_transcript_for_agent(raw, canon)

    def _personalize_transcript_for_agent(self, raw: List[Dict],
                                          canon: str) -> List[Dict]:
        """Personalize already-loaded transcript messages for one agent."""

        # First pass: collect tool_call_ids that belong to THIS agent so we
        # can keep matching tool results and drop everybody else's.
        own_tc_ids: set = set()
        for m in raw:
            if m.get("role") != "assistant":
                continue
            src = m.get("source") or {}
            if src.get("type") != "agent":
                continue
            sname = src.get("name", "")
            if not (canon and sname and sname.lower() == canon.lower()):
                continue
            for tc in (m.get("tool_calls") or []):
                tid = tc.get("id") if isinstance(tc, dict) else None
                if tid:
                    own_tc_ids.add(tid)

        out: List[Dict] = []
        for m in raw:
            role = m.get("role", "")
            src = m.get("source") or {}
            src_type = src.get("type", "")
            src_name = src.get("name", "")

            # Private per-agent traces — only keep this agent's own.
            if role == "sub_agent_trace":
                if src_type == "agent" and canon and src_name \
                        and src_name.lower() == canon.lower():
                    out.append(dict(m))
                continue

            # Tool results — keep only those answering this agent's tool_calls.
            # Orphans (other agents' tool results) are dropped so the summarizer
            # never sees them.
            if role == "tool":
                tcid = m.get("tool_call_id", "")
                if tcid and tcid in own_tc_ids:
                    out.append(dict(m))
                continue

            if role == "assistant" and src_type == "agent":
                if canon and src_name and src_name.lower() == canon.lower():
                    # Own turn — keep as assistant with tool_calls intact
                    out.append(dict(m))
                else:
                    # Another agent's turn — demote to user with prefix,
                    # strip tool_calls (private to them) and drop btw/task
                    # side-channels entirely (these aren't addressed to us).
                    if src.get("task_id") or src.get("btw"):
                        continue
                    # Tool-call-only turns have no text. Stripping the
                    # tool_calls leaves nothing useful — don't emit empty
                    # "[Agent X]:" stubs into the view. Handle both string
                    # and list (multimodal) content formats.
                    content = m.get("content", "")
                    if isinstance(content, str):
                        if not content.strip():
                            continue
                        text = content
                    elif isinstance(content, list):
                        # Collect text from every text block; drop the rest
                        # (tool_use blocks become meaningless once tool_calls
                        # are stripped).
                        _parts = [
                            b.get("text", "") for b in content
                            if isinstance(b, dict) and b.get("type") == "text"
                        ]
                        text = "\n".join(p for p in _parts if p.strip())
                        if not text.strip():
                            continue
                    else:
                        continue
                    mm = dict(m)
                    mm["role"] = "user"
                    prefix = f"[Agent {src_name}]: " if src_name else "[Agent]: "
                    mm["content"] = prefix + text
                    mm.pop("tool_calls", None)
                    out.append(mm)
                continue

            if role == "user":
                tgt = src.get("target_agent", "") if isinstance(src, dict) else ""
                # Drop btw/sub-task user messages addressed to another agent —
                # those are private side-channels, not part of this agent's
                # conversation view.
                if src.get("btw") and tgt and canon \
                        and tgt.lower() != canon.lower():
                    continue
                mm = dict(m)
                if tgt and canon and tgt.lower() != canon.lower():
                    prefix = f"[User to agent {tgt}]: "
                    content = mm.get("content", "")
                    if isinstance(content, str):
                        mm["content"] = prefix + content
                out.append(mm)
                continue

            # system, etc. — passthrough
            out.append(dict(m))
        return out

    def load_shared_for_agent(self, cid: str, agent_name: str) -> Optional[List[Dict]]:
        """Load shared context personalized for a specific agent.

        Shared stores agent-neutral messages (all prefixed).
        This reverses prefixes for the agent's own messages.
        """
        agent_name = self._canon_agent(agent_name) if agent_name else ""
        lock = self._get_conv_lock(cid)
        with lock:
            raw = self._read_ctx_file(self._shared_ctx_path(cid))
        if not raw:
            return None
        return [self._personalize_from_shared(m, agent_name) for m in raw]

    def _invalidate_ctx_cache(self, cid: str, agent_name: str = ""):
        with self._ctx_cache_lock:
            if agent_name:
                if cid in self._ctx_cache:
                    self._ctx_cache[cid].pop(agent_name, None)
            else:
                self._ctx_cache.pop(cid, None)

    def save_agent_context(self, cid: str, agent_name: str,
                           context_messages: List[Dict]) -> bool:
        """Write agent context to {agent}/context.jsonl (full replace)."""
        if not self.exists(cid):
            return False
        agent_name = self._canon_agent(agent_name) if agent_name else ""
        clean = [m for m in context_messages if not m.get("display_only")]
        # Cross-file UUID invariant: the same logical message must carry
        # the same msg_id here as in the transcript — preserved by
        # construction (msg_id is minted once in LLMMessage.__post_init__
        # and flows through every write path via dict(msg) transforms).
        for _m in clean:
            self._validate_message(_m)
        lock = self._get_conv_lock(cid)
        with lock:
            if agent_name:
                self._write_ctx_file(self._agent_ctx_path(cid, agent_name), clean)
            else:
                # Shared context full-rewrite: user-driven edit / delete
                # via the context editor (agent="shared"). Compare the
                # new state against what's on disk to find the earliest
                # seq whose content diverges, then wipe all pyramid
                # buckets that covered that range — they now point at
                # stale content. Wiping is deliberately coarse (any
                # bucket with last_seq >= min_changed_seq goes); the
                # bg worker will rebuild from the new shared state on
                # the next maybe_trigger.
                _old_shared = self._read_ctx_file(self._shared_ctx_path(cid))
                self._write_ctx_file(self._shared_ctx_path(cid), clean)
                try:
                    _min_changed = self._compute_min_changed_seq(
                        _old_shared, clean)
                    if _min_changed is not None:
                        from core.bucket_store import BucketStore
                        _bs = BucketStore.get(self._conv_dir(cid))
                        _wiped = _bs.invalidate_from_seq(_min_changed)
                        if _wiped:
                            logger.info(
                                "[convstore] shared edit at seq %d "
                                "invalidated %d pyramid bucket(s) "
                                "for cid=%s",
                                _min_changed, _wiped, cid[:8])
                            self._invalidate_pyramid_cache(cid)
                except Exception:
                    logger.warning(
                        "[convstore] pyramid invalidation on shared "
                        "edit failed for cid=%s", cid[:8],
                        exc_info=True)
        self._invalidate_ctx_cache(cid, agent_name)
        # Refresh the main cache's `agents` set. Without this, writing
        # the first context for a new agent (e.g. /compact on an agent
        # that had no ctx yet) leaves cache["agents"] stale — the UI
        # context-editor reads from list_agent_contexts → cache and
        # wouldn't see the newly created agent until a server restart.
        if agent_name:
            with self._cache_lock:
                self._cache.pop(cid, None)
            self._reload_cache(cid)
        return True

    @staticmethod
    def _compute_min_changed_seq(old: List[Dict],
                                   new: List[Dict]) -> Optional[int]:
        """Find the smallest seq whose presence or content differs
        between old and new shared state. Returns None if identical.

        A seq is considered "changed" if:
          - it was in old but is missing from new (deleted)
          - it was added in new but not in old (inserted)
          - same msg_id is in both but content or role differ (edited)

        The msg_id is the identity — compare by that, not by index, so
        reorderings in the list without content changes don't trigger
        unnecessary invalidation.
        """
        _old_by_id: Dict[str, Dict] = {
            m.get("msg_id"): m for m in old if m.get("msg_id")}
        _new_by_id: Dict[str, Dict] = {
            m.get("msg_id"): m for m in new if m.get("msg_id")}
        _changed_seqs: List[int] = []

        for mid, oldm in _old_by_id.items():
            newm = _new_by_id.get(mid)
            if newm is None:
                _s = oldm.get("seq") or 0
                if _s:
                    _changed_seqs.append(int(_s))
            elif (newm.get("content") != oldm.get("content")
                    or newm.get("role") != oldm.get("role")):
                _s = oldm.get("seq") or 0
                if _s:
                    _changed_seqs.append(int(_s))

        for mid, newm in _new_by_id.items():
            if mid not in _old_by_id:
                _s = newm.get("seq") or 0
                if _s:
                    _changed_seqs.append(int(_s))

        if not _changed_seqs:
            return None
        return min(_changed_seqs)

    def delete_agent_context(self, cid: str, agent_name: str) -> bool:
        agent_name = self._canon_agent(agent_name) if agent_name else ""
        """Delete agent context file + directory."""
        if not self.exists(cid):
            return False
        if agent_name:
            path = self._agent_ctx_path(cid, agent_name)
        else:
            path = self._shared_ctx_path(cid)
        lock = self._get_conv_lock(cid)
        with lock:
            SegmentedJsonl(path).delete()
        # Remove empty agent directory
        if agent_name and path.parent.is_dir():
            try:
                path.parent.rmdir()  # only succeeds if empty
            except OSError:
                pass
        self._invalidate_ctx_cache(cid, agent_name)
        # Reload main cache so agents set is updated
        with self._cache_lock:
            self._cache.pop(cid, None)
        self._reload_cache(cid)
        return True

    def save_context(self, cid: str, ctx: List[Dict]) -> bool:
        return self.save_agent_context(cid, "", ctx)

    def load_context(self, cid: str, user_id: str = "") -> Optional[List[Dict]]:
        return self.load_agent_context(cid, "")

    # ── Transcript read ───────────────────────────────────────────────

    def _scan_transcript(self, lines) -> List[Dict]:
        """Scan JSONL lines into transcript messages (with patches applied)."""
        msgs = []
        patches = {}
        trace_updates = {}  # trace_id -> list of (entry, content_update)
        for line in lines:
            t = line.get("t", "")
            if t == "msg_patch":
                # New format uses target_msg_id for the link; legacy
                # data-on-disk (pre-invariant-fix) still squats msg_id.
                mid = line.get("target_msg_id") or line.get("msg_id", "")
                if mid:
                    # Drop the patch's own identity fields from the
                    # payload we fold back into the target msg — they
                    # describe the patch line, not the message.
                    patches[mid] = {
                        k: v for k, v in line.items()
                        if k not in ("t", "msg_id", "target_msg_id",
                                     "ts", "seq")
                    }
                continue
            if t == "trace_update":
                tid = line.get("trace_id", "")
                if tid:
                    trace_updates.setdefault(tid, []).append(
                        (line.get("entry") or {}, line.get("content_update") or ""))
                continue
            if t != "msg":
                continue
            msg = {k: v for k, v in line.items() if k not in ("t", "ts", "private")}
            if "ts" in line:
                msg["timestamp"] = line["ts"]
            msgs.append(msg)
        if patches:
            for msg in msgs:
                mid = msg.get("msg_id", "")
                if mid and mid in patches:
                    msg.update(patches[mid])
        if trace_updates:
            for msg in msgs:
                if msg.get("role") != "sub_agent_trace":
                    continue
                tid = msg.get("trace_id", "")
                ups = trace_updates.get(tid)
                if not ups:
                    continue
                trace = list(msg.get("trace") or [])
                content = msg.get("content", "") or ""
                for entry, cu in ups:
                    if entry:
                        trace.append(entry)
                    if cu:
                        content += cu
                msg["trace"] = trace
                msg["content"] = content
        # Sort by (creation ts, creation seq) — see _read_ctx_file for
        # rationale. Same invariant: order = creation, not file position.
        msgs.sort(key=lambda m: (
            m.get("timestamp") or m.get("ts") or 0.0,
            m.get("seq") or 0,
        ))
        return msgs

    def load(self, cid: str, user_id: str = "") -> Optional[List[Dict]]:
        """Load entire transcript (all messages)."""
        if not self.exists(cid):
            return None
        if user_id:
            cache = self._load_cache(cid)
            if cache["user_id"] and cache["user_id"] != user_id:
                return None
        return self._read(cid, self._scan_transcript)

    def load_range_by_msg_id(self, cid: str,
                             from_msg_id: str,
                             to_msg_id: str,
                             user_id: str = "") -> Optional[List[Dict]]:
        """Load messages in [from_msg_id, to_msg_id] inclusive.

        Used by read_history(action="range") — drives the bucket nav hints
        that let an agent zoom from a bucket summary back to the exact
        original messages. Returns [] if either id is missing or out of
        order. Returns None when the conversation doesn't exist / the
        user doesn't own it.
        """
        if not self.exists(cid):
            return None
        if user_id:
            cache = self._load_cache(cid)
            if cache["user_id"] and cache["user_id"] != user_id:
                return None
        if not from_msg_id or not to_msg_id:
            return []
        all_msgs = self._read(cid, self._scan_transcript)
        if not all_msgs:
            return []
        start = end = -1
        for i, m in enumerate(all_msgs):
            mid = m.get("msg_id") if isinstance(m, dict) else getattr(m, "msg_id", "")
            if mid == from_msg_id and start < 0:
                start = i
            if mid == to_msg_id:
                end = i
        if start < 0 or end < 0 or end < start:
            return []
        return all_msgs[start:end + 1]

    def load_page(self, cid: str, limit: int = 50, offset: int = 0,
                  user_id: str = "") -> Optional[Dict]:
        """Load a paginated slice of the transcript.

        Reads from the END of the JSONL file — only parses the lines needed.
        For a 2000-message conversation with limit=50, offset=0, this reads
        ~50 lines from the tail instead of scanning all 2000.
        """
        if not self.exists(cid):
            return None
        if user_id:
            cache = self._load_cache(cid)
            if cache["user_id"] and cache["user_id"] != user_id:
                return None
        log = self._transcript_log(cid)
        total = self.message_count(cid)
        # _read_tail reads the file without holding the conv lock.
        # This avoids blocking _commit (set_status, append) while reading
        # large files. The file is append-only so reading stale data is safe
        # (we might miss the very last line, but that's acceptable for pagination).
        if not log.exists():
            return {"messages": [], "total_count": 0, "offset": 0,
                    "limit": limit, "has_more": False}
        try:
            result = self._read_tail(log, total, limit, offset)
            return result
        except Exception as e:
            logger.error("[convstore] load_page failed %s: %s", cid, e)
            return {"messages": [], "total_count": total, "offset": offset,
                    "limit": limit, "has_more": False}

    def _read_tail(self, log: SegmentedJsonl, total_msgs: int, limit: int, offset: int) -> Dict:
        """Read the last (offset + limit) display rows from a logical JSONL."""
        need = offset + limit + 20  # extra margin for msg_patch records + tool alignment
        raw_lines = []
        for line in log.iter_rows_reverse():
            t = line.get("t", "")
            if t in ("msg", "msg_patch", "trace_update"):
                raw_lines.append(line)
                if len(raw_lines) >= need:
                    break
        raw_lines.reverse()

        # Apply scan_transcript logic (separate msgs from patches)
        msgs = []
        patches = {}
        trace_updates = {}
        for line in raw_lines:
            t = line.get("t", "")
            if t == "msg_patch":
                mid = line.get("target_msg_id") or line.get("msg_id", "")
                if mid:
                    patches[mid] = {k: v for k, v in line.items()
                                    if k not in ("t", "msg_id", "target_msg_id", "ts", "seq")}
                continue
            if t == "trace_update":
                tid = line.get("trace_id", "")
                if tid:
                    trace_updates.setdefault(tid, []).append(
                        (line.get("entry") or {}, line.get("content_update") or ""))
                continue
            if t != "msg":
                continue
            msg = {k: v for k, v in line.items() if k not in ("t", "ts", "private")}
            if "ts" in line:
                msg["timestamp"] = line["ts"]
            msgs.append(msg)

        if patches:
            for msg in msgs:
                mid = msg.get("msg_id", "")
                if mid and mid in patches:
                    msg.update(patches[mid])
        if trace_updates:
            for msg in msgs:
                if msg.get("role") != "sub_agent_trace":
                    continue
                tid = msg.get("trace_id", "")
                ups = trace_updates.get(tid)
                if not ups:
                    continue
                trace = list(msg.get("trace") or [])
                content = msg.get("content", "") or ""
                for entry, cu in ups:
                    if entry:
                        trace.append(entry)
                    if cu:
                        content += cu
                msg["trace"] = trace
                msg["content"] = content

        # Slice: msgs is chronological, we want the last `limit` before `offset`
        total_tail = len(msgs)
        end = total_tail - offset
        start = max(0, end - limit)
        # Don't split a tool_call from its tool results
        while start > 0 and msgs[start].get("role") == "tool":
            start -= 1
        page = msgs[start:end] if end > 0 else []
        has_more = (total_msgs - offset - len(page)) > 0

        return {"messages": page, "total_count": total_msgs,
                "offset": offset, "limit": limit, "has_more": has_more}

    def patch_message(self, cid: str, msg_id: str, **fields) -> None:
        """Append a msg_patch record referencing an existing message.

        The patch is itself a first-class line: it carries its OWN
        msg_id / ts / seq (stamped by _stamp_line) and references the
        target via ``target_msg_id``. Before, the patch squatted the
        target's msg_id which broke the "every line has unique
        msg_id + monotonic seq" invariant and tripped seq bootstrap.
        """
        if not msg_id or not fields:
            return
        lock = self._get_conv_lock(cid)
        with lock:
            line = self._stamp_line(cid, {
                "t": "msg_patch",
                "target_msg_id": msg_id,
                **fields,
            })
            self._transcript_log(cid).append_dicts([line])
        self._notify_bg_transcript_chars(cid, self._row_payload_chars(line))
        self._maybe_persist_context_usage_from_patch(cid, line)

    def _maybe_persist_context_usage_from_patch(self, cid: str, line: Dict[str, Any]) -> None:
        source = line.get("source")
        entry = self._context_usage_entry_from_source(source, line.get("ts"))
        if not entry:
            return
        name, usage_entry = entry
        lock = self._get_extras_lock(cid)
        with lock:
            data = self._read_extras(cid)
            self._merge_context_usage_locked(cid, data, name, usage_entry)

    @staticmethod
    def _context_usage_entry_from_source(source: Any, ts: Any = None):
        if not isinstance(source, dict):
            return None
        name = source.get("name") or source.get("agent")
        used = source.get("context_used")
        max_tokens = source.get("context_max")
        if not name or used is None or max_tokens is None:
            return None
        try:
            used_i = int(used)
            max_i = int(max_tokens)
        except (TypeError, ValueError):
            return None
        if max_i <= 0:
            return None
        pct = source.get("context_pct")
        try:
            pct_f = float(pct) if pct is not None else used_i / max_i
        except (TypeError, ValueError):
            pct_f = used_i / max_i
        try:
            ts_f = float(ts) if ts is not None else time.time()
        except (TypeError, ValueError):
            ts_f = time.time()
        return name, {"used": used_i, "max": max_i, "pct": pct_f, "updated_at": ts_f}

    def _merge_context_usage_locked(self, cid: str, data: Dict[str, Any],
                                    name: str, usage_entry: Dict[str, Any]) -> bool:
        usage = dict(data.get("context_usage") or {})
        prev = usage.get(name)
        if isinstance(prev, dict) and float(prev.get("updated_at") or 0) > float(usage_entry.get("updated_at") or 0):
            return False
        usage[name] = usage_entry
        data["context_usage"] = usage
        self._write_extras(cid, data)

        with self._cache_lock:
            if cid in self._cache:
                self._cache[cid]["extra_keys"].add("context_usage")
                self._cache[cid].setdefault("extras", {})["context_usage"] = usage
                self._cache[cid]["updated_at"] = time.time()
        return True

    def _scan_context_usage_from_transcript(self, cid: str,
                                            usage: Dict[str, Any]) -> Tuple[Dict[str, Any], float]:
        """Scan transcript context usage without holding the conv lock."""
        usage = dict(usage or {})
        log = self._transcript_log(cid)
        transcript_mtime = log.latest_mtime()
        if not transcript_mtime:
            return usage, 0.0
        if self._context_usage_repair_mtime.get(cid, 0) >= transcript_mtime:
            return usage, transcript_mtime
        for line in log.iter_rows():
            entry = self._context_usage_entry_from_source(
                line.get("source"), line.get("ts"))
            if not entry:
                continue
            name, usage_entry = entry
            prev = usage.get(name)
            if (not isinstance(prev, dict)
                    or float(prev.get("updated_at") or 0) <= float(usage_entry.get("updated_at") or 0)):
                usage[name] = usage_entry
        return usage, transcript_mtime

    def _repair_context_usage_from_transcript(self, cid: str,
                                              data: Dict[str, Any]) -> Dict[str, Any]:
        usage = dict(data.get("context_usage") or {})
        usage, transcript_mtime = self._scan_context_usage_from_transcript(
            cid, usage)
        if not transcript_mtime:
            return usage
        self._context_usage_repair_mtime[cid] = transcript_mtime
        if usage != data.get("context_usage"):
            lock = self._get_extras_lock(cid)
            with lock:
                latest = self._read_extras(cid)
                latest_usage = dict(latest.get("context_usage") or {})
                for name, usage_entry in usage.items():
                    prev = latest_usage.get(name)
                    if (not isinstance(prev, dict)
                            or float(prev.get("updated_at") or 0) <= float(usage_entry.get("updated_at") or 0)):
                        latest_usage[name] = usage_entry
                usage = latest_usage
                if latest_usage != latest.get("context_usage"):
                    latest["context_usage"] = latest_usage
                    self._write_extras(cid, latest)
                    with self._cache_lock:
                        if cid in self._cache:
                            self._cache[cid]["extra_keys"].add("context_usage")
                            self._cache[cid].setdefault("extras", {})["context_usage"] = usage
                            self._cache[cid]["updated_at"] = time.time()
        return usage

    def message_count(self, cid: str) -> int:
        return self._load_cache(cid).get("msg_count", 0)

    # ── Metadata ──────────────────────────────────────────────────────

    def get_metadata(self, cid: str) -> Optional[Dict]:
        if not self.exists(cid):
            return None
        c = self._load_cache(cid)
        return {"user_id": c.get("user_id", ""), "status": c.get("status", "idle"),
                "created_at": c.get("created_at", 0), "updated_at": c.get("updated_at", 0),
                "expires_at": c.get("expires_at", 0), "message_count": c.get("msg_count", 0)}

    # ── Extras ────────────────────────────────────────────────────────

    def get_extra_cached(self, cid: str, key: str, default: Any = None) -> Any:
        """Get extra from extras.json file."""
        key = self._canon_extra_key(key)
        data = self._read_extras(cid)
        if key == "context_usage":
            return self._repair_context_usage_from_transcript(cid, data) or default
        return data.get(key, default)

    def get_extra_snapshot(self, cid: str, key: str,
                           default: Any = None) -> Any:
        """Return a cache-only extra snapshot without disk IO or repair.

        UI polling paths use this to stay O(1). If the conversation cache is
        not warm yet, callers get ``default`` instead of forcing a transcript
        scan or waiting behind a writer lock.
        """
        key = self._canon_extra_key(key)
        with self._cache_lock:
            value = ((self._cache.get(cid) or {}).get("extras") or {}).get(
                key, default)
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, list):
            return list(value)
        return value

    def get_extra(self, cid: str, key: str, default: Any = None,
                  user_id: str = "") -> Any:
        if not self.exists(cid):
            return default
        key = self._canon_extra_key(key)
        data = self._read_extras(cid)
        if key == "context_usage":
            return self._repair_context_usage_from_transcript(cid, data) or default
        return data.get(key, default)

    def get_extras(self, cid: str, user_id: str = "") -> Optional[dict]:
        if not self.exists(cid):
            return None
        data = self._read_extras(cid)
        if "context_usage" in data:
            data["context_usage"] = self._repair_context_usage_from_transcript(
                cid, data)
        return dict(data)

    def set_extra(self, cid: str, key: str, value: Any,
                  user_id: str = "") -> bool:
        if not self.exists(cid):
            # File gone but extras may still exist — clean up cache
            with self._cache_lock:
                self._cache.pop(cid, None)
            return False
        key = self._canon_extra_key(key)
        lock = self._get_extras_lock(cid)
        with lock:
            data = self._read_extras(cid)
            data[key] = value
            self._write_extras(cid, data)
        # Update in-memory cache for list_conversations (title, updated_at)
        with self._cache_lock:
            if cid in self._cache:
                self._cache[cid]["extra_keys"].add(key)
                self._cache[cid].setdefault("extras", {})[key] = value
                if key == "title":
                    self._cache[cid]["title"] = value
                self._cache[cid]["updated_at"] = time.time()
        return True

    def _delete_cli_runtime_session_dirs(self, cid: str, provider: str,
                                         agent_name: str = "") -> int:
        """Delete runtime session dirs for one CLI provider/conv.

        Used when a PawFlow context edit invalidates the provider's session.
        The live process is evicted separately; once extras are cleared, every
        file under the targeted provider dir is stale history.
        """
        import shutil
        try:
            owner = self._cid_user.get(cid, "") or self.get_user_id(cid) or ""
        except Exception:
            owner = self._cid_user.get(cid, "") or ""
        if not owner:
            return 0
        from core import paths as _paths
        base_map = {
            "codex": _paths.CODEX_SESSIONS_DIR,
            "gemini": _paths.GEMINI_SESSIONS_DIR,
        }
        base = base_map.get(provider)
        if base is None:
            return 0
        safe_owner = owner.replace(":", "_").replace("/", "_").replace("\\", "_")
        conv_dir = base / safe_owner / cid.replace(":", "_")
        if agent_name:
            targets = [conv_dir / agent_name]
        else:
            try:
                targets = [p for p in conv_dir.iterdir() if p.is_dir()]
            except OSError:
                targets = []
        removed = 0
        for target in targets:
            try:
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                    removed += 1
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
        if removed:
            logger.info("Deleted %d %s runtime session dir(s) for %s%s",
                        removed, provider, cid[:8], f"/{agent_name}" if agent_name else "")
        return removed

    def invalidate_claude_sessions(self, cid: str) -> None:
        """Clear all claude-code session IDs for this conversation.

        Called when the user manually modifies context (delete message,
        manual compact, etc.). Forces a fresh session on next message.

        Also wipes the stale session jsonls + companion dirs on disk so
        they don't pile up indefinitely across invalidations.

        Live-session reuse: any warm CC proc for this conv is now
        operating on a stale view of history. Kill every live session
        bound to `cid` so the next turn spawns fresh.
        """
        extras = self.get_extras(cid) or {}
        # Session invalidation means the next CLI turn must rebuild from the
        # current PawFlow context on disk. Drop the context cache too, otherwise
        # a stale short private context can survive after the provider session
        # pointer was correctly cleared.
        self._invalidate_ctx_cache(cid)
        _had_any = False
        # Clear ALL CLI session pointers. With the pointer wiped, the next
        # turn starts a fresh session instead of resuming the now-stale one.
        for key in list(extras.keys()):
            if (key.startswith("claude_session:")
                    or key.startswith("codex_session:")
                    or key.startswith("codex_app_server_thread:")
                    or key.startswith("codex_app_pool_idx:")
                    or key.startswith("gemini_acp_session:")
                    or key.startswith("gemini_acp_pool_idx:")
                    or key.startswith("gemini_acp_session_version:")):
                self.set_extra(cid, key, "")
                logger.info("Invalidated %s for conv %s", key, cid[:8])
                _had_any = True
        # Wipe stale session files on disk (all jsonls + companion dirs
        # for this conv). Safe: we just cleared the "current" flags,
        # so every jsonl under sess_dir is now obsolete.
        try:
            owner = self._cid_user.get(cid, "") or self.get_user_id(cid) or ""
            if owner:
                from core import paths as _paths
                safe_owner = owner.replace(":", "_").replace("/", "_").replace("\\", "_")
                sanitized_cid = cid.replace(":", "_")
                sess_dir = _paths.CLAUDE_SESSIONS_DIR / safe_owner / sanitized_cid
                if sess_dir.is_dir():
                    self._prune_stale_cc_sessions(sess_dir, cid, wipe_all=True)
            self._delete_cli_runtime_session_dirs(cid, "codex")
            self._delete_cli_runtime_session_dirs(cid, "gemini")
        except Exception as _e:
            logger.debug("invalidate_claude_sessions disk prune failed for %s: %s",
                         cid[:8], _e)
        # Kill any warm CC / codex / gemini session running in this conv
        # — its view of history is now stale (edit/compact/branch-switch).
        try:
            from core.cc_live_registry import LiveSessionRegistry
            n = LiveSessionRegistry.instance().kill_and_evict_by_conv(
                cid, reason="invalidate_claude_sessions")
            if n:
                logger.info(
                    "Invalidated %d live CC session(s) for conv %s",
                    n, cid[:8])
        except Exception as _e:
            logger.debug(
                "invalidate_claude_sessions live-evict failed for %s: %s",
                cid[:8], _e)
        try:
            from core.codex_live_registry import CodexLiveRegistry
            n = CodexLiveRegistry.instance().kill_and_evict_by_conv(
                cid, reason="invalidate_claude_sessions")
            if n:
                logger.info(
                    "Invalidated %d live codex container(s) for conv %s",
                    n, cid[:8])
        except Exception as _e:
            logger.debug(
                "invalidate_claude_sessions codex-evict failed for %s: %s",
                cid[:8], _e)
        try:
            from core.gemini_live_registry import GeminiLiveRegistry
            n = GeminiLiveRegistry.instance().kill_and_evict_by_conv(
                cid, reason="invalidate_claude_sessions")
            if n:
                logger.info(
                    "Invalidated %d live gemini container(s) for conv %s",
                    n, cid[:8])
        except Exception as _e:
            logger.debug(
                "invalidate_claude_sessions gemini-evict failed for %s: %s",
                cid[:8], _e)

    def invalidate_claude_session_for_agent(self, cid: str,
                                             agent_name: str) -> None:
        """Clear the claude-code session for ONE agent, purging its
        jsonl + companion dir on disk.

        Per-agent variant of `invalidate_claude_sessions`. Used after
        PawFlow compact: we killed that agent's CC session and want its
        stale jsonl gone, without touching other agents' live sessions
        in the same conversation.

        Implementation deletes by exact sid path rather than going
        through `_prune_stale_cc_sessions`, because the latter returns
        early when `live_sids` is empty (its contract is "don't guess")
        and we'd just have cleared the only extra for a single-agent
        conversation.
        """
        if not agent_name:
            return
        self._invalidate_ctx_cache(cid, agent_name)
        # Clear the resume pointer for ALL three CLIs (claude / codex / gemini)
        # so the next turn for this (conv, agent) starts a fresh session
        # regardless of which CLI is configured. Symmetric with the all-agent
        # variant `invalidate_claude_sessions`.
        extras = self.get_extras(cid) or {}
        cleared_any = False
        for _k in (
                f"claude_session:{agent_name}",
                f"codex_session:{agent_name}",
                f"codex_app_server_thread:{agent_name}",
                f"codex_app_pool_idx:{agent_name}",
                f"gemini_acp_session:{agent_name}",
                f"gemini_acp_pool_idx:{agent_name}",
                f"gemini_acp_session_version:{agent_name}"):
            if extras.get(_k):
                self.set_extra(cid, _k, "")
                logger.info("Invalidated %s for conv %s", _k, cid[:8])
                cleared_any = True
        # CC-specific disk prune happens below by sid; codex/gemini runtime
        # dirs are removed by exact (conv, agent) because their resume pointers
        # have just been cleared.
        key = f"claude_session:{agent_name}"
        sid = str(extras.get(key) or "")
        if sid:
            try:
                owner = self._cid_user.get(cid, "")
                if owner:
                    from core import paths as _paths
                    import shutil as _shutil
                    sanitized_cid = cid.replace(":", "_")
                    sess_dir = _paths.CLAUDE_SESSIONS_DIR / owner / sanitized_cid
                    if sess_dir.is_dir():
                        for jf in sess_dir.rglob(f"projects/*/{sid}.jsonl"):
                            try:
                                jf.unlink()
                                logger.info("Pruned CC session jsonl %s for %s/%s",
                                            jf.name, cid[:8], agent_name)
                                companion = jf.with_suffix("")
                                if companion.is_dir():
                                    _shutil.rmtree(companion, ignore_errors=True)
                            except OSError:
                                pass
            except Exception as _e:
                logger.debug(
                    "invalidate_claude_session_for_agent disk prune failed "
                    "for %s/%s: %s", cid[:8], agent_name, _e)
        try:
            self._delete_cli_runtime_session_dirs(cid, "codex", agent_name)
            self._delete_cli_runtime_session_dirs(cid, "gemini", agent_name)
        except Exception as _e:
            logger.debug(
                "invalidate_claude_session_for_agent cli disk prune failed "
                "for %s/%s: %s", cid[:8], agent_name, _e)
        # Kill any warm CC / codex / gemini session for this (conv, agent)
        # pair so the next turn spawns fresh.
        try:
            from core.cc_live_registry import LiveSessionRegistry
            n = LiveSessionRegistry.instance().kill_and_evict_by_conv_agent(
                cid, agent_name,
                reason="invalidate_claude_session_for_agent")
            if n:
                logger.info(
                    "Invalidated %d live CC session(s) for %s/%s",
                    n, cid[:8], agent_name)
        except Exception as _e:
            logger.debug(
                "invalidate_claude_session_for_agent live-evict failed "
                "for %s/%s: %s", cid[:8], agent_name, _e)
        try:
            from core.codex_live_registry import CodexLiveRegistry
            n = CodexLiveRegistry.instance().kill_and_evict_by_conv_agent(
                cid, agent_name,
                reason="invalidate_claude_session_for_agent")
            if n:
                logger.info(
                    "Invalidated %d live codex container(s) for %s/%s",
                    n, cid[:8], agent_name)
        except Exception as _e:
            logger.debug(
                "invalidate_claude_session_for_agent codex-evict failed "
                "for %s/%s: %s", cid[:8], agent_name, _e)
        try:
            from core.gemini_live_registry import GeminiLiveRegistry
            n = GeminiLiveRegistry.instance().kill_and_evict_by_conv_agent(
                cid, agent_name,
                reason="invalidate_claude_session_for_agent")
            if n:
                logger.info(
                    "Invalidated %d live gemini container(s) for %s/%s",
                    n, cid[:8], agent_name)
        except Exception as _e:
            logger.debug(
                "invalidate_claude_session_for_agent gemini-evict failed "
                "for %s/%s: %s", cid[:8], agent_name, _e)

    # ── Bindings (repository associations) ──────────────────

    def _bindings_path(self, cid: str) -> Path:
        return self._conv_dir(cid) / "bindings.json"

    def get_bindings(self, cid: str) -> Dict[str, list]:
        """Read all bindings for a conversation.

        Returns dict like {"agents": [{"name": "x", "scope": "global"}, ...], ...}
        Takes the per-conv lock to serialize with set_bindings's atomic
        replace — otherwise an open read handle can block MoveFileEx on
        Windows.
        """
        path = self._bindings_path(cid)
        lock = self._get_conv_lock(cid)
        with lock:
            if not path.exists():
                return {}
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}

    def set_bindings(self, cid: str, bindings: Dict[str, list]) -> None:
        """Replace all bindings for a conversation.

        Locks the per-conv lock so no reader holds an open handle on the
        destination during the atomic rename.
        """
        path = self._bindings_path(cid)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(bindings, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        lock = self._get_conv_lock(cid)
        with lock:
            tmp.replace(path)

    def add_binding(self, cid: str, rtype: str, name: str,
                    scope: str = "global") -> None:
        """Add a single binding (idempotent)."""
        lock = self._get_conv_lock(cid)
        with lock:
            data = self.get_bindings(cid)
            entries = data.setdefault(rtype, [])
            if not any(e["name"] == name for e in entries):
                entries.append({"name": name, "scope": scope})
                self.set_bindings(cid, data)

    def remove_binding(self, cid: str, rtype: str, name: str) -> bool:
        """Remove a binding by name. Returns True if found and removed."""
        lock = self._get_conv_lock(cid)
        with lock:
            data = self.get_bindings(cid)
            entries = data.get(rtype, [])
            before = len(entries)
            entries = [e for e in entries if e["name"] != name]
            if len(entries) == before:
                return False
            data[rtype] = entries
            self.set_bindings(cid, data)
            return True

    def list_bound(self, cid: str, rtype: str) -> List[Dict]:
        """List all bound items of a given type for a conversation."""
        return self.get_bindings(cid).get(rtype, [])

    # ── Delete ────────────────────────────────────────────────────────

    def delete(self, cid: str, user_id: str = "") -> bool:
        import os, shutil, stat
        conv_dir = self._conv_dir(cid)
        if not conv_dir.is_dir():
            with self._cache_lock:
                self._cache.pop(cid, None)
            return False

        def _force_remove(func, path, _exc_info):
            """Force-remove read-only files (git pack objects on Windows)."""
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            func(path)

        lock = self._get_conv_lock(cid)
        extras_lock = self._get_extras_lock(cid)
        # Resolve owner BEFORE popping _cid_user so edit-guard and session
        # workdir cleanup can find it.
        _owner = user_id or self._cid_user.get(cid, "")
        with lock:
            # set_extra writes extras.json via a separate lock and atomic
            # extras.tmp -> extras.json rename. Hold that same lock while
            # removing the directory, otherwise delete can race the tmp file
            # creation and hit ENOTEMPTY during rmtree.
            with extras_lock:
                shutil.rmtree(conv_dir, onerror=_force_remove)
        with self._cache_lock:
            self._cache.pop(cid, None)
        self._conv_locks.pop(cid, None)
        self._extras_locks.pop(cid, None)
        self._cid_user.pop(cid, None)
        # Clean up all conv-scoped resources
        try:
            from core.file_store import FileStore
            FileStore.instance().delete_by(conversation_id=cid)
        except Exception:
            logger.debug("exception suppressed", exc_info=True)
        # Drop edit-guard state for every agent in this conv — otherwise
        # the read-hashes / failed-edit counters leak until size eviction.
        try:
            if _owner:
                from core.handlers._edit_guard import clear_conversation as _eg_clear
                _eg_clear(_owner, cid)
        except Exception:
            logger.debug("exception suppressed", exc_info=True)
        # Clean up Claude Code session workdir (sessions/claude/<user>/<cid>/).
        # Without this, per-task session dirs accumulate forever since
        # task sub-convs are deleted on completion but their CC session
        # state (credentials, project jsonl, mcp_bridge logs) is never
        # reclaimed.
        if _owner:
            try:
                from core import paths as _paths
                _sanitized_cid = cid.replace(":", "_")
                _sess_dir = _paths.CLAUDE_SESSIONS_DIR / _owner / _sanitized_cid
                if _sess_dir.is_dir():
                    shutil.rmtree(_sess_dir, onerror=_force_remove)
            except Exception as _se:
                logger.debug("Failed to remove CC session workdir for %s: %s",
                             cid, _se)
        self._invalidate_ctx_cache(cid)
        return True

    def edit_message(self, cid: str, msg_id: str, content: Any,
                     role: str = "", user_id: str = "") -> int:
        """Edit a message by msg_id in transcript + shared + all agent contexts."""
        if not msg_id or not self.exists(cid):
            return 0

        lock = self._get_conv_lock(cid)
        updated = 0

        def _rewrite_jsonl(path: Path) -> int:
            log = SegmentedJsonl(path)
            if not log.exists():
                return 0
            changed = 0

            def _transform(line: Dict[str, Any]) -> Dict[str, Any]:
                nonlocal changed
                if (line.get("msg_id") == msg_id
                        or (line.get("role") == "sub_agent_trace"
                            and line.get("trace_id") == msg_id)):
                    line["content"] = content
                    if role:
                        line["role"] = role
                    changed += 1
                return line

            log.rewrite(_transform)
            return changed

        with lock:
            updated += _rewrite_jsonl(self._transcript_path(cid))
            _rewrite_jsonl(self._shared_ctx_path(cid))
            conv_dir = self._conv_dir(cid)
            if conv_dir.is_dir():
                for entry in conv_dir.iterdir():
                    if entry.is_dir() and self._jsonl_exists(entry / "context.jsonl"):
                        _rewrite_jsonl(entry / "context.jsonl")

        if updated:
            with self._cache_lock:
                self._cache.pop(cid, None)
            self._invalidate_ctx_cache(cid)
            self._load_cache(cid)
            self.invalidate_claude_sessions(cid)
        return updated

    def delete_message(self, cid: str, msg_id: str = "", index: int = -1,
                       user_id: str = "") -> bool:
        """Delete a message by msg_id from transcript + all contexts. Atomic."""
        if not msg_id and index < 0:
            return False
        if not self.exists(cid):
            return False

        # If we only have index, resolve to msg_id first
        if not msg_id and index >= 0:
            def _find_id(lines):
                count = 0
                for line in lines:
                    if line.get("t") == "msg" and not line.get("private"):
                        if count == index:
                            return line.get("msg_id", "")
                        count += 1
                return ""
            msg_id = self._read(cid, _find_id)
            if not msg_id:
                return False

        removed = self._remove_msg_ids_from_files(cid, {msg_id})
        return removed > 0

    def delete_messages(self, cid: str, msg_ids: list,
                        user_id: str = "") -> int:
        """Delete multiple messages by msg_id. Returns count of removed messages."""
        if not msg_ids or not self.exists(cid):
            return 0
        return self._remove_msg_ids_from_files(cid, set(msg_ids))

    def _remove_msg_ids_from_files(self, cid: str, ids: set) -> int:
        """Remove messages by msg_id from transcript + shared + all agent contexts."""
        lock = self._get_conv_lock(cid)
        removed = 0

        def _rewrite_jsonl(path: Path) -> int:
            """Rewrite a logical JSONL stream, removing rows with matching msg_id.

            Also removes:
              - sub_agent_trace messages whose trace_id matches an id in
                `ids` (legacy traces persisted without msg_id used the
                trace_id as the deletion key);
              - their associated trace_update lines (orphan otherwise).
            """
            log = SegmentedJsonl(path)
            if not log.exists():
                return 0
            count = 0
            trace_ids_to_drop = set()
            for line in log.iter_rows():
                if line.get("role") != "sub_agent_trace":
                    continue
                _tid = line.get("trace_id", "")
                if _tid and (line.get("msg_id") in ids or _tid in ids):
                    trace_ids_to_drop.add(_tid)

            def _transform(line: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                nonlocal count
                if line.get("msg_id") in ids:
                    count += 1
                    return None
                # Legacy sub_agent_trace without msg_id — match on trace_id
                if (line.get("role") == "sub_agent_trace"
                        and line.get("trace_id") in ids):
                    count += 1
                    return None
                # Drop orphan trace_update lines for deleted traces
                if (line.get("t") == "trace_update"
                        and line.get("trace_id") in trace_ids_to_drop):
                    return None
                return line

            log.rewrite(_transform)
            return count

        with lock:
            # 1. Transcript
            removed += _rewrite_jsonl(self._transcript_path(cid))
            # 2. Shared context
            _rewrite_jsonl(self._shared_ctx_path(cid))
            # 3. All agent contexts
            conv_dir = self._conv_dir(cid)
            if conv_dir.is_dir():
                for entry in conv_dir.iterdir():
                    if entry.is_dir() and self._jsonl_exists(entry / "context.jsonl"):
                        _rewrite_jsonl(entry / "context.jsonl")

        with self._cache_lock:
            self._cache.pop(cid, None)
        if removed:
            self._invalidate_ctx_cache(cid)  # clear ALL agent ctx caches
            self._load_cache(cid)
            self.invalidate_claude_sessions(cid)
        return removed

    # ── List ──────────────────────────────────────────────────────────

    def list_conversations(self, user_id: str = "") -> List[Dict]:
        self._ensure_loaded()
        result = []
        with self._cache_lock:
            for cid, c in self._cache.items():
                if ":task:" in cid:
                    continue
                if user_id and c.get("user_id") and c["user_id"] != user_id:
                    continue
                if c.get("expires_at", 0) > 0 and c["expires_at"] < time.time():
                    continue
                result.append({
                    "conversation_id": cid,
                    "title": c.get("title", ""),
                    "preview": c.get("preview", ""),
                    "message_count": c.get("msg_count", 0),
                    "status": c.get("status", "idle"),
                    "user_id": c.get("user_id", ""),
                    "created_at": c.get("created_at", 0),
                    "updated_at": c.get("updated_at", 0),
                    "expires_at": c.get("expires_at", 0),
                })
        result.sort(key=lambda x: x["updated_at"], reverse=True)
        return result

    def list_agent_contexts(self, cid: str) -> Dict[str, str]:
        c = self._load_cache(cid)
        result = {"*": "messages"}
        for a in c.get("agents", set()):
            result[a] = "diverged"
        return result

    # ── Display trace ─────────────────────────────────────────────────

    def create_display_trace(self, cid: str, trace_id: str,
                             source: Dict, user_id: str = "") -> bool:
        import uuid as _uuid
        lock = self._get_conv_lock(cid)
        # msg_id is required for the context editor's delete path
        # (selection sends msg_ids; without one the row is not deletable).
        with lock:
            line = self._stamp_line(cid, {
                "t": "msg", "role": "sub_agent_trace", "display_only": True,
                "trace_id": trace_id, "source": source, "content": "",
                "trace": [],
            })
            self._transcript_log(cid).append_dicts([line])
            self._notify_bg_transcript_chars(
                cid, self._row_payload_chars(line))
        return True

    def append_display_trace(self, cid: str, trace_id: str,
                             entry_data: Dict, content_update: str = "") -> bool:
        entry_data.setdefault("ts", time.time())
        lock = self._get_conv_lock(cid)
        with lock:
            line = self._stamp_line(cid, {
                "t": "trace_update",
                "trace_id": trace_id,
                "entry": entry_data,
                "content_update": content_update,
            })
            self._transcript_log(cid).append_dicts([line])
            self._notify_bg_transcript_chars(
                cid, self._row_payload_chars(line))
        return True

    # ── Cleanup ───────────────────────────────────────────────────────

    def _prune_cli_agent_runtime(self, provider: str, agent_dir: Path,
                                 session_id: str) -> int:
        """Prune old provider JSONL sessions inside a live agent runtime dir."""
        if not session_id or not agent_dir.is_dir():
            return 0
        removed = 0
        if provider == "codex-app-server":
            for path in agent_dir.glob(".codex/sessions/**/*.jsonl"):
                try:
                    if session_id not in path.name:
                        path.unlink()
                        removed += 1
                except Exception:
                    logger.debug("exception suppressed", exc_info=True)
        elif provider == "gemini":
            for path in agent_dir.glob(".gemini/tmp/gemini/chats/*.jsonl"):
                try:
                    current = False
                    with open(path, "r", encoding="utf-8", errors="replace") as fh:
                        for line in fh:
                            if "sessionId" not in line:
                                continue
                            try:
                                rec = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            if str(rec.get("sessionId") or "") == session_id:
                                current = True
                            break
                    if not current:
                        path.unlink()
                        removed += 1
                except Exception:
                    logger.debug("exception suppressed", exc_info=True)
        return removed

    def cleanup_orphan_cli_sessions(self) -> int:
        """Remove stale Codex/Gemini runtime dirs and old non-current JSONLs."""
        import shutil
        try:
            from core import paths as _paths
        except Exception:
            return 0
        self._ensure_loaded()
        with self._cache_lock:
            live_sanitized = {cid.replace(":", "_"): cid
                              for cid in self._cache.keys()}
        removed = 0
        conv_base = _paths.CONVERSATIONS_DIR
        providers = (
            ("codex-app-server", _paths.CODEX_SESSIONS_DIR, "codex_app_server_thread:"),
            ("gemini", _paths.GEMINI_SESSIONS_DIR, "gemini_acp_session:"),
        )
        for provider, base, key_prefix in providers:
            if not base.is_dir():
                continue
            for user_dir in base.iterdir():
                if not user_dir.is_dir():
                    continue
                for conv_dir in user_dir.iterdir():
                    if not conv_dir.is_dir():
                        continue
                    live_cid = live_sanitized.get(conv_dir.name)
                    if not live_cid:
                        if (conv_base / user_dir.name / conv_dir.name).is_dir():
                            live_cid = conv_dir.name.replace("__", ":")
                        else:
                            shutil.rmtree(conv_dir, ignore_errors=True)
                            removed += 1
                            logger.info("Removed orphan %s session dir: %s/%s",
                                        provider, user_dir.name, conv_dir.name)
                            continue
                    extras = self.get_extras(live_cid) or {}
                    for agent_dir in list(conv_dir.iterdir()):
                        if not agent_dir.is_dir():
                            continue
                        sid = str(extras.get(f"{key_prefix}{agent_dir.name}") or "")
                        if not sid:
                            shutil.rmtree(agent_dir, ignore_errors=True)
                            removed += 1
                            logger.info("Removed stale %s agent session dir: %s/%s/%s",
                                        provider, user_dir.name, conv_dir.name, agent_dir.name)
                            continue
                        removed += self._prune_cli_agent_runtime(provider, agent_dir, sid)
        return removed

    def vacuum(self, cid: str) -> dict:
        """Manual vacuum — no-op (extras are now atomic JSON, contexts are separate files)."""
        return {"status": "ok"}

    def cleanup(self) -> int:
        self._ensure_loaded()
        removed = 0
        now = time.time()
        with self._cache_lock:
            expired = [cid for cid, c in self._cache.items()
                       if c.get("expires_at", 0) > 0 and c["expires_at"] < now]
        for cid in expired:
            self.delete(cid)
            removed += 1
        removed += self.cleanup_orphan_claude_sessions()
        removed += self.cleanup_orphan_cli_sessions()
        return removed

    def cleanup_orphan_claude_sessions(self) -> int:
        """Remove Claude Code session workdirs whose conversation no
        longer exists.

        Task sub-convs used to leak their session dirs when the sub-conv
        was deleted but the corresponding sessions/claude/<user>/<cid>/
        tree was left behind (credentials, project jsonl, mcp_bridge
        log). We now clean up on delete(), but existing installs may
        still have piles of orphans — this method reclaims them.

        Returns the number of orphan session dirs removed.
        """
        import shutil
        try:
            from core import paths as _paths
        except Exception:
            return 0
        base = _paths.CLAUDE_SESSIONS_DIR
        if not base.is_dir():
            return 0
        conv_base = _paths.CONVERSATIONS_DIR
        self._ensure_loaded()
        # Map sanitized-name -> real cid so we can both filter live
        # convs AND look up their extras (for stale-session pruning).
        with self._cache_lock:
            live_sanitized = {cid.replace(":", "_"): cid
                              for cid in self._cache.keys()}
        removed = 0
        for user_dir in base.iterdir():
            if not user_dir.is_dir():
                continue
            for sess_dir in user_dir.iterdir():
                if not sess_dir.is_dir():
                    continue
                # _compact / _memory_extract are one-shot helpers — never
                # tied to a live conv, always safe to wipe as a safety net
                # in case the immediate post-use cleanup was skipped.
                _is_one_shot = sess_dir.name.startswith("_")
                live_cid = (None if _is_one_shot
                            else live_sanitized.get(sess_dir.name))
                if live_cid:
                    # Conv is alive — don't nuke the dir, but prune stale
                    # CC session jsonls inside. Every CC turn creates a
                    # new <uuid>.jsonl under claude/projects/-workspace/
                    # and only the one referenced by extras'
                    # claude_session:<agent> is current. The rest pile
                    # up indefinitely (user reported 90+ per live conv).
                    removed += self._prune_stale_cc_sessions(
                        sess_dir, live_cid)
                    continue
                # SAFETY NET: before rmtree'ing, double-check that no
                # conversation directory with this sanitized cid exists
                # on disk. The in-memory cache can be incomplete at boot
                # (observed: cleanup fires via the ContinuousFlowExecutor
                # boot hook before/while conversations are being scanned,
                # cache empty → every live session dir classified as
                # orphan → wiped. Main conv's extras['claude_session']
                # then pointed at a jsonl that no longer existed, CC
                # --resume failed with "No conversation found", fallback
                # to a new session blew away the agent's working memory).
                # Trusting the disk here is authoritative — if the conv
                # dir exists, the CC session is NOT an orphan regardless
                # of cache state.
                if not _is_one_shot:
                    _conv_dir = conv_base / user_dir.name / sess_dir.name
                    if _conv_dir.is_dir():
                        logger.warning(
                            "[cc-cleanup] refusing to delete %s/%s: "
                            "conv dir exists on disk but wasn't in "
                            "_cache at cleanup time (cache race with "
                            "boot-time _ensure_loaded). Pruning stale "
                            "jsonls inside instead.",
                            user_dir.name, sess_dir.name)
                        # Best-effort: prune old jsonls using the real
                        # cid (conv_dir.name with __ → : reverse).
                        _recovered_cid = sess_dir.name.replace("__", ":")
                        try:
                            removed += self._prune_stale_cc_sessions(
                                sess_dir, _recovered_cid)
                        except Exception:
                            logger.debug("exception suppressed", exc_info=True)
                        continue
                try:
                    shutil.rmtree(sess_dir, ignore_errors=True)
                    removed += 1
                    logger.info("Removed %s CC session dir: %s/%s",
                                "one-shot" if _is_one_shot else "orphan",
                                user_dir.name, sess_dir.name)
                except Exception as _e:
                    logger.debug("Failed to remove orphan session %s: %s",
                                 sess_dir, _e)
        return removed

    def _prune_stale_cc_sessions(self, sess_dir: Path, cid: str,
                                  wipe_all: bool = False) -> int:
        """Delete CC session jsonls that are no longer the current session.

        CC writes session transcripts to
          <sess_dir>/<agent>/projects/-workspace/<session_id>.jsonl
        and often drops a companion workdir <session_id>/ next to it.
        Current sessions are listed as extras["claude_session:<agent>"].
        Anything else is dead weight and can go - both the .jsonl and
        the companion dir sharing the same stem.

        wipe_all=True: ignore extras (live_sids={}) - used by
        invalidate_claude_sessions when the caller has deliberately
        killed the current session(s) and wants to nuke all jsonls +
        companion dirs for this conv.

        Source of truth: extras["claude_session:<agent>"].
        No mtime heuristic - the only callers are (a) the boot-time
        orphan sweep, where no live CC process exists, and (b)
        invalidate_claude_sessions with wipe_all=True, where the caller
        has already killed the session. A hardcoded grace window would
        be arbitrary AND wrong: too short, it wipes a live session
        whose stall budget is larger; too long, it keeps real orphans
        forever. extras is authoritative - trust it.
        """
        import shutil
        # Collect live session ids from extras (one per agent).
        live_sids = set()
        if not wipe_all:
            try:
                extras = self.get_extras(cid) or {}
                for k, v in extras.items():
                    if isinstance(k, str) and k.startswith("claude_session:") and v:
                        live_sids.add(str(v))
            except Exception:
                return 0
            if not live_sids:
                return 0  # nothing known - don't guess, leave alone
        removed = 0
        for jf in sess_dir.rglob("projects/*/*.jsonl"):
            try:
                stem = jf.stem
                if stem in live_sids:
                    continue
                jf.unlink()
                removed += 1
                # Companion workdir <sid>/ next to <sid>.jsonl
                companion = jf.with_suffix("")
                if companion.is_dir():
                    shutil.rmtree(companion, ignore_errors=True)
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
        if removed:
            logger.info("Pruned %d stale CC session jsonl(s) in %s",
                        removed, sess_dir.name)
        return removed

    def count(self) -> int:
        self._ensure_loaded()
        with self._cache_lock:
            return len(self._cache)

    # ── Compat ────────────────────────────────────────────────────────

    @staticmethod
    def filter_display_only(msgs: List[Dict]) -> List[Dict]:
        return [m for m in msgs if not (isinstance(m, dict) and m.get("display_only"))]

    def set_metadata_field(self, cid: str, field: str, value: Any) -> bool:
        return self.set_extra(cid, field, value)
