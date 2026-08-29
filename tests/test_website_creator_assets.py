"""Website Creator generalized streaming asset contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tasks.ai.workflow.website_creator_tasks import SaveWebsiteAssetHandler
from tools import fs_http


class _AssetRelay:
    _service_id = "relay-test"

    def __init__(self, payload: bytes = b"\x89PNG\r\n\x1a\nasset"):
        self.payload = payload
        self.files: dict[str, bytes] = {}
        self.fetches: list[dict] = []

    def exists(self, path, local=False):
        assert local is False
        return path in self.files

    def read_file(self, path, local=False):
        assert local is False
        return self.files[path]

    def atomic_write_file(self, path, content, local=False):
        assert local is False
        self.files[path] = bytes(content)
        return {"written": len(content)}

    def stat(self, path, local=False):
        assert local is False
        return SimpleNamespace(size=len(self.files[path]))

    def hash_file(self, path, local=False):
        assert local is False
        data = self.files[path]
        return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    def http_fetch_to_file(self, url, path, **kwargs):
        self.fetches.append({"url": url, "path": path, **kwargs})
        if len(self.payload) > int(kwargs["max_bytes"]):
            raise ValueError("HTTP response exceeds configured byte limit")
        self.files[path] = self.payload
        return {
            "status": 200,
            "headers": {"Content-Type": "image/png"},
            "url": url,
            "bytes": len(self.payload),
            "sha256": hashlib.sha256(self.payload).hexdigest(),
            "content_type": "image/png",
            "saved": True,
        }


def _handler(relay, **policy):
    handler = SaveWebsiteAssetHandler(
        "/workspace/pawflow-sites/run-1",
        source_url="https://example.com/",
        rights={
            "basis": "owner",
            "allowed_asset_kinds": [
                "image", "stylesheet", "script", "font", "media", "manifest",
            ],
        },
        total_budget_bytes=policy.pop("total_budget_bytes", 256 * 1024 * 1024),
        **policy,
    )
    handler.set_fs_service(relay)
    return handler


def _args(**overrides):
    values = {
        "url": "https://example.com/logo.png",
        "path": "assets/logo.png",
        "kind": "image",
    }
    values.update(overrides)
    return values


def test_asset_schema_requires_kind_and_lists_only_downloadable_kinds():
    schema = _handler(_AssetRelay()).parameters_schema
    assert schema["required"] == ["url", "path", "kind"]
    assert schema["properties"]["kind"]["enum"] == [
        "image", "stylesheet", "script", "font", "media", "manifest",
    ]


def test_asset_download_streams_with_kind_limit_and_replays_without_network():
    relay = _AssetRelay()
    handler = _handler(relay)
    first = json.loads(handler.execute(_args()))
    second = json.loads(handler.execute(_args()))

    assert first == second
    assert len(relay.fetches) == 1
    assert relay.fetches[0]["public_only"] is True
    assert relay.fetches[0]["expected_kind"] == "image"
    assert relay.fetches[0]["max_bytes"] == 12 * 1024 * 1024
    assert first["sha256"] == hashlib.sha256(relay.payload).hexdigest()
    checkpoint = json.loads(relay.files[
        "/workspace/pawflow-sites/run-1/assets/manifest/checkpoint.json"
    ])
    assert checkpoint["count"] == 1
    batch = json.loads(relay.files[
        "/workspace/pawflow-sites/run-1/assets/manifest/batch-0001.json"
    ])
    assert len(batch["entries"]) == 1


def test_asset_budget_is_durable_and_blocks_before_second_fetch():
    relay = _AssetRelay(payload=b"12345678")
    handler = _handler(relay, total_budget_bytes=12)
    handler.execute(_args(path="assets/one.png"))
    with pytest.raises(ValueError, match="byte limit"):
        handler.execute(_args(path="assets/two.png", url="https://example.com/two.png"))
    assert len(relay.fetches) == 2


def test_asset_third_party_requires_approved_origin_license_and_provenance(
    monkeypatch,
):
    monkeypatch.setattr(
        "tasks.ai.workflow.website_creator_tasks.validate_public_website_url",
        lambda value: value,
    )
    relay = _AssetRelay()
    handler = _handler(
        relay,
        approved_third_party_origins=["https://cdn.example.net"],
    )
    args = _args(url="https://cdn.example.net/logo.png")
    with pytest.raises(ValueError, match="third-party"):
        handler.execute(args)
    accepted = json.loads(handler.execute({
        **args,
        "third_party_approved": True,
        "immutable_url": "https://cdn.example.net/logo.v1.png",
        "license": "MIT",
        "provenance": "Vendor release 1.0 asset manifest.",
    }))
    assert accepted["policy"]["origin"] == "https://cdn.example.net"


def test_asset_rejects_tracking_and_source_application_bundles_by_default():
    handler = _handler(_AssetRelay(payload=b"console.log('x')"))
    with pytest.raises(ValueError, match="tracking"):
        handler.execute(_args(
            kind="script",
            url="https://example.com/analytics.js",
            path="assets/analytics.js",
            source_application_bundle=False,
        ))
    with pytest.raises(ValueError, match="application bundle"):
        handler.execute(_args(
            kind="script",
            url="https://example.com/app.js",
            path="assets/app.js",
        ))


def _fake_response(payload, content_type, url):
    class Response:
        status = 200
        headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(payload)),
        }

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size=-1):
            if not hasattr(self, "_done"):
                self._done = True
                return payload
            return b""

        def geturl(self):
            return url

    return Response()


def test_relay_validates_mime_before_atomic_publish(monkeypatch, tmp_path):
    target = tmp_path / "site.css"
    target.write_bytes(b"old")

    class Opener:
        def open(self, _request, timeout=0):
            return _fake_response(
                b"<html>not css</html>",
                "text/html",
                "https://example.com/site.css",
            )

    monkeypatch.setattr(fs_http, "_public_http_url", lambda value: value)
    monkeypatch.setattr("urllib.request.build_opener", lambda *_args: Opener())
    result = fs_http.action_http_fetch_to_file(
        str(tmp_path),
        str(target),
        {
            "url": "https://example.com/site.css",
            "max_bytes": 1024,
            "public_only": True,
            "expected_kind": "stylesheet",
        },
    )
    assert result["ok"] is False
    assert "content type" in result["error"].lower()
    assert target.read_bytes() == b"old"
    assert list(tmp_path.glob("*.part")) == []


@pytest.mark.parametrize(
    ("kind", "payload", "content_type", "suffix"),
    [
        ("stylesheet", b"body { color: red; }", "text/css", ".css"),
        ("script", b"console.log('ok');", "application/javascript", ".js"),
        ("font", b"wOFF" + b"0" * 32, "font/woff", ".woff"),
        ("media", b"\x00\x00\x00\x18ftypmp42" + b"0" * 32, "video/mp4", ".mp4"),
        ("manifest", b'{"name":"Site"}', "application/manifest+json", ".webmanifest"),
    ],
)
def test_relay_accepts_recognized_asset_kinds(
    monkeypatch, tmp_path, kind, payload, content_type, suffix,
):
    target = tmp_path / ("asset" + suffix)

    class Opener:
        def open(self, _request, timeout=0):
            return _fake_response(payload, content_type, "https://example.com/a" + suffix)

    monkeypatch.setattr(fs_http, "_public_http_url", lambda value: value)
    monkeypatch.setattr("urllib.request.build_opener", lambda *_args: Opener())
    result = fs_http.action_http_fetch_to_file(
        str(tmp_path),
        str(target),
        {
            "url": "https://example.com/a" + suffix,
            "max_bytes": 1024,
            "public_only": True,
            "expected_kind": kind,
        },
    )
    assert result["ok"] is True
    assert result["content_type"]
    assert target.read_bytes() == payload
