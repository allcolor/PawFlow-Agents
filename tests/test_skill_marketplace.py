import json

from core import FlowFile
from tasks.ai.actions.agent_resource import _handle_agent_resource
from tasks.ai.actions.command_dispatch import _parse_command


class _Response:
    def __init__(self, payload, status_code=200, content_type="application/json"):
        self._payload = payload
        self.status_code = status_code
        if isinstance(payload, str):
            self.content = payload.encode("utf-8")
        else:
            self.content = json.dumps(payload).encode("utf-8")
        self.headers = {"content-type": content_type}

    def json(self):
        if isinstance(self._payload, str):
            return json.loads(self._payload)
        return self._payload

    def iter_content(self, chunk_size=1):
        for idx in range(0, len(self.content), chunk_size):
            yield self.content[idx:idx + chunk_size]


def _patch_safe_review(monkeypatch):
    import core.review_bindings as review_bindings

    review = {
        "risk": "low",
        "allowed": True,
        "requires_human_review": False,
        "findings": [],
        "reviewer": "test",
    }
    metadata = {
        "hash": "test",
        "risk": "low",
        "allowed": True,
        "requires_human_review": False,
    }
    monkeypatch.setattr(review_bindings, "review_now", lambda *args, **kwargs: review)
    monkeypatch.setattr(review_bindings, "review_for_write", lambda *args, **kwargs: metadata)


def test_skill_search_slash_command_parses_source_and_query():
    body = _parse_command(
        "/skill search --source codex github comments",
        "conv1", "alice", "assistant")

    assert body["action"] == "search_skill_marketplace"
    assert body["source"] == "codex"
    assert body["query"] == "github comments"


def test_skill_import_slash_command_parses_safety_flags():
    body = _parse_command(
        "/skill import --source codex --review-only --force --scope conversation --name review-pr gh-address-comments",
        "conv1", "alice", "assistant")

    assert body["action"] == "import_skill_marketplace"
    assert body["source"] == "codex"
    assert body["review_only"] is True
    assert body["force"] is True
    assert body["scope"] == "conversation"
    assert body["name"] == "review-pr"
    assert body["ref"] == "gh-address-comments"


