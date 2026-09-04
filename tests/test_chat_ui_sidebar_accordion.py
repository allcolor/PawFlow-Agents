"""Structural contract for the two-section left sidebar accordion."""

from pathlib import Path

from chat_ui_testing import rendered_chat_html
import shutil
import subprocess

import pytest


UI = Path("tasks/io/chat_ui")
TEMPLATE = rendered_chat_html()
RESOURCES = (UI / "resources.js").read_text(encoding="utf-8")
RESOURCE_RENDER = (UI / "resources_render.js").read_text(encoding="utf-8")
AVATAR_HELPER = Path(
    "packages/pawflow.avatar-helper.pfpdir/content/ui/extension.js"
).read_text(encoding="utf-8")


def _sidebar() -> str:
    start = TEMPLATE.index('<div class="sidebar collapsed"')
    end = TEMPLATE.index(
        '<div class="dialog-bg" id="conversationSettingsDialog"', start
    )
    return TEMPLATE[start:end]


def test_sidebar_has_two_ordered_full_height_sections():
    sidebar = _sidebar()

    assert sidebar.count('data-sidebar-section="') == 2
    assert sidebar.index('id="conversationsPanel"') < sidebar.index(
        'id="resourcesPanel"'
    )
    assert 'id="conversationsPanel" class="sidebar-section active"' in sidebar
    assert 'id="resourcesPanel" class="sidebar-section"' in sidebar
    assert 'id="convList"' in sidebar
    assert 'id="resourcesContent"' in sidebar


def test_headers_expose_the_single_active_section_accessibly():
    sidebar = _sidebar()

    assert 'data-sidebar-header="conversations"' in sidebar
    assert 'data-sidebar-header="resources"' in sidebar
    assert 'onclick="setSidebarSection(\'conversations\')"' in sidebar
    assert 'onclick="setSidebarSection(\'resources\')"' in sidebar
    assert 'aria-expanded="true"' in sidebar
    assert 'aria-expanded="false"' in sidebar
    assert "sidebarSectionHeaderKey(event, 'conversations')" in sidebar
    assert "sidebarSectionHeaderKey(event, 'resources')" in sidebar


def test_active_body_owns_remaining_height_without_layout_bound_css_transition():
    assert ".sidebar-section.active { flex: 1 1 0;" in TEMPLATE
    assert ".sidebar-section-body {" in TEMPLATE
    assert "transition: flex-grow" not in TEMPLATE
    assert "transition: width" not in TEMPLATE
    assert ".sidebar-section.active > .sidebar-section-body" in TEMPLATE
    assert ".sidebar-settings#resourcesPanel" not in TEMPLATE
    assert "max-height: 50%" not in TEMPLATE


def test_sidebar_and_resource_controls_use_replaceable_motion_without_button_tiles():
    base = (UI / "css" / "00_base.css").read_text(encoding="utf-8")
    bridge = (UI / "css" / "99_theme_bridge.css").read_text(encoding="utf-8")

    assert "_sidebarAccordionAnimate(parts, first, generation)" in RESOURCES
    assert "'sidebar-accordion-section'" in RESOURCES
    assert "'sidebar-accordion-body'" in RESOURCES
    assert "duration: 500" in RESOURCES
    assert "easing: 'cubic-bezier(.4, 0, .2, 1)'" in RESOURCES
    assert 'class="resource-section-control resource-section-toggle"' in RESOURCES
    assert "resource-section-control resource-section-action" in RESOURCES
    assert ".resource-section-control { appearance: none;" in base
    assert ".resource-section .resource-section-control:hover" in bridge
    assert "background: transparent !important" in bridge


def test_one_controller_drives_clicks_commands_and_resource_hydration():
    assert "function setSidebarSection(sectionName)" in RESOURCES
    assert "function sidebarSectionHeaderKey(event, sectionName)" in RESOURCES
    assert "function toggleResourcesSection()" in RESOURCES
    assert "setSidebarSection('resources');" in RESOURCES
    assert "section.classList.toggle('active', isActive);" in RESOURCES
    assert "header.setAttribute('aria-expanded', isActive ? 'true' : 'false');" in RESOURCES
    assert "body.setAttribute('aria-hidden', isActive ? 'false' : 'true');" in RESOURCES
    assert "_panel.style.display = 'block'" not in RESOURCE_RENDER


