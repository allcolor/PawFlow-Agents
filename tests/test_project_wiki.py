"""Tests for relay-scoped automatic project wiki storage."""

import hashlib
import json
from unittest.mock import MagicMock

import pytest

from core.project_wiki import ProjectWiki
from core.project_wiki_digest import build_project_wiki_digest


def _source(text: str, mtime: int = 1):
    raw = text.encode()
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
        "mtime_ns": mtime,
    }


class _Relay:
    _service_id = "relay-a"

    def __init__(self, scans, files=None):
        self.scans = list(scans)
        self.files = files or {}
        self.writes = []

    def write_file(self, path, content, local=False):
        self.writes.append((path, content, local))

    def delete_file(self, path, local=False):
        return None

    def exec(self, path, command, env=None, local=False):
        payload = self.scans.pop(0)
        return {"stdout": json.dumps({"status": "scanned", "files": payload}),
                "stderr": "", "returncode": 0}

    def read_file(self, path, local=False):
        return self.files[path].encode()


class _Client:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def clone_for_call(self):
        return self

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return MagicMock(content=json.dumps(self.payload))


@pytest.fixture()
def wiki(tmp_path, monkeypatch):
    from core import paths
    monkeypatch.setattr(paths, "PROJECT_WIKIS_DIR", tmp_path / "wikis")
    ProjectWiki._instances.clear()
    value = ProjectWiki.for_relay("alice", "relay-a")
    yield value
    ProjectWiki._instances.clear()


def test_first_scan_only_queues_high_signal_sources(wiki):
    scan = {
        "README.md": _source("overview"),
        "core/central.py": _source("central"),
        "core/leaf.py": _source("leaf"),
    }
    result = wiki.scan_from_relay(
        _Relay([scan]), initial_paths=["core/central.py"])

    assert result["sources"] == 3
    assert set(result["changes"]) == {"README.md", "core/central.py"}
    assert wiki.status()["dirty_sources"] == 2
    assert (wiki.path / "schema.md").exists()
    assert (wiki.path / "index.md").exists()


def test_changed_source_makes_page_stale_until_replaced(wiki):
    first = {"README.md": _source("old")}
    second = {"README.md": _source("new", 2)}
    relay = _Relay([first, second])
    wiki.scan_from_relay(relay)
    wiki.upsert_page("overview", "Overview", "Project overview", "Old facts.",
                     ["README.md"])
    assert wiki.acknowledge(["README.md"])["cleared"] == ["README.md"]

    wiki.scan_from_relay(relay)
    assert "overview" in wiki.status()["stale_pages"]
    blocked = wiki.acknowledge(["README.md"])
    assert blocked["blocked"] == ["README.md"]

    wiki.upsert_page("overview", "Overview", "Project overview", "New facts.",
                     ["README.md"])
    assert wiki.acknowledge(["README.md"])["cleared"] == ["README.md"]
    assert wiki.status()["stale_pages"] == {}


def test_root_change_resets_generated_pages_and_reseeds_sources(wiki):
    first = {"README.md": _source("old root")}
    second = {
        "README.md": _source("new root", 2),
        "src/leaf.py": _source("leaf", 2),
    }
    relay = _Relay([first, second])
    wiki.scan_from_relay(relay, root=".")
    wiki.upsert_page(
        "old-overview", "Old overview", "Old root", "Old facts.",
        ["README.md"])
    wiki.acknowledge(["README.md"])

    result = wiki.scan_from_relay(relay, root="new-root")

    assert set(result["changes"]) == {"README.md"}
    assert wiki.status()["pages"] == 0
    assert wiki.status()["dirty_sources"] == 1
    with pytest.raises(KeyError):
        wiki.get_page("old-overview")
    assert not (wiki.path / "pages" / "old-overview.md").exists()


def test_root_reset_does_not_follow_manifest_page_path(wiki):
    first = {"README.md": _source("old root")}
    second = {"README.md": _source("new root", 2)}
    relay = _Relay([first, second])
    wiki.scan_from_relay(relay, root=".")
    outside = wiki.path.parent / "outside.md"
    outside.write_text("must survive", encoding="utf-8")
    wiki._manifest["pages"] = {
        "../../outside": {"path": "../outside.md", "sources": {}}}

    wiki.scan_from_relay(relay, root="new-root")

    assert outside.read_text(encoding="utf-8") == "must survive"


def test_query_lint_and_digest(wiki):
    scan = {
        "README.md": _source("overview"),
        "core/auth.py": _source("auth"),
    }
    wiki.scan_from_relay(_Relay([scan]), initial_paths=["core/auth.py"])
    wiki.upsert_page(
        "authentication", "Authentication", "Token validation flow",
        "The gateway validates bearer tokens. See [[missing-page|details]].",
        ["core/auth.py"],
    )

    assert wiki.query("token validation")[0]["slug"] == "authentication"
    lint = wiki.lint()
    assert lint["missing_links_or_files"] == ["missing-page"]
    digest = build_project_wiki_digest("alice", "relay-a")
    assert "1 pages" in digest
    assert "pending" in digest


