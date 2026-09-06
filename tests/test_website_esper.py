"""Behavioral contracts for the photographic public website."""
from __future__ import annotations

import functools
import http.server
import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

SITE = Path(__file__).resolve().parents[1] / "pawflow-website"


def node_result(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for website JavaScript contracts")
    result = subprocess.run([node, "-e", script], cwd=SITE, text=True,
                            capture_output=True, timeout=20, check=True)
    return json.loads(result.stdout)


def test_scene_graph_has_reachable_branches_and_real_assets():
    world = node_result("global.window={};require('./esper-world.js');console.log(JSON.stringify(window.ESPER_WORLD));")
    scenes = world["scenes"]
    pending, reached = [world["root"]], set()
    while pending:
        scene_id = pending.pop()
        if scene_id in reached:
            continue
        reached.add(scene_id)
        scene = scenes[scene_id]
        assert (SITE / scene["image"]).stat().st_size > 10_000
        assert scene["portals"], f"Dead end: {scene_id}"
        for portal in scene["portals"]:
            assert portal["target"] in scenes
            x, y, w, h = portal["rect"]
            assert 0 <= x < x + w <= 1
            assert 0 <= y < y + h <= 1
            assert portal["label"] and portal["href"]
            pending.append(portal["target"])
    assert reached == set(scenes)
    assert len(scenes["control"]["portals"]) == 4
    assert len(scenes["archive"]["portals"]) == 8
    for audio in ("ambient.mp3", "zoom-in.mp3", "zoom-out.mp3"):
        assert (SITE / "assets/media/esper" / audio).stat().st_size > 50_000


def test_camera_remains_finite_after_thousands_of_recursive_zooms():
    result = node_result("""
global.window={};global.devicePixelRatio=1;
global.Image=class {naturalWidth=1024;naturalHeight=1024;decode(){return Promise.resolve();}};
global.ResizeObserver=class {observe(){}disconnect(){}};
require('./esper-world.js');require('./esper-camera.js');
let calls=0;
const paint={setTransform(){},fillRect(){},drawImage(image,...coords){
  if(!coords.every(Number.isFinite))throw Error('Nonfinite canvas coordinates');
  if(coords[2]<=0||coords[3]<=0||coords[6]<=0||coords[7]<=0)throw Error('Empty draw');
  calls++;
}};
const canvas={getContext:()=>paint,parentElement:{getBoundingClientRect:()=>({width:1440,height:900})}};
(async()=>{
  const camera=new window.EsperCamera(canvas,window.ESPER_WORLD);
  await camera.ready;
  const path=camera.extend([window.ESPER_WORLD.root],4098);
  for(const depth of [0,.5,.999999,1,1.000001,12.5,127.5,1024.5,4096.5]){
    calls=0;camera.draw(depth,path);
    if(calls>110||calls===0)throw Error('Unbounded or blank rendering');
    for(const p of camera.state.portals){
      if(![p.x,p.y,p.w,p.h].every(Number.isFinite))throw Error('Nonfinite portal');
    }
  }
  for(const scene of Object.keys(window.ESPER_WORLD.scenes)){
    const route=camera.canonicalPath(scene);
    if(route.at(-1)!==scene)throw Error('Wrong destination');
    camera.draw(route.length-1,route);
    if(camera.state.scene!==scene)throw Error('Wrong photographed scene');
  }
  camera.destroy();
  console.log(JSON.stringify({ok:true,depth:4096.5}));
})().catch(e=>{console.error(e);process.exit(1)});
""")
    assert result == {"ok": True, "depth": 4096.5}


@pytest.fixture
def browser_site():
    playwright = pytest.importorskip("playwright.sync_api")
    binary = shutil.which("chromium") or shutil.which("chromium-browser")
    if not binary:
        pytest.skip("Installed Chromium is required for browser contracts")

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def copyfile(self, source, outputfile):
            try:
                super().copyfile(source, outputfile)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Quiet, directory=str(SITE)))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with playwright.sync_playwright() as engine:
            browser = engine.chromium.launch(
                headless=True, executable_path=binary, args=["--no-sandbox"])
            yield browser, f"http://127.0.0.1:{server.server_port}/"
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def ready(page):
    page.wait_for_function("window.PawFlowEsper && PawFlowEsper.state.ready")


def settled(page):
    page.wait_for_function("!PawFlowEsper.state.busy")


def jump(page, file, section=None):
    index = page.evaluate("""([file,id])=>PawFlowEsper.sections.find(
        s=>s.file===file&&(!id||s.id===id)).index""", [file, section])
    page.evaluate("(i)=>PawFlowEsper.navigate(i)", index)
    settled(page)
    return index


