"""Flow repository sidebar grouping invariants."""

from pathlib import Path

from tasks.ai.actions.agent_resource import _scan_flow_templates


def test_flow_template_scan_exposes_package_and_sorts_by_package():
    templates = _scan_flow_templates("")

    assert templates
    assert all(t.get("package") for t in templates)
    assert templates == sorted(
        templates,
        key=lambda t: (t["package"], t["name"], t["version"], t["scope"]),
    )


def test_flow_repository_sidebar_groups_templates_by_package():
    src = Path("tasks/io/chat_ui/resources.js").read_text(encoding="utf-8")

    assert "function _renderFlowPackageGroup" in src
    assert "const byPackage = new Map()" in src
    assert "repoHtml += _renderFlowPackageGroup(packageName, flows)" in src
