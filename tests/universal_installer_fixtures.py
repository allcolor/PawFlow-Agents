from __future__ import annotations

from pathlib import Path

from pawflow_installer.models import InstallRequest


def request_payload(*, relay=False, target="local", source="published"):
    target_payload = {"kind": "local"}
    reachability = {"mode": "local", "hostname": None, "certificate_sha256": None}
    if target == "ssh":
        target_payload = {
            "kind": "ssh",
            "host": "pawflow.example",
            "port": 22,
            "user": "operator",
            "identity_file": None,
            "host_key_policy": "strict",
        }
        reachability = {
            "mode": "tailscale",
            "hostname": "pawflow.tailnet.ts.net",
            "certificate_sha256": None,
        }
    relay_payload = {
        "install": relay,
        "server_url": "https://pawflow.example:9443" if relay else None,
        "server_name": "prod" if relay else None,
        "workspace_name": "work" if relay else None,
        "capabilities": (
            ["filesystem.read", "filesystem.write", "shell.exec"] if relay else []
        ),
        "paths": [str(Path("/srv/work").absolute())] if relay else [],
        "autostart": False,
        "artifact_path": None,
        "artifact_sha256": None,
    }
    return {
        "version": 1,
        "target": target_payload,
        "install": {
            "pawflow_home": str(Path("/srv/pawflow").absolute()),
            "port": 9443,
            "version": "1.0.0-beta.247",
            "source": source,
            "native": False,
            "keep_old_images": False,
            "skip_apparmor": False,
        },
        "reachability": reachability,
        "relay_desktop": relay_payload,
    }


def install_request(**kwargs):
    return InstallRequest.model_validate(request_payload(**kwargs))