def test_browser_every_section_and_interaction(browser_site):
    browser, base = browser_site
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base)
    ready(page)
    assert page.evaluate("PawFlowEsper.state.total") >= 150
    assert page.locator(".esper-portal").count() == 1

    # One wheel gesture performs one transition, even with repeated events.
    page.mouse.move(750, 730)
    page.mouse.wheel(0, 160)
    page.mouse.wheel(0, 160)
    settled(page)
    assert page.evaluate("PawFlowEsper.state.current") == 1
    page.locator("#esper-back").click()
    settled(page)
    assert page.evaluate("PawFlowEsper.state.current") == 0
    page.wait_for_function("PawFlowEsper.state.audio.time > .1")
    assert page.evaluate("PawFlowEsper.state.audio.loop")
    page.locator("#esper-sound").click()
    assert page.evaluate("PawFlowEsper.state.audio.paused")
    page.locator("#esper-sound").click()

    # Direct index navigation and physical branch entry.
    page.locator("#esper-motion").click()
    page.locator("#esper-index-open").click()
    page.locator("#esper-search").fill("howtos-home")
    page.locator("#esper-index-results a").click()
    settled(page)
    assert page.evaluate("PawFlowEsper.state.camera.scene") == "archive"
    assert page.locator(".esper-portal").count() == 8
    page.locator('.esper-portal[data-photo-target="agents"]').click()
    settled(page)
    assert page.evaluate("PawFlowEsper.state.section") == "agents-interop"
    assert page.locator(".esper-recipes a").count() == 9
    page.locator("#esper-junction").click()
    settled(page)
    assert page.evaluate("PawFlowEsper.state.camera.scene") == "archive"

    jump(page, "howtos.html", "voice-service")
    assert page.locator("#esper-content #voice-service").count() == 1
    voice = page.evaluate("PawFlowEsper.state.current")
    jump(page, "faq.html")
    page.go_back()
    settled(page)
    assert page.evaluate("PawFlowEsper.state.current") == voice
    page.go_forward()
    settled(page)
    assert page.evaluate("PawFlowEsper.state.file") == "faq.html"

    # Every indexed destination renders its complete source node.
    records = page.evaluate("PawFlowEsper.sections")
    actual = set()
    for record in records:
        page.evaluate("(i)=>PawFlowEsper.navigate(i)", record["index"])
        settled(page)
        assert page.locator("#esper-content > .esper-section").get_attribute("id") == record["id"]
        actual.add((record["file"], record["id"]))
        assert page.evaluate("Number.isFinite(PawFlowEsper.state.camera.pose.ux)")
    assert len(actual) == len(records)
    assert len({r["file"] for r in records}) == 11

    # Help typing owns its keys and wheel.
    page.locator(".pf-help-launcher").click()
    field = page.locator(".pf-help-panel textarea")
    field.fill("Local interaction check")
    current = page.evaluate("PawFlowEsper.state.current")
    field.press("ArrowRight")
    assert page.evaluate("PawFlowEsper.state.current") == current
    page.locator(".pf-help-close").click()
    assert not errors
    page.close()


def test_mobile_deep_link_and_reduced_motion(browser_site):
    browser, base = browser_site
    context = browser.new_context(viewport={"width": 390, "height": 844},
                                  is_mobile=True, has_touch=True,
                                  reduced_motion="reduce")
    page = context.new_page()
    page.goto(base + "howtos.html?read=voice-service#voice-service")
    ready(page)
    assert page.evaluate("PawFlowEsper.state.section") == "voice-service"
    assert page.evaluate("PawFlowEsper.state.reduced")
    jump(page, "howtos.html")
    assert page.locator(".esper-portal").count() == 8
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    for target in ("station", "agents", "train", "servers", "vault", "resources", "workshop", "observatory"):
        jump(page, "howtos.html")
        portal = page.locator(f'.esper-portal[data-photo-target="{target}"]')
        portal.tap()
        settled(page)
        assert page.evaluate("PawFlowEsper.state.camera.scene") == target
    page.locator("#esper-index-open").tap()
    page.locator("#esper-search").fill("install-docker")
    page.locator("#esper-index-results a").tap()
    settled(page)
    assert page.evaluate("PawFlowEsper.state.section") == "install-docker"
    context.close()


def test_preview_history_and_long_content_scroll(browser_site):
    browser, base = browser_site
    context = browser.new_context(reduced_motion="reduce")
    context.add_init_script("window.ESPER_PREVIEW=true;")
    page = context.new_page()
    page.goto(base + "index.html")
    ready(page)
    previous_depth = page.evaluate("PawFlowEsper.state.camera.depth")
    for _ in range(6):
        page.locator("#esper-next").click()
        settled(page)
        depth = page.evaluate("PawFlowEsper.state.camera.depth")
        assert depth > previous_depth
        previous_depth = depth
    jump(page, "howtos.html", "install-docker")
    assert "/index.html?page=howtos.html#install-docker" in page.url
    page.reload()
    ready(page)
    assert page.evaluate("PawFlowEsper.state.section") == "install-docker"
    reader = page.locator("#esper-content")
    reader.evaluate("(el)=>{el.style.maxHeight='150px';el.scrollTop=0;}")
    before = page.evaluate("PawFlowEsper.state.current")
    reader.hover()
    page.mouse.wheel(0, 90)
    page.wait_for_function("document.querySelector('#esper-content').scrollTop>0")
    assert page.evaluate("PawFlowEsper.state.current") == before
    jump(page, "faq.html")
    page.go_back()
    settled(page)
    assert page.evaluate("PawFlowEsper.state.section") == "install-docker"
    context.close()