def test_avatar_helper_uses_the_same_accordion_controller():
    open_resources = AVATAR_HELPER[
        AVATAR_HELPER.index("function openResources()"):
        AVATAR_HELPER.index("function openActions()")
    ]

    assert "setSidebarSection('resources')" in open_resources
    assert "panel.style.display = 'block'" not in open_resources
    assert "content.style.display = 'block'" not in open_resources


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_controller_switches_one_section_at_a_time_at_runtime():
    harness = r"""
const fs = require('fs');
function classList(active) {
  const values = new Set(active ? ['active'] : []);
  return {
    contains: (name) => values.has(name),
    toggle: (name, force) => {
      if (force) values.add(name); else values.delete(name);
      return !!force;
    },
  };
}
function part() {
  return {
    attrs: {}, textContent: '', style: {},
    setAttribute: function (name, value) { this.attrs[name] = value; },
  };
}
function section(name, active) {
  const header = part(), body = part(), chevron = part();
  const result = {
    classList: classList(active), header, body, chevron,
    querySelector: function (selector) {
      if (selector === '[data-sidebar-header="' + name + '"]') return header;
      if (selector === '.sidebar-section-body') return body;
      if (selector === '.sidebar-section-chevron') return name === 'resources' ? chevron : null;
      return null;
    },
  };
  result.style = {};
  result.getBoundingClientRect = () => ({height: result.classList.contains('active') ? 320 : 42});
  body.getBoundingClientRect = () => ({height: result.classList.contains('active') ? 278 : 0});
  return result;
}
const conversations = section('conversations', true);
const resources = section('resources', false);
const animations = [];
global.window = {
  localStorage: {getItem: () => '', setItem: () => {}},
  pfMotion: {
    reduced: () => false,
    cancel: () => {},
    read: callback => Promise.resolve(callback()),
    write: callback => Promise.resolve(callback()),
    replace: (element, channel, frames) => {
      animations.push({element, channel, frames});
      return Promise.resolve({status: 'finished', animation: null});
    },
  },
};
global.document = {
  querySelector: function (selector) {
    if (selector === '[data-sidebar-section="conversations"]') return conversations;
    if (selector === '[data-sidebar-section="resources"]') return resources;
    return null;
  },
  getElementById: () => null,
};
let loads = 0;
global.loadResources = function () { loads += 1; };
const sectionRequests = [];
global._setResourceSectionOpen = function (id, open) {
  sectionRequests.push([id, open]);
  return Promise.resolve({status: open ? 'open' : 'closed'});
};
eval(fs.readFileSync(process.argv[1], 'utf8'));

if (!setSidebarSection('resources')) throw new Error('resources rejected');
if (conversations.classList.contains('active')) throw new Error('conversations stayed active');
if (!resources.classList.contains('active')) throw new Error('resources did not activate');
if (resources.header.attrs['aria-expanded'] !== 'true') throw new Error('resources aria');
if (conversations.body.attrs['aria-hidden'] !== 'true') throw new Error('conversation body aria');
if (resources.chevron.textContent !== '\u25BC') throw new Error('resources chevron');
if (loads !== 0) throw new Error('opening resources started data acquisition');
if (animations.length !== 4) throw new Error('accordion did not animate both sections and bodies');
if (animations[0].frames[0].height !== '320px' || animations[0].frames[1].height !== '42px') {
  throw new Error('conversation section did not animate closed');
}
if (animations[2].frames[0].height !== '42px' || animations[2].frames[1].height !== '320px') {
  throw new Error('resources section did not animate open');
}

const event = {
  key: 'Enter', target: resources.header, currentTarget: resources.header,
  preventDefault: function () { this.prevented = true; },
};
sidebarSectionHeaderKey(event, 'conversations');
if (!event.prevented || !conversations.classList.contains('active')) {
  throw new Error('keyboard did not activate conversations');
}
if (resources.classList.contains('active')) throw new Error('two sections active');
"""
    completed = subprocess.run(
        ["node", "-e", harness, str(UI / "resources.js")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
