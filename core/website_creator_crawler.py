"""Pure parsing and identity helpers for the Website Creator crawler."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import gzip
import hashlib
from io import BytesIO
import json
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

import defusedxml.ElementTree as SafeET

from core.website_creator_contracts import (
    CanonicalUrlPolicy,
    ReferenceClassification,
    ReferenceKind,
    canonical_origin,
    canonicalize_url,
    classify_reference,
)


CRAWLER_SCHEMA_VERSION = 1
CRAWLER_USER_AGENT = "PawFlow-Website-Creator"
MAX_HTML_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_ROBOTS_BYTES = 1024 * 1024
MAX_SITEMAP_COMPRESSED_BYTES = 2 * 1024 * 1024
MAX_SITEMAP_DECOMPRESSED_BYTES = 8 * 1024 * 1024
MAX_SITEMAP_URLS = 20_000
MAX_SITEMAP_NESTING = 3


def stable_json_bytes(value: Any) -> bytes:
    """Serialize a cache or checkpoint contract deterministically."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def crawl_cache_identity(source_url: str, crawl: Mapping[str, Any]) -> str:
    """Bind reuse to every crawl input that changes the observable inventory."""

    payload = {
        "schema_version": CRAWLER_SCHEMA_VERSION,
        "source_url": canonicalize_url(source_url),
        "effective_limits": dict(crawl.get("effective_limits") or {}),
        "include_url_patterns": list(crawl.get("include_url_patterns") or []),
        "exclude_url_patterns": list(crawl.get("exclude_url_patterns") or []),
        "rights": crawl.get("rights"),
    }
    return sha256_bytes(stable_json_bytes(payload))


@dataclass(frozen=True)
class RobotsPolicy:
    """Selected robots group plus global sitemap declarations."""

    rules: tuple[tuple[bool, str], ...] = ()
    sitemaps: tuple[str, ...] = ()
    crawl_delay_ms: int = 0

    def allows(self, url: str) -> bool:
        parsed = urlsplit(canonicalize_url(url))
        candidate = parsed.path + (("?" + parsed.query) if parsed.query else "")
        matches = [
            (len(pattern), allowed)
            for allowed, pattern in self.rules
            if pattern and candidate.startswith(pattern)
        ]
        if not matches:
            return True
        longest = max(length for length, _allowed in matches)
        return any(allowed for length, allowed in matches if length == longest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rules": [
                {"allow": allowed, "path": path}
                for allowed, path in self.rules
            ],
            "sitemaps": list(self.sitemaps),
            "crawl_delay_ms": self.crawl_delay_ms,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "RobotsPolicy":
        raw = dict(value or {})
        return cls(
            rules=tuple(
                (bool(item.get("allow")), str(item.get("path") or ""))
                for item in raw.get("rules") or []
                if isinstance(item, Mapping)
            ),
            sitemaps=tuple(str(item) for item in raw.get("sitemaps") or []),
            crawl_delay_ms=max(0, int(raw.get("crawl_delay_ms") or 0)),
        )


def parse_robots(
    content: bytes,
    source_url: str,
    *,
    user_agent: str = CRAWLER_USER_AGENT,
) -> RobotsPolicy:
    """Parse the most specific applicable robots group without fetching."""

    text = content.decode("utf-8", errors="replace")
    groups: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    sitemaps: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        name, raw_value = line.split(":", 1)
        directive = name.strip().casefold()
        value = raw_value.strip()
        if directive == "user-agent":
            if current is None or current["rules"] or current["delay"] is not None:
                current = {"agents": [], "rules": [], "delay": None}
                groups.append(current)
            current["agents"].append(value.casefold())
        elif directive in {"allow", "disallow"} and current is not None:
            if value:
                current["rules"].append((directive == "allow", value))
        elif directive == "crawl-delay" and current is not None:
            try:
                delay = float(value)
            except ValueError:
                continue
            if delay >= 0:
                current["delay"] = min(int(delay * 1000), 86_400_000)
        elif directive == "sitemap" and value:
            try:
                sitemap = canonicalize_url(value, base_url=source_url)
            except ValueError:
                continue
            if canonical_origin(sitemap) == canonical_origin(source_url):
                sitemaps.append(sitemap)

    token = user_agent.casefold()
    matches: list[tuple[int, dict[str, Any]]] = []
    for group in groups:
        specificity = max(
            (
                1 if agent == "*" else len(agent)
                if token.startswith(agent) or agent in token
                else 0
            )
            for agent in group["agents"]
        ) if group["agents"] else 0
        if specificity:
            matches.append((specificity, group))
    if not matches:
        return RobotsPolicy(sitemaps=tuple(dict.fromkeys(sitemaps)))
    best = max(score for score, _group in matches)
    selected = [group for score, group in matches if score == best]
    rules = tuple(rule for group in selected for rule in group["rules"])
    delay = max((int(group["delay"] or 0) for group in selected), default=0)
    return RobotsPolicy(
        rules=rules,
        sitemaps=tuple(dict.fromkeys(sitemaps)),
        crawl_delay_ms=delay,
    )


