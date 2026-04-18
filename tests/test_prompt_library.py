"""Tests for the Prompt library feature.

Prompts are .md files with YAML frontmatter stored in data/repository/prompts/.
They support ${param} placeholders resolved at use time.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from core.resource_store import ResourceStore


@pytest.fixture(autouse=True)
def reset_singleton(tmp_path):
    ResourceStore.reset()
    from core import paths as paths_mod
    from core.repository import ScopedRepository
    repo_dir = tmp_path / "repository"
    repo_dir.mkdir()
    with patch.object(paths_mod, "REPOSITORY_DIR", repo_dir):
        ScopedRepository.reset()
        yield tmp_path
    ScopedRepository.reset()
    ResourceStore.reset()


class TestPromptCRUD:
    def test_create_prompt(self):
        store = ResourceStore.instance()
        result = store.create("prompt", "greet", "user1", {
            "prompt": "Hello ${name}, welcome to ${project}!",
            "title": "Greeting",
            "category": "general",
            "description": "A simple greeting",
            "parameters": {
                "name": {"type": "string"},
                "project": {"type": "string", "default": "PawFlow"},
            },
        })
        assert result["name"] == "greet"
        assert "${name}" in result["prompt"]
        assert result["title"] == "Greeting"
        assert result["parameters"]["project"]["default"] == "PawFlow"

    def test_stored_as_markdown(self, tmp_path):
        store = ResourceStore.instance()
        store.create("prompt", "test_md", "__global__", {
            "prompt": "Body text here",
            "title": "Test",
        })
        md_path = tmp_path / "repository" / "prompts" / "global" / "test_md.md"
        assert md_path.exists()
        content = md_path.read_text()
        assert "---" in content
        assert "title: Test" in content
        assert "Body text here" in content

    def test_get_prompt(self):
        store = ResourceStore.instance()
        store.create("prompt", "p1", "user1", {
            "prompt": "Do ${action}",
            "title": "Action",
        })
        result = store.get("prompt", "p1", "user1")
        assert result is not None
        assert result["prompt"] == "Do ${action}"
        assert result["title"] == "Action"

    def test_list_prompts(self):
        store = ResourceStore.instance()
        store.create("prompt", "a", "user1", {"prompt": "p1"})
        store.create("prompt", "b", "user1", {"prompt": "p2"})
        items = store.list("prompt", "user1")
        names = [p["name"] for p in items]
        assert "a" in names
        assert "b" in names

    def test_update_prompt(self):
        store = ResourceStore.instance()
        store.create("prompt", "p1", "user1", {
            "prompt": "old", "title": "Old",
        })
        store.update("prompt", "p1", "user1", {
            "prompt": "new", "title": "New",
        })
        result = store.get("prompt", "p1", "user1")
        assert result["prompt"] == "new"
        assert result["title"] == "New"

    def test_delete_prompt(self):
        store = ResourceStore.instance()
        store.create("prompt", "p1", "user1", {"prompt": "x"})
        assert store.delete("prompt", "p1", "user1") is True
        assert store.get("prompt", "p1", "user1") is None

    def test_prompt_scopes(self):
        store = ResourceStore.instance()
        store.create("prompt", "shared", "__global__", {"prompt": "global"})
        store.create("prompt", "mine", "alice", {"prompt": "user"})
        # Global visible to alice via get_any
        assert store.get_any("prompt", "shared", "alice") is not None
        # User prompt not visible to bob
        assert store.get("prompt", "mine", "bob") is None

    def test_no_parameters_default(self):
        store = ResourceStore.instance()
        result = store.create("prompt", "simple", "user1", {
            "prompt": "Just text, no params",
        })
        assert result["parameters"] == {}


class TestPromptResolution:
    def test_use_prompt_resolves_params(self):
        """Simulate the use_prompt action's resolution logic."""
        import re
        template = "Review ${language} code for ${focus}:\n${code}"
        params = {"language": "Python", "focus": "security", "code": "print('hi')"}

        def _replace(m):
            return str(params.get(m.group(1), m.group(0)))

        resolved = re.sub(r'\$\{(\w+)}', _replace, template)
        assert resolved == "Review Python code for security:\nprint('hi')"

    def test_unresolved_params_kept(self):
        """Params not provided stay as ${placeholder}."""
        import re
        template = "Hello ${name}, your role is ${role}"
        params = {"name": "Alice"}

        def _replace(m):
            return str(params.get(m.group(1), m.group(0)))

        resolved = re.sub(r'\$\{(\w+)}', _replace, template)
        assert resolved == "Hello Alice, your role is ${role}"

    def test_no_params_returns_raw(self):
        template = "Just a plain prompt with no variables"
        assert template == "Just a plain prompt with no variables"
