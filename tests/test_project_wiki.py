"""Tests for relay-scoped automatic project wiki storage."""

import hashlib
import json
from copy import deepcopy
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
        self.deletes = []
        self.exec_calls = []

    def write_file(self, path, content, local=False):
        self.writes.append((path, content, local))

    def delete_file(self, path, local=False):
        self.deletes.append((path, local))
        return None

    def exec(self, path, command, env=None, local=False):
        self.exec_calls.append((path, command, env, local))
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


def test_scan_executes_in_memory_without_relay_helper_file(wiki):
    relay = _Relay([{"README.md": _source("overview")}])

    wiki.scan_from_relay(relay)

    assert relay.writes == []
    assert relay.deletes == []
    path, command, env, local = relay.exec_calls[0]
    assert path == "."
    assert command.startswith("python3 -c ")
    assert ".pawflow_wiki_scan_" not in command
    assert env["PAWFLOW_WIKI_ROOT"] == "."
    assert env["PAWFLOW_WIKI_MAX_FILES"] == "0"
    assert local is False


def test_zero_batch_selects_all_sources_and_fetches_full_content(wiki):
    initial = {"README.md": _source("overview")}
    large_text = "x" * 100_000
    changed = {
        "README.md": _source("overview"),
        **{f"src/file_{number:02d}.py": _source(large_text + str(number), 2)
           for number in range(25)},
    }
    relay = _Relay(
        [initial, changed],
        files={path: large_text + str(number)
               for number, path in enumerate(
                   f"src/file_{index:02d}.py" for index in range(25))},
    )
    wiki.scan_from_relay(relay)
    wiki.acknowledge(["README.md"])
    wiki.scan_from_relay(relay)

    selection = wiki.select_update_batch(0)
    prepared = wiki.fetch_update_sources(relay, selection)

    assert len(selection["entries"]) == 25
    assert len(prepared["files"]) == 25
    assert all(item["truncated"] is False for item in prepared["files"])
    assert all(len(item["text"]) > 100_000 for item in prepared["files"])


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
    scan = {"README.md": _source("Project overview")}
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
    assert "response budget" not in prompt
    assert "6000 tokens" not in prompt
    assert client.calls[0]["max_tokens"] == 0


