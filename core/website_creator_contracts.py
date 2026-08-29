"""Canonical URL, reference, and inventory contracts for Website Creator."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
import hashlib
import ipaddress
import re
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import unquote_plus, urljoin, urlsplit, urlunsplit


INVENTORY_SCHEMA_VERSION = 1
_UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)
_PATH_SAFE = frozenset("/!$&'()*+,;=:@")
_QUERY_SAFE = frozenset("/?!$&'()*+,;=:@")
_KNOWN_TRACKING_PARAMETERS = frozenset({
    "dclid",
    "fbclid",
    "gclid",
    "gbraid",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "twclid",
    "wbraid",
})
_KNOWN_TRACKING_PREFIXES = ("utm_", "pk_")
_SAFE_PATH_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")
_WINDOWS_RESERVED_NAMES = frozenset({
    "aux", "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8",
    "com9", "con", "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7",
    "lpt8", "lpt9", "nul", "prn",
})


class ReferenceKind(str, Enum):
    """Machine-owned classification for one discovered website reference."""

    INTERNAL_PAGE = "internal_page"
    FIRST_PARTY_ASSET = "first_party_asset"
    APPROVED_THIRD_PARTY_ASSET = "approved_third_party_asset"
    EXTERNAL_NAVIGATION = "external_navigation"
    ACTIVE_ENDPOINT = "active_endpoint"
    IGNORED_SCHEME = "ignored_scheme"


class AssetKind(str, Enum):
    """Supported static subresource families."""

    IMAGE = "image"
    STYLESHEET = "stylesheet"
    SCRIPT = "script"
    FONT = "font"
    MEDIA = "media"
    MANIFEST = "manifest"
    OTHER = "other"


CRAWL_LIMIT_FIELDS = (
    "max_pages",
    "max_depth",
    "politeness_delay_ms",
    "request_timeout_seconds",
    "max_total_bytes",
    "max_duration_seconds",
)
DEFAULT_CRAWL_LIMITS: Mapping[str, int] = {
    "max_pages": 100,
    "max_depth": 3,
    "politeness_delay_ms": 750,
    "request_timeout_seconds": 30,
    "max_total_bytes": 256 * 1024 * 1024,
    "max_duration_seconds": 1_800,
}
CRAWL_LIMIT_BOUNDS: Mapping[str, tuple[int, int]] = {
    "max_pages": (1, 2_000),
    "max_depth": (0, 8),
    "politeness_delay_ms": (250, 5_000),
    "request_timeout_seconds": (1, 120),
    "max_total_bytes": (1, 2 * 1024 * 1024 * 1024),
    "max_duration_seconds": (1, 14_400),
}
_REUSABLE_ASSET_KINDS = frozenset(AssetKind) - {AssetKind.OTHER}
_CRAWL_LABEL_RE = re.compile(r"^\s*([a-z_]+)\s*:\s*(.*?)\s*$")


@dataclass(frozen=True)
class CrawlLimits:
    """Validated effective bounds frozen before any source fetch."""

    max_pages: int = int(DEFAULT_CRAWL_LIMITS["max_pages"])
    max_depth: int = int(DEFAULT_CRAWL_LIMITS["max_depth"])
    politeness_delay_ms: int = int(DEFAULT_CRAWL_LIMITS["politeness_delay_ms"])
    request_timeout_seconds: int = int(
        DEFAULT_CRAWL_LIMITS["request_timeout_seconds"]
    )
    max_total_bytes: int = int(DEFAULT_CRAWL_LIMITS["max_total_bytes"])
    max_duration_seconds: int = int(
        DEFAULT_CRAWL_LIMITS["max_duration_seconds"]
    )

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> "CrawlLimits":
        supplied = dict(values or {})
        unknown = set(supplied) - set(CRAWL_LIMIT_FIELDS)
        if unknown:
            raise ValueError(
                "unsupported crawl limit fields: " + ", ".join(sorted(unknown))
            )
        normalized = dict(DEFAULT_CRAWL_LIMITS)
        for name, value in supplied.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"crawl {name} must be an integer")
            minimum, maximum = CRAWL_LIMIT_BOUNDS[name]
            if value < minimum or value > maximum:
                raise ValueError(
                    f"crawl {name} must be between {minimum} and {maximum}"
                )
            normalized[name] = value
        return cls(**normalized)

    def to_dict(self) -> dict[str, int]:
        return {name: int(getattr(self, name)) for name in CRAWL_LIMIT_FIELDS}


@dataclass(frozen=True)
class SourceRightsDeclaration:
    """Explicit authorization boundary for reusable source subresources."""

    basis: str
    allowed_asset_kinds: tuple[AssetKind, ...] = ()
    provenance: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SourceRightsDeclaration":
        if not isinstance(value, Mapping):
            raise ValueError("crawl rights declaration must be an object")
        unknown = set(value) - {"basis", "allowed_asset_kinds", "provenance"}
        if unknown:
            raise ValueError(
                "unsupported crawl rights fields: " + ", ".join(sorted(unknown))
            )
        basis = str(value.get("basis") or "").strip().casefold()
        if basis not in {"owner", "permission", "none"}:
            raise ValueError("crawl rights basis must be owner, permission, or none")
        raw_kinds = value.get("allowed_asset_kinds") or ()
        if isinstance(raw_kinds, str) or not isinstance(raw_kinds, Sequence):
            raise ValueError("crawl allowed_asset_kinds must be an array")
        kinds: list[AssetKind] = []
        for raw_kind in raw_kinds:
            try:
                kind = AssetKind(str(raw_kind).strip().casefold())
            except ValueError as exc:
                raise ValueError(
                    f"unsupported reusable source asset kind: {raw_kind}"
                ) from exc
            if kind not in _REUSABLE_ASSET_KINDS:
                raise ValueError("source asset kind other cannot be approved")
            if kind not in kinds:
                kinds.append(kind)
        provenance = str(value.get("provenance") or "").strip()
        if basis == "none" and kinds:
            raise ValueError("crawl rights basis none cannot approve source assets")
        if basis == "permission" and not provenance:
            raise ValueError("crawl permission rights require provenance")
        return cls(
            basis=basis,
            allowed_asset_kinds=tuple(sorted(kinds, key=lambda item: item.value)),
            provenance=provenance,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "basis": self.basis,
            "allowed_asset_kinds": [item.value for item in self.allowed_asset_kinds],
            "provenance": self.provenance,
        }


def normalize_url_patterns(values: Any, field: str) -> tuple[str, ...]:
    """Validate a bounded ordered set of include or exclude regexes."""

    if values is None:
        return ()
    if isinstance(values, str) or not isinstance(values, Sequence):
        raise ValueError(f"crawl {field} must be an array")
    if len(values) > 50:
        raise ValueError(f"crawl {field} cannot contain more than 50 patterns")
    normalized: list[str] = []
    for value in values:
        pattern = str(value or "").strip()
        if not pattern or len(pattern) > 512:
            raise ValueError(f"crawl {field} patterns must contain 1 to 512 characters")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"crawl {field} contains an invalid regex") from exc
        if pattern not in normalized:
            normalized.append(pattern)
    return tuple(normalized)


def parse_labelled_crawl_options(message: str) -> dict[str, object]:
    """Parse only unambiguous one-setting-per-line crawl labels."""

    result: dict[str, object] = {}
    include_patterns: list[str] = []
    exclude_patterns: list[str] = []
    rights: dict[str, object] = {}
    for line in str(message or "").splitlines():
        match = _CRAWL_LABEL_RE.fullmatch(line)
        if match is None:
            continue
        name, raw = match.groups()
        if name in CRAWL_LIMIT_FIELDS and raw.isdecimal():
            result[name] = int(raw)
        elif name == "include_url_pattern" and raw:
            include_patterns.append(raw)
        elif name == "exclude_url_pattern" and raw:
            exclude_patterns.append(raw)
        elif name == "rights_basis" and raw.casefold() in {
            "owner", "permission", "none",
        }:
            rights["basis"] = raw.casefold()
        elif name == "rights_asset_kinds" and raw:
            rights["allowed_asset_kinds"] = [
                item.strip().casefold() for item in raw.split(",") if item.strip()
            ]
        elif name == "rights_provenance" and raw:
            rights["provenance"] = raw
    if include_patterns:
        result["include_url_patterns"] = include_patterns
    if exclude_patterns:
        result["exclude_url_patterns"] = exclude_patterns
    if rights:
        result["rights"] = rights
    return result


def prepare_crawl_contract(
    structured: Mapping[str, Any] | None,
    message: str,
) -> dict[str, object]:
    """Merge structured and strict-labelled input into a proposed crawl contract."""

    if structured is not None and not isinstance(structured, Mapping):
        raise ValueError("crawl request parameter must be an object")
    labelled = parse_labelled_crawl_options(message)
    supplied = {**labelled, **dict(structured or {})}
    allowed = set(CRAWL_LIMIT_FIELDS) | {
        "include_url_patterns", "exclude_url_patterns", "rights",
    }
    unknown = set(supplied) - allowed
    if unknown:
        raise ValueError(
            "unsupported crawl request fields: " + ", ".join(sorted(unknown))
        )
    limits = CrawlLimits.from_mapping({
        name: supplied[name] for name in CRAWL_LIMIT_FIELDS if name in supplied
    })
    rights = (
        SourceRightsDeclaration.from_mapping(supplied["rights"])
        if "rights" in supplied
        else None
    )
    explicit = all(name in supplied for name in CRAWL_LIMIT_FIELDS) and rights is not None
    return {
        "effective_limits": limits.to_dict(),
        "include_url_patterns": list(normalize_url_patterns(
            supplied.get("include_url_patterns"), "include_url_patterns",
        )),
        "exclude_url_patterns": list(normalize_url_patterns(
            supplied.get("exclude_url_patterns"), "exclude_url_patterns",
        )),
        "rights": rights.to_dict() if rights is not None else None,
        "confirmation_required": not explicit,
        "confirmed": explicit,
        "status": "confirmed" if explicit else "pending_confirmation",
    }


@dataclass(frozen=True)
class CanonicalUrlPolicy:
    """Explicit query filtering policy used by a crawl manifest."""

    drop_query_parameters: frozenset[str] = frozenset()
    allow_query_parameters: frozenset[str] | None = None
    drop_known_tracking_parameters: bool = False

    def __post_init__(self) -> None:
        drops = frozenset(
            str(value).strip().casefold()
            for value in self.drop_query_parameters
            if str(value).strip()
        )
        allows = self.allow_query_parameters
        normalized_allows = (
            None
            if allows is None
            else frozenset(
                str(value).strip().casefold()
                for value in allows
                if str(value).strip()
            )
        )
        object.__setattr__(self, "drop_query_parameters", drops)
        object.__setattr__(self, "allow_query_parameters", normalized_allows)

    def to_dict(self) -> dict[str, object]:
        """Return the stable representation included in cache identity."""

        return {
            "drop_query_parameters": sorted(self.drop_query_parameters),
            "allow_query_parameters": (
                None
                if self.allow_query_parameters is None
                else sorted(self.allow_query_parameters)
            ),
            "drop_known_tracking_parameters": self.drop_known_tracking_parameters,
        }


@dataclass(frozen=True)
class ReferenceClassification:
    """Canonical classification plus approval state for one reference."""

    original: str
    canonical_url: str | None
    kind: ReferenceKind
    asset_kind: AssetKind | None = None
    third_party_approved: bool = False
    ignored_scheme: str | None = None

    @property
    def approval_required(self) -> bool:
        return self.asset_kind is not None and not self.third_party_approved and (
            self.kind is ReferenceKind.EXTERNAL_NAVIGATION
        )

    def to_record(self) -> dict[str, object]:
        """Return a JSON-compatible inventory fragment."""

        return {
            "original": self.original,
            "canonical_url": self.canonical_url,
            "kind": self.kind.value,
            "asset_kind": (
                self.asset_kind.value if self.asset_kind is not None else None
            ),
            "third_party_approved": self.third_party_approved,
            "approval_required": self.approval_required,
            "ignored_scheme": self.ignored_scheme,
        }


def _normalize_component(value: str, safe: frozenset[str]) -> str:
    result: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if (
            character == "%"
            and index + 2 < len(value)
            and all(item in "0123456789abcdefABCDEF" for item in value[index + 1:index + 3])
        ):
            encoded = value[index + 1:index + 3].upper()
            decoded = chr(int(encoded, 16))
            result.append(decoded if decoded in _UNRESERVED else "%" + encoded)
            index += 3
            continue
        if character in _UNRESERVED or character in safe:
            result.append(character)
        else:
            result.extend(f"%{byte:02X}" for byte in character.encode("utf-8"))
        index += 1
    return "".join(result)


def _remove_dot_segments(path: str) -> str:
    leading = path.startswith("/")
    trailing = path.endswith("/") or path.endswith("/.") or path.endswith("/..")
    output: list[str] = []
    for segment in path.split("/"):
        if segment == ".":
            continue
        if segment == "..":
            if output and output[-1] not in {"", ".."}:
                output.pop()
            continue
        output.append(segment)
    normalized = "/".join(output)
    if leading and not normalized.startswith("/"):
        normalized = "/" + normalized
    if not normalized:
        normalized = "/"
    if trailing and normalized != "/" and not normalized.endswith("/"):
        normalized += "/"
    return normalized


def _query_parameter_name(part: str) -> str:
    raw_name = part.split("=", 1)[0]
    return unquote_plus(raw_name).strip().casefold()


def _is_known_tracking_parameter(name: str) -> bool:
    return (
        name in _KNOWN_TRACKING_PARAMETERS
        or any(name.startswith(prefix) for prefix in _KNOWN_TRACKING_PREFIXES)
    )


def _normalize_query(query: str, policy: CanonicalUrlPolicy) -> str:
    if not query:
        return ""
    kept: list[str] = []
    for part in query.split("&"):
        name = _query_parameter_name(part)
        if name in policy.drop_query_parameters:
            continue
        if (
            policy.allow_query_parameters is not None
            and name not in policy.allow_query_parameters
        ):
            continue
        if (
            policy.drop_known_tracking_parameters
            and _is_known_tracking_parameter(name)
        ):
            continue
        kept.append(_normalize_component(part, _QUERY_SAFE))
    return "&".join(kept)


def canonicalize_url(
    value: str,
    *,
    base_url: str | None = None,
    query_policy: CanonicalUrlPolicy | None = None,
) -> str:
    """Return one deterministic absolute HTTP(S) URL without its fragment."""

    raw = str(value or "").strip()
    if not raw:
        raise ValueError("website URL is required")
    if base_url is not None:
        base = canonicalize_url(base_url, query_policy=query_policy)
        raw = urljoin(base, raw)
    parsed = urlsplit(raw)
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("website URL must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("website URL must not contain credentials")

    host = parsed.hostname.rstrip(".")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            normalized_host = host.encode("idna").decode("ascii").casefold()
        except UnicodeError as exc:
            raise ValueError("website URL host is not valid IDNA") from exc
    else:
        normalized_host = literal.compressed.casefold()

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("website URL port is invalid") from exc
    default_port = 443 if scheme == "https" else 80
    rendered_host = (
        f"[{normalized_host}]" if ":" in normalized_host else normalized_host
    )
    netloc = rendered_host
    if port is not None and port != default_port:
        netloc += f":{port}"

    path = _normalize_component(parsed.path or "/", _PATH_SAFE)
    path = _remove_dot_segments(path)
    policy = query_policy or CanonicalUrlPolicy()
    query = _normalize_query(parsed.query, policy)
    return urlunsplit((scheme, netloc, path, query, ""))


def canonical_origin(value: str) -> str:
    """Return the normalized scheme and authority for one HTTP(S) URL."""

    parsed = urlsplit(canonicalize_url(value))
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def stable_record_id(kind: ReferenceKind | str, canonical_url: str) -> str:
    """Return a stable identifier for an inventory record."""

    kind_value = kind.value if isinstance(kind, ReferenceKind) else str(kind)
    payload = f"{kind_value}\n{canonical_url}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _coerce_asset_kind(value: AssetKind | str | None) -> AssetKind | None:
    if value is None:
        return None
    if isinstance(value, AssetKind):
        return value
    try:
        return AssetKind(str(value).strip().casefold())
    except ValueError as exc:
        raise ValueError(f"unsupported website asset kind: {value}") from exc


def _infer_asset_kind(
    canonical_url: str,
    *,
    tag: str,
    attribute: str,
    rel: Sequence[str],
) -> AssetKind | None:
    tag = tag.casefold()
    attribute = attribute.casefold()
    relations = {str(value).strip().casefold() for value in rel}
    suffix = PurePosixPath(urlsplit(canonical_url).path).suffix.casefold()

    if tag == "link":
        if "stylesheet" in relations:
            return AssetKind.STYLESHEET
        if "manifest" in relations:
            return AssetKind.MANIFEST
        if relations & {"icon", "apple-touch-icon", "mask-icon"}:
            return AssetKind.IMAGE
        if "preload" in relations:
            if "font" in relations:
                return AssetKind.FONT
    if tag == "script" and attribute == "src":
        return AssetKind.SCRIPT
    if tag in {"img", "picture"} or attribute in {"srcset", "poster"}:
        return AssetKind.IMAGE
    if tag in {"audio", "video", "track"}:
        return AssetKind.MEDIA

    if suffix in {".css"}:
        return AssetKind.STYLESHEET
    if suffix in {".js", ".mjs", ".cjs"}:
        return AssetKind.SCRIPT
    if suffix in {".woff", ".woff2", ".ttf", ".otf", ".eot"}:
        return AssetKind.FONT
    if suffix in {
        ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".avif", ".ico",
    }:
        return AssetKind.IMAGE
    if suffix in {
        ".mp3", ".m4a", ".ogg", ".wav", ".flac", ".mp4", ".webm", ".mov",
    }:
        return AssetKind.MEDIA
    if suffix in {".webmanifest"}:
        return AssetKind.MANIFEST
    if attribute in {"src", "srcset", "poster"}:
        return AssetKind.OTHER
    return None


def classify_reference(
    value: str,
    *,
    document_url: str,
    base_url: str | None = None,
    source_origin: str | None = None,
    tag: str = "",
    attribute: str = "",
    rel: Sequence[str] = (),
    asset_kind: AssetKind | str | None = None,
    active_endpoint: bool = False,
    approved_third_party_origins: Iterable[str] = (),
    query_policy: CanonicalUrlPolicy | None = None,
) -> ReferenceClassification:
    """Classify one extracted URL without conflating discovery and approval."""

    original = str(value or "").strip()
    if not original:
        raise ValueError("website reference is required")
    parsed_original = urlsplit(original)
    scheme = parsed_original.scheme.casefold()
    if scheme and scheme not in {"http", "https"}:
        return ReferenceClassification(
            original=original,
            canonical_url=None,
            kind=ReferenceKind.IGNORED_SCHEME,
            ignored_scheme=scheme,
        )

    resolution_base = (
        canonicalize_url(base_url, base_url=document_url, query_policy=query_policy)
        if base_url is not None
        else document_url
    )
    canonical = canonicalize_url(
        original,
        base_url=resolution_base,
        query_policy=query_policy,
    )
    if active_endpoint or attribute.casefold() in {"action", "formaction", "ping"}:
        return ReferenceClassification(
            original=original,
            canonical_url=canonical,
            kind=ReferenceKind.ACTIVE_ENDPOINT,
        )

    resolved_asset_kind = (
        _coerce_asset_kind(asset_kind)
        or _infer_asset_kind(
            canonical,
            tag=tag,
            attribute=attribute,
            rel=rel,
        )
    )
    own_origin = canonical_origin(source_origin or document_url)
    target_origin = canonical_origin(canonical)
    if target_origin == own_origin:
        kind = (
            ReferenceKind.FIRST_PARTY_ASSET
            if resolved_asset_kind is not None
            else ReferenceKind.INTERNAL_PAGE
        )
        return ReferenceClassification(
            original=original,
            canonical_url=canonical,
            kind=kind,
            asset_kind=resolved_asset_kind,
        )

    if resolved_asset_kind is None:
        return ReferenceClassification(
            original=original,
            canonical_url=canonical,
            kind=ReferenceKind.EXTERNAL_NAVIGATION,
        )

    approved = {
        canonical_origin(item) for item in approved_third_party_origins
    }
    is_approved = target_origin in approved
    return ReferenceClassification(
        original=original,
        canonical_url=canonical,
        kind=(
            ReferenceKind.APPROVED_THIRD_PARTY_ASSET
            if is_approved
            else ReferenceKind.EXTERNAL_NAVIGATION
        ),
        asset_kind=resolved_asset_kind,
        third_party_approved=is_approved,
    )


def _safe_path_segment(value: str) -> tuple[str, bool]:
    safe = _SAFE_PATH_SEGMENT_RE.sub("-", value.replace("%", "_")).strip("-")
    changed = safe != value
    if not safe or safe in {".", ".."}:
        safe = "_"
        changed = True
    stem = safe.split(".", 1)[0].casefold()
    if stem in _WINDOWS_RESERVED_NAMES:
        safe = "_" + safe
        changed = True
    return safe, changed


def _append_path_hash(path: str, canonical_url: str) -> str:
    digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:10]
    parent, _, filename = path.rpartition("/")
    stem, suffix = posix_split_suffix(filename)
    hashed = f"{stem}--{digest}{suffix}"
    return f"{parent}/{hashed}" if parent else hashed


def posix_split_suffix(filename: str) -> tuple[str, str]:
    """Split only the last POSIX filename suffix."""

    suffix = PurePosixPath(filename).suffix
    return (
        (filename[:-len(suffix)], suffix)
        if suffix
        else (filename, "")
    )


def _local_page_candidate(canonical_url: str) -> tuple[str, bool]:
    parsed = urlsplit(canonical_url)
    raw_segments = [item for item in parsed.path.split("/") if item]
    segments: list[str] = []
    changed = False
    for raw in raw_segments:
        safe, segment_changed = _safe_path_segment(raw)
        segments.append(safe)
        changed = changed or segment_changed

    trailing_slash = parsed.path.endswith("/")
    if not segments:
        segments = ["index.html"]
    elif trailing_slash:
        segments.append("index.html")
    else:
        stem, suffix = posix_split_suffix(segments[-1])
        if suffix.casefold() not in {".htm", ".html"}:
            segments[-1] = segments[-1] + ".html"
    path = "/".join(segments)
    return path, changed or bool(parsed.query)


def local_page_path(canonical_url: str) -> str:
    """Return one stable local page path before cross-record collision checks."""

    canonical = canonicalize_url(canonical_url)
    candidate, needs_hash = _local_page_candidate(canonical)
    return _append_path_hash(candidate, canonical) if needs_hash else candidate


def assign_local_page_paths(urls: Iterable[str]) -> dict[str, str]:
    """Assign deterministic paths and hash every case-insensitive collision."""

    canonical_urls = sorted({canonicalize_url(value) for value in urls})
    assigned = {
        url: local_page_path(url)
        for url in canonical_urls
    }
    groups: dict[str, list[str]] = defaultdict(list)
    for url, path in assigned.items():
        groups[path.casefold()].append(url)
    for colliding in groups.values():
        if len(colliding) < 2:
            continue
        for url in colliding:
            assigned[url] = _append_path_hash(assigned[url], url)
    return assigned


def inventory_relative_paths() -> dict[str, str]:
    """Return the canonical run-workspace inventory layout."""

    return {
        "index": "inventory/index.json",
        "pages": "inventory/pages.ndjson",
        "assets": "inventory/assets.ndjson",
        "external_links": "inventory/external-links.ndjson",
        "checkpoint": "inventory/checkpoint.json",
        "complete": "inventory/complete.json",
    }


_SHA256_PATTERN = "^[0-9a-f]{64}$"
_REFERENCE_KIND_VALUES = [item.value for item in ReferenceKind]
_ASSET_KIND_VALUES = [item.value for item in AssetKind]

INVENTORY_INDEX_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version", "source_url", "canonical_origin", "effective_limits",
        "policy", "counts", "records", "status",
    ],
    "properties": {
        "schema_version": {"const": INVENTORY_SCHEMA_VERSION},
        "source_url": {"type": "string", "format": "uri", "maxLength": 4096},
        "canonical_origin": {"type": "string", "format": "uri", "maxLength": 4096},
        "effective_limits": {"type": "object"},
        "policy": {"type": "object"},
        "counts": {
            "type": "object",
            "additionalProperties": {"type": "integer", "minimum": 0},
        },
        "records": {
            "type": "object",
            "additionalProperties": False,
            "required": list(inventory_relative_paths()),
            "properties": {
                key: {"const": value}
                for key, value in inventory_relative_paths().items()
            },
        },
        "status": {
            "enum": ["initializing", "running", "bounded", "errors", "complete"],
        },
    },
}

REFERENCE_RECORD_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "record_id", "source_page_url", "original", "canonical_url", "kind",
        "asset_kind", "third_party_approved", "approval_required",
        "ignored_scheme",
    ],
    "properties": {
        "record_id": {"type": "string", "pattern": _SHA256_PATTERN},
        "source_page_url": {"type": "string", "format": "uri"},
        "original": {"type": "string", "minLength": 1, "maxLength": 8192},
        "canonical_url": {
            "oneOf": [
                {"type": "string", "format": "uri", "maxLength": 8192},
                {"type": "null"},
            ],
        },
        "kind": {"enum": _REFERENCE_KIND_VALUES},
        "asset_kind": {
            "oneOf": [{"enum": _ASSET_KIND_VALUES}, {"type": "null"}],
        },
        "third_party_approved": {"type": "boolean"},
        "approval_required": {"type": "boolean"},
        "ignored_scheme": {
            "oneOf": [{"type": "string", "minLength": 1}, {"type": "null"}],
        },
    },
}

COMPLETE_MANIFEST_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version", "status", "crawl_status", "cache_identity",
        "accepted_omissions", "index_sha256", "files",
    ],
    "properties": {
        "schema_version": {"const": INVENTORY_SCHEMA_VERSION},
        "status": {"const": "complete"},
        "crawl_status": {"enum": ["complete", "bounded", "errors"]},
        "cache_identity": {"type": "string", "pattern": _SHA256_PATTERN},
        "accepted_omissions": {"type": "array", "items": {"type": "object"}},
        "index_sha256": {"type": "string", "pattern": _SHA256_PATTERN},
        "files": {
            "type": "object",
            "minProperties": 1,
            "additionalProperties": {
                "type": "string",
                "pattern": _SHA256_PATTERN,
            },
        },
    },
}


__all__ = [
    "AssetKind",
    "CRAWL_LIMIT_BOUNDS",
    "CRAWL_LIMIT_FIELDS",
    "COMPLETE_MANIFEST_SCHEMA",
    "CrawlLimits",
    "CanonicalUrlPolicy",
    "DEFAULT_CRAWL_LIMITS",
    "INVENTORY_INDEX_SCHEMA",
    "INVENTORY_SCHEMA_VERSION",
    "REFERENCE_RECORD_SCHEMA",
    "ReferenceClassification",
    "ReferenceKind",
    "SourceRightsDeclaration",
    "assign_local_page_paths",
    "canonical_origin",
    "canonicalize_url",
    "classify_reference",
    "inventory_relative_paths",
    "local_page_path",
    "normalize_url_patterns",
    "parse_labelled_crawl_options",
    "prepare_crawl_contract",
    "stable_record_id",
]
