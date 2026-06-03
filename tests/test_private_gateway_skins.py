from pathlib import Path

from services import private_gateway


class _GatewayHandler:
    def __init__(self, registry, path):
        import io

        self.command = "GET"
        self.path = path
        self.client_address = ("203.0.113.10", 12345)
        self.headers = {}
        self.server = type("Server", (), {"_route_registry": registry})()
        self.wfile = io.BytesIO()
        self.status = None
        self.sent_headers = []

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.sent_headers.append((name, value))

    def end_headers(self):
        pass


def test_private_gateway_bypasses_public_code_capability_route():
    from services.http_listener_service import RouteRegistry

    registry = RouteRegistry()
    registry.register(
        "GET", "/code/{session_id}/{token}/", "code",
        callback=lambda req: None, public=True)
    handler = _GatewayHandler(registry, "/code/sid/token/")

    handled = private_gateway._check_request_inner(
        handler, {"enabled": True, "secret_refs": "gateway_key"})

    assert handled is False
    assert handler.status is None


def test_private_gateway_still_challenges_plain_public_routes():
    from services.http_listener_service import RouteRegistry

    registry = RouteRegistry()
    registry.register(
        "GET", "/public", "public",
        callback=lambda req: None, public=True)
    handler = _GatewayHandler(registry, "/public")

    handled = private_gateway._check_request_inner(
        handler, {"enabled": True, "secret_refs": "gateway_key"})

    assert handled is True
    assert handler.status == 200
    assert b"<html" in handler.wfile.getvalue()


def test_bladerunner_private_gateway_skin_renders():
    html = private_gateway.render_challenge(
        error="Denied", cooldown=3, next_url="/chat?x=1&y=2",
        skin="bladerunner",
    ).decode("utf-8")

    assert "Blade Runner Gateway" in html
    assert "Private Gateway" in html
    assert "Voight-Kampff code" in html
    assert "Signal locked. Retry in " in html
    assert "Denied" in html
    assert "/chat?x=1&amp;y=2" in html


def test_private_gateway_skins_are_repository_resources():
    from core.private_gateway_skins import failure_redirect, list_skins, resolve_skin

    root = Path("data/repository/private_gateway_skin/global")
    expected = {
        "default", "google", "wifi", "terminal",
        "netflix", "captcha", "matrix", "bladerunner", "bing",
    }
    found = {p.name for p in root.iterdir() if p.is_dir()}
    assert found >= expected
    for name in expected:
        assert (root / name / "skin.json").is_file()
        assert (root / name / "template.html").is_file()

    refs = {skin["ref"] for skin in list_skins()}
    assert "global:bladerunner" in refs
    skin = resolve_skin("bladerunner")
    assert skin is not None
    assert skin["title"] == "Blade Runner"
    assert "Blade Runner Gateway" in skin["template"]
    assert failure_redirect("bing", "search this") == "https://www.bing.com/search?q=search%20this"


def test_private_gateway_skin_resource_store_uses_directory(tmp_path, monkeypatch):
    import core.paths as paths
    from core.repository import ScopedRepository
    from core.resource_store import ResourceStore

    monkeypatch.setattr(paths, "REPOSITORY_DIR", tmp_path / "repository")
    ScopedRepository.reset()
    ResourceStore.reset()

    store = ResourceStore.instance()
    created = store.create("private_gateway_skin", "neon_gate", "u1", {
        "title": "Neon Gate",
        "description": "test skin",
        "template": "<html>{{ next_url }} {{ error }} {{ cooldown }}</html>",
    })

    skin_dir = paths.repo_dir("private_gateway_skin", "user", "u1") / "neon_gate"
    assert skin_dir.is_dir()
    assert (skin_dir / "skin.json").is_file()
    assert (skin_dir / "template.html").is_file()
    assert not (paths.repo_dir("private_gateway_skin", "user", "u1") / "neon_gate.json").exists()
    assert created["template_length"] > 0
    assert store.get("private_gateway_skin", "neon_gate", "u1")["title"] == "Neon Gate"
