"""Tests for the perf-review-v2 cache fixes.

- KnowledgeGraph.for_user returns a cached instance and reloads on external
  file change (mtime).
- AgentDiary.read serves recent entries from the file tail and still honours
  type filters via fallback.
- ConfigStore.load_params/load_secrets cache by mtime and invalidate on save.
"""

import json
import os
import time

from core.agent_diary import AgentDiary
from core.config_store import ConfigStore
from core.config_value import ConfigValue
from core.knowledge_graph import KnowledgeGraph


def test_kg_for_user_returns_cached_instance(tmp_path):
    kg1 = KnowledgeGraph.for_user("alice", store_dir=str(tmp_path))
    kg1.add_triple("a", "knows", "b")
    kg2 = KnowledgeGraph.for_user("alice", store_dir=str(tmp_path))
    assert kg2 is kg1
    assert kg2.query_entity("a")


def test_kg_for_user_reloads_on_external_change(tmp_path):
    kg = KnowledgeGraph.for_user("bob", store_dir=str(tmp_path))
    kg.add_triple("x", "likes", "y")
    path = tmp_path / "bob.json"
    data = json.loads(path.read_text())
    data["triples"] = []
    path.write_text(json.dumps(data))
    # Force a distinct mtime even on coarse filesystems.
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + 5))
    kg2 = KnowledgeGraph.for_user("bob", store_dir=str(tmp_path))
    assert kg2 is kg
    assert kg2.query_entity("x") == []


def test_diary_read_tail_returns_newest(tmp_path):
    diary = AgentDiary.__new__(AgentDiary)
    diary._store_dir = tmp_path
    for i in range(50):
        diary.write("u", "a", f"entry {i}",
                    entry_type="observation" if i % 2 else "decision")
    out = diary.read("u", "a", limit=5)
    assert [e["text"] for e in out] == [
        "entry 49", "entry 48", "entry 47", "entry 46", "entry 45"]


def test_diary_read_type_filter_falls_back_to_full_scan(tmp_path):
    diary = AgentDiary.__new__(AgentDiary)
    diary._store_dir = tmp_path
    diary.write("u", "a", "the one decision", entry_type="decision")
    for i in range(300):
        diary.write("u", "a", f"obs {i}", entry_type="observation")
    out = diary.read("u", "a", limit=5, entry_type="decision")
    assert [e["text"] for e in out] == ["the one decision"]


def test_config_store_params_cached_and_invalidated_on_save(tmp_path):
    p = tmp_path / "params.json"
    ConfigStore.save_params(p, {"k": ConfigValue(value="v1")})
    first = ConfigStore.load_params(p)
    assert str(first["k"]) == "v1"
    # Cached: same content back without a re-parse.
    again = ConfigStore.load_params(p)
    assert str(again["k"]) == "v1"
    # A save invalidates.
    ConfigStore.save_params(p, {"k": ConfigValue(value="v2")})
    assert str(ConfigStore.load_params(p)["k"]) == "v2"


def test_config_store_cache_detects_external_write(tmp_path):
    p = tmp_path / "params.json"
    ConfigStore.save_params(p, {"k": ConfigValue(value="v1")})
    assert str(ConfigStore.load_params(p)["k"]) == "v1"
    p.write_text(json.dumps({"k": "v3"}), encoding="utf-8")
    st = p.stat()
    os.utime(p, (st.st_atime, st.st_mtime + 5))
    assert str(ConfigStore.load_params(p)["k"]) == "v3"


def test_config_store_cached_dict_is_a_copy(tmp_path):
    p = tmp_path / "params.json"
    ConfigStore.save_params(p, {"k": ConfigValue(value="v1")})
    first = ConfigStore.load_params(p)
    first["mutated"] = ConfigValue(value="x")
    second = ConfigStore.load_params(p)
    assert "mutated" not in second
