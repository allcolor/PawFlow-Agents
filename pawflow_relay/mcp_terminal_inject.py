"""Inject one PawFlow webchat prompt into a registered MCP client terminal."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess  # nosec B404 - fixed tmux argv
import sys
import time


_SAFE_TARGET = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")


def _marker(message_id: str, content: str) -> dict:
    return {
        "message_id": message_id,
        "prompt_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "created_at": time.time(),
    }


def append_injected_marker(state_path: str, message_id: str,
                           content: str) -> None:
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        _marker(message_id, content), separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()


def _run_tmux(target: str, state_path: str,
              message_id: str, content: str) -> dict:
    if not _SAFE_TARGET.fullmatch(target):
        raise ValueError("invalid tmux target")
    check = subprocess.run(  # nosec B603 B607 - fixed tmux executable/argv
        ["tmux", "has-session", "-t", target],
        capture_output=True, timeout=5)
    if check.returncode:
        raise RuntimeError("tmux session is not running")
    buffer_name = "pfmcp_" + re.sub(
        r"[^A-Za-z0-9_.-]", "_", message_id)[:80]
    loaded = subprocess.run(  # nosec B603 B607 - fixed tmux executable/argv
        ["tmux", "load-buffer", "-b", buffer_name, "-"],
        input=content.encode("utf-8"), capture_output=True, timeout=15)
    if loaded.returncode:
        raise RuntimeError(
            loaded.stderr.decode("utf-8", errors="replace")[:500]
            or "tmux load-buffer failed")
    pasted = subprocess.run(  # nosec B603 B607 - fixed tmux executable/argv
        ["tmux", "paste-buffer", "-p", "-b", buffer_name, "-t", target],
        capture_output=True, timeout=10)
    if pasted.returncode:
        raise RuntimeError(
            pasted.stderr.decode("utf-8", errors="replace")[:500]
            or "tmux paste-buffer failed")
    append_injected_marker(state_path, message_id, content)
    submitted = subprocess.run(  # nosec B603 B607 - fixed tmux executable/argv
        ["tmux", "send-keys", "-t", target, "Enter"],
        capture_output=True, timeout=10)
    if submitted.returncode:
        raise RuntimeError(
            submitted.stderr.decode("utf-8", errors="replace")[:500]
            or "tmux prompt submission failed")
    return {"ok": True, "terminal_kind": "tmux", "target": target}


def _run_loopback(target: str, secret: str,
                  message_id: str, content: str) -> dict:
    if not _SAFE_TARGET.fullmatch(target) or not target.startswith("127.0.0.1:"):
        raise ValueError("invalid loopback terminal target")
    port = int(target.rsplit(":", 1)[1])
    request = json.dumps({
        "secret": secret,
        "message_id": message_id,
        "content_b64": base64.b64encode(content.encode("utf-8")).decode("ascii"),
    }, separators=(",", ":")).encode("utf-8") + b"\n"
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        sock.sendall(request)
        response = b""
        while b"\n" not in response and len(response) < 65536:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
    result = json.loads(response.split(b"\n", 1)[0].decode("utf-8") or "{}")
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "terminal injection failed"))
    return {"ok": True, "terminal_kind": "winpty", "target": target}


def inject(kind: str, target: str, secret: str, state_path: str,
           message_id: str, content: str) -> dict:
    if not message_id:
        raise ValueError("message_id is required")
    if not content.strip():
        raise ValueError("content must be non-empty")
    if kind == "tmux":
        if not state_path:
            raise ValueError("state_path is required for tmux injection")
        return _run_tmux(target, state_path, message_id, content)
    if kind == "winpty":
        if not secret:
            raise ValueError("secret is required for winpty injection")
        return _run_loopback(target, secret, message_id, content)
    raise ValueError(f"unsupported terminal kind: {kind}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--secret", default="")
    parser.add_argument("--state-path", default="")
    parser.add_argument("--message-id", required=True)
    parser.add_argument("--content-b64", required=True)
    args = parser.parse_args(argv)
    try:
        content = base64.b64decode(args.content_b64).decode("utf-8")
        result = inject(
            args.kind, args.target, args.secret, args.state_path,
            args.message_id, content)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