def _bounded_gzip(content: bytes, maximum: int) -> bytes:
    with gzip.GzipFile(fileobj=BytesIO(content)) as stream:
        decoded = stream.read(maximum + 1)
    if len(decoded) > maximum:
        raise ValueError("sitemap decompressed size exceeds limit")
    return decoded


def parse_sitemap(
    content: bytes,
    sitemap_url: str,
    *,
    compressed: bool = False,
    max_urls: int = MAX_SITEMAP_URLS,
) -> tuple[str, list[str]]:
    """Return sitemap kind and bounded same-origin canonical locations."""

    if len(content) > MAX_SITEMAP_COMPRESSED_BYTES:
        raise ValueError("sitemap compressed size exceeds limit")
    if compressed or content.startswith(b"\x1f\x8b"):
        content = _bounded_gzip(content, MAX_SITEMAP_DECOMPRESSED_BYTES)
    elif len(content) > MAX_SITEMAP_DECOMPRESSED_BYTES:
        raise ValueError("sitemap size exceeds limit")
    try:
        root = SafeET.fromstring(content)
    except Exception as exc:
        raise ValueError("sitemap XML is invalid or unsafe") from exc
    root_kind = str(root.tag).rsplit("}", 1)[-1].casefold()
    if root_kind not in {"urlset", "sitemapindex"}:
        raise ValueError("sitemap root must be urlset or sitemapindex")
    values: list[str] = []
    source_origin = canonical_origin(sitemap_url)
    for element in root.iter():
        if str(element.tag).rsplit("}", 1)[-1].casefold() != "loc":
            continue
        if len(values) >= max_urls:
            raise ValueError("sitemap URL count exceeds limit")
        try:
            value = canonicalize_url(str(element.text or "").strip())
        except ValueError:
            continue
        if canonical_origin(value) == source_origin and value not in values:
            values.append(value)
    return root_kind, values


def parse_retry_after(value: str, now: float) -> float | None:
    """Return a non-negative retry delay for seconds or an HTTP date."""

    text = str(value or "").strip()
    if not text:
        return None
    if text.isdecimal():
        return float(int(text))
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, parsed.timestamp() - now)


def extract_css_references(css: str) -> list[str]:
    """Token-scan CSS url() and quoted @import references."""

    text = str(css or "")
    values: list[str] = []
    index = 0
    length = len(text)

    def skip_space(position: int) -> int:
        while position < length and text[position].isspace():
            position += 1
        return position

    def quoted(position: int) -> tuple[str, int]:
        quote = text[position]
        position += 1
        out: list[str] = []
        while position < length:
            char = text[position]
            if char == "\\" and position + 1 < length:
                out.append(text[position + 1])
                position += 2
                continue
            if char == quote:
                return "".join(out), position + 1
            out.append(char)
            position += 1
        return "", length

    while index < length:
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = length if end < 0 else end + 2
            continue
        if text[index] in {"'", '"'}:
            _ignored, index = quoted(index)
            continue
        if text[index:index + 3].casefold() == "url":
            cursor = skip_space(index + 3)
            if cursor < length and text[cursor] == "(":
                cursor = skip_space(cursor + 1)
                if cursor < length and text[cursor] in {"'", '"'}:
                    value, cursor = quoted(cursor)
                else:
                    start = cursor
                    while cursor < length and text[cursor] != ")":
                        cursor += 1
                    value = text[start:cursor].strip()
                if value and value not in values:
                    values.append(value)
                index = cursor + 1
                continue
        if text[index:index + 7].casefold() == "@import":
            cursor = skip_space(index + 7)
            if cursor < length and text[cursor] in {"'", '"'}:
                value, cursor = quoted(cursor)
                if value and value not in values:
                    values.append(value)
                index = cursor
                continue
        index += 1
    return values


