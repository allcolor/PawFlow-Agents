"""Tests for the installable pawflow.avatar-helper PFP package."""

from __future__ import annotations

import json
import runpy
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from core import pfp_package
from core.resource_store import ResourceStore


ROOT = Path("packages/pawflow.avatar-helper.pfpdir")
AVATAR_ROOT = Path("packages/pawflow.avatar-runtime.pfpdir")
MANIFEST = ROOT / "pfp.json"
UI = ROOT / "content/ui/extension.js"
TOOL = ROOT / "content/tools/pawflow-ui/main.py"
AGENT = ROOT / "content/agents/pawflow-helper.json"
SKILL = ROOT / "content/skills/pawflow-helper/SKILL.md"


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_avatar_helper_manifest_is_extension_first_and_non_destructive():
    manifest = _json(MANIFEST)
    assert manifest["package"] == "pawflow.avatar-helper"
    assert manifest["dependencies"] == [{
        "package": "pawflow.avatar-runtime",
        "version": ">=0.1.0,<1.0.0",
    }]
    objects = {obj["id"]: obj for obj in manifest["objects"]}
    assert set(objects) == {
        "skill:pawflow-helper",
        "agent:pawflow-helper",
        "ui_extension:avatar-helper",
        "tool:pawflow-ui",
    }

    extension = objects["ui_extension:avatar-helper"]
    assert extension["version_compat"] == "ui.v1"
    assert extension["assets"] == {
        "scripts": ["content/ui/extension.js"],
        "styles": ["content/ui/extension.css"],
    }
    assert "handlers" not in extension

    tool = objects["tool:pawflow-ui"]
    assert "node" not in tool["parameters"]
    assert tool["allowed_tools"] == []
    assert tool["allowed_services"] == []
    assert tool["permissions"] == {
        "browser": {
            "semantic": [{
                "package": "pawflow.avatar-helper",
                "operations": ["list", "get", "invoke"],
                "nodes": ["pawflow.avatar-helper:ui.guide"],
            }],
        },
    }


def test_avatar_helper_agent_and_skill_define_the_safety_boundary():
    agent = _json(AGENT)
    assert agent["assigned_skills"] == ["pawflow-helper"]
    assert "pawflow-ui" in agent["prompt"]
    assert "Never claim" in agent["prompt"]

    skill = SKILL.read_text(encoding="utf-8")
    assert "name: pawflow-helper" in skill
    assert "CSS selectors" in skill
    assert "does not submit forms" in skill
    assert "operation=get" in skill


