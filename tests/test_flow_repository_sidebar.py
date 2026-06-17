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
    src = "".join(p.read_text(encoding="utf-8") for p in sorted(Path("tasks/io/chat_ui").glob("resources*.js")))

    assert "function _renderFlowPackageGroup" in src
    assert "const byPackage = new Map()" in src
    assert "repoHtml += _renderFlowPackageGroup(packageName, flows)" in src


def test_resource_tree_collapsed_state_persists_in_local_storage():
    src = "".join(p.read_text(encoding="utf-8") for p in sorted(Path("tasks/io/chat_ui").glob("resources*.js")))

    assert "pawflow.resource_tree.collapsed.v1" in src
    assert "window.localStorage.getItem(_RESOURCE_TREE_STATE_KEY)" in src
    assert "window.localStorage.setItem(_RESOURCE_TREE_STATE_KEY" in src
    assert "_collapsedSections[id] = (id !== 'agent')" in src
    assert "'theme'" in src
    assert "_saveCollapsedSections();" in src
    assert "let _lastResourcesData = null" in src
    assert "if (isOpening && _lastResourcesData) _renderResourcesData(_lastResourcesData);" in src
    assert "_lastResourcesData = merged" in src
