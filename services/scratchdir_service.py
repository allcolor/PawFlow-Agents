"""Scoped filesystem facade for one relay-backed ScratchDir."""

from __future__ import annotations

from pathlib import PurePosixPath

from core.scratchdir_models import ScratchDirError
from services._filesystem_ops import _RelayFsOpsMixin

SCRATCHDIR_SERVICE = "scratchdir"
SCRATCHDIR_URL = "fs://scratchdir/"
SCRATCHDIR_MOUNT = "/scratch"


def normalize_scratchdir_path(path: object) -> str:
    """Return one safe logical path relative to the scoped root."""

    value = str(path if path is not None else ".").strip()
    if value.startswith("fs://"):
        prefix = SCRATCHDIR_URL
        if not value.startswith(prefix):
            raise ScratchDirError(
                "scratchdir_path_escape",
                "ScratchDir paths cannot target another filesystem",
            )
        value = value[len(prefix):] or "."
    if value == SCRATCHDIR_MOUNT:
        value = "."
    elif value.startswith(SCRATCHDIR_MOUNT + "/"):
        value = value[len(SCRATCHDIR_MOUNT) + 1:]
    elif value.startswith(("/", "\\")):
        raise ScratchDirError(
            "scratchdir_path_escape",
            "ScratchDir paths must be relative or start with /scratch",
        )
    if "\\" in value:
        raise ScratchDirError(
            "scratchdir_path_escape",
            "ScratchDir paths must use forward slashes",
        )
    if value in ("", "."):
        return "."
    components = value.split("/")
    if any(part in ("", ".", "..") for part in components):
        raise ScratchDirError(
            "scratchdir_path_escape",
            "ScratchDir path contains an unsafe component",
        )
    normalized = str(PurePosixPath(*components))
    if normalized.startswith("../") or normalized == "..":
        raise ScratchDirError(
            "scratchdir_path_escape",
            "ScratchDir path escapes its scoped root",
        )
    return normalized


class ScratchDirService(_RelayFsOpsMixin):
    """Expose one authenticated ScratchDir through the RelayService API."""

    # Runtime-only facade: an empty TYPE keeps service discovery from exposing
    # it as a configurable RelayService implementation.
    TYPE = ""
    service_type = "relay"
    is_scratchdir = True

    def __init__(self, relay, *, scratch_id: str, scope_hash: str, epoch: int) -> None:
        self._relay = relay
        self._service_id = str(
            getattr(relay, "_service_id", "")
            or getattr(relay, "service_id", "")
            or ""
        )
        self._ticket = {
            "scratch_id": scratch_id,
            "scope_hash": scope_hash,
            "epoch": int(epoch),
        }

    @property
    def config(self) -> dict:
        return getattr(self._relay, "config", {}) or {}

    def supports_capability(self, capability: str) -> bool:
        return capability == "scratchdir_v1"

    @staticmethod
    def _reject_local(kwargs: dict) -> None:
        if kwargs.get("local"):
            error = ScratchDirError(
                "scratchdir_scope_bypass",
                "local=true cannot be used with ScratchDir")
            raise RuntimeError(f"[{error.code}]: {error}") from error
        kwargs.pop("local", None)

    @staticmethod
    def _normalize_payload(action: str, path: object, kwargs: dict) -> str:
        normalized = normalize_scratchdir_path(path)
        if action == "copy_file":
            kwargs["dest_path"] = normalize_scratchdir_path(
                kwargs.get("dest_path", ".")
            )
        elif action == "batch_edit":
            edits = []
            for edit in kwargs.get("edits") or []:
                item = dict(edit)
                item["path"] = normalize_scratchdir_path(item.get("path", ""))
                edits.append(item)
            kwargs["edits"] = edits
        return normalized

    def _request(self, action: str, path: str = ".", **kwargs):
        kwargs = dict(kwargs)
        self._reject_local(kwargs)
        try:
            normalized = self._normalize_payload(action, path, kwargs)
        except ScratchDirError as exc:
            raise RuntimeError(f"[{exc.code}]: {exc}") from exc
        kwargs["scratchdir"] = dict(self._ticket)
        return self._relay._request(action, normalized, **kwargs)

    def _request_stream(self, action: str, path: str = ".",
                        on_output=None, **kwargs):
        kwargs = dict(kwargs)
        self._reject_local(kwargs)
        try:
            normalized = self._normalize_payload(action, path, kwargs)
        except ScratchDirError as exc:
            raise RuntimeError(f"[{exc.code}]: {exc}") from exc
        kwargs["scratchdir"] = dict(self._ticket)
        return self._relay._request_stream(
            action, normalized, on_output=on_output, **kwargs
        )
