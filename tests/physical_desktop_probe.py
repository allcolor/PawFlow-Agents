"""Disposable desktop/VNC and real Chromium profile acceptance for grouped relays."""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import os
import queue
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

if __package__:
    from tests import physical_audio_probe as audio
    from tests import physical_relay_probe as relay
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import physical_audio_probe as audio
    import physical_relay_probe as relay

kernel = relay.kernel
PROBE = "/opt/pawflow/physical_desktop_probe.py"


def exports(endpoint):
    """Desktop acceptance uses two writable shares; stage2 covers readonly mode."""
    result = relay.exports(endpoint)
    for export in result:
        export["mode"] = "rw"
        export["command"] = [arg for arg in export["command"] if arg != "--readonly"]
        export["command"].append("--allow-automation")
    return result


def validate_browser_state(state, name, round_number):
    kernel.check(set(state) == {"storage", "cookie"}, "Missing browser profile evidence")
    expected = None if round_number == 1 else name
    kernel.check(state.get("storage") == expected and state.get("cookie") == expected,
                 "Browser profile was lost or crossed logical identities: " + str(state))


class PageHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = ("<!doctype html><html><head><title>PawFlow acceptance</title></head>"
                "<body style='background:#18354d;color:white;font:32px sans-serif'>"
                "<h1>PawFlow " + self.server.relay_name + "</h1>"
                "<p>Private persistent browser profile</p></body></html>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def browser_probe(name, round_number):
    kernel.require_disposable()
    from playwright.sync_api import sync_playwright

    # Identical origin in both isolated network namespaces and after restart.
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 18080), PageHandler)
    server.relay_name = name
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    profile = Path("/home/pawflow/.chromium-profile")
    origin = "http://127.0.0.1:18080"
    try:
        with (
            sync_playwright() as playwright,
            playwright.chromium.launch_persistent_context(
                str(profile), executable_path="/usr/local/bin/chromium",
                headless=False, viewport={"width": 800, "height": 600},
                args=["--disable-dev-shm-usage", "--password-store=basic", "--no-first-run"],
            ) as context,
        ):
            page = context.new_page()
            page.goto(origin, wait_until="load", timeout=15000)
            state = {
                "storage": page.evaluate("localStorage.getItem('physical_relay')"),
                "cookie": next((item["value"] for item in context.cookies(origin)
                                if item["name"] == "physical_relay"), None),
            }
            validate_browser_state(state, name, round_number)
            page.evaluate("value => localStorage.setItem('physical_relay', value)", arg=name)
            context.add_cookies([{"name": "physical_relay", "value": name,
                                  "url": origin, "expires": time.time() + 3600}])
            # Navigation and script completion do not establish a painted surface.
            page.bring_to_front()
            page.wait_for_function(
                "document.visibilityState === 'visible' && "
                "performance.getEntriesByName('first-contentful-paint').length > 0",
                timeout=5000,
            )
            image = page.screenshot()
            result = {"name": name, "round": round_number, "previous_state": state,
                      "profile": str(profile),
                      "image": base64.b64encode(image).decode()}
        kernel.check((profile / "Default/Preferences").is_file(),
                     "Chromium did not save its actual profile")
        return result
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def png_bytes(result):
    image = base64.b64decode(result["image"], validate=True)
    kernel.check(image[:8] == b"\x89PNG\r\n\x1a\n" and len(image) > 24,
                 "Screenshot is not a PNG")
    kernel.check(struct.unpack("!II", image[16:24]) == (800, 600),
                 "Screenshot has unexpected dimensions")
    return image


def vnc_bytes(peer, session_id, minimum):
    data = bytearray()
    deadline = time.monotonic() + 10
    while len(data) < minimum:
        try:
            event = peer.events.get(timeout=max(0.001, deadline - time.monotonic()))
        except queue.Empty as error:
            raise RuntimeError("Missing VNC data for " + peer.name) from error
        kernel.check(event.get("session_id") == session_id, "VNC session crossed identities")
        kernel.check(event.get("type") == "desktop_ws_data" and event.get("opcode") == 2,
                     "VNC tunnel closed before expected data")
        data.extend(base64.b64decode(event["data"], validate=True))
    return bytes(data)


