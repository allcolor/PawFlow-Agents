"""Certificate material for claude-code-interactive MITM sessions.

The interactive Claude Code provider runs a local TLS proxy inside its
container and maps ``api.anthropic.com`` to 127.0.0.1. PawFlow owns a
local CA on the host and gives each session only a leaf certificate/key.
The CA private key never leaves ``data/system``.
"""

from __future__ import annotations
import logging

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import ipaddress
import os
import stat

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import core.paths as _paths


CA_CERT = _paths.SYSTEM_DIR / "cc_interactive_ca.crt"
CA_KEY = _paths.SYSTEM_DIR / "cc_interactive_ca.key"


@dataclass(frozen=True)
class LeafCert:
    cert_path: Path
    key_path: Path
    ca_cert_path: Path


def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    tmp.replace(path)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def ensure_ca() -> tuple[Path, Path]:
    """Create or return PawFlow's local CA certificate and private key."""
    if CA_CERT.exists() and CA_KEY.exists():
        return CA_CERT, CA_KEY

    from datetime import timedelta

    _paths.SYSTEM_DIR.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "PawFlow Claude Code Interactive CA"),
    ])
    now = _utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                content_commitment=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    CA_CERT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    _write_private(
        CA_KEY,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ),
    )
    return CA_CERT, CA_KEY


def _load_ca():
    ca_cert_path, ca_key_path = ensure_ca()
    ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
    ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
    return ca_cert, ca_key


def generate_leaf(
    out_dir: str | Path,
    common_name: str = "api.anthropic.com",
    extra_dns: Iterable[str] = (),
) -> LeafCert:
    """Generate a per-session leaf certificate signed by the local CA."""
    from datetime import timedelta

    ca_cert, ca_key = _load_ca()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    names = [x509.DNSName(common_name)]
    for item in extra_dns:
        item = str(item).strip()
        if not item:
            continue
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(item)))
        except ValueError:
            names.append(x509.DNSName(item))
    now = _utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .issuer_name(ca_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    cert_path = out / "api-anthropic.crt"
    key_path = out / "api-anthropic.key"
    ca_copy_path = out / "pawflow-ca.crt"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    _write_private(
        key_path,
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ),
    )
    ca_copy_path.write_bytes(CA_CERT.read_bytes())
    return LeafCert(cert_path=cert_path, key_path=key_path, ca_cert_path=ca_copy_path)


def ca_private_key_is_host_only(paths: Iterable[str | Path]) -> bool:
    """Return False if a planned container mount includes the CA private key."""
    ca_key = CA_KEY.resolve()
    for item in paths:
        try:
            if Path(item).resolve() == ca_key:
                return False
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            continue
    return True