def test_avatar_helper_ui_has_only_fixed_semantic_targets():
    source = UI.read_text(encoding="utf-8")
    for target in (
        "sidebar",
        "conversations",
        "resources",
        "pfp.repository",
        "actions",
        "plans",
        "files",
        "agent",
        "composer",
    ):
        assert f"'{target}'" in source
    for action in ("describe", "open", "focus", "guide", "clear"):
        assert f"{action}: {{" in source
    assert "enum: TARGET_IDS" in source
    assert "document.querySelector" not in source
    assert ".click(" not in source
    assert "eval(" not in source
    assert "innerHTML" not in source


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_avatar_helper_browser_runtime_opens_focuses_and_clears_targets():
    harness = r"""
const fs = require('fs');

function classes(initial) {
  const values = new Set(initial || []);
  return {
    add: (value) => values.add(value),
    remove: (value) => values.delete(value),
    contains: (value) => values.has(value),
    toggle: (value, force) => {
      const enabled = force === undefined ? !values.has(value) : !!force;
      if (enabled) values.add(value); else values.delete(value);
      return enabled;
    },
  };
}

function element(id, initialClasses) {
  return {
    id: id,
    hidden: false,
    style: {display: ''},
    classList: classes(initialClasses),
    attrs: {},
    parentNode: null,
    offsetWidth: 240,
    offsetHeight: 48,
    setAttribute: function (name, value) { this.attrs[name] = value; },
    removeAttribute: function (name) { delete this.attrs[name]; },
    addEventListener: function () {},
    scrollIntoView: function () { this.scrolled = true; },
    getClientRects: function () { return [{}]; },
    getBoundingClientRect: function () {
      return {left: 20, top: 20, right: 220, bottom: 60};
    },
  };
}

const elements = {
  sidebar: element('sidebar', ['collapsed']),
  convList: element('convList'),
  resourcesPanel: element('resourcesPanel'),
  resourcesContent: element('resourcesContent'),
  pfpDepotPanel: element('pfpDepotPanel'),
  actionMenuWrap: element('actionMenuWrap'),
  actionMenu: element('actionMenu'),
  plansPanel: element('plansPanel'),
  filesPanel: element('filesPanel'),
  activeAgentBadge: element('activeAgentBadge'),
  input: element('input'),
};
elements.resourcesPanel.style.display = 'none';
elements.plansPanel.style.display = 'none';
elements.filesPanel.style.display = 'none';

const body = {
  children: [],
  appendChild: function (node) {
    node.parentNode = this;
    this.children.push(node);
  },
  removeChild: function (node) {
    this.children = this.children.filter((item) => item !== node);
    node.parentNode = null;
  },
};

global.document = {
  body: body,
  getElementById: (id) => elements[id] || null,
  createElement: (tag) => element(tag),
};
let semanticSpec = null;
const hooks = {};
global.window = {
  innerWidth: 1200,
  innerHeight: 800,
  setTimeout: setTimeout,
  addEventListener: function () {},
  removeEventListener: function () {},
  getComputedStyle: (node) => ({
    display: node.style.display || '',
    visibility: 'visible',
  }),
  loadPlans: function () { elements.plansPanel.loaded = true; },
  loadConvFiles: function () { elements.filesPanel.loaded = true; },
  pawflow: {
    register: function (packageId, factory) {
      if (packageId !== 'pawflow.avatar-helper') throw new Error('wrong package');
      factory({
        ui: {slot: function (_slot, _id, render) { render(); }},
        semantic: {register: function (spec) { semanticSpec = spec; }},
        on: function (name, callback) { hooks[name] = callback; },
      });
    },
  },
};
global.loadResources = function () { elements.resourcesPanel.loaded = true; };
global.setSidebarSection = function (name) {
  elements.resourcesPanel.classList.toggle('active', name === 'resources');
  elements.resourcesPanel.style.display = '';
};
global.pawflow = global.window.pawflow;

eval(fs.readFileSync(process.argv[1], 'utf8'));

async function main() {
  if (!semanticSpec || semanticSpec.id !== 'ui.guide') {
    throw new Error('semantic node was not registered');
  }
  const before = semanticSpec.actions.describe.run({});
  if (before.targets.length !== 9 || before.surfaces.sidebarOpen) {
    throw new Error('invalid initial snapshot');
  }
  const guided = await semanticSpec.actions.guide.run({
    target: 'resources', message: 'Open resources here.',
  });
  if (!guided.target.visible || elements.sidebar.classList.contains('collapsed')) {
    throw new Error('resources were not opened');
  }
  if (!elements.resourcesPanel.classList.contains('active')) {
    throw new Error('resources accordion section was not activated');
  }
  if (!elements.resourcesPanel.classList.contains('pf-avatar-helper-focus')) {
    throw new Error('resources were not focused');
  }
  if (body.children.length !== 1 || body.children[0].textContent !== 'Open resources here.') {
    throw new Error('callout was not rendered safely');
  }
  semanticSpec.actions.clear.run({});
  if (elements.resourcesPanel.classList.contains('pf-avatar-helper-focus')
      || body.children.length !== 0) {
    throw new Error('guidance was not cleared');
  }
  let rejected = false;
  try { await semanticSpec.actions.open.run({target: 'body'}); }
  catch (error) { rejected = /Unknown PawFlow UI target/.test(error.message); }
  if (!rejected) throw new Error('arbitrary target was accepted');
  if (!hooks.shutdown || !hooks.conversation_changed || !hooks.agent_changed) {
    throw new Error('cleanup hooks are missing');
  }
}

main().catch((error) => { console.error(error); process.exit(1); });
"""
    completed = subprocess.run(
        ["node", "-e", harness, str(UI.resolve())],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


class _SemanticFake:
    def __init__(self):
        self.calls = []

    def list(self, package):
        self.calls.append(("list", package))
        return []

    def get(self, package, node):
        self.calls.append(("get", package, node))
        return {"id": node}

    def invoke(self, package, node, action, arguments):
        self.calls.append(("invoke", package, node, action, arguments))
        return {"ok": True}


class _PfpFake:
    def __init__(self, payload, semantic):
        self.payload = payload
        self.browser = types.SimpleNamespace(semantic=semantic)
        self.results = []

    def result(self, value):
        self.results.append(value)


def _run_tool(monkeypatch, payload, semantic):
    fake = _PfpFake(payload, semantic)
    module = types.ModuleType("pawflow")
    module.pfp = fake
    monkeypatch.setitem(sys.modules, "pawflow", module)
    runpy.run_path(str(TOOL), run_name="__main__")
    return fake


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"operation": "list"}, ("list", "pawflow.avatar-helper")),
        ({"operation": "get"}, (
            "get", "pawflow.avatar-helper", "pawflow.avatar-helper:ui.guide")),
        ({"operation": "invoke", "action": "guide", "arguments": {
            "target": "resources", "message": "Resources are here.",
        }}, (
            "invoke", "pawflow.avatar-helper", "pawflow.avatar-helper:ui.guide",
            "guide", {"target": "resources", "message": "Resources are here."})),
    ],
)
def test_avatar_helper_tool_is_pinned_to_its_semantic_node(
        monkeypatch, arguments, expected):
    semantic = _SemanticFake()
    fake = _run_tool(monkeypatch, {"arguments": arguments}, semantic)
    assert semantic.calls == [expected]
    assert len(fake.results) == 1