def test_auto_update_keeps_sources_pending_on_empty_llm_response(wiki):
    relay = _Relay(
        [{"README.md": _source("Project overview")}],
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


def test_auto_update_repairs_missing_page_sources_from_processed_snapshot(wiki):
    relay = _Relay(
        [{"README.md": _source("Project overview")}],
        files={"README.md": "Project overview"})
    wiki.scan_from_relay(relay)
    client = _Client({
        "pages": [{
            "slug": "overview",
            "title": "Overview",
            "summary": "Project overview",
            "content": "The project has one main service.",
        }],
        "processed_sources": ["README.md"],
    })

    result = wiki.auto_update(relay, client)

    assert result["status"] == "updated"
    assert result["remaining"] == 0
    assert wiki.get_page_data("overview")["sources"] == ["README.md"]


def test_auto_update_repairs_missing_sources_when_processed_list_is_empty(wiki):
    relay = _Relay(
        [{"README.md": _source("Project overview")}],
        files={"README.md": "Project overview"})
    wiki.scan_from_relay(relay)
    client = _Client({
        "pages": [{
            "slug": "overview",
            "title": "Overview",
            "summary": "Project overview",
            "content": "The project has one main service.",
        }],
        "processed_sources": [],
    })

    result = wiki.auto_update(relay, client)

    assert result["status"] == "updated"
    assert result["remaining"] == 0
    assert result["cleared"] == ["README.md"]
    assert wiki.get_page_data("overview")["sources"] == ["README.md"]


def test_auto_update_repairs_non_list_page_sources(wiki):
    relay = _Relay(
        [{"README.md": _source("Project overview")}],
        files={"README.md": "Project overview"})
    wiki.scan_from_relay(relay)
    client = _Client({
        "pages": [{
            "slug": "overview",
            "title": "Overview",
            "summary": "Project overview",
            "content": "The project has one main service.",
            "sources": "",
        }],
        "processed_sources": ["README.md"],
    })

    result = wiki.auto_update(relay, client)

    assert result["status"] == "updated"
    assert result["remaining"] == 0
    assert wiki.get_page_data("overview")["sources"] == ["README.md"]


def test_auto_update_ignores_uncitable_page_for_removed_only_batch(wiki):
    relay = _Relay([
        {"README.md": _source("Old project overview")},
        {},
    ])
    wiki.scan_from_relay(relay)
    assert wiki.acknowledge(["README.md"])["cleared"] == ["README.md"]
    wiki.scan_from_relay(relay)
    client = _Client({
        "pages": [{
            "slug": "removed-overview",
            "title": "Removed overview",
            "summary": "Removed source",
            "content": "The old project overview was removed.",
        }],
        "processed_sources": ["README.md"],
    })

    result = wiki.auto_update(relay, client)

    assert result["status"] == "updated"
    assert result["processed"] == 1
    assert result["remaining"] == 0
    assert wiki.status()["pages"] == 0


def test_auto_update_accepts_json_object_inside_markdown(wiki):
    relay = _Relay(
        [{"README.md": _source("Project overview")}],
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
        [{"README.md": _source("Project overview")}],
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
    first = {"README.md": _source("Old overview")}
    second = {"README.md": _source("New overview", 2)}
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
        relay.files["README.md"] = "New overview"
        wiki.scan_from_relay(relay)
        return original_complete(**kwargs)

    client.complete = mutate_then_complete

    result = wiki.auto_update(relay, client)

    assert result["status"] == "superseded"
    assert result["sources"] == ["README.md"]
    assert wiki.status()["dirty_sources"] == 1
    with pytest.raises(KeyError):
        wiki.get_page("overview")


def test_patch_validation_rejects_out_of_snapshot_citation_before_write(wiki):
    relay = _Relay(
        [{"README.md": _source("Project overview")}],
        files={"README.md": "Project overview"})
    wiki.scan_from_relay(relay)
    selection = wiki.select_update_batch()

    with pytest.raises(ValueError, match="outside the selected snapshot"):
        wiki.validate_update_patch(selection, {
            "pages": [{
                "slug": "overview", "title": "Overview", "summary": "",
                "content": "Unsupported facts.", "sources": ["core/other.py"],
            }],
            "processed_sources": ["README.md"],
        })

    assert wiki.status()["pages"] == 0
    assert wiki.status()["dirty_sources"] == 1


def test_apply_update_patch_is_replay_safe(wiki):
    relay = _Relay(
        [{"README.md": _source("Project overview")}],
        files={"README.md": "Project overview"})
    wiki.scan_from_relay(relay)
    selection = wiki.select_update_batch()
    patch = wiki.validate_update_patch(selection, {
        "pages": [{
            "slug": "overview", "title": "Overview", "summary": "Summary",
            "content": "Current project facts.", "sources": ["README.md"],
        }],
        "processed_sources": ["README.md"],
    })

    first = wiki.apply_update_patch(relay, selection, patch, "run:snapshot:patch")
    second = wiki.apply_update_patch(relay, selection, patch, "run:snapshot:patch")

    assert first["created"] == ["overview"]
    assert first["remaining"] == 0
    assert second == {**first, "replayed": True}
    assert wiki.list_pages()[0]["slug"] == "overview"


def test_preview_update_patch_classifies_without_writes_or_acknowledgement(wiki):
    relay = _Relay(
        [{"README.md": _source("Project overview")}],
        files={"README.md": "Project overview"})
    wiki.scan_from_relay(relay)
    selection = wiki.select_update_batch()
    patch = wiki.validate_update_patch(selection, {
        "pages": [{
            "slug": "overview", "title": "Overview", "summary": "Summary",
            "content": "Current project facts.", "sources": ["README.md"],
        }],
        "processed_sources": ["README.md"],
    })
    before = wiki.status()

    result = wiki.preview_update_patch(relay, selection, patch)

    assert result["status"] == "shadow"
    assert result["created"] == ["overview"]
    assert result["cleared"] == ["README.md"]
    assert wiki.status() == before
    assert wiki.list_pages() == []
    assert wiki._manifest.get("applied_patches", {}) == {}


@pytest.mark.parametrize(
    ("existing_content", "classification"),
    ((None, "created"), ("Old facts.", "updated"), ("Current facts.", "unchanged")),
)
def test_shadow_classification_matches_live_apply_without_mutation(
        wiki, existing_content, classification):
    relay = _Relay(
        [{"README.md": _source("Project overview")}],
        files={"README.md": "Project overview"})
    wiki.scan_from_relay(relay)
    if existing_content is not None:
        wiki.upsert_page(
            slug="overview", title="Overview", summary="Summary",
            content=existing_content, sources=["README.md"])
    selection = wiki.select_update_batch()
    patch = wiki.validate_update_patch(selection, {
        "pages": [{
            "slug": "overview", "title": "Overview", "summary": "Summary",
            "content": "Current facts.", "sources": ["README.md"],
        }],
        "processed_sources": ["README.md"],
    })
    before_manifest = deepcopy(wiki._manifest)
    before_files = {
        path.relative_to(wiki.path).as_posix(): path.read_bytes()
        for path in wiki.path.rglob("*") if path.is_file()
    }

    shadow = wiki.preview_update_patch(relay, selection, patch)

    after_files = {
        path.relative_to(wiki.path).as_posix(): path.read_bytes()
        for path in wiki.path.rglob("*") if path.is_file()
    }
    assert wiki._manifest == before_manifest
    assert after_files == before_files

    live = wiki.apply_update_patch(
        relay, selection, patch, f"parity:{classification}")

    for key in (
            "created", "updated", "unchanged", "cleared", "processed", "blocked"):
        assert shadow[key] == live[key]
    assert shadow[classification] == ["overview"]
    assert shadow["status"] == "shadow"
    assert live["status"] == "updated"
    assert live["remaining"] == 0


def test_apply_rechecks_unscanned_source_bytes(wiki):
    relay = _Relay(
        [{"README.md": _source("Old overview")}],
        files={"README.md": "Old overview"})
    wiki.scan_from_relay(relay)
    selection = wiki.select_update_batch()
    patch = wiki.validate_update_patch(selection, {
        "pages": [{
            "slug": "overview", "title": "Overview", "summary": "Old",
            "content": "Facts from the old source.", "sources": ["README.md"],
        }],
        "processed_sources": ["README.md"],
    })
    relay.files["README.md"] = "Changed without a manifest scan"

    result = wiki.apply_update_patch(relay, selection, patch, "run:old:patch")

    assert result["status"] == "superseded"
    assert result["sources"] == ["README.md"]
    assert wiki.status()["dirty_sources"] == 1
    assert wiki.status()["pages"] == 0


def test_relay_is_the_project_identity(tmp_path, monkeypatch):
    from core import paths
    monkeypatch.setattr(paths, "PROJECT_WIKIS_DIR", tmp_path / "wikis")
    ProjectWiki._instances.clear()
    first = ProjectWiki.for_relay("alice", "relay-a")
    same = ProjectWiki.for_relay("alice", "relay-a")
    other = ProjectWiki.for_relay("alice", "relay-b")

    assert first is same
    assert first.path != other.path


def test_scan_refuses_the_local_surface(wiki):
    # local=true executes on the server/host, whose tree is the deployed
    # runtime — one such scan poisons the manifest with phantom sources
    # that the next relay scan reports as removed.
    relay = _Relay([{"README.md": _source("overview")}])
    with pytest.raises(ValueError, match="local"):
        wiki.scan_from_relay(relay, local=True)
    assert relay.exec_calls == []


def test_auto_update_refuses_the_local_surface(wiki):
    with pytest.raises(ValueError, match="local"):
        wiki.auto_update(_Relay([]), _Client({}), local=True)


def test_acknowledge_expands_glob_patterns_against_pending(wiki):
    # Recovery path for a poisoned manifest: thousands of phantom
    # sources are cleared with a prefix pattern, without touching the
    # legitimate pending entries.
    wiki.scan_from_relay(_Relay([{"README.md": _source("overview")}]))
    wiki.scan_from_relay(_Relay([{
        "README.md": _source("overview"),
        "app/data/runtime/theme.json": _source("phantom-a"),
        "usr/lib/python3/os.py": _source("phantom-b"),
        "core/real.py": _source("legit"),
    }]))
    before = wiki.status()["dirty_sources"]
    assert before >= 3

    result = wiki.acknowledge(["app/*", "usr/*"])

    assert "app/data/runtime/theme.json" in result["cleared"]
    assert "usr/lib/python3/os.py" in result["cleared"]
    remaining = wiki._manifest["dirty_sources"]
    assert "core/real.py" in remaining
    assert not any(p.startswith(("app/", "usr/")) for p in remaining)


def test_maintenance_wiki_scan_is_pinned_to_the_relay_surface():
    from pathlib import Path
    source = Path("core/project_maintenance.py").read_text(encoding="utf-8")
    wiki_part = source[source.index("ProjectWiki.for_relay"):]
    assert "scan_from_relay(" in wiki_part
    assert "job.service, job.root, local=False" in wiki_part
    assert "job.service, llm_client, local=False" in wiki_part
