"""ACP registry client: schema, quarantine, matrix, cache, digests, service configs."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from core.acp import registry as reg


ROOT = Path(__file__).resolve().parents[1]

GOOSE_ARCHIVE = "https://github.com/block/goose/releases/download/v1.49.0/goose-x86_64-unknown-linux-gnu.tar.gz"


def _tar_gz(files: dict[str, bytes], *, mode: int = 0o755) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            info.mode = mode
            tf.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return buf.getvalue()


GOOSE_TAR = _tar_gz({"goose": b"#!/bin/sh\necho goose\n"})
GOOSE_SHA = hashlib.sha256(GOOSE_TAR).hexdigest()


def _index():
    return {
        "version": "1.0.0",
        "agents": [
            {
                "id": "codex-acp", "name": "Codex", "version": "1.8.0",
                "description": "ACP adapter for OpenAI's coding assistant",
                "repository": "https://github.com/agentclientprotocol/codex-acp",
                "authors": ["OpenAI"], "license": "Apache-2.0",
                "license_url": "https://github.com/agentclientprotocol/codex-acp/blob/main/LICENSE",
                "distribution": {"npx": {"package": "@agentclientprotocol/codex-acp@1.8.0"}},
            },
            {
                "id": "goose", "name": "goose", "version": "1.49.0",
                "description": "A local, extensible, open source AI agent",
                "license": "Apache-2.0",
                "distribution": {"binary": {
                    "linux-x86_64": {"archive": GOOSE_ARCHIVE, "cmd": "./goose",
                                     "args": ["acp"], "sha256": GOOSE_SHA.upper()},
                    "darwin-aarch64": {"archive": GOOSE_ARCHIVE.replace("x86_64-unknown-linux-gnu", "aarch64-apple-darwin"),
                                       "cmd": "./goose", "args": ["acp"]},
                }},
            },
            {
                "id": "fast-agent", "name": "fast-agent", "version": "0.10.1",
                "description": "Code and build agents", "license": "Apache 2.0",
                "distribution": {"uvx": {"package": "fast-agent-acp==0.10.1", "args": ["-x"],
                                          "env": {"FAST_AGENT_MODEL": "codexplan"}}},
            },
            {
                "id": "cursor", "name": "Cursor", "version": "2026.08.31",
                "description": "Cursor's coding agent",
                "distribution": {"binary": {"linux-x86_64": {
                    "archive": "https://downloads.cursor.com/lab/agent-cli-package.tar.gz",
                    "cmd": "./dist-package/cursor-agent", "args": ["acp"]}}},
            },
            {
                "id": "plain-http", "name": "Insecure", "version": "0.1.0",
                "description": "archive over http must be skipped",
                "distribution": {"binary": {"linux-x86_64": {
                    "archive": "http://example.com/agent.tar.gz", "cmd": "./agent"}}},
            },
            {
                "id": "Broken Entry", "name": "broken", "version": "1.0.0",
                "description": "invalid id, skipped", "distribution": {"npx": {"package": "x"}},
            },
        ],
    }


def _quarantine():
    return {"fast-agent": "Timeout after 120s waiting for initialize response"}


def _matrix():
    return {"date": "2026-09-03", "agents": [
        {"id": "codex-acp", "authMethods": ["agent"], "capabilities": {"loadSession": True}},
        {"id": "goose", "authMethods": ["terminal", "env"], "capabilities": {"loadSession": False}},
        {"id": "cursor", "authMethods": ["weird"], "capabilities": {}},
    ]}


class FakeFetch:
    def __init__(self, documents=None):
        self.documents = {
            reg.REGISTRY_INDEX_URL: json.dumps(_index()).encode(),
            reg.QUARANTINE_URL: json.dumps(_quarantine()).encode(),
            reg.PROTOCOL_MATRIX_URL: json.dumps(_matrix()).encode(),
            GOOSE_ARCHIVE: GOOSE_TAR,
        }
        self.documents.update(documents or {})
        self.calls: list[str] = []
        self.fail = False

    def __call__(self, url, limit):
        self.calls.append(url)
        if self.fail:
            raise ConnectionError("offline")
        if url not in self.documents:
            raise KeyError(url)
        return self.documents[url]


@pytest.fixture
def clock():
    state = {"now": 1_000_000.0}
    return state


@pytest.fixture
def cache(tmp_path, clock):
    fetch = FakeFetch()
    c = reg.RegistryCache(tmp_path / "cache", fetch=fetch, now=lambda: clock["now"])
    c.fetch = fetch  # test handle
    return c


# -- catalogue --------------------------------------------------------------------


def test_catalogue_validates_quarantines_and_annotates_from_matrix(cache):
    cat = reg.load_catalogue(cache)
    ids = [e.id for e in cat.entries]
    assert ids == ["codex-acp", "goose", "fast-agent", "cursor"], "http archive and bad id are skipped"
    assert cat.registry_version == "1.0.0" and cat.stale is False

    codex = cat.get("codex-acp")
    assert codex.packages["npx"].package == "@agentclientprotocol/codex-acp@1.8.0"
    assert codex.auth_types == ("agent",) and codex.load_session is True
    assert not codex.quarantined and not codex.proprietary

    goose = cat.get("goose")
    assert sorted(goose.binaries) == ["darwin-aarch64", "linux-x86_64"]
    assert goose.binaries["linux-x86_64"].sha256 == GOOSE_SHA  # lower-cased
    assert goose.auth_types == ("terminal", "env") and goose.load_session is False

    fast = cat.get("fast-agent")
    assert fast.quarantined and "Timeout" in fast.quarantine_reason

    cursor = cat.get("cursor")
    assert cursor.proprietary and cursor.auth_types == () and cursor.load_session is None
    row = cursor.summary()
    assert row["license"] == "proprietary" and row["distributions"] == ["binary"]
    assert "archive" not in json.dumps(row), "catalogue rows carry no download URLs"

    with pytest.raises(reg.RegistryError, match="unknown ACP registry agent"):
        cat.get("nope")


def test_cache_honours_ttl_and_falls_back_offline(cache, clock):
    reg.load_catalogue(cache)
    first = list(cache.fetch.calls)
    assert set(first) == {reg.REGISTRY_INDEX_URL, reg.QUARANTINE_URL, reg.PROTOCOL_MATRIX_URL}

    clock["now"] += 3600
    reg.load_catalogue(cache)
    assert cache.fetch.calls == first, "inside the TTL nothing is refetched"

    clock["now"] += reg.CACHE_TTL_SECONDS
    cache.fetch.fail = True
    cat = reg.load_catalogue(cache)
    assert cat.stale is True and [e.id for e in cat.entries][:1] == ["codex-acp"]

    cache.fetch.fail = False
    cat = reg.load_catalogue(cache, refresh=True)
    assert cat.stale is False


def test_no_cache_and_no_network_is_an_error(tmp_path):
    fetch = FakeFetch()
    fetch.fail = True
    c = reg.RegistryCache(tmp_path / "cold", fetch=fetch)
    with pytest.raises(reg.RegistryUnavailable, match="no cached copy"):
        reg.load_catalogue(c)


def test_registry_documents_are_https_only(tmp_path):
    c = reg.RegistryCache(tmp_path, fetch=FakeFetch())
    with pytest.raises(reg.RegistryError, match="https"):
        c.get("registry", "http://cdn.agentclientprotocol.com/registry.json")


def test_vendored_schema_is_the_upstream_agent_schema():
    schema = json.loads((ROOT / "core" / "acp" / "registry_schema.json").read_text(encoding="utf-8"))
    assert schema["required"] == ["id", "name", "version", "description", "distribution"]
    assert set(schema["properties"]["distribution"]["properties"]) == {"binary", "npx", "uvx"}
    assert tuple(schema["definitions"]["binaryDistribution"]["propertyNames"]["enum"]) == reg.PLATFORMS
    with pytest.raises(reg.RegistryError, match="distribution"):
        reg.validate_entry({"id": "x", "name": "x", "version": "1.0.0", "description": "x"})
    with pytest.raises(reg.RegistryError, match="environment name"):
        reg.validate_entry({"id": "x", "name": "x", "version": "1.0.0", "description": "x",
                            "distribution": {"npx": {"package": "p", "env": {"BAD-NAME": "1"}}}})
    for version in ("1.2.3/../../outside", "1.2.3\\..\\outside", "1.2.3..outside"):
        with pytest.raises(reg.RegistryError, match="unsafe version"):
            reg.validate_entry({
                "id": "x", "name": "x", "version": version, "description": "x",
                "distribution": {"npx": {"package": "p"}},
            })


# -- platform -------------------------------------------------------------------


@pytest.mark.parametrize("system,machine,expected", [
    ("Linux", "x86_64", "linux-x86_64"),
    ("linux", "amd64", "linux-x86_64"),
    ("Linux", "aarch64", "linux-aarch64"),
    ("Darwin", "arm64", "darwin-aarch64"),
    ("Windows", "AMD64", "windows-x86_64"),
])
def test_platform_for(system, machine, expected):
    assert reg.platform_for(system, machine) == expected


def test_platform_for_rejects_unknown():
    with pytest.raises(reg.RegistryError, match="architecture"):
        reg.platform_for("Linux", "riscv64")
    with pytest.raises(reg.RegistryError, match="operating system"):
        reg.platform_for("FreeBSD", "x86_64")
    assert reg.host_platform() in reg.PLATFORMS


# -- binaries -----------------------------------------------------------------------


def test_materialise_verifies_digest_extracts_and_reuses(cache, tmp_path):
    cat = reg.load_catalogue(cache)
    goose = cat.get("goose")
    base = tmp_path / "agents"

    done = reg.materialise_binary(goose, "linux-x86_64", base_dir=base, fetch=cache.fetch)
    assert done.directory == base / "goose" / "1.49.0" / "linux-x86_64"
    assert done.command == done.directory / "goose" and done.command.stat().st_mode & 0o111
    assert done.args == ("acp",) and done.verified is True
    assert done.archive_sha256 == GOOSE_SHA
    assert (done.directory / "archive.sha256").read_text().strip() == GOOSE_SHA
    assert not list(base.glob("**/archive.tar")), "the archive itself is not kept"

    calls = len(cache.fetch.calls)
    again = reg.materialise_binary(goose, "linux-x86_64", base_dir=base, fetch=cache.fetch)
    assert again.command == done.command and len(cache.fetch.calls) == calls

    (done.directory / "archive.sha256").write_text("0" * 64, encoding="utf-8")
    with pytest.raises(reg.RegistryError, match="cached archive digest mismatch"):
        reg.materialise_binary(goose, "linux-x86_64", base_dir=base, fetch=cache.fetch)

    with pytest.raises(reg.RegistryError, match="no binary for linux-aarch64"):
        reg.materialise_binary(goose, "linux-aarch64", base_dir=base, fetch=cache.fetch)


def test_materialise_refuses_digest_mismatch(cache, tmp_path):
    cat = reg.load_catalogue(cache)
    goose = cat.get("goose")
    tampered = FakeFetch({GOOSE_ARCHIVE: _tar_gz({"goose": b"#!/bin/sh\necho evil\n"})})
    with pytest.raises(reg.RegistryError, match="digest mismatch"):
        reg.materialise_binary(goose, "linux-x86_64", base_dir=tmp_path, fetch=tampered)
    assert not (tmp_path / "goose").exists(), "nothing is left behind on refusal"


def test_materialise_records_the_observed_digest_when_none_is_published(cache, tmp_path):
    cat = reg.load_catalogue(cache)
    cursor = cat.get("cursor")
    payload = _tar_gz({"dist-package/cursor-agent": b"#!/bin/sh\n"})
    fetch = FakeFetch({"https://downloads.cursor.com/lab/agent-cli-package.tar.gz": payload})
    done = reg.materialise_binary(cursor, "linux-x86_64", base_dir=tmp_path, fetch=fetch)
    assert done.verified is False
    assert done.archive_sha256 == hashlib.sha256(payload).hexdigest()
    config = reg.service_config_for_binary(cursor, done, cwd=str(tmp_path))
    assert config["acp_registry"]["archive_verified"] is False
    assert config["acp_registry"]["archive_sha256"] == done.archive_sha256


@pytest.mark.parametrize("url", [
    "https://example.com/agent.dmg", "https://example.com/agent.pkg",
    "https://example.com/agent.deb", "https://example.com/agent.rpm",
    "https://example.com/agent.msi", "https://example.com/Agent.AppImage",
])
def test_installer_formats_are_refused(url):
    with pytest.raises(reg.RegistryError, match="installer formats"):
        reg.archive_kind(url)


def test_archive_kinds():
    assert reg.archive_kind("https://x/a.zip") == "zip"
    assert reg.archive_kind("https://x/a.tar.gz") == "tar"
    assert reg.archive_kind("https://x/a.tbz2") == "tar"
    assert reg.archive_kind("https://x/agent") == "raw"


def test_zip_and_raw_binaries_extract_and_escapes_are_refused(tmp_path):
    entry = reg.parse_entry({
        "id": "zipper", "name": "Zipper", "version": "1.0.0", "description": "zip",
        "distribution": {"binary": {
            "linux-x86_64": {"archive": "https://x/z.zip", "cmd": "bin/agent"},
            "linux-aarch64": {"archive": "https://x/agent-arm", "cmd": "./agent-arm"},
        }},
    })
    good = FakeFetch({"https://x/z.zip": _zip({"bin/agent": b"#!/bin/sh\n"}),
                      "https://x/agent-arm": b"\x7fELF-not-really"})
    done = reg.materialise_binary(entry, "linux-x86_64", base_dir=tmp_path, fetch=good)
    assert done.command.name == "agent" and done.command.stat().st_mode & 0o111
    raw = reg.materialise_binary(entry, "linux-aarch64", base_dir=tmp_path, fetch=good)
    assert raw.command.read_bytes().startswith(b"\x7fELF") and raw.command.stat().st_mode & 0o111

    escaping = FakeFetch({"https://x/z.zip": _zip({"../outside": b"x", "bin/agent": b""})})
    with pytest.raises(reg.RegistryError, match="escapes"):
        reg.materialise_binary(entry, "linux-x86_64", base_dir=tmp_path / "esc", fetch=escaping)
    assert not (tmp_path / "outside").exists()

    missing = FakeFetch({"https://x/z.zip": _zip({"bin/other": b""})})
    with pytest.raises(reg.RegistryError, match="cmd not found"):
        reg.materialise_binary(entry, "linux-x86_64", base_dir=tmp_path / "miss", fetch=missing)

    raw_escape = reg.parse_entry({
        "id": "raw-escape", "name": "Raw escape", "version": "1.0.0",
        "description": "raw", "distribution": {"binary": {
            "linux-x86_64": {
                "archive": "https://x/raw-agent", "cmd": "sub/../../../outside",
            },
        }},
    })
    with pytest.raises(reg.RegistryError, match="cmd escapes"):
        reg.materialise_binary(
            raw_escape, "linux-x86_64", base_dir=tmp_path / "raw",
            fetch=FakeFetch({"https://x/raw-agent": b"binary"}),
        )
    assert not (tmp_path / "raw" / "raw-escape" / "outside").exists()


@pytest.mark.parametrize("limit", [6, 5])
def test_archive_download_streams_to_disk_and_hashes(monkeypatch, tmp_path, limit):
    class Response:
        closed = False

        def close(self):
            self.closed = True

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            assert chunk_size == 64 * 1024
            yield b"abc"
            yield b""
            yield b"def"

    response = Response()
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: response)
    destination = tmp_path / "archive.bin"

    if limit < 6:
        with pytest.raises(reg.RegistryError, match="exceeds"):
            reg._download_to_file("https://example.com/archive.bin", limit, destination)
        assert response.closed
        return

    observed = reg._download_to_file(
        "https://example.com/archive.bin", limit, destination)

    assert destination.read_bytes() == b"abcdef"
    assert observed == hashlib.sha256(b"abcdef").hexdigest()
    assert response.closed


def test_zip_extraction_refuses_an_expanded_archive_over_the_limit(monkeypatch, tmp_path):
    archive = tmp_path / "large.zip"
    archive.write_bytes(_zip({"bin/agent": b"12345"}))
    monkeypatch.setattr(reg, "MAX_ARCHIVE_BYTES", 4)

    with pytest.raises(reg.RegistryError, match="expanded archive exceeds"):
        reg._safe_extract_zip(archive, tmp_path / "expanded")


# -- service configs -----------------------------------------------------------------


def test_package_service_config_needs_the_runner(cache, tmp_path):
    cat = reg.load_catalogue(cache)
    codex = cat.get("codex-acp")

    config = reg.service_config_for_package(
        codex, "npx", cwd=str(tmp_path), which=lambda name: f"/usr/local/bin/{name}")
    assert config["provider"] == "acp" and config["auth_mode"] == "none"
    assert config["acp_command"] == "/usr/local/bin/npx"
    assert config["acp_args"] == ["--yes", "@agentclientprotocol/codex-acp@1.8.0"]
    assert config["acp_env"] == {} and config["acp_cwd"] == str(tmp_path)
    assert config["acp_mcp_mode"] == "pawflow"
    assert config["acp_load_session"] is True
    assert config["acp_auto_auth_single_method"] is True and config["acp_auth_method_id"] == ""
    assert config["acp_title_override"] == "Codex"
    assert config["acp_registry"] == {
        "id": "codex-acp", "version": "1.8.0", "distribution": "npx",
        "license": "Apache-2.0",
        "license_url": "https://github.com/agentclientprotocol/codex-acp/blob/main/LICENSE",
        "auth_types": ["agent"], "auth_hint": "agent",
    }

    with pytest.raises(reg.RegistryError, match="needs 'npx'"):
        reg.service_config_for_package(codex, "npx", cwd=str(tmp_path), which=lambda name: None)
    with pytest.raises(reg.RegistryError, match="no uvx distribution"):
        reg.service_config_for_package(codex, "uvx", cwd=str(tmp_path), which=lambda name: "/x")


def test_uvx_entry_reports_the_missing_tool_and_quarantine(cache, tmp_path):
    cat = reg.load_catalogue(cache)
    fast = cat.get("fast-agent")
    with pytest.raises(reg.RegistryError, match="quarantined"):
        reg.service_config_for_package(fast, "uvx", cwd=str(tmp_path), which=lambda name: "/x")
    clean = reg.parse_entry(fast.raw, matrix={})
    with pytest.raises(reg.RegistryError, match="needs 'uvx'"):
        reg.service_config_for_package(clean, "uvx", cwd=str(tmp_path), which=lambda name: None)
    config = reg.service_config_for_package(clean, "uvx", cwd=str(tmp_path), which=lambda name: "/opt/uv/uvx")
    assert config["acp_args"] == ["fast-agent-acp==0.10.1", "-x"]
    assert config["acp_env"] == {"FAST_AGENT_MODEL": "codexplan"}
    assert config["acp_auto_auth_single_method"] is False, "no matrix row, no auto auth"


def test_binary_service_config_and_update_check(cache, tmp_path):
    cat = reg.load_catalogue(cache)
    goose = cat.get("goose")
    done = reg.materialise_binary(goose, "linux-x86_64", base_dir=tmp_path, fetch=cache.fetch)
    config = reg.service_config_for_binary(goose, done, cwd=str(tmp_path))
    assert config["acp_command"] == str(done.command) and config["acp_args"] == ["acp"]
    assert config["acp_load_session"] is False
    assert config["acp_auto_auth_single_method"] is False, "two auth types advertised"
    assert config["acp_registry"]["platform"] == "linux-x86_64"
    assert config["acp_registry"]["archive_verified"] is True

    report = reg.check_update(config, cat)
    assert report == {"id": "goose", "pinned": "1.49.0", "latest": "1.49.0",
                      "update_available": False, "quarantined": False,
                      "quarantine_reason": "", "stale_catalogue": False}

    newer = _index()
    newer["agents"][1]["version"] = "1.50.0"
    cache.fetch.documents[reg.REGISTRY_INDEX_URL] = json.dumps(newer).encode()
    report = reg.check_update(config, reg.load_catalogue(cache, refresh=True))
    assert report["update_available"] is True and report["latest"] == "1.50.0"
    assert config["acp_registry"]["version"] == "1.49.0", "never auto-upgraded"

    with pytest.raises(reg.RegistryError, match="not imported"):
        reg.check_update({"provider": "acp"}, cat)


def test_quarantined_binary_cannot_be_materialised(cache, tmp_path):
    cat = reg.load_catalogue(cache)
    goose = reg.parse_entry(cat.get("goose").raw, quarantine={"goose": "withdrawn"})
    with pytest.raises(reg.RegistryError, match="quarantined"):
        reg.materialise_binary(goose, "linux-x86_64", base_dir=tmp_path, fetch=cache.fetch)
