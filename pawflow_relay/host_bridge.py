"""Tracked WSL-to-Windows TCP bridge for the relay host helper."""

import argparse
import ipaddress
import os
import select
import socket
import struct
import sys
import threading
from pathlib import Path

from pawflow_relay.auth import probe_host_helper


def _default_gateway():
    """Return the Linux default gateway from /proc/net/route."""
    try:
        for line in Path("/proc/net/route").read_text(
                encoding="utf-8").splitlines()[1:]:
            fields = line.split()
            if len(fields) >= 3 and fields[1] == "00000000":
                return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
    except (OSError, ValueError):
        pass
    return ""


def _nameserver():
    """Return the first IPv4 resolver address visible from WSL."""
    try:
        for line in Path("/etc/resolv.conf").read_text(
                encoding="utf-8").splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[0] == "nameserver":
                ipaddress.IPv4Address(fields[1])
                return fields[1]
    except (OSError, ValueError):
        pass
    return ""


def target_candidates(extra_target=""):
    """Return ordered, unique Windows routes for mirrored and NAT WSL."""
    candidates = [
        "127.0.0.1",
        _default_gateway(),
        _nameserver(),
        extra_target,
    ]
    result = []
    for candidate in candidates:
        if candidate and candidate not in result:
            result.append(candidate)
    return result


def select_target(target_port, token, extra_target="", timeout=2):
    """Select the first address answering an authenticated helper ping."""
    failures = []
    for host in target_candidates(extra_target):
        endpoint = f"{host}:{target_port}"
        try:
            probe_host_helper(endpoint, token, timeout=timeout)
            return host
        except (OSError, RuntimeError, ValueError) as exc:
            failures.append(f"{host}={exc}")
    raise RuntimeError(
        "No authenticated Windows host-helper route is reachable: "
        + "; ".join(failures))


def _relay_connection(client, target_port, token, extra_target=""):
    """Forward one connection through the currently reachable Windows route.

    WSL's host-facing address can change while the relay stays running (for
    example after a VPN or network transition).  Resolve it here rather than
    once in ``serve`` so the next request heals without restarting either
    container.
    """
    upstream = None
    try:
        target_host = select_target(target_port, token, extra_target)
        upstream = socket.create_connection((target_host, target_port), timeout=10)
        sockets = (client, upstream)
        while True:
            readable, _, exceptional = select.select(sockets, (), sockets, 30)
            if exceptional:
                break
            if not readable:
                continue
            for source in readable:
                data = source.recv(65536)
                if not data:
                    return
                destination = upstream if source is client else client
                destination.sendall(data)
    except (OSError, RuntimeError, ValueError):
        return
    finally:
        client.close()
        if upstream is not None:
            upstream.close()


def serve(listen_port, target_port, token, extra_target="",
          stop_event=None, ready_event=None):
    """Serve the raw bridge until stopped."""
    if not token:
        raise ValueError("PAWFLOW_HOST_HELPER_TOKEN is required")
    stop_event = stop_event or threading.Event()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", listen_port))  # nosec B104 - WSL bridge listener.
        server.listen(16)
        server.settimeout(1)
        if ready_event is not None:
            ready_event.set()
        sys.stderr.write(
            f"[HostBridge] listening on {listen_port}; "
            f"Windows helper route is resolved per connection "
            f"(port {target_port})\n")
        while not stop_event.is_set():
            try:
                client, _address = server.accept()
            except TimeoutError:
                continue
            threading.Thread(
                target=_relay_connection,
                args=(client, target_port, token, extra_target),
                daemon=True,
                name="host-bridge-connection",
            ).start()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-port", required=True, type=int)
    parser.add_argument("--target-port", required=True, type=int)
    parser.add_argument("--exit-on-stdin-eof", action="store_true")
    args = parser.parse_args(argv)
    stop_event = threading.Event()
    if args.exit_on_stdin_eof:
        def _stop_on_stdin_eof():
            try:
                # Raw fd read: sys.stdin.buffer holds the BufferedReader lock,
                # and a daemon thread blocked in it makes interpreter shutdown
                # die with "Fatal Python error: _enter_buffered_busy".
                while os.read(0, 4096):
                    pass
            except OSError:
                pass
            finally:
                stop_event.set()

        threading.Thread(
            target=_stop_on_stdin_eof, daemon=True,
            name="host-bridge-parent-watch").start()
    serve(
        args.listen_port,
        args.target_port,
        os.environ.get("PAWFLOW_HOST_HELPER_TOKEN", ""),
        os.environ.get("PAWFLOW_WINDOWS_HOST_IP", ""),
        stop_event=stop_event,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
