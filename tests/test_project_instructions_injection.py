"""AGENTS.md is injected as project instructions, like CLAUDE.md.

Two sites decide what reaches the agent bootstrap:
- tools/_fs_read.py action_project_context scans the project root for key
  config files on the relay;
- services/filesystem_service.py get_project_prompt renders the scanned
  instruction files into the system prompt supplement.
Both must know AGENTS.md, or the file exists in the repo but never reaches
the agent.
"""

from services.filesystem_service import RelayService
from tools.fs_actions import action_project_context


def _scan(tmp_path):
    return action_project_context(str(tmp_path), str(tmp_path), {})


def test_project_scan_reads_agents_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Agent Instructions\nrelease rule",
                                        encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# Dev context", encoding="utf-8")
    ctx = _scan(tmp_path)
    assert ctx["config_files"]["AGENTS.md"].startswith("# Agent Instructions")
    assert "CLAUDE.md" in ctx["config_files"]


def test_project_prompt_renders_agents_md_section():
    svc = RelayService({"_service_id": "TestRelay"})
    svc._project_context = {
        "config_files": {
            "CLAUDE.md": "# Dev context",
            "AGENTS.md": "# Agent Instructions\nread the release wiki page",
        },
    }
    prompt = svc.get_project_prompt()
    assert "### CLAUDE.md" in prompt
    assert "### AGENTS.md" in prompt
    assert "read the release wiki page" in prompt
    # CLAUDE.md keeps precedence in ordering
    assert prompt.index("### CLAUDE.md") < prompt.index("### AGENTS.md")
