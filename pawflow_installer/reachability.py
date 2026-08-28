"""Reachability planning, Tailscale parsing and HTTPS bootstrap probes."""

from __future__ import annotations

import hashlib
import http.client
import json
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from pawflow_installer.models import InstallRequest


class CertificateTrustRequired(RuntimeError):
    def __init__(self, fingerprint: str):
        super().__init__(
            f"HTTPS certificate is not trusted; confirm SHA-256 fingerprint {fingerprint}"
        )
        self.fingerprint = fingerprint


@dataclass(frozen=True)
class InstallApiStatus:
    url: str
    status: int
    payload: dict[str, Any]
    certificate_sha256: str


def parse_tailscale_status(payload: str | bytes | dict[str, Any]) -> dict[str, str]:
    data = json.loads(payload) if isinstance(payload, (str, bytes)) else payload
    self_node = data.get("Self") or {}
    dns_name = str(self_node.get("DNSName") or "").rstrip(".")
    addresses = self_node.get("TailscaleIPs") or []
    ip = str(addresses[0]) if addresses else ""
    if not dns_name and not ip:
        raise ValueError("Tailscale status does not contain a DNS name or IP")
    return {"dns_name": dns_name, "ip": ip}


def resolve_server_url(
    request: InstallRequest, tailscale_status: dict[str, str] | None = None
) -> str:
    reachability = request.reachability
    port = request.install.port
    if reachability.mode == "local":
        return f"https://localhost:{port}"
    if reachability.mode in {"existing_https", "public_manual"}:
        return str(reachability.hostname).rstrip("/")
    host = reachability.hostname
    if not host and tailscale_status:
        host = tailscale_status.get("dns_name") or tailscale_status.get("ip")
    if not host:
        raise ValueError("Tailscale reachability requires a confirmed DNS name or IP")
    parsed = urlparse(f"https://{host}")
    hostname = parsed.hostname or ""
    rendered = f"[{hostname}]" if ":" in hostname else hostname
    return f"https://{rendered}:{port}"


def wizard_url(server_url: str) -> str:
    parsed = urlparse(server_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("wizard URL requires an HTTPS server origin")
    return server_url.rstrip("/") + "/install"


def _certificate_fingerprint(connection: http.client.HTTPSConnection) -> str:
    sock = connection.sock
    if sock is None:
        raise RuntimeError("HTTPS connection has no TLS socket")
    certificate = sock.getpeercert(binary_form=True)
    if not certificate:
        raise RuntimeError("HTTPS peer did not provide a certificate")
    return hashlib.sha256(certificate).hexdigest()


def probe_install_api(
    server_url: str,
    *,
    accepted_certificate_sha256: str | None = None,
    confirm_certificate: Callable[[str], bool] | None = None,
    gateway_key: str | None = None,
) -> InstallApiStatus:
    parsed = urlparse(server_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("PawFlow bootstrap probe requires HTTPS")
    port = parsed.port or 443
    context = ssl.create_default_context()
    if accepted_certificate_sha256 or confirm_certificate:
        context = ssl._create_unverified_context()  # nosec B323 - explicit fingerprint pin below
    connection = http.client.HTTPSConnection(parsed.hostname, port, context=context)
    try:
        connection.connect()
        fingerprint = _certificate_fingerprint(connection)
        expected = (accepted_certificate_sha256 or "").lower()
        if expected and fingerprint.lower() != expected:
            raise ssl.SSLError(
                f"certificate fingerprint mismatch: expected {expected}, got {fingerprint}"
            )
        if not expected and confirm_certificate and not confirm_certificate(fingerprint):
            raise CertificateTrustRequired(fingerprint)
        headers = {"X-PawFlow-Gateway-Key": gateway_key} if gateway_key else {}
        connection.request("GET", "/install/api", headers=headers)
        response = connection.getresponse()
        raw = response.read()
    except ssl.SSLCertVerificationError:
        inspection = http.client.HTTPSConnection(
            parsed.hostname, port, context=ssl._create_unverified_context()  # nosec B323
        )
        try:
            inspection.connect()
            fingerprint = _certificate_fingerprint(inspection)
        finally:
            inspection.close()
        raise CertificateTrustRequired(fingerprint)
    finally:
        connection.close()
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    return InstallApiStatus(
        url=server_url.rstrip("/") + "/install/api",
        status=response.status,
        payload=payload,
        certificate_sha256=fingerprint,
    )