def test_search_codex_uses_github_index_and_skill_frontmatter(monkeypatch):
    from core import skill_marketplace

    calls = []

    def fake_get(url, headers=None, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/contents/skills/.curated?ref=main"):
            return _Response([{
                "name": "gh-address-comments",
                "path": "skills/.curated/gh-address-comments",
                "type": "dir",
                "html_url": "https://github.com/openai/skills/tree/main/skills/.curated/gh-address-comments",
            }])
        if url.endswith("/contents/skills/.system?ref=main"):
            return _Response([])
        if url.endswith("/skills/.curated/gh-address-comments/SKILL.md"):
            return _Response(
                "---\nname: gh-address-comments\ndescription: Address GitHub pull request comments.\n---\n\nReview comments.",
                content_type="text/plain")
        raise AssertionError(url)

    monkeypatch.setattr(skill_marketplace.requests, "get", fake_get)

    result = skill_marketplace.search_marketplace("codex", "github comments")

    assert result["count"] == 1
    assert result["results"][0]["name"] == "gh-address-comments"
    assert result["results"][0]["import_supported"] is True
    assert all(call[1].get("timeout") == 15 for call in calls)
    assert any(call[1].get("stream") is True for call in calls)


def test_fetch_text_streams_with_size_cap(monkeypatch):
    from core import skill_marketplace

    class LargeResponse:
        status_code = 200

        def iter_content(self, chunk_size=1):
            yield b"x" * (skill_marketplace._MAX_FILE_BYTES * 2 + 1)

    monkeypatch.setattr(
        skill_marketplace.requests, "get",
        lambda url, headers=None, **kwargs: LargeResponse())

    try:
        skill_marketplace._fetch_text("https://raw.test/SKILL.md")
    except skill_marketplace.SkillMarketplaceError as exc:
        assert "exceeds import cap" in str(exc)
    else:
        raise AssertionError("oversized fetch should fail")


def test_claude_repo_ref_validates_owner_repo(monkeypatch):
    from core import skill_marketplace

    calls = []
    monkeypatch.setattr(
        skill_marketplace, "_fetch_json",
        lambda url: calls.append(url) or {"plugins": []})

    try:
        skill_marketplace._fetch_claude_ref("../repo:skill")
    except skill_marketplace.SkillMarketplaceError as exc:
        assert "Invalid GitHub owner" in str(exc)
    else:
        raise AssertionError("unsafe owner should fail")
    assert calls == []


def test_import_marketplace_review_only_does_not_create(monkeypatch):
    from core import skill_marketplace

    created = []
    _patch_safe_review(monkeypatch)

    def fake_get(url, headers=None, **kwargs):
        if url.endswith("/contents/skills/.curated/review-pr?ref=main"):
            return _Response([{
                "name": "SKILL.md",
                "path": "skills/.curated/review-pr/SKILL.md",
                "type": "file",
                "size": 96,
                "download_url": "https://raw.test/SKILL.md",
            }])
        if url.endswith("/contents/skills/.system/review-pr?ref=main"):
            return _Response({}, status_code=404)
        if url == "https://raw.test/SKILL.md":
            return _Response(
                "---\nname: review-pr\ndescription: Review pull requests.\n---\n\nReview the requested PR.",
                content_type="text/plain")
        raise AssertionError(url)

    class Store:
        def create(self, *args, **kwargs):
            created.append((args, kwargs))

    from core.resource_store import ResourceStore
    monkeypatch.setattr(skill_marketplace.requests, "get", fake_get)
    monkeypatch.setattr(ResourceStore, "instance", staticmethod(lambda: Store()))

    result = skill_marketplace.import_marketplace_skill(
        "codex", "review-pr", user_id="alice", review_only=True)

    assert result["ok"] is True
    assert result["imported"] is False
    assert result["skill"]["name"] == "review-pr"
    assert created == []


def test_import_marketplace_omits_binary_assets(monkeypatch):
    from core import skill_marketplace

    _patch_safe_review(monkeypatch)

    def fake_get(url, headers=None, **kwargs):
        if url.endswith("/contents/skills/.curated/review-pr?ref=main"):
            return _Response([
                {
                    "name": "SKILL.md",
                    "path": "skills/.curated/review-pr/SKILL.md",
                    "type": "file",
                    "size": 96,
                    "download_url": "https://raw.test/SKILL.md",
                },
                {
                    "name": "assets",
                    "path": "skills/.curated/review-pr/assets",
                    "type": "dir",
                },
            ])
        if url.endswith("/contents/skills/.curated/review-pr/assets?ref=main"):
            return _Response([{
                "name": "logo.png",
                "path": "skills/.curated/review-pr/assets/logo.png",
                "type": "file",
                "size": 42,
                "download_url": "https://raw.test/logo.png",
            }])
        if url.endswith("/contents/skills/.system/review-pr?ref=main"):
            return _Response({}, status_code=404)
        if url == "https://raw.test/SKILL.md":
            return _Response(
                "---\nname: review-pr\ndescription: Review pull requests.\n---\n\nReview the requested PR.",
                content_type="text/plain")
        raise AssertionError(url)

    monkeypatch.setattr(skill_marketplace.requests, "get", fake_get)

    result = skill_marketplace.import_marketplace_skill(
        "codex", "review-pr", user_id="alice", review_only=True)

    assert result["ok"] is True
    assert result["package"]["package_files_count"] == 1
    assert "package_hash" in result["package"]


def test_import_marketplace_creates_low_risk_skill(monkeypatch):
    from core import skill_marketplace

    created = []
    _patch_safe_review(monkeypatch)

    def fake_get(url, headers=None, **kwargs):
        if url.endswith("/contents/skills/.curated/review-pr?ref=main"):
            return _Response([{
                "name": "SKILL.md",
                "path": "skills/.curated/review-pr/SKILL.md",
                "type": "file",
                "size": 96,
                "download_url": "https://raw.test/SKILL.md",
            }])
        if url.endswith("/contents/skills/.system/review-pr?ref=main"):
            return _Response({}, status_code=404)
        if url == "https://raw.test/SKILL.md":
            return _Response(
                "---\nname: review-pr\ndescription: Review pull requests.\n---\n\nReview the requested PR.",
                content_type="text/plain")
        raise AssertionError(url)

    class Store:
        def create(self, *args, **kwargs):
            created.append((args, kwargs))

    from core.resource_store import ResourceStore
    monkeypatch.setattr(skill_marketplace.requests, "get", fake_get)
    monkeypatch.setattr(ResourceStore, "instance", staticmethod(lambda: Store()))

    result = skill_marketplace.import_marketplace_skill(
        "codex", "review-pr", user_id="alice")

    assert result["imported"] is True
    args, kwargs = created[0]
    assert args[:3] == ("skill", "review-pr", "alice")
    assert args[3]["prompt"] == "Review the requested PR."
    assert args[3]["imported_from"]["source"] == "codex"


def test_import_action_blocks_human_review_without_force(monkeypatch):
    result_payload = {
        "ok": True,
        "imported": False,
        "blocked": False,
        "requires_human_review": True,
        "message": "requires review",
    }

    import core.skill_marketplace as skill_marketplace
    monkeypatch.setattr(
        skill_marketplace,
        "import_marketplace_skill",
        lambda **kwargs: result_payload,
    )

    ff = FlowFile(content=b"")
    result = _handle_agent_resource(object(), "import_skill_marketplace", {
        "conversation_id": "conv1",
        "source": "codex",
        "ref": "review-pr",
    }, object(), "alice", ff)

    assert result == [ff]
    body = json.loads(ff.get_content().decode("utf-8"))
    assert body["requires_human_review"] is True
    assert body["imported"] is False