def _srcset_values(value: str) -> Iterable[str]:
    for candidate in str(value or "").split(","):
        url = candidate.strip().split(None, 1)[0] if candidate.strip() else ""
        if url:
            yield url


@dataclass(frozen=True)
class HtmlInventory:
    title: str
    canonical_url: str
    base_url: str
    references: tuple[ReferenceClassification, ...]


def extract_html_inventory(
    content: bytes,
    page_url: str,
    *,
    source_origin: str,
    approved_third_party_origins: Iterable[str] = (),
    query_policy: CanonicalUrlPolicy | None = None,
) -> HtmlInventory:
    """Extract bounded structural references from one already-bounded HTML body."""

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content, "html.parser")
    title = str(soup.title.string if soup.title and soup.title.string else "").strip()
    base_tag = soup.find("base", href=True)
    try:
        base_url = canonicalize_url(
            str(base_tag.get("href")), base_url=page_url, query_policy=query_policy,
        ) if base_tag is not None else canonicalize_url(page_url, query_policy=query_policy)
    except ValueError:
        base_url = canonicalize_url(page_url, query_policy=query_policy)
    canonical_url = canonicalize_url(page_url, query_policy=query_policy)
    canonical_tag = soup.find(
        "link",
        href=True,
        rel=lambda value: value and "canonical" in {
            str(item).casefold() for item in (value if isinstance(value, list) else [value])
        },
    )
    if canonical_tag is not None:
        try:
            candidate = canonicalize_url(
                str(canonical_tag.get("href")), base_url=base_url,
                query_policy=query_policy,
            )
            if canonical_origin(candidate) == canonical_origin(source_origin):
                canonical_url = candidate
        except ValueError:
            pass

    references: list[ReferenceClassification] = []
    seen: set[tuple[str | None, str, str | None]] = set()

    def add(value: str, tag: str, attribute: str, rel=(), active=False) -> None:
        try:
            result = classify_reference(
                value,
                document_url=page_url,
                base_url=base_url,
                source_origin=source_origin,
                tag=tag,
                attribute=attribute,
                rel=tuple(rel),
                active_endpoint=active,
                approved_third_party_origins=approved_third_party_origins,
                query_policy=query_policy,
            )
        except ValueError:
            return
        key = (
            result.canonical_url,
            result.kind.value,
            result.asset_kind.value if result.asset_kind else None,
        )
        if key not in seen:
            seen.add(key)
            references.append(result)

    for tag in soup.find_all(True):
        name = str(tag.name or "").casefold()
        rel = tag.get("rel") or ()
        if isinstance(rel, str):
            rel = rel.split()
        if tag.has_attr("href") and name != "base":
            add(str(tag.get("href")), name, "href", rel=rel)
        if tag.has_attr("src"):
            add(str(tag.get("src")), name, "src", rel=rel)
        if tag.has_attr("srcset"):
            for value in _srcset_values(str(tag.get("srcset"))):
                add(value, name, "srcset", rel=rel)
        if tag.has_attr("poster"):
            add(str(tag.get("poster")), name, "poster", rel=rel)
        if tag.has_attr("action"):
            add(str(tag.get("action")), name, "action", active=True)
        if tag.has_attr("formaction"):
            add(str(tag.get("formaction")), name, "formaction", active=True)
        if tag.has_attr("ping"):
            for value in str(tag.get("ping") or "").split():
                add(value, name, "ping", active=True)
        if tag.has_attr("style"):
            for value in extract_css_references(str(tag.get("style"))):
                add(value, name, "style")
    for style in soup.find_all("style"):
        for value in extract_css_references(style.get_text("", strip=False)):
            add(value, "style", "text")
    return HtmlInventory(
        title=title[:1000],
        canonical_url=canonical_url,
        base_url=base_url,
        references=tuple(references),
    )


__all__ = [
    "CRAWLER_SCHEMA_VERSION",
    "CRAWLER_USER_AGENT",
    "HtmlInventory",
    "MAX_HTML_RESPONSE_BYTES",
    "MAX_ROBOTS_BYTES",
    "MAX_SITEMAP_COMPRESSED_BYTES",
    "MAX_SITEMAP_DECOMPRESSED_BYTES",
    "MAX_SITEMAP_NESTING",
    "MAX_SITEMAP_URLS",
    "RobotsPolicy",
    "crawl_cache_identity",
    "extract_css_references",
    "extract_html_inventory",
    "parse_retry_after",
    "parse_robots",
    "parse_sitemap",
    "sha256_bytes",
    "stable_json_bytes",
]
