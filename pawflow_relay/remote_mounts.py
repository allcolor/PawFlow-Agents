"""Relay-side rclone mount reconciliation for conversation remote FS bindings."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess  # nosec B404
import sys
import threading
from pathlib import Path
from typing import Any, Dict


class RemoteMountManager:
    """Apply server-provided rclone mount manifests inside a relay runtime."""

    def __init__(self, remote_root: str = "/remote",
                 state_dir: str = "/tmp/pawflow_remote_mounts"):  # nosec B108 - relay-local rclone state dir.
        self.remote_root = remote_root
        self.state_dir = Path(state_dir)
        self.config_path = self.state_dir / "rclone.conf"
        self._active: Dict[str, str] = {}
        self._lock = threading.Lock()

    def reconcile(self, manifest: Dict[str, Any]) -> None:
        with self._lock:
            desired = {}
            for mount in manifest.get("mounts") or []:
                name = mount.get("remote_name") or ""
                if not name:
                    continue
                if mount.get("error"):
                    sys.stderr.write(
                        f"[RemoteFS] skipping {name}: {mount.get('error')}\n")
                    continue
                desired[name] = mount

            for name in sorted(set(self._active) - set(desired)):
                self._unmount(name)
                self._active.pop(name, None)

            self._write_config(desired)
            for name, mount in desired.items():
                digest = self._digest_mount(mount)
                if self._active.get(name) == digest and self._is_mounted(name):
                    continue
                if name in self._active:
                    self._unmount(name)
                if self._mount(name, mount):
                    self._active[name] = digest

    def _digest_mount(self, mount: Dict[str, Any]) -> str:
        payload = repr({
            "remote_name": mount.get("remote_name"),
            "mount_path": mount.get("mount_path"),
            "mode": mount.get("mode"),
            "rclone_config": mount.get("rclone_config"),
        }).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _sudo(self, argv: list[str]) -> list[str]:
        if os.geteuid() == 0:
            return argv
        if shutil.which("sudo"):
            return ["sudo", "-n"] + argv
        return argv

    def _run(self, argv: list[str], what: str) -> bool:
        try:
            proc = subprocess.run(argv, capture_output=True, text=True)  # nosec B603
        except Exception as exc:
            sys.stderr.write(f"[RemoteFS] {what} failed: {exc}\n")
            return False
        if proc.returncode != 0:
            sys.stderr.write(
                f"[RemoteFS] {what} failed rc={proc.returncode} "
                f"stderr={proc.stderr.strip()!r}\n")
            return False
        return True

    def _ensure_root(self) -> bool:
        return self._run(self._sudo(["mkdir", "-p", self.remote_root]),
                         f"mkdir {self.remote_root}")

    def _ensure_mountpoint(self, target: Path) -> bool:
        if not self._run(self._sudo(["mkdir", "-p", str(target)]),
                         f"mkdir {target}"):
            return False
        uid, gid = os.geteuid(), os.getegid()
        if uid != 0:
            return self._run(self._sudo(["chown", f"{uid}:{gid}", str(target)]),
                             f"chown {target}")
        return True

    def _write_config(self, mounts: Dict[str, Dict[str, Any]]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        parts = []
        for name, mount in sorted(mounts.items()):
            cfg = mount.get("rclone_config") or {}
            raw = cfg.get("_raw") if isinstance(cfg, dict) else ""
            if raw:
                section = raw.strip()
                if not section.startswith("["):
                    section = f"[{name}]\n" + section
                parts.append(section)
                continue
            lines = [f"[{name}]"]
            for key, value in sorted((cfg or {}).items()):
                if key.startswith("_") or value is None:
                    continue
                lines.append(f"{key} = {value}")
            parts.append("\n".join(lines))
        self.config_path.write_text("\n\n".join(parts) + ("\n" if parts else ""),
                                    encoding="utf-8")
        try:
            self.config_path.chmod(0o600)
        except OSError:
            pass

    def _is_mounted(self, name: str) -> bool:
        target = Path(self.remote_root) / name
        try:
            return os.path.ismount(target)
        except OSError:
            return False

    def _mount(self, name: str, mount: Dict[str, Any]) -> bool:
        if not shutil.which("rclone"):
            sys.stderr.write("[RemoteFS] rclone not installed; remote mounts disabled\n")
            return False
        if not self._ensure_root():
            return False
        target = Path(self.remote_root) / name
        if not self._ensure_mountpoint(target):
            return False
        cmd = [
            "rclone", "mount", f"{name}:", str(target),
            "--config", str(self.config_path),
            "--daemon",
            "--vfs-cache-mode", "writes",
        ]
        if mount.get("mode") == "read":
            cmd.append("--read-only")
        ok = self._run(cmd, f"rclone mount {name}")
        if ok:
            if not self._is_mounted(name):
                sys.stderr.write(
                    f"[RemoteFS] rclone mount {name} returned success but {target} is not mounted\n")
                return False
            sys.stderr.write(f"[RemoteFS] mounted {name} at {target}\n")
        return ok

    def _unmount(self, name: str) -> None:
        target = str(Path(self.remote_root) / name)
        candidates = []
        if shutil.which("fusermount3"):
            candidates.append(["fusermount3", "-u", target])
        if shutil.which("fusermount"):
            candidates.append(["fusermount", "-u", target])
        candidates.append(self._sudo(["umount", target]))
        for cmd in candidates:
            if self._run(cmd, f"unmount {target}"):
                sys.stderr.write(f"[RemoteFS] unmounted {target}\n")
                return
