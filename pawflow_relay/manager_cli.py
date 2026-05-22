"""Command line interface for the standalone PawFlow Relay client."""

from __future__ import annotations

import argparse
import sys

from pawflow_relay.manager import (
    add_server,
    add_workspace,
    delete_server,
    delete_workspace,
    list_servers,
    list_workspaces,
    start_workspace,
    stop_workspace_runtime,
    update_server_auth,
)
from pawflow_relay.register import acquire_gateway_cookie


def _print_server(profile: dict) -> None:
    auth = "logged-in" if profile.get("session_token") else "not-logged-in"
    gateway = "gateway" if profile.get("gateway_key") else "no-gateway"
    user = profile.get("username") or "-"
    print(f"{profile['name']}\t{profile['url']}\t{auth}\t{gateway}\tuser={user}")


def _print_workspace(share: dict) -> None:
    image = share.get("docker_image") or "default-image"
    local = "local" if share.get("allow_local") else "docker"
    exec_mode = "exec" if share.get("allow_exec", True) else "no-exec"
    desktop = "desktop" if share.get("allow_remote_desktop", True) else "no-desktop"
    print(
        f"{share['name']}\tserver={share['server']}\tmode={share.get('mode', 'rw')}\t"
        f"relay={share.get('relay_id', '-')}\t{local}\t{exec_mode}\t{desktop}\timage={image}\t{share['path']}"
    )


def _print_result(args, value: dict | list) -> None:
    if getattr(args, "json", False):
        import json
        print(json.dumps(value))
        return
    if isinstance(value, dict) and "servers" in value and "workspaces" in value:
        print(f"servers={len(value.get('servers') or [])} workspaces={len(value.get('workspaces') or [])}")
        return
    if isinstance(value, list):
        for item in value:
            if "url" in item:
                _print_server(item)
            else:
                _print_workspace(item)
        return
    if isinstance(value, dict) and "url" in value:
        _print_server(value)
        return
    if isinstance(value, dict) and "path" in value:
        _print_workspace(value)
        return
    print(value)


def _login_server(name: str) -> dict:
    from pawflow_cli.auth import authenticate
    from pawflow_relay.manager import get_server

    profile = get_server(name)
    gateway_cookie = profile.get("gateway_cookie", "")
    gateway_key = profile.get("gateway_key", "")
    if gateway_key and not gateway_cookie:
        gateway_cookie = acquire_gateway_cookie(profile["url"], gateway_key)
    auth = authenticate(profile["url"], force=True, gateway_cookie=gateway_cookie)
    return update_server_auth(
        name,
        gateway_cookie=gateway_cookie,
        session_token=auth["token"],
        username=auth["username"],
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pawflow-relay",
        description="Manage standalone PawFlow Relay client servers and workspace shares.",
    )
    parser.add_argument("--json", action="store_true",
                        help="Emit machine-readable JSON for desktop integrations")
    sub = parser.add_subparsers(dest="command", required=True)

    server = sub.add_parser("server", help="Manage PawFlow servers")
    server_sub = server.add_subparsers(dest="server_command", required=True)
    server_add = server_sub.add_parser("add", help="Add or update a PawFlow server")
    server_add.add_argument("name")
    server_add.add_argument("url")
    server_add.add_argument("--gateway-key", default="")
    server_login = server_sub.add_parser("login", help="Login to a configured server")
    server_login.add_argument("name")
    server_delete = server_sub.add_parser("delete", help="Delete a configured server")
    server_delete.add_argument("name")
    server_sub.add_parser("list", help="List configured servers")

    workspace = sub.add_parser("workspace", help="Manage local workspace shares")
    workspace_sub = workspace.add_subparsers(dest="workspace_command", required=True)
    workspace_add = workspace_sub.add_parser("add", help="Add or update a workspace share")
    workspace_add.add_argument("name")
    workspace_add.add_argument("--server", required=True)
    workspace_add.add_argument("--path", required=True)
    workspace_add.add_argument("--mode", choices=["rw", "ro"], default="rw")
    workspace_add.add_argument("--docker-image", default="")
    workspace_add.add_argument("--no-exec", action="store_true",
                               help="Disable command execution in the relay container")
    workspace_add.add_argument("--no-remote-desktop", action="store_true",
                               help="Disable Docker desktop/VNC/audio support")
    workspace_add.add_argument("--allow-local", action="store_true")
    workspace_delete = workspace_sub.add_parser("delete", help="Delete a workspace share")
    workspace_delete.add_argument("name")
    workspace_sub.add_parser("list", help="List configured workspace shares")

    start = sub.add_parser("start", help="Start a configured workspace relay")
    start.add_argument("workspace")

    sub.add_parser("status", help="Show local relay client configuration status")
    cleanup = sub.add_parser("cleanup", help="Cleanup a configured workspace relay runtime")
    cleanup.add_argument("workspace")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "server":
            if args.server_command == "add":
                _print_result(args, add_server(args.name, args.url, args.gateway_key))
                return 0
            if args.server_command == "login":
                _print_result(args, _login_server(args.name))
                return 0
            if args.server_command == "delete":
                _print_result(args, delete_server(args.name))
                return 0
            if args.server_command == "list":
                _print_result(args, list_servers())
                return 0

        if args.command == "workspace":
            if args.workspace_command == "add":
                _print_result(args, add_workspace(
                    args.name,
                    args.server,
                    args.path,
                    mode=args.mode,
                    docker_image=args.docker_image,
                    allow_local=args.allow_local,
                    allow_exec=not args.no_exec,
                    allow_remote_desktop=not args.no_remote_desktop,
                ))
                return 0
            if args.workspace_command == "delete":
                _print_result(args, delete_workspace(args.name))
                return 0
            if args.workspace_command == "list":
                _print_result(args, list_workspaces())
                return 0

        if args.command == "start":
            start_workspace(args.workspace)
            return 0

        if args.command == "status":
            _print_result(args, {"servers": list_servers(), "workspaces": list_workspaces()})
            return 0

        if args.command == "cleanup":
            _print_result(args, stop_workspace_runtime(args.workspace))
            return 0

    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
