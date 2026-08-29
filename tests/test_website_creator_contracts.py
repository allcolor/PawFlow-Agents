"""Canonical inventory and URL policy contracts for Website Creator."""

from __future__ import annotations

import jsonschema
import pytest

from core.website_creator_contracts import (
    COMPLETE_MANIFEST_SCHEMA,
    INVENTORY_INDEX_SCHEMA,
    REFERENCE_RECORD_SCHEMA,
    AssetKind,
    CanonicalUrlPolicy,
    ReferenceKind,
    assign_local_page_paths,
    canonical_origin,
    canonicalize_url,
    classify_reference,
    inventory_relative_paths,
    local_page_path,
    stable_record_id,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("HTTPS://Example.COM:443/a/./b/../c#part", "https://example.com/a/c"),
        ("http://Example.COM:80", "http://example.com/"),
        ("https://Example.COM:8443/", "https://example.com:8443/"),
        ("https://example.com/%7euser/%2f", "https://example.com/~user/%2F"),
        ("https://bücher.example/été", "https://xn--bcher-kva.example/%C3%A9t%C3%A9"),
        ("https://[2001:0db8::1]:443/a", "https://[2001:db8::1]/a"),
    ],
)
def test_canonicalize_url_normalizes_equivalent_urls(value, expected):
    assert canonicalize_url(value) == expected


def test_canonicalize_url_preserves_query_by_default_and_applies_explicit_policy():
    value = "https://example.com/p?b=2&utm_source=x&a=1&token=a%2fb#fragment"
    assert canonicalize_url(value) == (
        "https://example.com/p?b=2&utm_source=x&a=1&token=a%2Fb"
    )
    policy = CanonicalUrlPolicy(
        drop_query_parameters=frozenset({"B"}),
        allow_query_parameters=frozenset({"a", "utm_source", "token"}),
        drop_known_tracking_parameters=True,
    )
    assert canonicalize_url(value, query_policy=policy) == (
        "https://example.com/p?a=1&token=a%2Fb"
    )


def test_canonicalize_url_keeps_trailing_slash_and_redirect_destinations_distinct():
    assert canonicalize_url("https://example.com/about") != canonicalize_url(
        "https://example.com/about/"
    )
    assert canonicalize_url(
        "../final#ignored", base_url="https://example.com/old/path"
    ) == "https://example.com/final"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "relative/path",
        "file:///etc/passwd",
        "https://user:secret@example.com/",
        "https://example.com:bad/",
    ],
)
def test_canonicalize_url_rejects_invalid_or_unsafe_shapes(value):
    with pytest.raises(ValueError):
        canonicalize_url(value)


def test_base_href_is_resolved_before_the_reference():
    result = classify_reference(
        "images/logo.png",
        document_url="https://example.com/docs/page.html",
        base_url="/static/v2/",
        tag="img",
        attribute="src",
    )
    assert result.canonical_url == "https://example.com/static/v2/images/logo.png"
    assert result.kind is ReferenceKind.FIRST_PARTY_ASSET


@pytest.mark.parametrize(
    ("kwargs", "kind", "asset_kind", "approval_required"),
    [
        (
            {"value": "/about", "tag": "a", "attribute": "href"},
            ReferenceKind.INTERNAL_PAGE,
            None,
            False,
        ),
        (
            {"value": "/app.css", "tag": "link", "attribute": "href", "rel": ["stylesheet"]},
            ReferenceKind.FIRST_PARTY_ASSET,
            AssetKind.STYLESHEET,
            False,
        ),
        (
            {"value": "https://cdn.example.net/app.js", "tag": "script", "attribute": "src"},
            ReferenceKind.EXTERNAL_NAVIGATION,
            AssetKind.SCRIPT,
            True,
        ),
        (
            {
                "value": "https://cdn.example.net/app.js",
                "tag": "script",
                "attribute": "src",
                "approved_third_party_origins": ["https://CDN.example.net:443"],
            },
            ReferenceKind.APPROVED_THIRD_PARTY_ASSET,
            AssetKind.SCRIPT,
            False,
        ),
        (
            {"value": "https://elsewhere.example/page", "tag": "a", "attribute": "href"},
            ReferenceKind.EXTERNAL_NAVIGATION,
            None,
            False,
        ),
        (
            {"value": "/submit", "tag": "form", "attribute": "action"},
            ReferenceKind.ACTIVE_ENDPOINT,
            None,
            False,
        ),
        (
            {"value": "mailto:hello@example.com", "tag": "a", "attribute": "href"},
            ReferenceKind.IGNORED_SCHEME,
            None,
            False,
        ),
    ],
)
def test_every_reference_classification(kwargs, kind, asset_kind, approval_required):
    result = classify_reference(
        document_url="https://example.com/index.html",
        **kwargs,
    )
    assert result.kind is kind
    assert result.asset_kind is asset_kind
    assert result.approval_required is approval_required


def test_local_paths_are_stable_query_aware_and_collision_safe():
    urls = [
        "https://example.com/",
        "https://example.com/about",
        "https://example.com/about/",
        "https://example.com/about.html",
        "https://example.com/About",
        "https://example.com/search?q=one",
        "https://example.com/search?q=two",
        "https://example.com/été",
    ]
    assigned = assign_local_page_paths(urls)
    assert assigned["https://example.com/"] == "index.html"
    assert assigned["https://example.com/about/"] == "about/index.html"
    assert len({path.casefold() for path in assigned.values()}) == len(assigned)
    assert all(".." not in path.split("/") for path in assigned.values())
    assert local_page_path("https://example.com/search?q=one").endswith(".html")
    assert assign_local_page_paths(reversed(urls)) == assigned


def test_inventory_schemas_are_closed_and_bind_canonical_paths():
    paths = inventory_relative_paths()
    index = {
        "schema_version": 1,
        "source_url": "https://example.com/",
        "canonical_origin": canonical_origin("https://example.com/"),
        "effective_limits": {"max_pages": 100},
        "policy": {"rights_declared": True},
        "counts": {"pages": 1, "assets": 0, "errors": 0},
        "records": paths,
        "status": "complete",
    }
    jsonschema.validate(index, INVENTORY_INDEX_SCHEMA)

    classification = classify_reference(
        "/about", document_url="https://example.com/"
    )
    record = {
        "record_id": stable_record_id(classification.kind, classification.canonical_url),
        "source_page_url": "https://example.com/",
        **classification.to_record(),
    }
    jsonschema.validate(record, REFERENCE_RECORD_SCHEMA)

    digest = "a" * 64
    jsonschema.validate(
        {
            "schema_version": 1,
            "status": "complete",
            "crawl_status": "complete",
            "cache_identity": digest,
            "accepted_omissions": [],
            "index_sha256": digest,
            "files": {value: digest for value in paths.values() if value != paths["complete"]},
        },
        COMPLETE_MANIFEST_SCHEMA,
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**index, "unexpected": True}, INVENTORY_INDEX_SCHEMA)
