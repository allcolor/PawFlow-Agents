"""Flow repository sidebar grouping invariants."""

from pathlib import Path

import pytest

from tasks.ai.actions.agent_resource import _scan_flow_templates
from test_webchat_motion_browser import (
    CHAT_UI, _shell_html, chromium_browser,  # noqa: F401
)


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
    assert "return _setResourceSectionOpen(id, isOpening);" in src
    assert "if (isOpening && _lastResourcesData)" not in src
    assert "_mergedData = Object.assign({}, _resData" in src
    assert "_lastResourcesData = _mergedData" in src


@pytest.mark.parametrize("reduced", [False, True])
def test_flow_package_disclosures_are_independent_and_survive_refresh(
        chromium_browser, reduced):
    from playwright.sync_api import expect

    context = chromium_browser.new_context(
        reduced_motion="reduce" if reduced else "no-preference",
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("http://pawflow.test/", lambda route: route.fulfill(
        content_type="text/html", body=_shell_html(),
    ))

    def load_tree():
        for script in (
            "ui_motion.js", "ui_disclosure.js", "resources_patch.js",
            "resources.js", "resources_flow_templates.js",
        ):
            page.add_script_tag(path=str(CHAT_UI / script))
        page.evaluate("""() => {
          window.t = value => value;
          window.escapeHtml = value => {
            const node = document.createElement('span');
            node.textContent = String(value);
            return node.innerHTML;
          };
          window.escapeAttr = value => escapeHtml(value).replaceAll('"', '&quot;');
          window.jsStringArg = value => escapeAttr(JSON.stringify(value));
          window._scopeBadge = () => '';
          document.getElementById('sidebarShell').classList.remove('collapsed');
          document.getElementById('sidebar').classList.remove('collapsed');
          setSidebarSection('resources');
          window.renderTree = (version = '1.0.0') => {
            const groups = ['google_chat', 'http_bots'].map(packageName =>
              _renderFlowPackageGroup(packageName, [{
                id: packageName + '.bot', name: packageName + '_bot',
                package: packageName, scope: 'global', version, tasks_count: 5,
              }]));
            _patchResourcesContent(document.getElementById('resourcesContent'),
              _repoSectionHeader('Flows', '_flow_repo')
              + groups.join('') + _sectionFooter());
          };
          renderTree();
        }""")

    def body(package):
        return page.locator("#res-section-_flow_pkg_" + package)

    try:
        page.goto("http://pawflow.test/")
        load_tree()
        page.get_by_role("button", name="Flows", exact=False).click()
        parent = page.locator("#res-section-_flow_repo")
        expect(parent).to_be_visible()
        expect(body("google_chat")).to_be_hidden()
        expect(body("http_bots")).to_be_hidden()

        page.get_by_text("google_chat", exact=True).click()
        expect(body("google_chat")).to_be_visible(timeout=2000)
        expect(body("http_bots")).to_be_hidden()
        page.get_by_text("http_bots", exact=True).click()
        expect(body("http_bots")).to_be_visible(timeout=2000)
        page.evaluate("""async () => {
          await _setResourceSectionOpen('_flow_pkg_google_chat', true);
          await _setResourceSectionOpen('_flow_pkg_http_bots', true);
          window.savedBodies = ['_flow_repo', '_flow_pkg_google_chat',
            '_flow_pkg_http_bots'].map(id => document.getElementById('res-section-' + id));
          renderTree('2.0.0');
        }""")
        assert page.evaluate("savedBodies.every(node => node.isConnected)")
        expect(body("google_chat")).to_contain_text("v2.0.0")
        expect(body("http_bots")).to_contain_text("v2.0.0")
        expect(body("google_chat")).to_be_visible()
        expect(body("http_bots")).to_be_visible()

        page.get_by_text("google_chat", exact=True).click()
        expect(body("google_chat")).to_be_hidden(timeout=2000)
        expect(parent).to_be_visible()
        expect(body("http_bots")).to_be_visible()
        page.get_by_role("button", name="Flows", exact=False).click()
        expect(parent).to_be_hidden(timeout=2000)
        page.get_by_role("button", name="Flows", exact=False).click()
        expect(body("http_bots")).to_be_visible(timeout=2000)
        expect(body("google_chat")).to_be_hidden()

        page.reload()
        load_tree()
        expect(parent).to_be_visible()
        expect(body("google_chat")).to_be_hidden()
        expect(body("http_bots")).to_be_visible()
        trigger = page.get_by_role("button", name="google_chat", exact=False)
        trigger.focus()
        page.keyboard.press("Enter")
        expect(body("google_chat")).to_be_visible(timeout=2000)
        expect(trigger).to_have_attribute("aria-expanded", "true")
        assert not body("google_chat").evaluate("node => node.inert")
        assert errors == []
    finally:
        context.close()
