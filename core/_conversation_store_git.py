"""ConversationStore git history/retention/branches/tags/fork."""

import json
import logging
import shutil
import subprocess  # nosec B404
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.segmented_jsonl import SegmentedJsonl

logger = logging.getLogger(__name__)
# Split out of conversation_store.py for the <=800-line rule; composed back into
# ConversationStore (invariant 2: MRO/shared state on the host).

from core._conversation_store_base import (  # noqa: F401,E402
    _CTX_CACHE_MAX_MESSAGES, _CTX_CACHE_MAX_CHARS, _CTX_CACHE_MAX_CONVS, _CONV_LOCK_DIAG_MS, _GIT_RETENTION_DAYS, _GIT_RETENTION_COMMITS, _GIT_RETENTION_INTERVAL_SEC, _HOT_METADATA_FLUSH_INTERVAL_SEC, _HOT_METADATA_FLUSH_MSG_DELTA, _HOT_METADATA_KEYS, _HOT_METADATA_EXECUTOR, _GIT_RETENTION_EXECUTOR, _GIT_RETENTION_RUNNING, _GIT_RETENTION_RUNNING_LOCK, ConversationLockedError, _ConversationTimedRLock)
import core._conversation_store_base as _csb  # noqa: E402


class _CsGitMixin:
    """git history/retention/branches/tags/fork."""

    @staticmethod
    def _jsonl_exists(path: Path) -> bool:
        return SegmentedJsonl(path).exists()

    def _extras_path(self, cid: str) -> Path:
        return self._conv_dir(cid) / "extras.json"

    def _git(self, cid: str, *args: str, check: bool = True,
             timeout: Optional[float] = None) -> subprocess.CompletedProcess:
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

    def _maybe_schedule_git_retention(self, cid: str) -> None:
        """Schedule Git retention off the snapshot hot path when interval is due."""
        if _csb._GIT_RETENTION_DAYS <= 0 and _csb._GIT_RETENTION_COMMITS <= 0:
            return
        if _csb._GIT_RETENTION_INTERVAL_SEC <= 0:
            return
        try:
            marker = self._conv_dir(cid) / ".git" / "pawflow-retention-last-run"
            if marker.exists() and time.time() - marker.stat().st_mtime < _csb._GIT_RETENTION_INTERVAL_SEC:
                return
        except Exception:
            return
        with _GIT_RETENTION_RUNNING_LOCK:
            if cid in _GIT_RETENTION_RUNNING:
                return
            _GIT_RETENTION_RUNNING.add(cid)
        try:
            _csb._GIT_RETENTION_EXECUTOR.submit(self._git_retention_worker, cid)
        except Exception:
            with _GIT_RETENTION_RUNNING_LOCK:
                _GIT_RETENTION_RUNNING.discard(cid)
            logger.debug("git retention scheduling failed for %s", cid[:8], exc_info=True)

    def _git_retention_worker(self, cid: str) -> None:
        try:
            result = self._maybe_prune_git_history(cid, force=False)
            status = result.get("status") if isinstance(result, dict) else ""
            if status not in ("skipped", "missing"):
                logger.info("[convstore] background git retention for %s: %s",
                            cid[:8], status)
        finally:
            with _GIT_RETENTION_RUNNING_LOCK:
                _GIT_RETENTION_RUNNING.discard(cid)

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

        if _csb._GIT_RETENTION_DAYS <= 0 and _csb._GIT_RETENTION_COMMITS <= 0:
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
                    and _csb._GIT_RETENTION_INTERVAL_SEC > 0):
                age = now - marker.stat().st_mtime
                if age < _csb._GIT_RETENTION_INTERVAL_SEC:
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
            if _csb._GIT_RETENTION_DAYS > 0:
                cutoff = int(now - _csb._GIT_RETENTION_DAYS * 86400)
                for idx, (_h, ts) in enumerate(commits):
                    if ts >= cutoff:
                        keep_start = min(keep_start, idx)
                        break
            if _csb._GIT_RETENTION_COMMITS > 0:
                keep_start = min(keep_start, max(0, len(commits) - _csb._GIT_RETENTION_COMMITS))
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
        existing = {f for f in files if (conv_dir / f).exists()}
        tracked: set[str] = set()
        if (conv_dir / ".git").exists():
            try:
                out = self._git(cid, "ls-files", "-z", check=False,
                                timeout=30).stdout
                for rel in out.split("\0"):
                    if not rel:
                        continue
                    top = rel.split("/", 1)[0]
                    if rel in files or top in files:
                        tracked.add(rel if rel in files else top)
            except Exception:
                logger.debug("git tracked snapshot scan failed for %s",
                             cid[:8], exc_info=True)
        return [f for f in files if f in existing or f in tracked]

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
        with self._cache_lock:
            self._agent_ctx_exists_cache = {
                key for key in self._agent_ctx_exists_cache
                if key[0] != cid
            }
        self._invalidate_pyramid_cache(cid)

    def _reset_jsonl_runtime_after_history_change(self, cid: str) -> None:
        conv_dir = self._conv_dir(cid)
        SegmentedJsonl.close_append_handles(conv_dir)
        SegmentedJsonl.invalidate_index_cache(conv_dir)

    def is_temporary(self, cid: str) -> bool:
        """A conversation is temporary iff it carries a non-zero TTL.

        Temporary conversations — e.g. the per-session conversations bots
        create with a sliding TTL — are deliberately excluded from durable
        side effects: they are never git-historized (see ``git_snapshot``)
        and never feed auto-memory (see ``auto_extract_memories``).
        """
        try:
            expires = self.get_extra(cid, "_meta_expires_at", default=0) or 0
            return float(expires) > 0
        except (TypeError, ValueError):
            return False

    def git_snapshot(self, cid: str, message: str = "",
                     command_timeout: Optional[float] = None):
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
        # Temporary (TTL-bearing) conversations are never historized.
        if self.is_temporary(cid):
            return
        try:
            self.flush_append_handles(cid)
            # Selective add: durable transcript/shared/extras only. Agent
            # contexts and summaries are derived and intentionally omitted.
            # Do not run retention or derived-state cleanup here: this method
            # is called after every turn, and those maintenance paths can take
            # tens of seconds on Windows/WSL. Rollback still works from the
            # durable files; cleanup belongs to explicit retention/init paths.
            existing = self._git_snapshot_files(cid)
            if not existing:
                return
            timeout = None if command_timeout is None else max(0.25, float(command_timeout))
            self._git(cid, "add", "--", *existing, check=False,
                      timeout=timeout)
            # Commit only if something staged
            diff = self._git(cid, "diff", "--cached", "--quiet",
                             check=False, timeout=timeout)
            if diff.returncode == 0:
                return  # nothing staged
            msg = message or f"snapshot {time.strftime('%H:%M:%S')}"
            self._git(cid, "commit", "-m", msg, "-q", timeout=timeout)
            logger.debug("[convstore] git snapshot for %s: %s", cid[:8], msg)
            self._maybe_schedule_git_retention(cid)
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
            self._reset_jsonl_runtime_after_history_change(cid)
            # Restore the durable conversation tree exactly as it existed at
            # the target commit while keeping the current branch checked out.
            # `git checkout <hash> -- .` restores files present in the target
            # but can leave later tracked files behind; read-tree resets the
            # index/worktree to the target tree so deletions are represented in
            # the rollback commit as well.
            self._git(cid, "read-tree", "--reset", "-u", commit_hash)
            self._purge_derived_state_after_history_change(cid)
            # Reload cache from rolled-back state
            self._reset_jsonl_runtime_after_history_change(cid)
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
            head = (git_dir / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
            prefix = "ref: refs/heads/"
            if head.startswith(prefix):
                return head[len(prefix):]
            return ""
        except OSError:
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
            self._reset_jsonl_runtime_after_history_change(cid)
            self._git(cid, "checkout", branch_name)
            self._purge_derived_state_after_history_change(cid)
            self._reset_jsonl_runtime_after_history_change(cid)
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
                count = 0
                for line in r.stdout.strip().split("\n"):
                    if not line.strip():
                        continue
                    try:
                        if json.loads(line).get("role"):
                            count += 1
                    except json.JSONDecodeError:
                        continue
                return count
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