def test_structured_page_listing_edit_data_and_safe_delete(wiki):
    wiki.scan_from_relay(_Relay([{
        "README.md": _source("overview"),
    }]))
    wiki.upsert_page(
        "overview", "Overview", "Project summary", "Editable body.",
        ["README.md"])

    pages = wiki.list_pages()
    assert pages == [{
        "slug": "overview", "title": "Overview",
        "summary": "Project summary", "sources": ["README.md"],
        "updated_at": pages[0]["updated_at"], "stale": [],
    }]
    assert wiki.list_pages("summary")[0]["slug"] == "overview"
    assert wiki.list_pages("missing") == []
    page = wiki.get_page_data("overview")
    assert page["content"] == "Editable body."
    assert page["sources"] == ["README.md"]
    assert "---" not in page["content"]

    assert wiki.delete_page("overview") is True
    assert wiki.delete_page("overview") is False
    assert wiki.list_pages() == []
    assert not (wiki.path / "pages" / "overview.md").exists()


def test_auto_update_writes_pages_and_acknowledges_sources(wiki):
    scan = {"README.md": _source("overview")}
    relay = _Relay([scan], files={"README.md": "Project overview"})
    wiki.scan_from_relay(relay)
    client = _Client({
        "pages": [{
            "slug": "overview",
            "title": "Overview",
            "summary": "Project overview",
            "content": "The project has one main service.",
            "sources": ["README.md"],
        }],
        "processed_sources": ["README.md"],
    })

    result = wiki.auto_update(relay, client)

    assert result["status"] == "updated"
    assert result["remaining"] == 0
    assert "one main service" in wiki.get_page("overview")
    prompt = client.calls[0]["messages"][0].content
    assert "SOURCE README.md" in prompt
    assert "Return one JSON object only" in prompt
    assert "excluding any internal reasoning" in prompt
    assert "6000 tokens" in prompt
    assert client.calls[0]["max_tokens"] == 0


def test_auto_update_keeps_sources_pending_on_empty_llm_response(wiki):
    relay = _Relay(
        [{"README.md": _source("overview")}],
        files={"README.md": "Project overview"})
    wiki.scan_from_relay(relay)
    client = _Client({})
    client.complete = MagicMock(return_value=MagicMock(
        content="", finish_reason="stop"))

    result = wiki.auto_update(relay, client)

    assert result == {
        "status": "pending", "reason": "invalid LLM response",
        "remaining": 1,
    }
    assert wiki.status()["dirty_sources"] == 1


def test_auto_update_accepts_json_object_inside_markdown(wiki):
    relay = _Relay(
        [{"README.md": _source("overview")}],
        files={"README.md": "Project overview"})
    wiki.scan_from_relay(relay)
    payload = {
        "pages": [{
            "slug": "overview", "title": "Overview", "summary": "Summary",
            "content": "Current facts.", "sources": ["README.md"],
        }],
        "processed_sources": ["README.md"],
    }
    client = _Client({})
    client.complete = MagicMock(return_value=MagicMock(
        content="Result:\n```json\n" + json.dumps(payload) + "\n```"))

    result = wiki.auto_update(relay, client)

    assert result["status"] == "updated"
    assert result["remaining"] == 0


def test_auto_update_keeps_sources_pending_when_llm_call_fails(wiki):
    relay = _Relay(
        [{"README.md": _source("overview")}],
        files={"README.md": "Project overview"})
    wiki.scan_from_relay(relay)
    client = _Client({})
    client.complete = MagicMock(side_effect=RuntimeError("provider unavailable"))

    result = wiki.auto_update(relay, client)

    assert result == {
        "status": "pending", "reason": "LLM call failed", "remaining": 1,
    }
    assert wiki.status()["dirty_sources"] == 1


def test_auto_update_refuses_response_when_source_changed_during_llm_call(wiki):
    first = {"README.md": _source("old")}
    second = {"README.md": _source("new", 2)}
    relay = _Relay([first, second], files={"README.md": "Old overview"})
    wiki.scan_from_relay(relay)
    client = _Client({
        "pages": [{
            "slug": "overview", "title": "Overview", "summary": "Old",
            "content": "Facts from the old source.", "sources": ["README.md"],
        }],
        "processed_sources": ["README.md"],
    })
    original_complete = client.complete

    def mutate_then_complete(**kwargs):
        wiki.scan_from_relay(relay)
        return original_complete(**kwargs)

    client.complete = mutate_then_complete

    result = wiki.auto_update(relay, client)

    assert result["status"] == "superseded"
    assert result["sources"] == ["README.md"]
    assert wiki.status()["dirty_sources"] == 1
    with pytest.raises(KeyError):
        wiki.get_page("overview")


def test_relay_is_the_project_identity(tmp_path, monkeypatch):
    from core import paths
    monkeypatch.setattr(paths, "PROJECT_WIKIS_DIR", tmp_path / "wikis")
    ProjectWiki._instances.clear()
    first = ProjectWiki.for_relay("alice", "relay-a")
    same = ProjectWiki.for_relay("alice", "relay-a")
    other = ProjectWiki.for_relay("alice", "relay-b")

    assert first is same
    assert first.path != other.path
