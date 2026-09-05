#!/usr/bin/env python3
"""Run a native CLI login in the existing noVNC authentication container."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

RESULT = Path("/tmp/native-cli-login.result.json")


def login_command(provider, binary):
    if provider == "cursor-acp":
        return [binary, "login"]
    if provider == "grok-build-acp":
        return [binary, "--no-auto-update", "login", "--device-auth"]
    if provider == "opencode":
        return [binary, "auth", "login"]
    raise ValueError("Unsupported native CLI provider")


def main():
    provider = os.environ["PAWFLOW_NATIVE_PROVIDER"]
    home = Path(os.environ["PAWFLOW_NATIVE_HOME"])
    os.environ.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
                      XDG_DATA_HOME=str(home / ".local/share"),
                      XDG_STATE_HOME=str(home / ".local/state"),
                      XDG_CACHE_HOME=str(home / ".cache"),
                      DISPLAY=":99", BROWSER="/usr/local/bin/open-browser",
                      LANG="C.UTF-8", LC_ALL="C.UTF-8", TERM="xterm-256color")
    for name in ("CI", "NO_BROWSER", "NO_OPEN_BROWSER", "GITHUB_ACTIONS",
                 "CURSOR_API_KEY", "CURSOR_AUTH_TOKEN", "XAI_API_KEY"):
        os.environ.pop(name, None)
    if "--inner" in sys.argv:
        try:
            code = subprocess.call(login_command(provider, os.environ["PAWFLOW_NATIVE_BIN"]))
        except OSError:
            code = 1
        if code == 0 and provider == "cursor-acp":
            (home / ".pawflow-login-complete").touch(mode=0o600)
        RESULT.write_text(json.dumps({"ok": code == 0}), encoding="utf-8")
        RESULT.chmod(0o600)
        print("Login completed." if code == 0 else "Login failed.")
        time.sleep(360)
        return

    uid, gid = map(int, os.environ["PAWFLOW_NATIVE_USER"].split(":"))
    home.mkdir(parents=True, exist_ok=True)
    os.chown(home, uid, gid)
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)
    os.umask(0o077)
    os.chdir(home)
    children = []
    try:
        children.append(subprocess.Popen(["Xvfb", ":99", "-screen", "0", "1280x800x24", "-ac"]))
        time.sleep(0.5)
        children.append(subprocess.Popen(["x11vnc", "-display", ":99", "-nopw", "-forever",
                                          "-shared", "-rfbport", "5900"]))
        children.append(subprocess.Popen(["websockify", "--web", "/usr/share/novnc",
                                          "--timeout=0", "6080", "localhost:5900"]))
        children.append(subprocess.Popen(["xterm", "-fa", "Monospace", "-fs", "14",
                                          "-bg", "black", "-fg", "white", "-e",
                                          sys.executable, __file__, "--inner"]))
        # A closed browser must not leave a login container running forever.
        time.sleep(360)
    finally:
        for child in children:
            child.terminate()


if __name__ == "__main__":
    main()