def test_avatar_helper_tool_rejects_missing_invoke_action(monkeypatch):
    with pytest.raises(ValueError, match="action is required"):
        _run_tool(
            monkeypatch,
            {"arguments": {"operation": "invoke", "arguments": {}}},
            _SemanticFake(),
        )


def test_avatar_helper_builds_and_requires_installed_avatar_runtime(tmp_path):
    keypair = pfp_package.create_signing_key()
    helper_artifact = pfp_package.build_pfp(
        str(ROOT),
        output_path=str(tmp_path / "pawflow.avatar-helper-0.1.0.pfp"),
        private_key=keypair["private_key"],
    )
    missing_plan = pfp_package.inspect_pfp(
        helper_artifact["path"], user_id="helper-user")
    assert {row["status"] for row in missing_plan["objects"]} == {
        "missing_dependency"}

    runtime_artifact = pfp_package.build_pfp(
        str(AVATAR_ROOT),
        output_path=str(tmp_path / "pawflow.avatar-runtime-0.1.0.pfp"),
        private_key=keypair["private_key"],
    )
    runtime_install = pfp_package.install_pfp(
        runtime_artifact["path"], user_id="helper-user", force=True)
    assert runtime_install["ok"] is True

    plan = pfp_package.inspect_pfp(
        helper_artifact["path"], user_id="helper-user")
    assert [(row["id"], row["status"]) for row in plan["objects"]] == [
        ("skill:pawflow-helper", "new"),
        ("agent:pawflow-helper", "new"),
        ("ui_extension:avatar-helper", "new"),
        ("tool:pawflow-ui", "new"),
    ]
    installed = pfp_package.install_pfp(
        helper_artifact["path"], user_id="helper-user", force=True)
    assert installed["ok"] is True
    assert installed["skipped"] == []

    store = ResourceStore.instance()
    assert store.get("skill", "pawflow-helper", "helper-user") is not None
    agent = store.get("agent", "pawflow-helper", "helper-user")
    assert agent["assigned_skills"] == ["pawflow-helper"]
    tool = store.get("tool", "pawflow-ui", "helper-user")
    assert tool["package_runtime"]["permissions"]["browser"]["semantic"][0][
        "nodes"] == ["pawflow.avatar-helper:ui.guide"]
    extensions = pfp_package.list_installed_ui_extensions(
        user_id="helper-user", scope="user")
    assert {row["package"] for row in extensions} == {
        "pawflow.avatar-runtime", "pawflow.avatar-helper"}

    removed = pfp_package.uninstall_pfp(
        "pawflow.avatar-helper", user_id="helper-user", scope="user", force=True)
    assert removed["ok"] is True
    assert store.get("agent", "pawflow-helper", "helper-user") is None
    pfp_package.uninstall_pfp(
        "pawflow.avatar-runtime", user_id="helper-user", scope="user", force=True)
