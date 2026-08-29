"""Pure parser tests for the deterministic Website Creator crawler."""

from __future__ import annotations

import gzip

import pytest

from core.website_creator_contracts import AssetKind, ReferenceKind
from core.website_creator_crawler import (
    extract_css_references,
    extract_html_inventory,
    parse_retry_after,
    parse_robots,
    parse_sitemap,
)


def test_robots_selects_specific_group_allow_precedence_and_sitemaps():
    policy = parse_robots(b"""
User-agent: *
Disallow: /private
Crawl-delay: 1

User-agent: PawFlow-Website-Creator
Disallow: /private
Allow: /private/public
Crawl-delay: 2.5
Sitemap: https://example.com/sitemap.xml
Sitemap: https://other.example/sitemap.xml
""", "https://example.com/")
    assert policy.allows("https://example.com/private/no") is False
    assert policy.allows("https://example.com/private/public/page") is True
    assert policy.crawl_delay_ms == 2500
    assert policy.sitemaps == ("https://example.com/sitemap.xml",)


def test_sitemap_parses_urlset_and_bounded_gzip_index():
    urlset = b"""<?xml version='1.0'?>
<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
  <url><loc>https://example.com/a</loc></url>
  <url><loc>https://other.example/no</loc></url>
</urlset>"""
    kind, values = parse_sitemap(urlset, "https://example.com/sitemap.xml")
    assert kind == "urlset"
    assert values == ["https://example.com/a"]

    index = gzip.compress(b"""<sitemapindex>
      <sitemap><loc>https://example.com/child.xml</loc></sitemap>
    </sitemapindex>""")
    kind, values = parse_sitemap(
        index, "https://example.com/sitemap.xml.gz", compressed=True,
    )
    assert kind == "sitemapindex"
    assert values == ["https://example.com/child.xml"]


def test_sitemap_rejects_entities_and_url_count_overflow():
    with pytest.raises(ValueError, match="invalid or unsafe"):
        parse_sitemap(
            b"<!DOCTYPE x [<!ENTITY e SYSTEM 'file:///etc/passwd'>]><urlset>&e;</urlset>",
            "https://example.com/sitemap.xml",
        )
    with pytest.raises(ValueError, match="count"):
        parse_sitemap(
            b"<urlset><url><loc>https://example.com/a</loc></url></urlset>",
            "https://example.com/sitemap.xml",
            max_urls=0,
        )


def test_css_scanner_ignores_comments_and_strings_but_reads_url_and_import():
    css = """
/* url('/ignored.png') */
.x::before { content: "url('/also-ignored.png')"; }
@import "theme.css";
.hero { background: url(../img/hero.webp); }
@font-face { src: url('font.woff2') format('woff2'); }
"""
    assert extract_css_references(css) == [
        "theme.css", "../img/hero.webp", "font.woff2",
    ]


def test_html_extraction_honors_base_canonical_and_all_reference_families():
    html = b"""<html><head>
      <base href='/static/'>
      <link rel='canonical' href='/canonical/'>
      <link rel='stylesheet' href='site.css'>
      <style>.hero { background: url('hero.webp') }</style>
      <title> Example page </title>
    </head><body>
      <a href='/next'>Next</a>
      <img src='logo.png' srcset='small.png 1x, large.png 2x'>
      <video poster='poster.jpg'><source src='movie.mp4'></video>
      <form action='/submit'></form>
      <a href='https://outside.example/page'>Outside</a>
      <script src='https://cdn.example/app.js'></script>
      <a href='mailto:test@example.com'>Mail</a>
    </body></html>"""
    result = extract_html_inventory(
        html,
        "https://example.com/page",
        source_origin="https://example.com",
    )
    assert result.title == "Example page"
    assert result.base_url == "https://example.com/static/"
    assert result.canonical_url == "https://example.com/canonical/"
    keyed = {
        (item.canonical_url, item.kind, item.asset_kind)
        for item in result.references
    }
    assert (
        "https://example.com/next", ReferenceKind.INTERNAL_PAGE, None,
    ) in keyed
    assert (
        "https://example.com/static/site.css",
        ReferenceKind.FIRST_PARTY_ASSET,
        AssetKind.STYLESHEET,
    ) in keyed
    assert any(item.kind is ReferenceKind.ACTIVE_ENDPOINT for item in result.references)
    assert any(item.kind is ReferenceKind.IGNORED_SCHEME for item in result.references)
    third_party = next(
        item for item in result.references
        if item.canonical_url == "https://cdn.example/app.js"
    )
    assert third_party.kind is ReferenceKind.EXTERNAL_NAVIGATION
    assert third_party.approval_required is True


def test_retry_after_accepts_seconds_and_http_date():
    assert parse_retry_after("12", 1000.0) == 12.0
    assert parse_retry_after("Thu, 01 Jan 1970 00:17:00 GMT", 1000.0) == 20.0
    assert parse_retry_after("bad", 1000.0) is None