def exercise(peers, round_number):
    evidence = {}
    for name, peer in peers.items():
        desktop = peer.command("start_desktop", resolution="800x600", display=99,
                               vnc_port=5900, novnc_port=6080)
        kernel.check(bool(desktop.get("session_id")), "Desktop failed: " + str(desktop))
        kernel.check(desktop.get("display") == ":99" and desktop.get("novnc_port") == 6080,
                     "Desktop did not use the expected private display and port")
        session_id = str(uuid.uuid4())
        opened = peer.command("desktop_ws_open", session_id=session_id, port=6080)
        kernel.check(opened.get("ok") is True, "VNC bridge failed: " + str(opened))
        banner = vnc_bytes(peer, session_id, 12)
        kernel.check(banner == b"RFB 003.008\n", "Unexpected VNC protocol banner")
        sent = peer.command("desktop_ws_send", session_id=session_id,
                            data=base64.b64encode(banner).decode())
        kernel.check(sent.get("ok") is True, "VNC bridge send failed")
        security = vnc_bytes(peer, session_id, 2)
        kernel.check(security[0] > 0 and 1 in security[1:],
                     "VNC did not answer the protocol negotiation")
        peer.command("desktop_ws_close", session_id=session_id)
        screenshot = peer.command("screen_screenshot")
        image = png_bytes(screenshot)
        (kernel.OUTPUT / (name + "-desktop-" + str(round_number) + ".png")).write_bytes(image)
        browser = peer.command(
            "exec", path="/workspace", timeout=25,
            argv=[sys.executable, "-I", PROBE, "--browser-name", name,
                  "--round", str(round_number)])
        kernel.check(browser.get("returncode") == 0, "Chromium failed: " + str(browser))
        report = json.loads(browser["stdout"])
        image = png_bytes(report)
        (kernel.OUTPUT / (name + "-browser-" + str(round_number) + ".png")).write_bytes(image)
        report.pop("image")
        status = peer.command("desktop_status")
        kernel.check(status.get("running") is True
                     and status.get("session_id") == desktop["session_id"],
                     "Desktop died while running Chromium")
        evidence[name] = {"desktop": desktop, "vnc_banner": banner.decode(),
                          "browser": report, "browser_png_sha256": hashlib.sha256(image).hexdigest()}
    kernel.check(evidence["alpha"]["desktop"]["session_id"]
                 != evidence["beta"]["desktop"]["session_id"], "Desktop identity collision")
    audio_reports = audio.exercise(peers, evidence, round_number)
    for name, report in audio_reports.items():
        evidence[name]["audio"] = report
    return evidence


def run_acceptance():
    kernel.require_disposable()
    kernel.check(os.geteuid() == 0, "Desktop acceptance must start as root")
    kernel.check(not kernel.STAGING.exists(), "Acceptance staging must be fresh")
    kernel.OUTPUT.mkdir(exist_ok=True)
    for name in ("alpha", "beta"):
        root = kernel.source(name)
        (root / "workspace").mkdir(parents=True)
        (root / "workspace/sentinel").write_text(name)
        (root / "home").mkdir()
    address = socket.gethostbyname(socket.gethostname())
    server = relay.FixtureServer((address, 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    completed = []
    try:
        for round_number in (1, 2):
            peers = {}
            with (kernel.OUTPUT / ("desktop-runtime-" + str(round_number) + ".log")).open("wb") as log:
                process = subprocess.Popen([sys.executable, "-I", kernel.RUNTIME],
                                           stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT)
                try:
                    endpoint = "ws://" + address + ":" + str(server.server_address[1])
                    process.stdin.write(json.dumps({"exports": exports(endpoint)}).encode())
                    process.stdin.close()
                    peers = server.wait_peers(round_number, process)
                    evidence = exercise(peers, round_number)
                    owned = kernel.descendants(process.pid, kernel.process_table())
                    kernel.check(len(owned) >= 17, "Expected desktop descendants were not observed")
                    process.send_signal(signal.SIGTERM)
                    kernel.check(process.wait(timeout=30) == 0, "Physical desktop stop failed")
                    kernel.wait_cleanup(owned)
                    completed.append({"round": round_number, "workers": evidence,
                                      "owned_processes": owned})
                finally:
                    if process.poll() is None:
                        for name, peer in peers.items():
                            try:
                                diagnostic = peer.command("read_file", path="/tmp/desktop.log")
                                kernel.publish(kernel.OUTPUT / (name + "-desktop-diagnostic.json"), diagnostic)
                            except (RuntimeError, OSError):
                                pass
                        process.terminate()
                        try:
                            process.wait(timeout=90)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=10)
        kernel.check(not server.errors, "Fixture server failed: " + str(server.errors))
        kernel.publish(kernel.OUTPUT / "desktop-result.json",
                       {"status": "passed", "rounds": completed})
    except Exception as error:
        kernel.publish(kernel.OUTPUT / "desktop-result.json",
                       {"status": "failed", "error": str(error), "completed_rounds": completed})
        raise
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-name", choices=("alpha", "beta"))
    parser.add_argument("--round", type=int, choices=(1, 2))
    args = parser.parse_args()
    if args.browser_name:
        if args.round is None:
            parser.error("--round is required with --browser-name")
        print(json.dumps(browser_probe(args.browser_name, args.round)))
    else:
        raise SystemExit(run_acceptance())
