"""Standalone entry point: python -m pawflow_relay.

The default interface is the relay client manager:

    python -m pawflow_relay server add local https://pawflow.example:9090
    python -m pawflow_relay workspace add repo --server local --path .
    python -m pawflow_relay start repo

The legacy direct mode is still available with --server/--dir.
"""

import argparse
import os
import signal
import sys


_MANAGER_COMMANDS = {"server", "workspace", "start", "status", "cleanup"}


def _first_command(argv):
    for arg in argv:
        if arg == "--json":
            continue
        if arg.startswith("-"):
            return ""
        return arg
    return ""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if _first_command(argv) in _MANAGER_COMMANDS:
        from pawflow_relay.manager_cli import main as manager_main
        return manager_main(argv)

    parser = argparse.ArgumentParser(
        prog="pawflow-relay",
        description="PawFlow Relay — connect a directory to a PawFlow server")
    parser.add_argument("--server", required=True,
                        help="PawFlow server URL (e.g. https://pawflow.allcolor.org:9090)")
    parser.add_argument("--dir", default=".",
                        help="Directory to share (default: current dir)")
    parser.add_argument("--docker-image", default="",
                        help="Docker image for sandboxed relay (e.g. pawflow-relay-dev:latest)")
    parser.add_argument("--docker-cpus", default="",
                        help="CPU limit for Docker relay (default: 2)")
    parser.add_argument("--docker-memory", default="",
                        help="Memory limit for Docker relay (default: 4g)")
    parser.add_argument("--allow-local", action="store_true", default=False,
                        help="Allow tools to execute on the host machine (not just Docker)")
    parser.add_argument("--gateway-key", default=os.environ.get("PAWFLOW_GATEWAY_KEY", ""),
                        help="Gateway API key for private gateways")
    parser.add_argument("--token", default="",
                        help="Session token (skip login)")
    parser.add_argument("--username", default="",
                        help="Username (required if --token is provided)")
    args = parser.parse_args(argv)

    # Acquire gateway cookie if key provided
    gateway_cookie = ""
    if args.gateway_key:
        try:
            from pawflow_relay.utils import api_call
            import http.client
            from urllib.parse import urlparse
            parsed = urlparse(args.server)
            use_ssl = parsed.scheme == "https"
            host = parsed.hostname or "localhost"
            port = parsed.port or (443 if use_ssl else 80)
            if use_ssl:
                import ssl
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=10)
            else:
                conn = http.client.HTTPConnection(host, port, timeout=10)
            conn.request("POST", "/auth/gateway",
                         body=args.gateway_key.encode("utf-8"),
                         headers={"Content-Type": "text/plain"})
            resp = conn.getresponse()
            resp.read()
            cookie_header = resp.getheader("Set-Cookie", "")
            for part in cookie_header.split(";"):
                part = part.strip()
                if part.startswith("_pf_gw="):
                    gateway_cookie = part.split("=", 1)[1]
                    break
            conn.close()
            if gateway_cookie:
                print("[Relay] Gateway cookie acquired.", file=sys.stderr)
            else:
                print("[Relay] Warning: gateway POST returned no cookie.", file=sys.stderr)
        except Exception as e:
            print(f"[Relay] Gateway auth failed: {e}", file=sys.stderr)

    # Authenticate
    session_token = args.token
    username = args.username

    if not session_token:
        # Browser-based OAuth login
        try:
            from pawflow_cli.auth import authenticate
            auth = authenticate(args.server)
            session_token = auth["token"]
            username = auth["username"]
        except ImportError:
            print("Error: --token and --username required (pawflow_cli not available for browser auth)",
                  file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Authentication failed: {e}", file=sys.stderr)
            sys.exit(1)

    if not session_token or not username:
        print("Error: no session token. Use --token/--username or install pawflow_cli for browser auth.",
              file=sys.stderr)
        sys.exit(1)

    print(f"[Relay] Authenticated as {username}", file=sys.stderr)
    print(f"[Relay] Directory: {os.path.abspath(args.dir)}", file=sys.stderr)

    from pawflow_relay.thread import RelayThread

    relay = RelayThread(
        server_url=args.server,
        session_token=session_token,
        username=username,
        directory=args.dir,
        docker_image=args.docker_image,
        gateway_cookie=gateway_cookie,
        docker_cpus=args.docker_cpus,
        docker_memory=args.docker_memory,
        allow_local=args.allow_local,
    )

    def _signal_handler(sig, frame):
        print("\n[Relay] Stopping...", file=sys.stderr)
        relay.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)

    try:
        relay.start()
        print(f"[Relay] Connected as {relay.relay_id}", file=sys.stderr)
        relay.wait()
    except KeyboardInterrupt:
        relay.stop()
    except Exception as e:
        print(f"[Relay] Error: {e}", file=sys.stderr)
        relay.stop()
        sys.exit(1)


if __name__ == "__main__":
    raise SystemExit(main() or 0)
