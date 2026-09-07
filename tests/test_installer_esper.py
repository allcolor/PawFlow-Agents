"""Contracts for the real ESPER installer, with an isolated browser API."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERSION = ROOT / "data/repository/flows/global/default/pawflow_installer/versions"
ASSETS = VERSION / "assets"
FLOW = json.loads((VERSION / "1.0.0.json").read_text())
MEDIA = sorted(path.name for path in (ASSETS / "esper").iterdir() if path.is_file() and path.suffix != ".json")


@pytest.mark.parametrize("filename", MEDIA)
def test_packaged_routes_deliver_binary_assets(filename, monkeypatch):
    from core import FlowFile
    from tasks.system.generate_flowfile import GenerateFlowFileTask
    from tasks.io.handle_http_response import HandleHTTPResponseTask

    path = "/install/assets/" + filename
    routes = FLOW["tasks"]["http_in"]["parameters"]["routes"]
    route = next(item for item in routes if item["pattern"] == path)
    assert route["method"] == "GET" and route["public"]
    assert "*" not in route["pattern"] and "{" not in route["pattern"]
    edge = next(item for item in FLOW["relations"]
                if item["from"] == "http_in" and item["type"] == route["relationship"])
    definition = FLOW["tasks"][edge["to"]]
    assert definition["type"] == "generateFlowFile"
    task = GenerateFlowFileTask(definition["parameters"])
    task.set_flow_source_dir(str(VERSION))
    result = task.execute(FlowFile(attributes={"http.request.id": "installer-media-test"}))[0]
    assert result.get_content() == (ASSETS / "esper" / filename).read_bytes()
    outgoing = next(item for item in FLOW["relations"]
                    if item["from"] == edge["to"] and item["type"] == "success")
    response = HandleHTTPResponseTask(FLOW["tasks"][outgoing["to"]]["parameters"])
    sent = []

    def send(request_id, status, headers, body):
        sent.append((request_id, status, headers, body))
        return True

    sink = SimpleNamespace(submit_response=send, submit_stream_response=lambda a, b, c, d: send(a, b, c, b"".join(d)))
    monkeypatch.setattr(response, "get_service", lambda _: sink)
    response.execute(result)
    assert sent[0][0:2] == ("installer-media-test", 200)
    assert sent[0][3] == (ASSETS / "esper" / filename).read_bytes()
    expected = {".jpg": "image/jpeg", ".mp3": "audio/mpeg", ".css": "text/css", ".js": "application/javascript"}
    assert sent[0][2]["Content-Type"].startswith(expected[Path(filename).suffix])
    assert sent[0][2]["Cache-Control"] == "private, no-cache"
    assert sent[0][2]["X-Content-Type-Options"] == "nosniff"
    assert FLOW["services"]["http_listener"]["parameters"]["private_gateway_service_id"] == chr(36) + "{private_gateway_service_id}"


def test_persistent_template_refresh_includes_media(tmp_path, monkeypatch):
    import core._install_base as base

    target = tmp_path / "installer" / "versions" / "1.0.0.json"
    monkeypatch.setattr(base, "INSTALLER_TEMPLATE", target)
    monkeypatch.setattr(base, "DEFAULT_INSTALLER_FLOW_DIR", VERSION.parent)
    assert base._refresh_installer_template_from_default_data()
    for filename in MEDIA:
        assert (target.parent / "assets/esper" / filename).read_bytes() == (ASSETS / "esper" / filename).read_bytes()


def test_installer_scripts_parse_and_keep_flow_expression_boundaries():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for JavaScript syntax checks")
    html = (ASSETS / "install.html").read_text()
    assert chr(36) + "{" not in html and chr(96) not in html
    scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, re.S)
    scripts.append((ASSETS / "esper/installer-esper.js").read_text())
    for script in scripts:
        subprocess.run([node, "--check", "-"], input=script, text=True, capture_output=True, check=True)
    assert len(MEDIA) == 14


def route_installer(page, api):
    """Serve only packaged files and fake installation responses; no external network."""
    def handler(route):
        request = route.request
        path = urlparse(request.url).path
        if urlparse(request.url).hostname != "installer.invalid":
            route.abort()
            return
        if request.method == "POST":
            data = request.post_data_json
            api["posts"].append((path, data))
            if path == "/install/api/finalize":
                if api.get("hold"):
                    api["held"].append(route)
                    return
                route.fulfill(json={"install_complete": True})
            elif path.endswith("/prepare"):
                route.fulfill(json={"service_id": data["credential_service_id"], "message": "Prepared"})
            elif path.endswith("/paste"):
                route.fulfill(json={"service_id": data["credential_service_id"]})
            elif path.endswith("/service-parameter-helper"):
                route.fulfill(json={"values": [{"label": "Test model", "value": "test-model"}]})
            else:
                route.fulfill(json={"ok": True})
            return
        if path == "/install/api":
            route.fulfill(json={"install_complete": False, "private_gateway_skins": [{"name": "matrix", "title": "Matrix"}]})
        elif path == "/install":
            route.fulfill(body=(ASSETS / "install.html").read_bytes(), content_type="text/html")
        elif path == "/chat":
            route.fulfill(body="<h1>First conversation</h1>", content_type="text/html")
        elif path == "/login":
            route.fulfill(body="<h1>Isolated login desktop</h1>", content_type="text/html")
        elif path.startswith("/install/assets/") and path.rsplit("/", 1)[-1] in MEDIA:
            name = path.rsplit("/", 1)[-1]
            if api.get("missing_images") and name.endswith(".jpg"):
                route.abort()
                return
            content_type = {".jpg": "image/jpeg", ".mp3": "audio/mpeg", ".css": "text/css", ".js": "application/javascript"}[Path(name).suffix]
            route.fulfill(body=(ASSETS / "esper" / name).read_bytes(), content_type=content_type)
        else:
            route.fulfill(status=404, body="Not found")
    page.route("**/*", handler)


@pytest.fixture(scope="module")
def browser():
    playwright = pytest.importorskip("playwright.sync_api")
    binary = shutil.which("chromium") or shutil.which("chromium-browser")
    if not binary:
        pytest.skip("Installed Chromium is required for browser contracts")
    with playwright.sync_playwright() as engine:
        instance = engine.chromium.launch(headless=True, executable_path=binary, args=["--no-sandbox"])
        yield instance
        instance.close()


@pytest.fixture
def wizard(browser):
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
    page = context.new_page()
    api = {"posts": [], "held": []}
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    route_installer(page, api)
    page.goto("https://installer.invalid/install")
    page.wait_for_function("window.InstallerEsper && InstallerEsper.state.ready")
    page.wait_for_function("document.querySelector('#state').textContent.includes('private_gateway_skins')")
    yield page, api
    assert not errors
    context.close()


def at_step(page, index):
    page.wait_for_function("(index) => InstallerEsper.state.step === index && !InstallerEsper.state.busy", arg=index)
    assert page.locator(".screen.active").get_attribute("data-step") == str(index)


def next_step(page, index):
    page.locator("#next").click()
    at_step(page, index)


def admin(page):
    page.locator('[name="admin_username"]').fill("captain")
    page.locator('[name="admin_password"]').fill("Installer-Test!42")
    page.locator('[name="admin_password_confirm"]').fill("Installer-Test!42")


def configure_to_review(page):
    admin(page)
    next_step(page, 1)
    next_step(page, 2)
    page.locator('[name="new_gateway_key"]').fill("Installer-Gateway!42")
    next_step(page, 3)
    page.locator('[data-llm-provider="0"]').select_option("openai")
    page.locator('[data-llm-param="api_key"]').fill("test-api-key")
    page.locator('[data-llm-param="default_model"]').fill("test-model")
    next_step(page, 4)
    assert page.get_by_text(
        "Relay image profiles are prepared by the installer before this bootstrap UI opens.",
        exact=True,
    ).is_visible()
    page.locator("#relay_server_enabled").check()
    page.locator("#relay_server_id").fill("lab_relay")
    next_step(page, 5)
    page.locator("#voice_tts_enabled").check()
    page.locator("#voice_stt_enabled").check()
    next_step(page, 6)
    page.locator("#first_conversation_title").fill("My first mission")
    page.locator("#first_conversation_relay").select_option("lab_relay")
    next_step(page, 7)


def test_real_validation_navigation_and_finalize_payload(wizard):
    page, api = wizard
    next_step(page, 0)
    assert page.locator("#error").inner_text()
    assert page.locator('[data-step-target="7"]').is_disabled()
    admin(page)
    # Enter in an early form advances; it never sends finalization.
    page.locator('[name="admin_password_confirm"]').press("Enter")
    at_step(page, 1)
    assert not api["posts"]
    page.locator('[data-step-target="0"]').click()
    at_step(page, 0)
    configure_to_review(page)
    page.locator('[data-step-target="3"]').click()
    at_step(page, 3)
    assert page.locator('[data-llm-param="api_key"]').input_value() == "test-api-key"
    page.locator('[data-step-target="7"]').click()
    at_step(page, 7)
    page.locator("#finalize").click()
    page.wait_for_url("**/chat")
    submissions = [body for path, body in api["posts"] if path.endswith("/finalize")]
    assert len(submissions) == 1
    payload = submissions[0]
    assert payload["admin_username"] == "captain"
    assert payload["admin_password"] == payload["admin_password_confirm"] == "Installer-Test!42"
    assert payload["new_gateway_key"] == "Installer-Gateway!42"
    llms = json.loads(payload["llm_services"])
    assert llms[0]["config"]["api_key"] == "test-api-key"
    assert llms[0]["config"]["default_model"] == "test-model"
    assert json.loads(payload["relay_server"])["service_id"] == "lab_relay"
    assert json.loads(payload["voice_services"])["tts"]["enabled"]
    first = json.loads(payload["first_conversation"])
    assert first["title"] == "My first mission" and first["relay_id"] == "lab_relay"
    assert first["agents"][0]["llm_service"] == llms[0]["service_id"]


def test_finalize_is_explicit_single_flight_and_retryable(wizard):
    page, api = wizard
    configure_to_review(page)
    page.mouse.move(100, 450)
    page.mouse.wheel(0, 200)
    at_step(page, 7)
    assert not api["posts"]
    api["hold"] = True
    page.locator("#finalize").click()
    page.wait_for_function("InstallerEsper.state.locked")
    page.evaluate("document.querySelector('#wizard').requestSubmit()")
    assert len(api["held"]) == 1
    assert page.locator('[data-step-target="0"]').is_disabled()
    api["held"].pop().fulfill(status=500, json={"error": "Simulated deployment failure"})
    page.wait_for_function("!InstallerEsper.state.locked")
    assert "Simulated deployment failure" in page.locator("#error").inner_text()
    page.locator('[data-step-target="0"]').click()
    at_step(page, 0)
    assert page.locator('[name="admin_password"]').input_value() == "Installer-Test!42"
    page.locator('[name="admin_password_confirm"]').fill("Different-Test!42")
    page.locator('[data-step-target="7"]').click()
    at_step(page, 0)
    assert "match" in page.locator("#error").inner_text()
    assert page.locator('[data-step-target="7"]').is_disabled()
    page.locator('[name="admin_password_confirm"]').fill("Installer-Test!42")
    for index in range(1, 8):
        next_step(page, index)
    api["hold"] = False
    page.locator("#finalize").click()
    page.wait_for_url("**/chat")
    assert len([path for path, _ in api["posts"] if path.endswith("/finalize")]) == 2


def test_animated_photos_sound_and_login_do_not_reset_form(wizard):
    page, _ = wizard
    admin(page)
    page.locator("#esper-motion").click()
    page.locator("#esper-portal").click()
    page.wait_for_function("InstallerEsper.state.busy && InstallerEsper.state.depth > .1 && InstallerEsper.state.depth < .9")
    assert page.locator("#wizard").evaluate("(el)=>el.inert")
    at_step(page, 1)
    page.wait_for_function("InstallerEsper.state.musicTime > .1")
    first_time = page.evaluate("InstallerEsper.state.musicTime")
    page.locator("#back").click()
    at_step(page, 0)
    assert page.evaluate("InstallerEsper.state.depth") == 0
    assert page.evaluate("InstallerEsper.state.musicTime") > first_time
    assert page.locator('[name="admin_username"]').input_value() == "captain"
    page.evaluate("showVncDialog('/login')")
    page.wait_for_function("InstallerEsper.state.musicPaused")
    page.keyboard.press("PageDown")
    at_step(page, 0)
    page.locator("#vnc_close").click()
    page.wait_for_function("!InstallerEsper.state.musicPaused")
    page.locator("#esper-sound").click()
    assert page.evaluate("InstallerEsper.state.musicPaused")
    stored = page.evaluate("Object.fromEntries(Object.entries(localStorage))")
    assert all(key.startswith("pawflow-install-") for key in stored)
    assert "Installer-Test!42" not in json.dumps(stored)


@pytest.mark.parametrize("missing_images", [False, True])
def test_mobile_form_scroll_and_missing_images_are_usable(browser, missing_images):
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, reduced_motion="reduce")
    page = context.new_page()
    api = {"posts": [], "held": [], "missing_images": missing_images}
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    route_installer(page, api)
    page.goto("https://installer.invalid/install")
    if missing_images:
        page.wait_for_function("document.querySelector('#esper-media-note').textContent.length > 0")
    else:
        page.wait_for_function("window.InstallerEsper && InstallerEsper.state.ready")
    assert page.evaluate("InstallerEsper.state.ready") is not missing_images
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    admin(page)
    if missing_images:
        next_step(page, 1)
    else:
        page.locator("#esper-portal").tap()
        at_step(page, 1)
    next_step(page, 2)
    page.locator('[name="new_gateway_key"]').fill("Installer-Gateway!42")
    next_step(page, 3)
    screen = page.locator(".screen.active")
    screen.evaluate("(el)=>{el.scrollTop=0;}")
    screen.hover()
    page.mouse.wheel(0, 200)
    page.wait_for_function("document.querySelector('.screen.active').scrollTop > 0")
    at_step(page, 3)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors
    context.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_oauth_credentials_and_parameter_helper_keep_working(wizard, width):
    page, api = wizard
    page.set_viewport_size({"width": width, "height": 1000})
    admin(page)
    next_step(page, 1)
    page.locator("#add_oauth_provider").click()
    page.locator('[data-oauth-provider="0"]').select_option("github")
    next_step(page, 1)
    assert "requires" in page.locator("#error").inner_text()
    page.locator('[data-oauth-field="oauth_github_client_id"]').fill("fixture-client")
    page.locator('[data-oauth-field="oauth_github_client_secret"]').fill("fixture-secret")
    next_step(page, 2)
    page.locator('[name="new_gateway_key"]').fill("Installer-Gateway!42")
    next_step(page, 3)
    page.locator('[data-help-key="default_model"]').click()
    assert page.locator(".helper-pop .helper-body").inner_text()
    page.locator("[data-helper-close]").click()
    page.locator(".param-fill").first.click()
    page.locator(".helper-pop [data-helper-apply]").wait_for()
    assert page.locator(".helper-pop").evaluate("(el)=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight;}")
    page.locator(".helper-pop [data-helper-apply]").click()
    assert page.locator('[data-llm-param="default_model"]').input_value() == "test-model"
    page.locator('[data-cred-add="0"]').click()
    page.wait_for_function("document.querySelector('#credential_help').textContent.includes('Prepared')")
    page.locator('[data-cred-paste="0"]').click()
    credentials = json.dumps({"access_token": "fixture-access", "refresh_token": "fixture-refresh"})
    page.locator("#credential_json").fill(credentials)
    page.locator("#save_credentials").click()
    page.wait_for_function("document.querySelector('#credential_help').textContent.includes('Credentials saved')")
    pasted = [body for path, body in api["posts"] if path.endswith("/paste")]
    assert pasted[0]["credentials"] == credentials
    next_step(page, 4)
    page.locator('[data-step-target="1"]').click()
    at_step(page, 1)
    assert page.locator('[data-oauth-field="oauth_github_client_secret"]').input_value() == "fixture-secret"
