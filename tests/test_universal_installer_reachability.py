import pytest

from pawflow_installer.reachability import (
    parse_tailscale_status,
    resolve_server_url,
    wizard_url,
)
from tests.universal_installer_fixtures import install_request


def test_tailscale_status_prefers_magic_dns_name():
    status = parse_tailscale_status({
        "Self": {
            "DNSName": "pawflow.tailnet.ts.net.",
            "TailscaleIPs": ["100.64.0.4"],
        }
    })
    assert status == {
        "dns_name": "pawflow.tailnet.ts.net",
        "ip": "100.64.0.4",
    }


def test_remote_tailscale_url_is_https_and_keeps_explicit_port():
    request = install_request(target="ssh")
    assert resolve_server_url(request) == "https://pawflow.tailnet.ts.net:9443"
    assert wizard_url(resolve_server_url(request)).endswith(":9443/install")


def test_missing_tailscale_identity_fails_instead_of_public_fallback():
    request = install_request(target="ssh")
    request.reachability.hostname = None
    with pytest.raises(ValueError, match="Tailscale"):
        resolve_server_url(request)


def test_wizard_url_rejects_plain_http():
    with pytest.raises(ValueError, match="HTTPS"):
        wizard_url("http://pawflow.example")
