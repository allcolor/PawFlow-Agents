"""Immutable standard-API checkpoints and exact conversation forks."""

import logging
import shutil
import subprocess  # nosec B404
import time


logger = logging.getLogger(__name__)


class _CsCheckpointMixin:
    """Verified off-branch checkpoints composed into ConversationStore."""

    @staticmethod
    def _api_checkpoint_ref(checkpoint_id: str) -> str:
        normalized = str(checkpoint_id or "").strip().lower()
        if (len(normalized) not in {40, 64}
                or any(char not in "0123456789abcdef" for char in normalized)):
            raise ValueError("Invalid API checkpoint identity")
        return f"refs/pawflow/checkpoints/{normalized}"

    def create_api_checkpoint(self, cid: str, message: str = "") -> str:
        """Capture and verify an immutable durable-state commit off-branch."""

        conv_dir = self._conv_dir(cid)
        if not conv_dir.is_dir() or not (conv_dir / ".git").is_dir():
            raise ValueError(f"Conversation {cid[:16]} has no Git history")
        if self.is_temporary(cid):
            return ""
        with self._get_conv_lock(cid):
            self.flush_append_handles(cid)
            existing = self._git_snapshot_files(cid)
            if existing:
                self._git(cid, "add", "--", *existing, timeout=30)
            tree = self._git(
                cid, "write-tree", timeout=30).stdout.strip()
            checkpoint_id = self._git(
                cid,
                "commit-tree",
                tree,
                "-m",
                str(message or "standard API checkpoint"),
                timeout=30,
            ).stdout.strip().lower()
            checkpoint_ref = self._api_checkpoint_ref(checkpoint_id)
            zero = "0" * len(checkpoint_id)
            created = self._git(
                cid,
                "update-ref",
                checkpoint_ref,
                checkpoint_id,
                zero,
                check=False,
                timeout=30,
            )
            if created.returncode != 0 and not self.verify_api_checkpoint(
                    cid, checkpoint_id):
                raise RuntimeError("Could not publish immutable API checkpoint")
            if not self.verify_api_checkpoint(cid, checkpoint_id):
                self.discard_api_checkpoint(cid, checkpoint_id)
                raise RuntimeError("Could not verify immutable API checkpoint")
            return checkpoint_id

    def verify_api_checkpoint(self, cid: str, checkpoint_id: str) -> bool:
        """Return whether the immutable checkpoint ref resolves to its commit."""

        try:
            checkpoint_ref = self._api_checkpoint_ref(checkpoint_id)
        except ValueError:
            return False
        conv_dir = self._conv_dir(cid)
        if not (conv_dir / ".git").is_dir():
            return False
        try:
            resolved = self._git(
                cid,
                "rev-parse",
                "--verify",
                f"{checkpoint_ref}^{{commit}}",
                check=False,
                timeout=30,
            )
            return (
                resolved.returncode == 0
                and resolved.stdout.strip().lower() == checkpoint_id.lower()
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def discard_api_checkpoint(self, cid: str, checkpoint_id: str) -> bool:
        """Delete exactly one checkpoint ref, leaving branch history untouched."""

        if not self.verify_api_checkpoint(cid, checkpoint_id):
            return False
        checkpoint_ref = self._api_checkpoint_ref(checkpoint_id)
        result = self._git(
            cid,
            "update-ref",
            "-d",
            checkpoint_ref,
            checkpoint_id.lower(),
            check=False,
            timeout=30,
        )
        return result.returncode == 0 and not self.verify_api_checkpoint(
            cid, checkpoint_id)

    def fork_at_checkpoint(
            self,
            cid: str,
            checkpoint_id: str,
            *,
            user_id: str,
    ) -> str:
        """Clone a conversation at one verified checkpoint, not its live head."""

        if not str(user_id or "").strip():
            raise ValueError("Fork owner is required")
        checkpoint_ref = self._api_checkpoint_ref(checkpoint_id)
        if not self.verify_api_checkpoint(cid, checkpoint_id):
            raise ValueError("API checkpoint is unavailable")
        source_dir = self._conv_dir(cid)
        new_cid = self.generate_id()
        dest_dir = (
            self._store_dir / self._safe_name(user_id) / self._safe_name(new_cid))
        if dest_dir.exists():
            raise RuntimeError("Fork destination already exists")
        try:
            subprocess.run(  # nosec B603, B607
                ["git", "clone", "--no-checkout", str(source_dir), str(dest_dir)],
                capture_output=True, text=True, check=True, timeout=30,
            )
            subprocess.run(  # nosec B603, B607
                [
                    "git", "-C", str(dest_dir), "fetch", "origin",
                    f"+{checkpoint_ref}:{checkpoint_ref}",
                ],
                capture_output=True, text=True, check=True, timeout=30,
            )
            subprocess.run(  # nosec B603, B607
                [
                    "git", "-C", str(dest_dir), "checkout", "-B", "live",
                    checkpoint_id.lower(),
                ],
                capture_output=True, text=True, check=True, timeout=30,
            )
            subprocess.run(  # nosec B603, B607
                ["git", "-C", str(dest_dir), "update-ref", "-d", checkpoint_ref],
                capture_output=True, text=True, check=False, timeout=10,
            )
            subprocess.run(  # nosec B603, B607
                ["git", "-C", str(dest_dir), "remote", "remove", "origin"],
                capture_output=True, text=True, check=False, timeout=10,
            )
            subprocess.run(  # nosec B603, B607
                ["git", "-C", str(dest_dir), "config", "user.email", "pawflow@local"],
                capture_output=True, text=True, check=True, timeout=10,
            )
            subprocess.run(  # nosec B603, B607
                ["git", "-C", str(dest_dir), "config", "user.name", "PawFlow"],
                capture_output=True, text=True, check=True, timeout=10,
            )
        except (subprocess.CalledProcessError, FileNotFoundError,
                subprocess.TimeoutExpired) as exc:
            shutil.rmtree(dest_dir, ignore_errors=True)
            raise RuntimeError(f"Checkpoint fork failed: {exc}") from exc

        self._cid_user[new_cid] = user_id
        try:
            extras = self._read_extras(new_cid)
            extras["forked_from"] = cid
            extras["forked_from_checkpoint"] = checkpoint_id.lower()
            extras["_meta_user_id"] = user_id
            extras["_meta_created_at"] = time.time()
            self._write_extras(new_cid, extras)
            source_title = self.get_extra(cid, "title") or "Conversation"
            self.set_extra(new_cid, "title", f"{source_title} (fork)")
            self._reload_cache(new_cid)
            self.git_snapshot(new_cid, "forked from standard API checkpoint")
        except Exception:
            self._cid_user.pop(new_cid, None)
            shutil.rmtree(dest_dir, ignore_errors=True)
            raise
        logger.info(
            "[convstore] checkpoint-forked %s at %s -> %s",
            cid[:8], checkpoint_id[:12], new_cid[:8],
        )
        return new_cid
