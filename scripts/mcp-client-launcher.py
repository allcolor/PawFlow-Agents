#!/usr/bin/env python3
"""Launch the bundled PawFlow stdio bridge from a private connection profile."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launch a configured PawFlow MCP bridge")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--client-name", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    profile_path = Path(args.profile).expanduser().resolve()
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[pawflow-mcp] cannot read profile: {exc}", file=sys.stderr)
        return 2
    required = ("url", "api_key", "relay_dir")
    missing = [name for name in required if not profile.get(name)]
    if missing:
        print(
            f"[pawflow-mcp] profile is missing: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 2
    runtime_root = Path(__file__).resolve().parent / "runtime"
    sys.path.insert(0, str(runtime_root))
    os.environ["PAWFLOW_RELAY_RUNTIME_ROOT"] = str(runtime_root)
    os.environ["PAWFLOW_MCP_API_KEY"] = str(profile["api_key"])
    gateway_key = str(profile.get("gateway_key") or "")
    if gateway_key:
        os.environ["PAWFLOW_GATEWAY_KEY"] = gateway_key
    else:
        os.environ.pop("PAWFLOW_GATEWAY_KEY", None)
    from pawflow_relay.mcp_stdio import main as bridge_main

    bridge_args = [
        "--url", str(profile["url"]),
        "--relay-dir", str(profile["relay_dir"]),
        "--client-name", args.client_name,
    ]
    if profile.get("readonly"):
        bridge_args.append("--readonly")
    if profile.get("allow_exec"):
        bridge_args.append("--allow-exec")
    return bridge_main(bridge_args)


if __name__ == "__main__":
    raise SystemExit(main())
