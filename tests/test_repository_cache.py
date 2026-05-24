import json


def test_repository_list_uses_signature_cache_and_detects_file_changes(monkeypatch):
    from core import paths
    from core.repository import ScopedRepository

    ScopedRepository.reset()
    repo = ScopedRepository.instance()
    repo.create("tools", "reader", "user", {
        "source": "print('one')",
        "description": "Reader",
    }, user_id="alice")

    calls = {"read": 0}
    original_read = repo._read

    def counted_read(*args, **kwargs):
        calls["read"] += 1
        return original_read(*args, **kwargs)

    monkeypatch.setattr(repo, "_read", counted_read)

    first = repo.list("tools", "user", user_id="alice")
    second = repo.list("tools", "user", user_id="alice")

    assert first == second
    assert calls["read"] == 1

    path = paths.repo_file("tools", "reader", "user", "alice")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["description"] = "Updated"
    path.write_text(json.dumps(data), encoding="utf-8")

    updated = repo.list("tools", "user", user_id="alice")
    assert updated[0]["description"] == "Updated"
    assert calls["read"] == 2
