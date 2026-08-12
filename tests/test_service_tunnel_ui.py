import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
UI = ROOT / "tasks" / "io" / "chat_ui"
REQUIRED_KEYS = {
    "serviceTunnels",
    "serviceTunnelsManage",
    "serviceTunnelsDescription",
    "serviceTunnelCreate",
    "serviceTunnelApprovedServices",
    "serviceTunnelAccessRelay",
    "serviceTunnelServiceRelay",
    "serviceTunnelService",
    "serviceTunnelLocalPort",
    "serviceTunnelName",
    "serviceTunnelPersistent",
    "serviceTunnelStart",
    "serviceTunnelStop",
    "serviceTunnelDelete",
    "serviceTunnelRefresh",
    "serviceTunnelNoTunnels",
    "serviceTunnelNoServices",
    "serviceTunnelNoEligibleRelays",
    "serviceTunnelLoopbackHelp",
    "serviceTunnelSelectConversation",
    "serviceTunnelConfirmDelete",
    "serviceTunnelCatalogAdd",
    "serviceTunnelTargetHost",
    "serviceTunnelTargetPort",
}


def test_service_tunnel_ui_module_is_loaded_and_reachable_from_relays():
    serve = (ROOT / "tasks" / "io" / "serve_chat_ui.py").read_text(
        encoding="utf-8")
    render = (UI / "resources_render.js").read_text(encoding="utf-8")
    module = UI / "service_tunnels.js"

    assert module.exists()
    assert '"service_tunnels.js"' in serve
    assert serve.index('"resources_render.js"') < serve.index('"service_tunnels.js"')
    assert "showServiceTunnelsDialog()" in render


def test_service_tunnel_ui_exposes_manager_actions_and_loopback_boundary():
    source = (UI / "service_tunnels.js").read_text(encoding="utf-8")

    for action in (
        "service_tunnels_list",
        "service_tunnel_catalog",
        "service_tunnel_catalog_save",
        "service_tunnel_catalog_delete",
        "service_tunnel_create",
        "service_tunnel_start",
        "service_tunnel_stop",
        "service_tunnel_status",
        "service_tunnel_delete",
    ):
        assert action in source
    assert "127.0.0.1" in source
    assert "bind_host" in source


def test_service_tunnel_i18n_keys_exist_in_every_catalog():
    catalogs = {}
    for language in ("en", "fr", "es"):
        catalogs[language] = json.loads(
            (UI / "i18n" / f"{language}.json").read_text(encoding="utf-8"))
        assert REQUIRED_KEYS <= set(catalogs[language])

    assert set(catalogs["en"]) == set(catalogs["fr"]) == set(catalogs["es"])
