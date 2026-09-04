"""Real-Chromium gates for the native WebChat motion system.

The fixture starts from the HTML emitted by ``render_chat_page`` with the real
CSS cascade inlined, then loads the shipped controller modules. Timing budgets
are enforced only on the declared reference browser image; deterministic
behavior, geometry, accessibility, clone counts, and lifecycle cleanup run on
every machine that has Playwright and Chromium.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
from pathlib import Path

import pytest

from chat_ui_testing import rendered_chat_html


ROOT = Path(__file__).resolve().parents[1]
CHAT_UI = ROOT / "tasks" / "io" / "chat_ui"
CONTROLLERS = (
    "ui_motion.js",
    "ui_disclosure.js",
    "ui_projection.js",
    "ui_floating_layer.js",
    "resources_patch.js",
)
REFERENCE_BROWSER = os.environ.get("PAWFLOW_REFERENCE_BROWSER") == "1"


def _chromium_executable(playwright) -> str | None:
    configured = os.environ.get("PAWFLOW_CHROMIUM_EXECUTABLE", "")
    candidates = (
        configured,
        playwright.chromium.executable_path,
        shutil.which("chromium") or "",
        shutil.which("chromium-browser") or "",
    )
    return next((path for path in candidates if path and Path(path).is_file()), None)


@pytest.fixture(scope="module")
def chromium_browser():
    playwright_module = pytest.importorskip("playwright.sync_api")
    manager = playwright_module.sync_playwright()
    playwright = manager.start()
    executable = _chromium_executable(playwright)
    if executable is None:
        playwright.stop()
        pytest.skip("Chromium executable is not installed")
    browser = playwright.chromium.launch(
        headless=True,
        executable_path=executable,
        args=["--no-sandbox"],
    )
    yield browser
    browser.close()
    playwright.stop()


def _shell_html() -> str:
    # Production scripts need a backend. The browser fixture uses the exact
    # rendered shell/CSS and injects only the real modules under test.
    html = rendered_chat_html(inline_css=True)
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html,
                  flags=re.IGNORECASE | re.DOTALL)
    # The component contract is deliberately linked after the server-rendered
    # theme instead of joining _CSS_MODULES. Inline that final production layer
    # too, otherwise set_content() cannot resolve its relative HTTP URL.
    contract = (CHAT_UI / "css" / "100_component_contract.css").read_text(
        encoding="utf-8"
    )
    return html.replace("</head>", f"<style>{contract}</style></head>")


def _new_motion_page(chromium_browser, *, rows: int = 0,
                     reduced: bool = False,
                     viewport: tuple[int, int] = (1280, 800)):
    context = chromium_browser.new_context(
        viewport={"width": viewport[0], "height": viewport[1]},
        reduced_motion="reduce" if reduced else "no-preference",
    )
    page = context.new_page()
    page.set_content(_shell_html(), wait_until="domcontentloaded")
    page.evaluate(
        """
        () => {
          window.__PF_MOTION_DIAGNOSTICS__ = true;
          window.__PF_FLOATING_DIAGNOSTICS__ = true;
          window.__pfNativeCounts = {resizeObservers: 0, mutationObservers: 0};
          const NativeResizeObserver = window.ResizeObserver;
          const NativeMutationObserver = window.MutationObserver;
          window.ResizeObserver = function(callback) {
            const observer = new NativeResizeObserver(callback);
            const disconnect = observer.disconnect.bind(observer);
            let active = true;
            window.__pfNativeCounts.resizeObservers += 1;
            observer.disconnect = function() {
              if (active) window.__pfNativeCounts.resizeObservers -= 1;
              active = false;
              disconnect();
            };
            return observer;
          };
          window.ResizeObserver.prototype = NativeResizeObserver.prototype;
          window.MutationObserver = function(callback) {
            const observer = new NativeMutationObserver(callback);
            const disconnect = observer.disconnect.bind(observer);
            let active = true;
            window.__pfNativeCounts.mutationObservers += 1;
            observer.disconnect = function() {
              if (active) window.__pfNativeCounts.mutationObservers -= 1;
              active = false;
              disconnect();
            };
            return observer;
          };
          window.MutationObserver.prototype = NativeMutationObserver.prototype;
          window._isSectionCollapsed = () => false;
          window.__pfLongTasks = [];
          if (typeof PerformanceObserver === 'function') {
            try {
              window.__pfLongTaskObserver = new PerformanceObserver(list => {
                for (const entry of list.getEntries()) {
                  window.__pfLongTasks.push(entry.duration);
                }
              });
              window.__pfLongTaskObserver.observe({entryTypes: ['longtask']});
            } catch (_error) {}
          }
        }
        """
    )
    for controller in CONTROLLERS:
        page.add_script_tag(path=str(CHAT_UI / controller))
    page.evaluate(
        """
        rowCount => {
          window.__pfNativeBaseline = Object.assign({}, window.__pfNativeCounts);
          const host = document.createElement('main');
          host.id = 'pf-motion-browser-fixture';
          host.style.cssText = [
            'position:fixed', 'inset:0', 'z-index:2147483647',
            'box-sizing:border-box', 'overflow:auto', 'padding:18px',
            'background:#10131a', 'color:#f2f4f8',
            'font:14px/1.4 system-ui,sans-serif'
          ].join(';');
          host.innerHTML = [
            '<button id="fixture-trigger" type="button">Toggle details</button>',
            '<section id="fixture-panel"><input id="fixture-focus" value="focus target">',
            '<div style="height:72px;padding:8px">Deterministic disclosure content</div></section>',
            '<div id="fixture-source" hidden></div>',
            '<div id="fixture-projection" style="height:240px;overflow:auto"></div>',
            '<div id="fixture-resources"></div>'
          ].join('');
          document.body.appendChild(host);
          const source = host.querySelector('#fixture-source');
          for (let index = 0; index < rowCount; index += 1) {
            const row = document.createElement('article');
            row.dataset.msgid = 'fixture-' + index;
            row.innerHTML = '<span>message ' + index + '</span>';
            source.appendChild(row);
          }
          const panel = host.querySelector('#fixture-panel');
          const trigger = host.querySelector('#fixture-trigger');
          window.__pfFixture = {
            host,
            source,
            destination: host.querySelector('#fixture-projection'),
            resources: host.querySelector('#fixture-resources'),
            trigger,
            panel,
            projectionActive: true,
          };
          window.__pfFixture.disclosure = window.pfDisclosure.create({
            trigger,
            panel,
            open: false,
            duration: 80,
          });
          trigger.addEventListener('click', () => {
            window.__pfFixture.pendingDisclosure =
              window.__pfFixture.disclosure.toggle();
          });
          window.__pfFixture.projection = window.pfProjection.create({
            source,
            destination: window.__pfFixture.destination,
            isActive: () => window.__pfFixture.projectionActive,
            project: node => node.cloneNode(true),
          });
        }
        """,
        rows,
    )
    page.evaluate(
        "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
    )
    return context, page


@pytest.mark.parametrize("row_count", [500, 1000])
def test_streaming_projection_updates_only_one_key(chromium_browser, row_count):
    context, page = _new_motion_page(chromium_browser, rows=row_count)
    try:
        result = page.evaluate(
            """
            async rowCount => {
              const before = window.pfProjection.diagnostics();
              const target = window.__pfFixture.source.children[Math.floor(rowCount / 2)];
              target.querySelector('span').firstChild.data += ' streamed';
              await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
              const after = window.pfProjection.diagnostics();
              window.__pfFixture.projectionActive = false;
              window.__pfFixture.projection.setActive(false);
              target.querySelector('span').firstChild.data += ' hidden';
              await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
              const hidden = window.pfProjection.diagnostics();
              window.__pfFixture.projection.destroy();
              return {
                rows: window.__pfFixture.destination.children.length,
                initialClones: before.clones,
                cloneDelta: after.clones - before.clones,
                patchDelta: after.patchedRows - before.patchedRows,
                dirtyDelta: after.dirtyKeys - before.dirtyKeys,
                hiddenCloneDelta: hidden.clones - after.clones,
                hiddenPatchDelta: hidden.patchedRows - after.patchedRows,
                controllersAfterDestroy: window.pfProjection.diagnostics().controllers,
              };
            }
            """,
            row_count,
        )
        assert result == {
            "rows": row_count,
            "initialClones": row_count,
            "cloneDelta": 1,
            "patchDelta": 1,
            "dirtyDelta": 1,
            "hiddenCloneDelta": 0,
            "hiddenPatchDelta": 0,
            "controllersAfterDestroy": 0,
        }
    finally:
        context.close()


@pytest.mark.parametrize("viewport", [(1280, 800), (390, 844)])
@pytest.mark.parametrize("reduced", [False, True])
def test_disclosure_geometry_accessibility_and_first_paint(
        chromium_browser, viewport, reduced):
    context, page = _new_motion_page(
        chromium_browser, reduced=reduced, viewport=viewport,
    )
    try:
        page.evaluate(
            """
            async () => {
              const trigger = window.__pfFixture.trigger;
              trigger.click();
              await window.__pfFixture.pendingDisclosure;
              trigger.click();
              await window.__pfFixture.pendingDisclosure;
              window.__pfLongTasks.length = 0;
            }
            """
        )
        metrics = page.evaluate(
            """
            async () => {
              const samples = [];
              const trigger = window.__pfFixture.trigger;
              trigger.click();
              await new Promise(resolve => requestAnimationFrame(resolve));
              const firstOpeningHeight = window.__pfFixture.panel.getBoundingClientRect().height;
              await window.__pfFixture.pendingDisclosure;
              const settledOpeningHeight = window.__pfFixture.panel.getBoundingClientRect().height;
              await window.__pfFixture.disclosure.set(false);
              for (let index = 0; index < 30; index += 1) {
                const started = performance.now();
                trigger.click();
                await new Promise(resolve => requestAnimationFrame(resolve));
                samples.push(performance.now() - started);
                await window.__pfFixture.pendingDisclosure;
              }
              await window.__pfFixture.disclosure.set(true);
              const openRect = window.__pfFixture.panel.getBoundingClientRect();
              const open = {
                expanded: trigger.getAttribute('aria-expanded'),
                hidden: window.__pfFixture.panel.hidden,
                ariaHidden: window.__pfFixture.panel.getAttribute('aria-hidden'),
                inert: window.__pfFixture.panel.hasAttribute('inert'),
                width: openRect.width,
                height: openRect.height,
              };
              window.__pfFixture.panel.querySelector('input').focus();
              await window.__pfFixture.disclosure.set(false);
              const closed = {
                expanded: trigger.getAttribute('aria-expanded'),
                hidden: window.__pfFixture.panel.hidden,
                ariaHidden: window.__pfFixture.panel.getAttribute('aria-hidden'),
                inert: window.__pfFixture.panel.hasAttribute('inert'),
                focusRestored: document.activeElement === trigger,
              };
              return {
                samples,
                firstOpeningHeight,
                settledOpeningHeight,
                longTasks: window.__pfLongTasks.slice(),
                open,
                closed,
                motion: window.pfMotion.diagnostics(),
              };
            }
            """
        )
        page.evaluate("() => window.__pfFixture.disclosure.set(true)")
        open_png = page.locator("#pf-motion-browser-fixture").screenshot()
        page.evaluate("() => window.__pfFixture.disclosure.set(false)")
        closed_png = page.locator("#pf-motion-browser-fixture").screenshot()

        assert metrics["open"]["expanded"] == "true"
        assert metrics["open"]["hidden"] is False
        assert metrics["open"]["ariaHidden"] is None
        assert metrics["open"]["inert"] is False
        assert metrics["open"]["width"] > 0
        assert metrics["open"]["height"] > 0
        assert metrics["closed"] == {
            "expanded": "false",
            "hidden": True,
            "ariaHidden": "true",
            "inert": True,
            "focusRestored": True,
        }
        assert metrics["motion"]["activeAnimations"] == 0
        if reduced:
            assert metrics["firstOpeningHeight"] == metrics["settledOpeningHeight"]
        else:
            assert metrics["firstOpeningHeight"] < metrics["settledOpeningHeight"]
        assert hashlib.sha256(open_png).digest() != hashlib.sha256(closed_png).digest()
        assert max(metrics["samples"]) < 1000
        if REFERENCE_BROWSER:
            p95 = sorted(metrics["samples"])[math.ceil(len(metrics["samples"]) * 0.95) - 1]
            assert p95 < 16.7
            assert metrics["longTasks"] == []
    finally:
        context.close()


def test_sidebar_accordion_and_workspace_layout_morph_in_real_chromium(
        chromium_browser):
    context = chromium_browser.new_context(
        viewport={"width": 1280, "height": 800}, reduced_motion="no-preference",
    )
    page = context.new_page()
    try:
        page.set_content(_shell_html(), wait_until="domcontentloaded")
        for script in ("ui_motion.js", "resources.js", "workspace.js"):
            page.add_script_tag(path=str(CHAT_UI / script))
        result = page.evaluate(
            """
            async () => {
              const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
              const finish = animations => Promise.all(animations.map(animation =>
                animation.finished.catch(() => undefined)));
              const sidebar = document.getElementById('sidebar');
              sidebar.classList.remove('collapsed');
              await frame();

              const conversations = document.getElementById('conversationsPanel');
              const resources = document.getElementById('resourcesPanel');
              const conversationsBody = document.getElementById('conversationsBody');
              const resourcesBody = document.getElementById('resourcesBody');
              const resourceSection = document.createElement('section');
              resourceSection.className = 'resource-section';
              resourceSection.innerHTML = [
                '<div class="resource-section-header-row">',
                '<button class="resource-section-control resource-section-toggle">Agents</button>',
                '<button class="resource-section-control resource-section-action">+</button>',
                '</div>'
              ].join('');
              resourcesBody.appendChild(resourceSection);
              const resourceControlStyles = Array.from(
                resourceSection.querySelectorAll('.resource-section-control')
              ).map(control => ({
                background: getComputedStyle(control).backgroundColor,
                shadow: getComputedStyle(control).boxShadow,
              }));
              const accordionBefore = {
                conversations: conversations.getBoundingClientRect().height,
                resources: resources.getBoundingClientRect().height,
              };
              setSidebarSection('resources');
              await frame();
              const accordionAnimations = [
                ...conversations.getAnimations(), ...conversationsBody.getAnimations(),
                ...resources.getAnimations(), ...resourcesBody.getAnimations(),
              ];
              const accordionKeyframes = accordionAnimations.map(animation =>
                animation.effect.getKeyframes());
              const accordionDurations = accordionAnimations.map(animation =>
                animation.effect.getTiming().duration);
              await new Promise(resolve => setTimeout(resolve, 150));
              const accordionDuring = {
                conversations: conversations.getBoundingClientRect().height,
                resources: resources.getBoundingClientRect().height,
              };
              await finish(accordionAnimations);
              await frame();
              const accordionAfter = {
                conversations: conversations.getBoundingClientRect().height,
                resources: resources.getBoundingClientRect().height,
                conversationsActive: conversations.classList.contains('active'),
                resourcesActive: resources.classList.contains('active'),
              };

              const openspace = document.getElementById('tabContentOpenspace');
              workspaceRegisterSurface(openspace, {
                tabId: 'openspace', type: 'openspace', title: 'OpenSpace', closable: true,
              });
              await workspaceSetLayout(2);
              const chat = document.getElementById('tabContentChat');
              const tiledWidth = chat.getBoundingClientRect().width;

              const maximize = workspaceSetLayout(1);
              await frame();
              const maximizeAnimations = chat.getAnimations();
              const maximizeKeyframes = maximizeAnimations.map(animation =>
                animation.effect.getKeyframes());
              await maximize;
              const fullWidth = chat.getBoundingClientRect().width;

              const restore = workspaceSetLayout(2);
              await frame();
              const restoreAnimations = chat.getAnimations();
              const restoreKeyframes = restoreAnimations.map(animation =>
                animation.effect.getKeyframes());
              await restore;
              const restoredWidth = chat.getBoundingClientRect().width;
              return {
                accordionBefore,
                accordionAfter,
                resourceControlStyles,
                accordionAnimationCount: accordionAnimations.length,
                accordionKeyframes,
                accordionDurations,
                accordionDuring,
                tiledWidth,
                fullWidth,
                restoredWidth,
                maximizeAnimationCount: maximizeAnimations.length,
                restoreAnimationCount: restoreAnimations.length,
                maximizeKeyframes,
                restoreKeyframes,
              };
            }
            """
        )

        assert result["accordionAnimationCount"] == 4
        assert all(style["background"] == "rgba(0, 0, 0, 0)"
                   for style in result["resourceControlStyles"])
        assert all(style["shadow"] == "none"
                   for style in result["resourceControlStyles"])
        assert result["accordionBefore"]["conversations"] > result["accordionBefore"]["resources"]
        assert result["accordionAfter"]["conversations"] < result["accordionAfter"]["resources"]
        assert result["accordionAfter"]["conversationsActive"] is False
        assert result["accordionAfter"]["resourcesActive"] is True
        assert all(len(frames) == 2 for frames in result["accordionKeyframes"])
        assert set(result["accordionDurations"]) == {500}
        conversations_progress = (
            result["accordionBefore"]["conversations"]
            - result["accordionDuring"]["conversations"]
        ) / (
            result["accordionBefore"]["conversations"]
            - result["accordionAfter"]["conversations"]
        )
        resources_progress = (
            result["accordionDuring"]["resources"]
            - result["accordionBefore"]["resources"]
        ) / (
            result["accordionAfter"]["resources"]
            - result["accordionBefore"]["resources"]
        )
        assert 0.15 < conversations_progress < 0.85
        assert 0.15 < resources_progress < 0.85
        assert result["fullWidth"] > result["tiledWidth"]
        assert abs(result["restoredWidth"] - result["tiledWidth"]) < 1
        assert result["maximizeAnimationCount"] == 1
        assert result["restoreAnimationCount"] == 1
        maximize_transform = result["maximizeKeyframes"][0][0]["transform"]
        restore_transform = result["restoreKeyframes"][0][0]["transform"]
        assert "scale(" in maximize_transform
        assert "scale(" in restore_transform
        assert maximize_transform != restore_transform
    finally:
        context.close()


def test_resources_service_node_keeps_its_own_scroll_after_opening(
        chromium_browser):
    context = chromium_browser.new_context(
        viewport={"width": 1280, "height": 800}, reduced_motion="no-preference",
    )
    page = context.new_page()
    try:
        page.set_content(_shell_html(), wait_until="domcontentloaded")
        for script in (
            "ui_motion.js", "ui_disclosure.js", "resources_patch.js", "resources.js",
        ):
            page.add_script_tag(path=str(CHAT_UI / script))
        page.evaluate(
            """
            async () => {
              document.getElementById('sidebarShell').classList.remove('collapsed');
              document.getElementById('sidebar').classList.remove('collapsed');
              setSidebarSection('resources');
              const content = document.getElementById('resourcesContent');
              const rows = Array.from({length: 40}, (_, index) =>
                '<div style="height:24px">service ' + index + '</div>').join('');
              _patchResourcesContent(content, [
                '<section class="resource-section" data-resource-section="_svc">',
                '<div class="resource-section-header-row">',
                '<button class="resource-section-toggle" aria-controls="res-section-_svc"',
                ' aria-expanded="false">Services</button></div>',
                '<div class="resource-section-body" id="res-section-_svc"',
                ' style="max-height:260px;overflow-y:auto" hidden aria-hidden="true" inert>',
                rows, '</div></section>',
              ].join(''));
              await _toggleSection('_svc');
            }
            """
        )
        service_body = page.locator("#res-section-_svc")
        box = service_body.bounding_box()
        assert box is not None
        before = service_body.evaluate(
            "element => ({"
            " scrollTop: element.scrollTop,"
            " clientHeight: element.clientHeight,"
            " scrollHeight: element.scrollHeight,"
            " overflowY: getComputedStyle(element).overflowY"
            "})"
        )
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.wheel(0, 240)
        page.wait_for_timeout(50)
        after = service_body.evaluate("element => element.scrollTop")

        assert before["scrollHeight"] > before["clientHeight"]
        assert before["overflowY"] == "auto"
        assert before["scrollTop"] == 0
        assert after > 0
    finally:
        context.close()


def test_desktop_tab_rail_keeps_content_present_while_sliding(chromium_browser):
    context = chromium_browser.new_context(
        viewport={"width": 1280, "height": 800}, reduced_motion="no-preference",
    )
    page = context.new_page()
    try:
        page.set_content(_shell_html(), wait_until="domcontentloaded")
        content = page.locator("#tabBar > .tab-bar-content")
        assert content.count() == 1

        def snapshot():
            return page.evaluate(
                """
                () => {
                  const rail = document.getElementById('tabBar');
                  const content = rail.querySelector('.tab-bar-content');
                  const icon = document.getElementById('tabChat');
                  const railRect = rail.getBoundingClientRect();
                  const iconRect = icon.getBoundingClientRect();
                  const style = getComputedStyle(content);
                  return {
                    railLeft: railRect.left,
                    iconCenter: iconRect.left + iconRect.width / 2,
                    iconOffset: iconRect.left + iconRect.width / 2 - railRect.left,
                    opacity: Number(style.opacity),
                    visibility: style.visibility,
                    railDurations: rail.getAnimations()
                      .map(animation => animation.effect.getTiming().duration),
                    contentAnimationCount: content.getAnimations().length,
                  };
                }
                """
            )

        page.mouse.move(640, 400)
        closed = snapshot()
        handle_box = page.locator("#tabBar .tab-bar-handle").bounding_box()
        assert handle_box is not None
        page.mouse.move(
            handle_box["x"] + handle_box["width"] / 2,
            handle_box["y"] + handle_box["height"] / 2,
        )
        page.wait_for_timeout(300)
        opening = snapshot()
        page.wait_for_timeout(650)
        opened = snapshot()
        page.mouse.move(640, 400)
        page.wait_for_timeout(300)
        closing = snapshot()
        page.wait_for_timeout(650)
        closed_again = snapshot()

        assert opened["railLeft"] < opening["railLeft"] < closed["railLeft"]
        assert opened["railLeft"] < closing["railLeft"] < closed_again["railLeft"]
        for state in (closed, opening, opened, closing, closed_again):
            assert state["opacity"] == pytest.approx(1, abs=0.01)
            assert state["visibility"] == "visible"
            assert state["contentAnimationCount"] == 0
            assert abs(state["iconOffset"] - opened["iconOffset"]) < 1
        assert opening["iconCenter"] < closed["iconCenter"]
        assert closing["iconCenter"] > opened["iconCenter"]
        assert 900 in opening["railDurations"]
        assert 900 in closing["railDurations"]
        assert 500 not in opening["railDurations"]
        assert 500 not in closing["railDurations"]
    finally:
        context.close()


def test_chrome_grips_move_through_intermediate_geometry_in_real_chromium(
        chromium_browser):
    context = chromium_browser.new_context(
        viewport={"width": 1280, "height": 800}, reduced_motion="no-preference",
    )
    page = context.new_page()
    try:
        page.set_content(_shell_html(), wait_until="domcontentloaded")
        page.evaluate(
            """
            () => {
              const values = new Map([
                ['pawflow.composerDrawerOpen', '1'],
                ['pawflow.headerBarOpen', '1'],
              ]);
              Object.defineProperty(window, 'localStorage', {
                configurable: true,
                value: {
                  getItem: key => values.has(key) ? values.get(key) : null,
                  setItem: (key, value) => values.set(key, String(value)),
                },
              });
              window.LOGIN_URL = '';
            }
            """
        )
        for script in ("ui_motion.js", "state.js"):
            page.add_script_tag(path=str(CHAT_UI / script))
        result = page.evaluate(
            """
            async () => {
              const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
              const sample = async (element, dimension, toggle, follower) => {
                const followerDelta = () => {
                  if (!follower) return null;
                  const ownerRect = element.getBoundingClientRect();
                  const followerRect = follower.getBoundingClientRect();
                  if (ownerRect.height < 0.5) return Math.abs(follower.offsetTop);
                  return Math.abs(
                    followerRect.top + followerRect.height / 2 - ownerRect.bottom);
                };
                const before = element.getBoundingClientRect()[dimension];
                const beforeFollowerDelta = followerDelta();
                const pending = toggle();
                await frame();
                const animations = element.getAnimations();
                const durations = animations.map(animation =>
                  animation.effect.getTiming().duration);
                await new Promise(resolve => setTimeout(resolve, 150));
                const during = element.getBoundingClientRect()[dimension];
                const duringFollowerDelta = followerDelta();
                await pending;
                await frame();
                return {
                  before,
                  during,
                  after: element.getBoundingClientRect()[dimension],
                  durations,
                  followerDeltas: [beforeFollowerDelta, duringFollowerDelta, followerDelta()],
                };
              };

              await Promise.all([
                _applyHeaderBar(false),
                _applyComposerDrawer(false),
              ]);
              document.getElementById('promptControlsPanel').classList.add('visible');
              await _applyComposerDrawer(false);
              const headerGrip = document.getElementById('headerGrip');
              const headerClosing = await sample(
                document.querySelector('.header-shell'), 'height', toggleHeaderBar,
                headerGrip);
              const headerOpening = await sample(
                document.querySelector('.header-shell'), 'height', toggleHeaderBar,
                headerGrip);
              const composerClosing = await sample(
                document.querySelector('.composer-context-row'), 'height',
                toggleComposerDrawer);
              const composerOpening = await sample(
                document.querySelector('.composer-context-row'), 'height',
                toggleComposerDrawer);
              return {
                headerClosing,
                headerOpening,
                composerClosing,
                composerOpening,
                headerCollapsed: document.getElementById('headerBar')
                  .classList.contains('collapsed'),
                composerCollapsed: document.querySelector('.input-area')
                  .classList.contains('composer-drawer-collapsed'),
              };
            }
            """
        )

        for name in ("headerClosing", "composerClosing"):
            motion = result[name]
            assert set(motion["durations"]) == {500}
            assert motion["before"] > motion["during"] > motion["after"], (name, motion)
            progress = ((motion["before"] - motion["during"])
                        / (motion["before"] - motion["after"]))
            assert 0.15 < progress < 0.85
        for name in ("headerOpening", "composerOpening"):
            motion = result[name]
            assert set(motion["durations"]) == {500}
            assert motion["before"] < motion["during"] < motion["after"], (name, motion)
            progress = ((motion["during"] - motion["before"])
                        / (motion["after"] - motion["before"]))
            assert 0.15 < progress < 0.85
        for name in ("headerClosing", "headerOpening"):
            assert max(result[name]["followerDeltas"]) < 1, (name, result[name])
        assert result["headerCollapsed"] is False
        assert result["composerCollapsed"] is False
    finally:
        context.close()


def test_desktop_sidebar_keeps_content_present_while_sliding(chromium_browser):
    context = chromium_browser.new_context(
        viewport={"width": 1280, "height": 800}, reduced_motion="no-preference",
    )
    page = context.new_page()
    try:
        page.set_content(_shell_html(), wait_until="domcontentloaded")
        page.evaluate("() => { window.LOGIN_URL = ''; }")
        for script in ("ui_motion.js", "state.js"):
            page.add_script_tag(path=str(CHAT_UI / script))

        def snapshot():
            return page.evaluate(
                """
                () => {
                  const shell = document.getElementById('sidebarShell');
                  const sidebar = document.getElementById('sidebar');
                  const content = document.getElementById('conversationsPanel');
                  const grip = document.getElementById('sidebarToggle');
                  const main = document.querySelector('.main');
                  const shellRect = shell.getBoundingClientRect();
                  const sidebarRect = sidebar.getBoundingClientRect();
                  const contentRect = content.getBoundingClientRect();
                  const gripRect = grip.getBoundingClientRect();
                  const mainRect = main.getBoundingClientRect();
                  const style = getComputedStyle(content);
                  return {
                    shellLeft: shellRect.left,
                    sidebarOffset: sidebarRect.left - shellRect.left,
                    contentOffset: contentRect.left - sidebarRect.left,
                    gripOffset: gripRect.left - shellRect.right,
                    opacity: Number(style.opacity),
                    visibility: style.visibility,
                    railDurations: shell.getAnimations()
                      .map(animation => animation.effect.getTiming().duration),
                    contentAnimationCount: content.getAnimations().length,
                    mainLeft: mainRect.left,
                    mainWidth: mainRect.width,
                  };
                }
                """
            )

        closed = snapshot()
        page.evaluate("() => { window.__sidebarMotion = toggleSidebar(); }")
        page.wait_for_timeout(300)
        opening = snapshot()
        page.wait_for_timeout(650)
        opened = snapshot()
        page.evaluate("() => { window.__sidebarMotion = toggleSidebar(); }")
        page.wait_for_timeout(300)
        closing = snapshot()
        page.wait_for_timeout(650)
        closed_again = snapshot()

        assert closed["shellLeft"] < opening["shellLeft"] < opened["shellLeft"]
        assert closed_again["shellLeft"] < closing["shellLeft"] < opened["shellLeft"]
        for state in (closed, opening, opened, closing, closed_again):
            assert state["opacity"] == pytest.approx(1, abs=0.01)
            assert state["visibility"] == "visible"
            assert state["contentAnimationCount"] == 0
            assert abs(state["sidebarOffset"]) < 1
            assert abs(state["contentOffset"]) < 1
            assert abs(state["gripOffset"]) < 1
            assert abs(state["mainLeft"] - closed["mainLeft"]) < 1
            assert abs(state["mainWidth"] - closed["mainWidth"]) < 1
        assert 900 in opening["railDurations"]
        assert 900 in closing["railDurations"]
    finally:
        context.close()


def test_workspace_selected_header_and_title_drag_persist_in_real_chromium(
        chromium_browser):
    context = chromium_browser.new_context(
        viewport={"width": 1280, "height": 800}, reduced_motion="no-preference",
    )
    page = context.new_page()
    try:
        page.set_content(_shell_html(), wait_until="domcontentloaded")
        page.evaluate(
            """
            () => {
              const values = new Map();
              Object.defineProperty(window, 'localStorage', {
                configurable: true,
                value: {
                  getItem: key => values.has(key) ? values.get(key) : null,
                  setItem: (key, value) => values.set(key, String(value)),
                },
              });
            }
            """
        )
        for script in ("ui_motion.js", "workspace.js"):
            page.add_script_tag(path=str(CHAT_UI / script))
        page.evaluate(
            """
            () => {
              const openspace = document.getElementById('tabContentOpenspace');
              workspaceRegisterSurface(openspace, {
                tabId: 'openspace', type: 'openspace', title: 'OpenSpace', closable: true,
              });
              workspaceSetLayout(2);
              workspaceFocusSurface('openspace');
            }
            """
        )

        selected = page.locator(
            "#tabContentOpenspace > .workspace-surface-header")
        unselected = page.locator(
            "#tabContentChat > .workspace-surface-header")
        assert selected.evaluate("element => getComputedStyle(element).backgroundColor") \
            != unselected.evaluate("element => getComputedStyle(element).backgroundColor")
        assert selected.get_attribute("draggable") == "true"

        selected.drag_to(unselected, target_position={"x": 8, "y": 14})
        page.wait_for_timeout(50)
        result = page.evaluate(
            """
            () => {
              const order = Array.from(document.getElementById('workspaceBoard').children)
                .map(panel => panel.dataset.tab)
                .filter(Boolean);
              const state = JSON.parse(
                localStorage.getItem('pawflow.workspace.state.v2') || 'null');
              return {
                order,
                stored: state.surfaces.map(surface => surface.surfaceId),
                selected: workspaceSelectedTab(),
              };
            }
            """
        )
        assert result["order"][:2] == ["openspace", "chat"]
        assert result["stored"][:2] == ["openspace", "chat"]
        assert result["selected"] == "openspace"
    finally:
        context.close()


def test_all_buttons_share_prompt_bar_surface_zoom_and_tooltip_in_chromium(
        chromium_browser):
    context = chromium_browser.new_context(
        viewport={"width": 1280, "height": 800}, reduced_motion="no-preference",
    )
    page = context.new_page()
    try:
        page.set_content(_shell_html(), wait_until="domcontentloaded")
        for script in ("ui_motion.js", "ui_floating_layer.js", "tooltips.js"):
            page.add_script_tag(path=str(CHAT_UI / script))
        page.evaluate(
            """
            () => {
              const button = document.createElement('button');
              button.id = 'buttonContractFixture';
              button.type = 'button';
              button.title = 'Confirm action';
              button.textContent = 'Confirm';
              button.style.cssText = 'position:fixed;left:520px;top:240px;padding:8px 12px';
              document.body.appendChild(button);
            }
            """
        )

        rest = page.evaluate(
            """
            () => ['appearanceBtn', 'fileAttachBtn', 'sendBtn', 'buttonContractFixture']
              .map(id => {
                const style = getComputedStyle(document.getElementById(id));
                return {id, background: style.backgroundColor, border: style.borderTopColor};
              })
            """
        )
        for state in rest:
            assert state["background"] == "rgba(0, 0, 0, 0)", state
            assert state["border"] == "rgba(0, 0, 0, 0)", state

        fixture = page.locator("#buttonContractFixture")
        fixture.hover()
        page.wait_for_timeout(220)
        hovered = fixture.evaluate(
            """
            element => {
              const style = getComputedStyle(element);
              const matrix = new DOMMatrixReadOnly(style.transform);
              return {
                background: style.backgroundColor,
                border: style.borderTopColor,
                scale: Math.hypot(matrix.a, matrix.b),
              };
            }
            """
        )
        tooltip = page.locator("#pfCssTooltip")
        assert hovered["background"] == "rgba(0, 0, 0, 0)"
        assert hovered["border"] != "rgba(0, 0, 0, 0)"
        assert hovered["scale"] > 1.05
        assert tooltip.get_attribute("aria-hidden") == "false"
        assert "Confirm action" in tooltip.inner_text()
        assert fixture.get_attribute("title") is None
        assert fixture.get_attribute("aria-describedby") == "pfCssTooltip"
    finally:
        context.close()


def test_top_grip_real_click_has_one_continuous_trajectory(chromium_browser):
    context = chromium_browser.new_context(
        viewport={"width": 1280, "height": 800}, reduced_motion="no-preference",
    )
    page = context.new_page()
    try:
        page.set_content(_shell_html(), wait_until="domcontentloaded")
        page.evaluate(
            """
            () => {
              const values = new Map([['pawflow.headerBarOpen', '1']]);
              Object.defineProperty(window, 'localStorage', {
                configurable: true,
                value: {
                  getItem: key => values.has(key) ? values.get(key) : null,
                  setItem: (key, value) => values.set(key, String(value)),
                },
              });
              window.LOGIN_URL = '';
            }
            """
        )
        for script in ("ui_motion.js", "state.js"):
            page.add_script_tag(path=str(CHAT_UI / script))
        page.evaluate("() => _applyHeaderBar(false)")

        def click_trace():
            page.evaluate(
                """
                () => {
                  window.__pfHeaderClickTrace = [];
                  const grip = document.getElementById('headerGrip');
                  grip.addEventListener('click', () => {
                    const shell = document.querySelector('.header-shell');
                    const started = performance.now();
                    const capture = now => {
                      const shellRect = shell.getBoundingClientRect();
                      const gripRect = grip.getBoundingClientRect();
                      window.__pfHeaderClickTrace.push({
                        time: now - started,
                        shellHeight: shellRect.height,
                        gripHeight: gripRect.height,
                        gripCenter: gripRect.top + gripRect.height / 2,
                      });
                    };
                    capture(started);
                    const frame = now => {
                      capture(now);
                      if (now - started < 550) requestAnimationFrame(frame);
                    };
                    requestAnimationFrame(frame);
                  }, {capture: true, once: true});
                }
                """
            )
            grip_box = page.locator("#headerGrip").bounding_box()
            assert grip_box is not None
            center = (
                grip_box["x"] + grip_box["width"] / 2,
                grip_box["y"] + grip_box["height"] / 2,
            )
            page.mouse.move(*center)
            page.wait_for_timeout(220)
            page.mouse.click(*center)
            page.wait_for_timeout(600)
            return page.evaluate("() => window.__pfHeaderClickTrace")

        closing = click_trace()
        opening = click_trace()

        for name, samples, direction in (
            ("closing", closing, -1),
            ("opening", opening, 1),
        ):
            heights = [sample["shellHeight"] for sample in samples]
            assert direction * (heights[-1] - heights[0]) > 40, (name, heights)
            assert max(sample["gripHeight"] for sample in samples) - min(
                sample["gripHeight"] for sample in samples
            ) < 0.5, (name, samples)
            for sample in samples:
                expected_center = max(7.5, sample["shellHeight"])
                assert abs(sample["gripCenter"] - expected_center) < 1, (
                    name, sample,
                )
            speeds = [
                abs(after["shellHeight"] - before["shellHeight"])
                / max(1, after["time"] - before["time"])
                for before, after in zip(samples, samples[1:])
            ]
            assert max(speeds) < 0.8, (name, max(speeds), samples)
    finally:
        context.close()


def _stop_cdp_trace(page, cdp) -> dict[str, float | int]:
    completed = []
    cdp.on("Tracing.tracingComplete", lambda event: completed.append(event))
    cdp.send("Tracing.end")
    for _ in range(20):
        if completed:
            break
        page.wait_for_timeout(25)
    assert completed, "Chromium did not finish the performance trace"
    stream = completed[0]["stream"]
    chunks = []
    while True:
        part = cdp.send("IO.read", {"handle": stream})
        chunks.append(part.get("data", ""))
        if part.get("eof"):
            break
    cdp.send("IO.close", {"handle": stream})
    events = json.loads("".join(chunks)).get("traceEvents", [])
    dispatch = [event.get("dur", 0) / 1000 for event in events
                if event.get("name") == "EventDispatch"]
    return {
        "events": len(events),
        "layoutEvents": sum(event.get("name") in {"Layout", "UpdateLayoutTree"}
                            for event in events),
        "maxEventDispatchMs": max(dispatch, default=0),
    }


def test_cdp_trace_and_lifecycle_soak_return_to_baseline(chromium_browser):
    context, page = _new_motion_page(chromium_browser, rows=500, reduced=True)
    try:
        cdp = context.new_cdp_session(page)
        cdp.send("Tracing.start", {
            "categories": "devtools.timeline,blink.user_timing,v8.execute",
            "transferMode": "ReturnAsStream",
        })
        result = page.evaluate(
            """
            async () => {
              const fixture = window.__pfFixture;
              const rootIdentity = fixture.resources;
              const sectionHtml = [
                '<section data-resource-section="sample">',
                '<div class="resource-section-header-row"><button class="resource-section-toggle">Sample</button></div>',
                '<div id="res-section-sample" class="resource-section-body"><button>Child</button></div>',
                '</section>'
              ].join('');
              window.pfResources.patchContent(fixture.resources, sectionHtml);
              const sectionIdentity = fixture.resources.firstElementChild;
              for (let index = 0; index < 100; index += 1) {
                await window.pfResources.setSectionOpen('sample', false);
                await window.pfResources.setSectionOpen('sample', true);
                const trigger = document.createElement('button');
                const layer = document.createElement('div');
                layer.innerHTML = '<button class="ctx-menu-item">Item</button>';
                fixture.host.append(trigger, layer);
                const floating = window.pfFloatingLayer.open({
                  channel: 'soak-layer',
                  element: layer,
                  trigger,
                  animate: false,
                  removeOnClose: true,
                });
                await floating.close({reason: 'soak', restoreFocus: false});
                trigger.remove();
              }
              const identityPreserved = rootIdentity === fixture.resources
                && sectionIdentity === fixture.resources.firstElementChild;
              window.pfResources.clear(fixture.resources);
              fixture.disclosure.destroy();
              fixture.projection.destroy();
              if (window.__pfLongTaskObserver) window.__pfLongTaskObserver.disconnect();
              await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
              return {
                identityPreserved,
                floating: window.pfFloatingLayer.diagnostics(),
                motion: window.pfMotion.diagnostics(),
                nativeBaseline: Object.assign({}, window.__pfNativeBaseline),
                nativeCounts: Object.assign({}, window.__pfNativeCounts),
              };
            }
            """
        )
        trace = _stop_cdp_trace(page, cdp)
        assert result["identityPreserved"] is True
        assert result["floating"]["activeLayers"] == 0
        assert result["floating"]["listeners"] == 0
        assert result["motion"]["activeAnimations"] == 0
        assert result["motion"]["queuedReads"] == 0
        assert result["motion"]["queuedWrites"] == 0
        assert result["motion"]["framePending"] is False
        assert result["nativeCounts"] == result["nativeBaseline"]
        assert trace["events"] > 0
        assert trace["layoutEvents"] > 0
        if REFERENCE_BROWSER:
            assert trace["maxEventDispatchMs"] < 50
    finally:
        context.close()
