"""File-backed Website Creator batches and deterministic static finalization."""

from __future__ import annotations

import hashlib
import json
import posixpath
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from core.website_creator_contracts import (
    ReferenceKind,
    canonicalize_url,
    classify_reference,
)


BATCH_SCHEMA_VERSION = 1
FINALIZE_SCHEMA_VERSION = 1
BATCH_SIZE = 25
_PHASES = frozenset({"mapping", "build", "correction"})
_BUILD_RESULT_FIELDS = frozenset({
    "pages_built",
    "skipped_pages",
    "assets_materialized",
    "files_changed",
    "validation",
    "remaining_issues",
})
_SKIP_REASONS = frozenset({
    "accepted_omission", "explicit_user_request", "not_applicable",
})


def _stable_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: Any) -> str:
    payload = value if isinstance(value, bytes) else _stable_json(value)
    return hashlib.sha256(payload).hexdigest()


def _validate_digest(value: str, field: str, *, optional: bool = False) -> str:
    digest = str(value or "").strip().casefold()
    if optional and not digest:
        return ""
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError(f"Website Creator {field} must be a SHA-256 digest")
    return digest


def _safe_relative_path(value: Any, field: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    normalized = posixpath.normpath(raw)
    if (
        not raw
        or raw.startswith("/")
        or normalized in {"", ".", ".."}
        or normalized.startswith("../")
    ):
        raise ValueError(f"Website Creator {field} escapes the run workspace")
    return normalized


def _write_if_changed(service, path: str, content: bytes) -> None:
    if service.exists(path, local=False):
        if service.read_file(path, local=False) == content:
            return
    service.atomic_write_file(path, content, local=False)


def _read_json(service, path: str) -> dict[str, Any]:
    try:
        value = json.loads(service.read_file(path, local=False))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid Website Creator JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Website Creator JSON must contain an object: {path}")
    return value


class WebsiteBatchCoordinator:
    """Prepare, replay, validate, and merge one deterministic page phase."""

    def __init__(
        self,
        service,
        workspace: str,
        *,
        phase: str,
        manifest_digest: str,
        template_digest: str,
        mapping_revision: str,
    ):
        normalized_phase = str(phase or "").strip().casefold()
        if normalized_phase not in _PHASES:
            raise ValueError("Website Creator batch phase is unsupported")
        self.service = service
        self.workspace = posixpath.normpath(str(workspace or ""))
        if not self.workspace.startswith("/"):
            raise ValueError("Website Creator workspace must be absolute")
        self.phase = normalized_phase
        self.manifest_digest = _validate_digest(
            manifest_digest, "inventory manifest digest",
        )
        self.template_digest = _validate_digest(
            template_digest,
            "template digest",
            optional=normalized_phase == "mapping",
        )
        self.mapping_revision = _validate_digest(
            mapping_revision,
            "mapping revision",
            optional=normalized_phase == "mapping",
        )
        self.root = posixpath.join(self.workspace, normalized_phase)
        self.manifest_path = posixpath.join(self.root, "batches.json")
        self.merged_path = posixpath.join(self.root, "merged.json")

    @staticmethod
    def _normalize_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(entry, Mapping):
            raise ValueError("Website Creator batch entries must be objects")
        page_url = canonicalize_url(str(entry.get("page_url") or ""))
        normalized = {
            "page_url": page_url,
            "local_path": _safe_relative_path(entry.get("local_path"), "page path"),
            "source_record_id": str(entry.get("source_record_id") or ""),
            "raw_html_path": str(entry.get("raw_html_path") or ""),
        }
        if normalized["source_record_id"]:
            _validate_digest(normalized["source_record_id"], "source record id")
        if normalized["raw_html_path"]:
            normalized["raw_html_path"] = _safe_relative_path(
                normalized["raw_html_path"], "raw HTML path",
            )
        if entry.get("skip_allowed") is True:
            decision_id = str(entry.get("skip_decision_id") or "").strip()
            if not decision_id:
                raise ValueError("approved page skips require a durable decision id")
            normalized["skip_allowed"] = True
            normalized["skip_decision_id"] = decision_id
        for field in ("template_component", "implementation", "notes"):
            if field in entry:
                normalized[field] = str(entry.get(field) or "")[:8000]
        if "issues" in entry:
            issues = entry.get("issues")
            if not isinstance(issues, list) or len(issues) > 50:
                raise ValueError("Website Creator page issues must be a bounded array")
            normalized["issues"] = [
                dict(value) if isinstance(value, Mapping) else str(value)[:1000]
                for value in issues
            ]
        return normalized

    def _identity(self, entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return {
            "schema_version": BATCH_SCHEMA_VERSION,
            "phase": self.phase,
            "inventory_manifest_digest": self.manifest_digest,
            "ordered_page_urls": [str(entry["page_url"]) for entry in entries],
            "template_digest": self.template_digest,
            "mapping_revision": self.mapping_revision,
        }

    def _batch_is_complete(self, row: Mapping[str, Any]) -> bool:
        path = str(row.get("result_path") or "")
        if not path or not self.service.exists(path, local=False):
            return False
        try:
            stored = _read_json(self.service, path)
            if (
                stored.get("schema_version") != BATCH_SCHEMA_VERSION
                or stored.get("phase") != self.phase
                or stored.get("batch_id") != row.get("batch_id")
            ):
                return False
            batch = _read_json(self.service, str(row["input_path"]))
            self._validate_result(batch, stored.get("result"))
        except (KeyError, TypeError, ValueError):
            return False
        return True

    def prepare(self, entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence):
            raise ValueError("Website Creator batch entries must be an array")
        normalized = [self._normalize_entry(entry) for entry in entries]
        urls = [entry["page_url"] for entry in normalized]
        if len(set(urls)) != len(urls):
            raise ValueError("Website Creator accepted pages must be unique")
        if not normalized:
            raise ValueError("Website Creator batch preparation requires accepted pages")

        self.service.mkdir(self.root, local=False)
        batches: list[dict[str, Any]] = []
        for offset in range(0, len(normalized), BATCH_SIZE):
            index = offset // BATCH_SIZE
            chunk = normalized[offset:offset + BATCH_SIZE]
            identity = self._identity(chunk)
            batch_id = _digest({**identity, "entries": chunk})
            input_path = posixpath.join(self.root, f"batch-{index + 1:04d}.json")
            result_path = posixpath.join(
                self.root,
                f"result-{index + 1:04d}-{batch_id[:16]}.json",
            )
            batch = {
                "schema_version": BATCH_SCHEMA_VERSION,
                "phase": self.phase,
                "index": index,
                "batch_id": batch_id,
                "identity": identity,
                "entries": chunk,
            }
            _write_if_changed(self.service, input_path, _stable_json(batch))
            batches.append({
                "index": index,
                "batch_id": batch_id,
                "entry_count": len(chunk),
                "input_path": input_path,
                "result_path": result_path,
            })

        manifest_without_digest = {
            "schema_version": BATCH_SCHEMA_VERSION,
            "phase": self.phase,
            "inventory_manifest_digest": self.manifest_digest,
            "template_digest": self.template_digest,
            "mapping_revision": self.mapping_revision,
            "entry_count": len(normalized),
            "batch_count": len(batches),
            "batches": batches,
        }
        manifest = {
            **manifest_without_digest,
            "manifest_digest": _digest(manifest_without_digest),
        }
        _write_if_changed(self.service, self.manifest_path, _stable_json(manifest))
        return self.summary(manifest)

    def _manifest(self) -> dict[str, Any]:
        manifest = _read_json(self.service, self.manifest_path)
        if (
            manifest.get("schema_version") != BATCH_SCHEMA_VERSION
            or manifest.get("phase") != self.phase
        ):
            raise ValueError("Website Creator batch manifest contract mismatch")
        expected = _digest({
            key: value
            for key, value in manifest.items()
            if key != "manifest_digest"
        })
        if manifest.get("manifest_digest") != expected:
            raise ValueError("Website Creator batch manifest digest mismatch")
        return manifest

    def summary(self, manifest: Mapping[str, Any] | None = None) -> dict[str, Any]:
        current = dict(manifest or self._manifest())
        rows = list(current.get("batches") or [])
        completed = [self._batch_is_complete(row) for row in rows]
        cursor = next(
            (index for index, value in enumerate(completed) if not value),
            len(rows),
        )
        result = {
            "phase": self.phase,
            "manifest_path": self.manifest_path,
            "manifest_digest": str(current["manifest_digest"]),
            "batch_count": len(rows),
            "entry_count": int(current.get("entry_count") or 0),
            "completed_batches": sum(completed),
            "cursor": cursor,
            "current_batch_path": "",
            "current_result_path": "",
        }
        if cursor < len(rows):
            result["current_batch_path"] = str(rows[cursor]["input_path"])
            result["current_result_path"] = str(rows[cursor]["result_path"])
        return result

    def current_batch(self) -> dict[str, Any] | None:
        manifest = self._manifest()
        for row in manifest["batches"]:
            if not self._batch_is_complete(row):
                batch = _read_json(self.service, row["input_path"])
                batch["result_path"] = row["result_path"]
                return batch
        return None

    @staticmethod
    def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError(f"Website Creator {label} has an invalid schema")

    def _validate_mapping_result(
        self,
        batch: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._exact_keys(result, {"entries"}, "mapping batch result")
        raw_entries = result.get("entries")
        if not isinstance(raw_entries, list) or len(raw_entries) > BATCH_SIZE:
            raise ValueError("Website Creator mapping batch exceeds 25 entries")
        expected = {
            entry["page_url"]: entry for entry in batch.get("entries") or []
        }
        normalized: list[dict[str, Any]] = []
        seen: list[str] = []
        fields = {
            "page_url", "local_path", "template_component", "implementation", "notes",
        }
        for raw in raw_entries:
            self._exact_keys(raw, fields, "mapping batch entry")
            page_url = canonicalize_url(str(raw.get("page_url") or ""))
            local_path = _safe_relative_path(raw.get("local_path"), "mapping page path")
            if page_url not in expected or local_path != expected[page_url]["local_path"]:
                raise ValueError("Website Creator mapping references an unaccepted page")
            seen.append(page_url)
            normalized.append({
                "page_url": page_url,
                "local_path": local_path,
                "template_component": str(raw.get("template_component") or "").strip(),
                "implementation": str(raw.get("implementation") or "").strip(),
                "notes": str(raw.get("notes") or "").strip(),
            })
        if sorted(seen) != sorted(expected) or len(seen) != len(set(seen)):
            raise ValueError("Website Creator mapping must cover every batch page exactly once")
        if any(not row["template_component"] or not row["implementation"] for row in normalized):
            raise ValueError("Website Creator mapping entries require implementation details")
        return {"entries": normalized}

    def _validate_build_result(
        self,
        batch: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._exact_keys(result, set(_BUILD_RESULT_FIELDS), f"{self.phase} batch result")
        expected = {
            entry["page_url"]: entry for entry in batch.get("entries") or []
        }
        built_raw = result.get("pages_built")
        skipped_raw = result.get("skipped_pages")
        if not isinstance(built_raw, list) or not isinstance(skipped_raw, list):
            raise ValueError("Website Creator build coverage must use arrays")
        built = [canonicalize_url(str(value or "")) for value in built_raw]
        skipped: list[dict[str, Any]] = []
        for raw in skipped_raw:
            self._exact_keys(
                raw,
                {"page_url", "reason", "decision_id"},
                "skipped page",
            )
            page_url = canonicalize_url(str(raw.get("page_url") or ""))
            reason = str(raw.get("reason") or "").strip().casefold()
            decision_id = str(raw.get("decision_id") or "").strip()
            accepted = expected.get(page_url) or {}
            if (
                accepted.get("skip_allowed") is not True
                or reason not in _SKIP_REASONS
                or decision_id != accepted.get("skip_decision_id")
            ):
                raise ValueError("Website Creator page lacks an approved skip decision")
            skipped.append({
                "page_url": page_url,
                "reason": reason,
                "decision_id": decision_id,
            })
        covered = built + [row["page_url"] for row in skipped]
        if (
            sorted(covered) != sorted(expected)
            or len(covered) != len(set(covered))
        ):
            raise ValueError(
                "Website Creator build must cover every batch page exactly once"
            )
        for page_url in built:
            if page_url not in expected:
                raise ValueError("Website Creator build references an unaccepted page")

        normalized: dict[str, Any] = {
            "pages_built": built,
            "skipped_pages": skipped,
        }
        for field in (
            "assets_materialized", "files_changed", "validation", "remaining_issues",
        ):
            values = result.get(field)
            if not isinstance(values, list) or len(values) > 1000:
                raise ValueError(f"Website Creator {field} must be a bounded array")
            normalized[field] = list(values)
        return normalized

    def _validate_result(
        self,
        batch: Mapping[str, Any],
        result: Any,
    ) -> dict[str, Any]:
        if not isinstance(result, Mapping):
            raise ValueError("Website Creator batch result must be an object")
        if self.phase == "mapping":
            return self._validate_mapping_result(batch, result)
        return self._validate_build_result(batch, result)

    def store_result(self, index: int, result: Mapping[str, Any]) -> dict[str, Any]:
        manifest = self._manifest()
        rows = list(manifest["batches"])
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(rows):
            raise ValueError("Website Creator batch index is invalid")
        row = rows[index]
        batch = _read_json(self.service, row["input_path"])
        normalized = self._validate_result(batch, result)
        stored = {
            "schema_version": BATCH_SCHEMA_VERSION,
            "phase": self.phase,
            "index": index,
            "batch_id": row["batch_id"],
            "result": normalized,
        }
        self.service.atomic_write_file(
            row["result_path"], _stable_json(stored), local=False,
        )
        return self.summary(manifest)

    def merge(self) -> dict[str, Any]:
        manifest = self._manifest()
        results: list[dict[str, Any]] = []
        for row in manifest["batches"]:
            if not self._batch_is_complete(row):
                raise ValueError("Website Creator cannot merge incomplete batches")
            stored = _read_json(self.service, row["result_path"])
            results.append(dict(stored["result"]))

        if self.phase == "mapping":
            entries = [entry for result in results for entry in result["entries"]]
            urls = [entry["page_url"] for entry in entries]
            if len(urls) != len(set(urls)) or len(urls) != manifest["entry_count"]:
                raise ValueError("Website Creator mapping merge is not exactly-once")
            payload: dict[str, Any] = {
                "entries": entries,
                "entry_count": len(entries),
            }
        else:
            built = [value for result in results for value in result["pages_built"]]
            skipped = [value for result in results for value in result["skipped_pages"]]
            payload = {
                "pages_built": built,
                "skipped_pages": skipped,
                "built_count": len(built),
                "skipped_count": len(skipped),
                "assets_materialized": sorted({
                    str(value)
                    for result in results
                    for value in result["assets_materialized"]
                }),
                "files_changed": sorted({
                    str(value)
                    for result in results
                    for value in result["files_changed"]
                }),
                "validation": [
                    value for result in results for value in result["validation"]
                ],
                "remaining_issues": [
                    value
                    for result in results
                    for value in result["remaining_issues"]
                ],
            }
        merged_without_digest = {
            "schema_version": BATCH_SCHEMA_VERSION,
            "phase": self.phase,
            "batch_manifest_digest": manifest["manifest_digest"],
            **payload,
        }
        merged = {
            **merged_without_digest,
            "result_digest": _digest(merged_without_digest),
        }
        self.service.atomic_write_file(
            self.merged_path, _stable_json(merged), local=False,
        )
        return {
            **merged,
            "result_path": self.merged_path,
        }


class StaticSiteFinalizer:
    """Rewrite static references and emit a machine-owned completeness report."""

    def __init__(
        self,
        service,
        workspace: str,
        *,
        inventory_manifest_digest: str,
        mapping_digest: str,
        template_digest: str,
        accepted_omissions: Sequence[Mapping[str, Any]],
        attribution_paths: Sequence[str],
    ):
        self.service = service
        self.workspace = posixpath.normpath(str(workspace or ""))
        if not self.workspace.startswith("/"):
            raise ValueError("Website Creator workspace must be absolute")
        self.site_root = posixpath.join(self.workspace, "site")
        self.report_path = posixpath.join(self.workspace, "reports", "finalize.json")
        self.inventory_manifest_digest = _validate_digest(
            inventory_manifest_digest, "inventory manifest digest",
        )
        self.mapping_digest = _validate_digest(mapping_digest, "mapping digest")
        self.template_digest = _validate_digest(template_digest, "template digest")
        self.accepted_omissions = [dict(value) for value in accepted_omissions]
        self.attribution_paths = [
            _safe_relative_path(value, "attribution path")
            for value in attribution_paths
        ]
        self._issues: list[dict[str, Any]] = []

    def _issue(self, code: str, detail: str, *, url: str = "", path: str = "") -> None:
        issue = {
            "code": code,
            "detail": str(detail)[:1000],
            "url": str(url)[:8192],
            "path": str(path)[:4096],
        }
        if issue not in self._issues:
            self._issues.append(issue)

    def _workspace_path(self, relative: Any, field: str) -> str:
        raw = str(relative or "").replace("\\", "/")
        if raw == self.workspace or raw.startswith(self.workspace.rstrip("/") + "/"):
            full = posixpath.normpath(raw)
        else:
            full = posixpath.normpath(posixpath.join(
                self.workspace, _safe_relative_path(raw, field),
            ))
        if not (
            full == self.workspace
            or full.startswith(self.workspace.rstrip("/") + "/")
        ):
            raise ValueError(f"Website Creator {field} escapes the run workspace")
        return full

    def _site_path(self, relative: Any) -> str:
        path = posixpath.normpath(posixpath.join(
            self.site_root,
            _safe_relative_path(relative, "page path"),
        ))
        if not path.startswith(self.site_root.rstrip("/") + "/"):
            raise ValueError("Website Creator page path escapes site output")
        return path

    def _hash(self, path: str) -> str:
        return str(self.service.hash_file(path, local=False).get("sha256") or "")

    def _materialize_assets(
        self,
        assets: Sequence[Mapping[str, Any]],
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        by_url: dict[str, dict[str, Any]] = {}
        stylesheets: list[dict[str, Any]] = []
        for raw in assets:
            try:
                url = canonicalize_url(str(raw.get("url") or ""))
                source_path = self._workspace_path(raw.get("path"), "asset path")
            except ValueError as exc:
                self._issue("asset_path_escape", str(exc), url=str(raw.get("url") or ""))
                continue
            if url in by_url:
                self._issue("duplicate_asset_url", "asset URL occurs more than once", url=url)
                continue
            required = raw.get("required") is True
            if not self.service.exists(source_path, local=False):
                if required:
                    self._issue(
                        "missing_required_asset", "required asset file is missing",
                        url=url, path=source_path,
                    )
                continue
            expected_size = int(raw.get("bytes") or 0)
            expected_hash = str(raw.get("sha256") or "")
            hashed = self.service.hash_file(source_path, local=False)
            if (
                int(hashed.get("bytes") or 0) != expected_size
                or str(hashed.get("sha256") or "") != expected_hash
            ):
                self._issue(
                    "asset_hash_mismatch", "asset bytes do not match the accepted manifest",
                    url=url, path=source_path,
                )
                continue
            relative = posixpath.relpath(source_path, self.workspace)
            if relative.startswith("site/"):
                output_path = source_path
            else:
                output_path = self._site_path(relative)
                content = self.service.read_file(source_path, local=False)
                _write_if_changed(self.service, output_path, content)
            entry = {
                **dict(raw),
                "url": url,
                "source_path": source_path,
                "output_path": output_path,
            }
            by_url[url] = entry
            if str(raw.get("kind") or "").casefold() == "stylesheet":
                stylesheets.append(entry)
            if (
                str(raw.get("kind") or "").casefold() == "script"
                and dict(raw.get("policy") or {}).get("source_application_bundle") is True
            ):
                self._issue(
                    "prohibited_source_script",
                    "source application bundles are prohibited by default",
                    url=url,
                    path=source_path,
                )
        return by_url, stylesheets

    @staticmethod
    def _relative_target(current_path: str, target_path: str) -> str:
        return posixpath.relpath(target_path, posixpath.dirname(current_path))

    def _rewrite_reference(
        self,
        value: str,
        *,
        document_url: str,
        current_path: str,
        page_paths: Mapping[str, str],
        asset_entries: Mapping[str, Mapping[str, Any]],
        tag: str,
        attribute: str,
        base_url: str | None = None,
    ) -> tuple[str | None, str]:
        original = str(value or "").strip()
        if not original or original.startswith("#"):
            return original, "unchanged"
        scheme = urlsplit(original).scheme.casefold()
        if scheme in {"javascript", "data"} and scheme == "javascript":
            return None, "active_endpoint"
        if not scheme and not original.startswith("/"):
            local_candidate = posixpath.normpath(posixpath.join(
                posixpath.dirname(current_path), original.split("#", 1)[0].split("?", 1)[0],
            ))
            if (
                local_candidate.startswith(self.site_root.rstrip("/") + "/")
                and self.service.exists(local_candidate, local=False)
            ):
                return original, "local_output"
        try:
            classified = classify_reference(
                original,
                document_url=document_url,
                base_url=base_url,
                tag=tag,
                attribute=attribute,
            )
        except ValueError as exc:
            self._issue("invalid_reference", str(exc), url=original, path=current_path)
            return original, "invalid"
        canonical = classified.canonical_url
        if classified.kind is ReferenceKind.ACTIVE_ENDPOINT:
            return None, "active_endpoint"
        if classified.kind is ReferenceKind.INTERNAL_PAGE:
            target = page_paths.get(str(canonical))
            if target is None:
                self._issue(
                    "unresolved_internal_page",
                    "accepted internal page has no local output",
                    url=str(canonical or original),
                    path=current_path,
                )
                return original, "missing"
            return self._relative_target(current_path, target), "rewritten"
        if classified.kind in {
            ReferenceKind.FIRST_PARTY_ASSET,
            ReferenceKind.APPROVED_THIRD_PARTY_ASSET,
        }:
            asset = asset_entries.get(str(canonical))
            if asset is None:
                return original, "unmanaged_asset"
            return self._relative_target(
                current_path, str(asset["output_path"]),
            ), "rewritten"
        return original, "external_or_ignored"

    def _rewrite_css_text(
        self,
        css: str,
        *,
        document_url: str,
        current_path: str,
        page_paths: Mapping[str, str],
        asset_entries: Mapping[str, Mapping[str, Any]],
        declaration_list: bool = False,
    ) -> str:
        try:
            import tinycss2
        except ImportError:
            return self._rewrite_css_fallback(
                css,
                document_url=document_url,
                current_path=current_path,
                page_paths=page_paths,
                asset_entries=asset_entries,
            )

        nodes = (
            tinycss2.parse_declaration_list(css, skip_comments=False, skip_whitespace=False)
            if declaration_list
            else tinycss2.parse_stylesheet(css, skip_comments=False, skip_whitespace=False)
        )

        def quoted(value: str) -> str:
            return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

        def replace(raw: str) -> str:
            rewritten, _status = self._rewrite_reference(
                raw,
                document_url=document_url,
                current_path=current_path,
                page_paths=page_paths,
                asset_entries=asset_entries,
                tag="style",
                attribute="url",
            )
            return raw if rewritten is None else rewritten

        def visit(tokens: Sequence[Any], *, import_rule: bool = False) -> None:
            for token in tokens:
                token_type = getattr(token, "type", "")
                if token_type == "url":  # nosec B105 - tinycss2 token type, not a password
                    value = replace(str(token.value))
                    token.value = value
                    token.representation = f"url({quoted(value)})"
                elif token_type == "function":  # nosec B105 - tinycss2 token type
                    if str(getattr(token, "lower_name", "")) == "url":
                        significant = [
                            item for item in token.arguments
                            if getattr(item, "type", "") not in {"whitespace", "comment"}
                        ]
                        if len(significant) == 1 and hasattr(significant[0], "value"):
                            value = replace(str(significant[0].value))
                            significant[0].value = value
                            if hasattr(significant[0], "representation"):
                                significant[0].representation = quoted(value)
                    else:
                        visit(token.arguments)
                elif import_rule and token_type == "string":  # nosec B105 - token type
                    value = replace(str(token.value))
                    token.value = value
                    token.representation = quoted(value)
                    import_rule = False

        for node in nodes:
            if getattr(node, "type", "") == "at-rule":
                visit(
                    node.prelude,
                    import_rule=str(getattr(node, "lower_at_keyword", "")) == "import",
                )
                if node.content is not None:
                    visit(node.content)
            elif hasattr(node, "prelude"):
                visit(node.prelude)
                if getattr(node, "content", None) is not None:
                    visit(node.content)
            elif hasattr(node, "value") and isinstance(node.value, list):
                visit(node.value)
        return tinycss2.serialize(nodes)

    def _rewrite_css_fallback(
        self,
        css: str,
        *,
        document_url: str,
        current_path: str,
        page_paths: Mapping[str, str],
        asset_entries: Mapping[str, Mapping[str, Any]],
    ) -> str:
        """Token-aware fallback for relay images awaiting the declared dependency."""

        def replace(raw: str) -> str:
            rewritten, _status = self._rewrite_reference(
                raw,
                document_url=document_url,
                current_path=current_path,
                page_paths=page_paths,
                asset_entries=asset_entries,
                tag="style",
                attribute="url",
            )
            return raw if rewritten is None else rewritten

        def quoted(value: str, quote: str = '"') -> str:
            escaped = value.replace("\\", "\\\\").replace(quote, "\\" + quote)
            return quote + escaped + quote

        def consume_string(index: int) -> tuple[str, str, int]:
            quote = css[index]
            cursor = index + 1
            value: list[str] = []
            while cursor < len(css):
                character = css[cursor]
                if character == "\\" and cursor + 1 < len(css):
                    value.append(css[cursor + 1])
                    cursor += 2
                    continue
                if character == quote:
                    return "".join(value), css[index:cursor + 1], cursor + 1
                value.append(character)
                cursor += 1
            return "".join(value), css[index:], len(css)

        output: list[str] = []
        index = 0
        import_value_pending = False
        while index < len(css):
            if css.startswith("/*", index):
                end = css.find("*/", index + 2)
                end = len(css) if end < 0 else end + 2
                output.append(css[index:end])
                index = end
                continue
            if css[index:index + 7].casefold() == "@import" and (
                index + 7 == len(css)
                or not (css[index + 7].isalnum() or css[index + 7] in "_-")
            ):
                output.append(css[index:index + 7])
                index += 7
                import_value_pending = True
                continue
            if css[index] in {'"', "'"}:
                value, original, end = consume_string(index)
                if import_value_pending:
                    output.append(quoted(replace(value), css[index]))
                    import_value_pending = False
                else:
                    output.append(original)
                index = end
                continue
            if css[index:index + 3].casefold() == "url" and (
                index == 0 or not (css[index - 1].isalnum() or css[index - 1] in "_-")
            ):
                cursor = index + 3
                while cursor < len(css) and css[cursor].isspace():
                    cursor += 1
                if cursor < len(css) and css[cursor] == "(":
                    argument_start = cursor + 1
                    argument_end = argument_start
                    quote = ""
                    escaped = False
                    while argument_end < len(css):
                        character = css[argument_end]
                        if escaped:
                            escaped = False
                        elif character == "\\":
                            escaped = True
                        elif quote:
                            if character == quote:
                                quote = ""
                        elif character in {'"', "'"}:
                            quote = character
                        elif character == ")":
                            break
                        argument_end += 1
                    raw = css[argument_start:argument_end].strip()
                    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
                        raw = raw[1:-1]
                    output.append("url(" + quoted(replace(raw)) + ")")
                    index = min(argument_end + 1, len(css))
                    import_value_pending = False
                    continue
            output.append(css[index])
            if import_value_pending and css[index] == ";":
                import_value_pending = False
            index += 1
        return "".join(output)

    def _rewrite_html(
        self,
        page_url: str,
        path: str,
        page_paths: Mapping[str, str],
        asset_entries: Mapping[str, Mapping[str, Any]],
    ) -> None:
        content = self.service.read_file(path, local=False)
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError:
            self._issue("invalid_html", "generated HTML is not UTF-8", path=path)
            return
        soup = BeautifulSoup(decoded, "html.parser")
        base_url = page_url
        base = soup.find("base", href=True)
        if base is not None:
            try:
                base_url = canonicalize_url(str(base.get("href")), base_url=page_url)
            except ValueError as exc:
                self._issue("invalid_base_url", str(exc), path=path)
            base.decompose()

        for tag in soup.find_all(True):
            name = str(tag.name or "").casefold()
            for attribute in ("href", "src", "poster", "action", "formaction", "ping"):
                if not tag.has_attr(attribute):
                    continue
                rewritten, status = self._rewrite_reference(
                    str(tag.get(attribute) or ""),
                    document_url=page_url,
                    current_path=path,
                    page_paths=page_paths,
                    asset_entries=asset_entries,
                    tag=name,
                    attribute=attribute,
                    base_url=base_url,
                )
                if status == "active_endpoint":
                    del tag.attrs[attribute]
                    tag.attrs["data-pawflow-disabled"] = "active_endpoint"
                elif rewritten is not None:
                    tag.attrs[attribute] = rewritten
            if tag.has_attr("srcset"):
                candidates = []
                for candidate in str(tag.get("srcset") or "").split(","):
                    parts = candidate.strip().split()
                    if not parts:
                        continue
                    rewritten, _status = self._rewrite_reference(
                        parts[0],
                        document_url=page_url,
                        current_path=path,
                        page_paths=page_paths,
                        asset_entries=asset_entries,
                        tag=name,
                        attribute="srcset",
                        base_url=base_url,
                    )
                    candidates.append(" ".join([rewritten or parts[0], *parts[1:]]))
                tag.attrs["srcset"] = ", ".join(candidates)
            if tag.has_attr("style"):
                tag.attrs["style"] = self._rewrite_css_text(
                    str(tag.get("style") or ""),
                    document_url=page_url,
                    current_path=path,
                    page_paths=page_paths,
                    asset_entries=asset_entries,
                    declaration_list=True,
                )
        for style in soup.find_all("style"):
            css = style.string
            if css is not None:
                style.string.replace_with(self._rewrite_css_text(
                    str(css),
                    document_url=page_url,
                    current_path=path,
                    page_paths=page_paths,
                    asset_entries=asset_entries,
                ))
        _write_if_changed(self.service, path, str(soup).encode("utf-8"))

    def _output_hashes(
        self,
        page_paths: Mapping[str, str],
        stylesheets: Sequence[Mapping[str, Any]],
    ) -> dict[str, str]:
        paths = set(page_paths.values()) | {
            str(entry["output_path"]) for entry in stylesheets
        }
        return {
            posixpath.relpath(path, self.workspace): self._hash(path)
            for path in sorted(paths)
            if self.service.exists(path, local=False)
        }

    def _contract(
        self,
        page_paths: Mapping[str, str],
        asset_entries: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        return {
            "schema_version": FINALIZE_SCHEMA_VERSION,
            "inventory_manifest_digest": self.inventory_manifest_digest,
            "mapping_digest": self.mapping_digest,
            "template_digest": self.template_digest,
            "pages": {
                url: posixpath.relpath(path, self.workspace)
                for url, path in sorted(page_paths.items())
            },
            "assets": [
                {
                    "url": url,
                    "path": posixpath.relpath(str(entry["source_path"]), self.workspace),
                    "sha256": str(entry.get("sha256") or ""),
                    "required": entry.get("required") is True,
                }
                for url, entry in sorted(asset_entries.items())
            ],
            "accepted_omissions": self.accepted_omissions,
            "attribution_paths": self.attribution_paths,
        }

    def run(
        self,
        *,
        pages: Sequence[Mapping[str, Any]],
        assets: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        self._issues = []
        page_paths: dict[str, str] = {}
        local_paths: dict[str, str] = {}
        for raw in pages:
            try:
                page_url = canonicalize_url(str(raw.get("page_url") or ""))
                path = self._site_path(raw.get("local_path"))
            except ValueError as exc:
                self._issue("page_path_escape", str(exc), url=str(raw.get("page_url") or ""))
                continue
            folded = path.casefold()
            if page_url in page_paths or folded in local_paths:
                self._issue(
                    "duplicate_page_output", "accepted pages do not map exactly once",
                    url=page_url, path=path,
                )
                continue
            page_paths[page_url] = path
            local_paths[folded] = page_url
            if not self.service.exists(path, local=False):
                self._issue(
                    "missing_page_output", "accepted page has no generated HTML",
                    url=page_url, path=path,
                )

        for omission in self.accepted_omissions:
            if not str(omission.get("decision_id") or "").strip():
                self._issue(
                    "omission_without_decision",
                    "accepted omission lacks a durable user decision",
                    url=str(omission.get("page_url") or omission.get("url") or ""),
                )
        for relative in self.attribution_paths:
            path = self._workspace_path(relative, "attribution path")
            if not self.service.exists(path, local=False):
                self._issue(
                    "missing_attribution", "required template attribution is missing",
                    path=path,
                )

        asset_entries, stylesheets = self._materialize_assets(assets)
        contract = self._contract(page_paths, asset_entries)
        before_hashes = self._output_hashes(page_paths, stylesheets)
        before_key = _digest({"contract": contract, "generated_files": before_hashes})
        if self.service.exists(self.report_path, local=False):
            prior = _read_json(self.service, self.report_path)
            if (
                prior.get("replay_key") == before_key
                and prior.get("generated_files") == before_hashes
            ):
                return {**prior, "replayed": True}

        for page_url, path in page_paths.items():
            if self.service.exists(path, local=False):
                self._rewrite_html(
                    page_url, path, page_paths, asset_entries,
                )
        for entry in stylesheets:
            output_path = str(entry["output_path"])
            try:
                css = self.service.read_file(output_path, local=False).decode("utf-8")
            except UnicodeDecodeError:
                self._issue(
                    "invalid_stylesheet", "stylesheet is not UTF-8",
                    url=str(entry["url"]), path=output_path,
                )
                continue
            rewritten = self._rewrite_css_text(
                css,
                document_url=str(entry["url"]),
                current_path=output_path,
                page_paths=page_paths,
                asset_entries=asset_entries,
            )
            _write_if_changed(
                self.service, output_path, rewritten.encode("utf-8"),
            )

        generated_files = self._output_hashes(page_paths, stylesheets)
        replay_key = _digest({
            "contract": contract,
            "generated_files": generated_files,
        })
        blocking = list(self._issues)
        report = {
            "schema_version": FINALIZE_SCHEMA_VERSION,
            "passed": not blocking,
            "replayed": False,
            "replay_key": replay_key,
            "inventory_manifest_digest": self.inventory_manifest_digest,
            "mapping_digest": self.mapping_digest,
            "template_digest": self.template_digest,
            "counts": {
                "accepted_pages": len(pages),
                "generated_pages": sum(
                    self.service.exists(path, local=False)
                    for path in page_paths.values()
                ),
                "required_assets": sum(
                    entry.get("required") is True for entry in assets
                ),
                "materialized_assets": len(asset_entries),
                "blocking_issues": len(blocking),
            },
            "blocking_issues": blocking[:100],
            "non_blocking": {
                "external_navigation": "retained",
                "approved_third_party_assets": sum(
                    bool(dict(entry.get("policy") or {}).get("third_party"))
                    for entry in asset_entries.values()
                ),
            },
            "generated_files": generated_files,
        }
        self.service.mkdir(posixpath.dirname(self.report_path), local=False)
        self.service.atomic_write_file(
            self.report_path, _stable_json(report), local=False,
        )
        return report


__all__ = [
    "BATCH_SCHEMA_VERSION",
    "BATCH_SIZE",
    "FINALIZE_SCHEMA_VERSION",
    "StaticSiteFinalizer",
    "WebsiteBatchCoordinator",
]
