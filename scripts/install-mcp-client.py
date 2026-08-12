#!/usr/bin/env python3
"""Guided installer for the standalone PawFlow MCP stdio client."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import getpass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import shlex
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Iterable
import urllib.request
from urllib.parse import urlparse
import zipfile


CLIENT_ALIASES = {
    "cc": "cc",
    "claude": "cc",
    "claude-code": "cc",
    "codex": "codex",
    "agy": "agy",
    "antigravity": "agy",
    "opencode": "opencode",
    "open-code": "opencode",
    "jcode": "jcode",
    "pi": "pi",
    "hermes": "hermes",
    "hermes-agent": "hermes",
}
CLIENT_LABELS = {
    "cc": "Claude Code",
    "codex": "Codex",
    "agy": "Agy",
    "opencode": "OpenCode",
    "jcode": "JCode",
    "pi": "Pi",
    "hermes": "Hermes",
}
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
FRP_VERSION = "0.70.1"
FRP_ASSETS = {
    ("windows", "amd64"): (
        "windows_amd64", "zip",
        "531f3cd3cc41c0b4f077b54fe6b7dd83c0ff727e7f0bf412a4c78fa279165de5"),
    ("windows", "arm64"): (
        "windows_arm64", "zip",
        "74d3acaf0f03ee190dd0462f9b49861dca50b0559c5488af4b36572fc951fcca"),
    ("linux", "amd64"): (
        "linux_amd64", "tar.gz",
        "333da23d1b9009d7c01638e9ba38cf4600f7d37d393f854e96ee1396adefa9a6"),
    ("linux", "arm64"): (
        "linux_arm64", "tar.gz",
        "3990f396a9a490ee7f0e5f355287750ed41520064ed999eab443b5e9a78d773d"),
    ("darwin", "amd64"): (
        "darwin_amd64", "tar.gz",
        "cbf69cf26e5553e914e97d37f5d4367fa30f5f531d073a889465af4719281e25"),
    ("darwin", "arm64"): (
        "darwin_arm64", "tar.gz",
        "cfa733b5a261c1647edee3c1fc4133d2542989b28f5602e81d47fc821d25c55f"),
}


@dataclass(frozen=True)
class InstallRequest:
    name: str
    url: str
    api_key: str
    gateway_key: str
    relay_dir: Path
    clients: tuple[str, ...]
    readonly: bool = False
    allow_exec: bool = False
    allow_service_tunnels: bool = False


def default_install_root() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            raise ValueError("LOCALAPPDATA is required on Windows")
        return Path(base) / "PawFlow" / "MCP"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PawFlow" / "MCP"
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base) if base else Path.home() / ".local" / "share") / "pawflow" / "mcp"


def _validate_request(request: InstallRequest) -> InstallRequest:
    if not _NAME_RE.fullmatch(request.name):
        raise ValueError("Server name may contain only letters, digits, dot, dash, and underscore")
    parsed = urlparse(request.url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Published URL must be an absolute HTTP(S) URL")
    if "/mcp/" not in parsed.path:
        raise ValueError("Published URL must contain /mcp/")
    if not request.api_key:
        raise ValueError("A PawFlow MCP API key is required")
    if not request.relay_dir.expanduser().is_dir():
        raise ValueError(f"Relay directory does not exist: {request.relay_dir}")
    if not request.clients:
        raise ValueError(
            "Select at least one client: cc, codex, agy, opencode, jcode, pi, or hermes")
    unknown = set(request.clients) - set(CLIENT_LABELS)
    if unknown:
        raise ValueError(f"Unsupported clients: {', '.join(sorted(unknown))}")
    return request


def parse_clients(value: str | Iterable[str]) -> tuple[str, ...]:
    raw = value.split(",") if isinstance(value, str) else list(value)
    clients: list[str] = []
    for item in raw:
        key = str(item).strip().lower()
        if not key:
            continue
        client = CLIENT_ALIASES.get(key)
        if not client:
            raise ValueError(
                f"Unsupported client {item!r}; use cc, codex, agy, "
                "opencode, jcode, pi, or hermes")
        if client not in clients:
            clients.append(client)
    return tuple(clients)


def _safe_chmod(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        pass


def _backup(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    candidate = path.with_name(f"{path.name}.bak-{stamp}-{time.time_ns()}")
    shutil.copy2(path, candidate)
    return candidate


def _atomic_write(path: Path, content: str, *, secret: bool = False,
                  backup: bool = True) -> tuple[bool, Path | None]:
    encoded = content.encode("utf-8")
    if path.exists() and path.read_bytes() == encoded:
        if secret:
            _safe_chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        return False, None
    path.parent.mkdir(parents=True, exist_ok=True)
    if secret:
        _safe_chmod(path.parent, stat.S_IRWXU)
    backup_path = _backup(path) if backup and path.exists() else None
    handle = tempfile.NamedTemporaryFile(
        mode="wb", prefix=f".{path.name}.", dir=path.parent, delete=False)
    tmp = Path(handle.name)
    try:
        with handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        _safe_chmod(
            tmp,
            stat.S_IRUSR | stat.S_IWUSR if secret
            else stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH,
        )
        os.replace(tmp, path)
        if secret:
            _safe_chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    finally:
        if tmp.exists():
            tmp.unlink()
    return True, backup_path


def _load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return dict(default)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read JSON configuration {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON configuration must contain an object: {path}")
    return value


def _write_json(path: Path, value: dict, *, secret: bool = False,
                backup: bool = True) -> tuple[bool, Path | None]:
    return _atomic_write(
        path,
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        secret=secret,
        backup=backup,
    )


def _replace_runtime(bundle_root: Path, install_root: Path) -> None:
    source = bundle_root / "runtime"
    launcher_source = bundle_root / "launcher.py"
    client_source = bundle_root / "client.py"
    hook_source = bundle_root / "hook.py"
    if not (source / "pawflow_relay" / "mcp_stdio.py").is_file():
        raise ValueError(f"Bundle runtime is incomplete: {source}")
    if not launcher_source.is_file():
        raise ValueError(f"Bundle launcher is missing: {launcher_source}")
    if not client_source.is_file():
        raise ValueError(f"Bundle client launcher is missing: {client_source}")
    if not hook_source.is_file():
        raise ValueError(f"Bundle lifecycle hook is missing: {hook_source}")
    install_root.mkdir(parents=True, exist_ok=True)
    target = install_root / "runtime"
    staged = install_root / f".runtime-new-{os.getpid()}-{time.time_ns()}"
    old = install_root / f".runtime-old-{os.getpid()}-{time.time_ns()}"
    shutil.copytree(source, staged)
    try:
        if target.exists():
            os.replace(target, old)
        os.replace(staged, target)
    except Exception:
        if not target.exists() and old.exists():
            os.replace(old, target)
        raise
    finally:
        if staged.exists():
            shutil.rmtree(staged)
        if old.exists():
            shutil.rmtree(old)
    launcher_target = install_root / "launch.py"
    tmp_launcher = install_root / f".launch-{os.getpid()}-{time.time_ns()}.py"
    shutil.copy2(launcher_source, tmp_launcher)
    os.replace(tmp_launcher, launcher_target)
    _safe_chmod(
        launcher_target,
        stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
        | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH,
    )
    client_target = install_root / "run-client.py"
    tmp_client = install_root / f".client-{os.getpid()}-{time.time_ns()}.py"
    shutil.copy2(client_source, tmp_client)
    os.replace(tmp_client, client_target)
    _safe_chmod(
        client_target,
        stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
        | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH,
    )
    hook_target = install_root / "hook.py"
    tmp_hook = install_root / f".hook-{os.getpid()}-{time.time_ns()}.py"
    shutil.copy2(hook_source, tmp_hook)
    os.replace(tmp_hook, hook_target)
    _safe_chmod(
        hook_target,
        stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
        | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH,
    )


def _frp_asset() -> tuple[str, str, str]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    machine = {"x86_64": "amd64", "aarch64": "arm64"}.get(machine, machine)
    try:
        return FRP_ASSETS[(system, machine)]
    except KeyError as exc:
        raise ValueError(
            f"FRP {FRP_VERSION} is not available for {system}-{machine}") from exc


def _ensure_frpc(runtime_root: Path) -> Path:
    """Install the verified official FRP client into the MCP relay runtime."""
    platform_name, extension, expected_sha256 = _frp_asset()
    executable = "frpc.exe" if platform.system().lower() == "windows" else "frpc"
    destination = runtime_root / "bin" / executable
    if destination.is_file():
        return destination
    archive_name = f"frp_{FRP_VERSION}_{platform_name}.{extension}"
    url = (
        f"https://github.com/fatedier/frp/releases/download/"
        f"v{FRP_VERSION}/{archive_name}")
    request = urllib.request.Request(
        url, headers={"User-Agent": "PawFlow-MCP-installer"})
    with urllib.request.urlopen(request, timeout=120) as response:  # nosec B310
        payload = response.read()
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"FRP checksum mismatch for {archive_name}: "
            f"expected {expected_sha256}, got {actual_sha256}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pawflow-mcp-frpc-") as temp:
        archive_path = Path(temp) / archive_name
        archive_path.write_bytes(payload)
        if extension == "zip":
            with zipfile.ZipFile(archive_path) as archive:
                members = [
                    name for name in archive.namelist()
                    if Path(name).name == executable and not name.endswith("/")
                ]
                if len(members) != 1:
                    raise ValueError(f"{executable} was not found in {archive_name}")
                binary = archive.read(members[0])
        else:
            with tarfile.open(archive_path, "r:gz") as archive:
                members = [
                    member for member in archive.getmembers()
                    if member.isfile() and Path(member.name).name == executable
                ]
                if len(members) != 1:
                    raise ValueError(f"{executable} was not found in {archive_name}")
                extracted = archive.extractfile(members[0])
                if extracted is None:
                    raise ValueError(f"Could not read {executable} from {archive_name}")
                binary = extracted.read()
    destination.write_bytes(binary)
    if os.name != "nt":
        _safe_chmod(
            destination,
            stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
            | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH,
        )
    return destination


def _entry(install_root: Path, profile_path: Path, python: str,
           relay_dir: Path, client: str) -> dict:
    return {
        "type": "stdio",
        "command": str(Path(python).resolve()),
        "args": [
            str((install_root / "launch.py").resolve()),
            "--profile",
            str(profile_path.resolve()),
            "--client-name",
            CLIENT_LABELS[client],
        ],
        "cwd": str(relay_dir.resolve()),
    }


def _agy_list_entry(name: str, entry: dict) -> dict:
    return {"serverName": name, **entry, "disabled": False}


def _command_string(values: list[str]) -> str:
    return subprocess.list2cmdline(values) if os.name == "nt" else shlex.join(values)


def _hook_argv(install_root: Path, profile_path: Path, session_dir: Path,
               python: str, client: str) -> list[str]:
    return [
        str(Path(python).resolve()), str((install_root / "hook.py").resolve()),
        "--profile", str(profile_path.resolve()),
        "--state", str((session_dir / f"hook-state-{client}.json").resolve()),
        "--marker", str((session_dir / "injected-prompts.jsonl").resolve()),
        "--client", client,
    ]


def _hook_handler(argv: list[str], *, split_args: bool) -> dict:
    if split_args:
        return {
            "type": "command", "command": argv[0], "args": argv[1:],
            "timeout": 30,
        }
    return {"type": "command", "command": _command_string(argv), "timeout": 30}


def _hooks(handler: dict, events: tuple[str, ...]) -> dict:
    return {event: [{"hooks": [dict(handler)]}] for event in events}


def _write_agy_home(session_dir: Path, name: str, entry: dict,
                    hook_handler: dict) -> list[Path]:
    """Write an isolated Agy/Gemini home for one local client instance."""
    results: list[Path] = []
    gemini = session_dir / "agy-home" / ".gemini"
    cli_dir = gemini / "antigravity-cli"
    cli_settings_path = cli_dir / "settings.json"
    settings = {
        "mcpServers": {name: entry},
        "allowMCPServers": [name],
        "mcp": {"allowed": [name]},
        "permissions": {"allow": [f"mcp({name}/*)", f"mcp_{name}_*"]},
        "hooks": _hooks(hook_handler, ("PreInvocation", "Stop")),
    }
    _write_json(cli_settings_path, settings, backup=False)
    results.append(cli_settings_path)
    list_path = cli_dir / "mcp_config.json"
    _write_json(
        list_path, {"mcpServers": [_agy_list_entry(name, entry)]},
        backup=False)
    results.append(list_path)
    settings_path = gemini / "settings.json"
    _write_json(settings_path, settings, backup=False)
    results.append(settings_path)
    root_config_path = gemini / "mcp_config.json"
    _write_json(
        root_config_path, {"mcpServers": {name: entry}}, backup=False)
    results.append(root_config_path)
    return results


def _adapter_command(argv: list[str], event: str, operation: str = "hook") -> list[str]:
    command = [*argv, "--operation", operation]
    if event:
        command.extend(["--event", event])
    return command


def _javascript_runner(argv: list[str]) -> str:
    return f"""
const PAWFLOW_HOOK = {json.dumps(argv)};
async function pawflowRun(event, payload, operation = "hook") {{
  const command = [...PAWFLOW_HOOK, "--operation", operation];
  if (event) command.push("--event", event);
  const child = Bun.spawn(command, {{stdin: "pipe", stdout: "pipe", stderr: "inherit"}});
  child.stdin.write(JSON.stringify(payload || {{}}));
  child.stdin.end();
  const output = await new Response(child.stdout).text();
  await child.exited;
  try {{ return JSON.parse(output || "{{}}"); }} catch {{ return {{}}; }}
}}
""".strip()


def _write_opencode_home(session_dir: Path, name: str, entry: dict,
                         hook_argv: list[str]) -> list[Path]:
    root = session_dir / "opencode-home"
    config_path = root / "opencode.json"
    plugin_path = root / "plugins" / "pawflow.js"
    command = [entry["command"], *entry.get("args", [])]
    config = {"mcp": {name: {
        "type": "local", "command": command, "cwd": entry["cwd"],
        "enabled": True,
    }}, "plugin": [plugin_path.resolve().as_uri()]}
    _write_json(config_path, config, backup=False)
    runner = _javascript_runner(hook_argv)
    plugin = runner + """

export const PawFlow = async () => {
  const pending = new Map();
  const roles = new Map();
  const assistantParts = new Map();
  const assistantIDs = new Map();
  return {
    "chat.message": async (input, output) => {
      const prompt = output.parts
        .filter((part) => part && part.type === "text")
        .map((part) => part.text || "").join("\\n");
      const result = await pawflowRun("chat.message", {
        prompt, session_id: input.sessionID, message_id: input.messageID || ""
      });
      if (result.context) pending.set(input.sessionID, result.context);
    },
    "experimental.chat.system.transform": async (input, output) => {
      const context = pending.get(input.sessionID);
      if (context) {
        output.system.push(context);
        pending.delete(input.sessionID);
      }
    },
    event: async ({event}) => {
      const properties = event.properties || {};
      if (event.type === "message.updated" && properties.info) {
        roles.set(properties.info.id, properties.info.role);
      }
      if (event.type === "message.part.updated" && properties.part) {
        const part = properties.part;
        if (roles.get(part.messageID) === "assistant" && part.type === "text") {
          if (!assistantParts.has(properties.sessionID)) assistantParts.set(properties.sessionID, new Map());
          assistantParts.get(properties.sessionID).set(part.id, part.text || "");
          assistantIDs.set(properties.sessionID, part.messageID);
        }
      }
      if (event.type === "session.idle") {
        const parts = assistantParts.get(properties.sessionID);
        const last_assistant_message = parts ? [...parts.values()].join("\\n") : "";
        await pawflowRun("session.idle", {
          session_id: properties.sessionID,
          turn_id: assistantIDs.get(properties.sessionID) || "",
          last_assistant_message
        });
        assistantParts.delete(properties.sessionID);
        assistantIDs.delete(properties.sessionID);
      }
    }
  };
};
"""
    _atomic_write(plugin_path, plugin + "\n", backup=False)
    return [config_path, plugin_path]


def _write_pi_home(session_dir: Path, hook_argv: list[str]) -> list[Path]:
    root = session_dir / "pi-home"
    extension_path = root / "extensions" / "pawflow.js"
    runner = _javascript_runner(hook_argv)
    extension = runner + """

function messageText(message) {
  if (!message) return "";
  if (typeof message.content === "string") return message.content;
  if (Array.isArray(message.content)) {
    return message.content.filter((part) => part && part.type === "text")
      .map((part) => part.text || "").join("\\n");
  }
  return "";
}

export default async function pawflowExtension(pi) {
  const listed = await pawflowRun("", {}, "list-tools");
  for (const tool of listed.tools || []) {
    pi.registerTool({
      name: tool.name,
      label: tool.title || tool.name,
      description: tool.description || "",
      parameters: tool.inputSchema || {type: "object", properties: {}},
      async execute(_id, params) {
        const result = await pawflowRun("", {name: tool.name, arguments: params || {}}, "call-tool");
        return {content: result.content || [{type: "text", text: ""}], details: result.structuredContent || {}};
      }
    });
  }
  pi.on("before_agent_start", async (event, ctx) => {
    const result = await pawflowRun("before_agent_start", {
      prompt: event.prompt, session_id: ctx.sessionManager.getSessionId(),
      turn_id: ctx.sessionManager.getLeafId() || ""
    });
    if (!result.context) return;
    return {message: {
      customType: "pawflow-context", content: result.context,
      display: false, details: {source: "pawflow"}
    }};
  });
  pi.on("agent_end", async (event, ctx) => {
    const assistant = [...event.messages].reverse().find((message) => message.role === "assistant");
    await pawflowRun("agent_end", {
      session_id: ctx.sessionManager.getSessionId(),
      turn_id: ctx.sessionManager.getLeafId() || "",
      last_assistant_message: messageText(assistant)
    });
  });
}
"""
    _atomic_write(extension_path, extension + "\n", backup=False)
    return [extension_path]


def _write_jcode_home(session_dir: Path, name: str, entry: dict,
                      hook_argv: list[str]) -> list[Path]:
    root = session_dir / "jcode-home"
    mcp_path = root / "mcp.json"
    _write_json(mcp_path, {"mcpServers": {name: entry}}, backup=False)
    hook = _command_string(hook_argv)
    config_path = root / "config.toml"
    config = (
        "[hooks]\n"
        f"session_start = {json.dumps(hook + ' --event session_start')}\n"
        f"turn_start = {json.dumps(hook + ' --event turn_start')}\n"
        f"turn_end = {json.dumps(hook + ' --event turn_end')}\n"
    )
    _atomic_write(config_path, config, backup=False)
    overlay_path = root / "prompt-overlay.md"
    overlay = f"""# PawFlow published conversation

This isolated JCode session is bound to the PawFlow MCP server `{name}`.
Before answering the first user request, call `get_initial_context` and use its
document as authoritative conversation context. Retain the returned cursor.
Before each later user request, call `get_context_updates` with the last cursor
and incorporate the returned messages before answering. Update the cursor from
every response. Lifecycle hooks already persist local user prompts and final
assistant replies, so do not call `send_user_message` or `send_agent_message`
for ordinary turns.
"""
    _atomic_write(overlay_path, overlay, backup=False)
    return [mcp_path, config_path, overlay_path]


def _write_hermes_home(session_dir: Path, name: str, entry: dict,
                       hook_argv: list[str]) -> list[Path]:
    root = session_dir / "hermes-home"
    config_path = root / "config.yaml"
    command = json.dumps(entry["command"])
    args = json.dumps(entry.get("args") or [])
    config = (
        "mcp_servers:\n"
        f"  {name}:\n"
        f"    command: {command}\n"
        f"    args: {args}\n"
        "    enabled: true\n"
        "plugins:\n"
        "  enabled:\n"
        "    - pawflow\n"
    )
    _atomic_write(config_path, config, backup=False)
    plugin_root = root / "plugins" / "pawflow"
    manifest_path = plugin_root / "plugin.yaml"
    _atomic_write(
        manifest_path,
        "name: pawflow\n"
        "version: \"1.0.0\"\n"
        "description: PawFlow conversation bridge\n"
        "hooks:\n"
        "  - pre_llm_call\n"
        "  - post_llm_call\n",
        backup=False)
    plugin_path = plugin_root / "__init__.py"
    plugin = f'''"""PawFlow conversation lifecycle plugin for Hermes."""
import json
import subprocess

HOOK = {hook_argv!r}

def _run(event, payload):
    try:
        result = subprocess.run(
            [*HOOK, "--operation", "hook", "--event", event],
            input=json.dumps(payload, default=str), text=True, capture_output=True,
            timeout=30, check=False)
        return json.loads(result.stdout or "{{}}")
    except Exception:
        return {{}}

def _pre(**kwargs):
    result = _run("pre_llm_call", kwargs)
    return {{"context": result.get("context", "")}} if result.get("context") else None

def _post(**kwargs):
    _run("post_llm_call", kwargs)

def register(ctx):
    ctx.register_hook("pre_llm_call", _pre)
    ctx.register_hook("post_llm_call", _post)
'''
    _atomic_write(plugin_path, plugin, backup=False)
    return [config_path, manifest_path, plugin_path]


def install(request: InstallRequest, *, bundle_root: Path,
            install_root: Path | None = None, python: str | None = None) -> dict:
    request = _validate_request(request)
    bundle_root = bundle_root.resolve()
    install_root = (install_root or default_install_root()).expanduser().resolve()
    python = python or sys.executable
    _replace_runtime(bundle_root, install_root)
    if request.allow_service_tunnels:
        _ensure_frpc(install_root / "runtime")

    session_dir = install_root / "sessions" / request.name
    profile_path = session_dir / "profile.json"
    profile = {
        "version": 1,
        "url": request.url.rstrip("/"),
        "api_key": request.api_key,
        "gateway_key": request.gateway_key,
        "relay_dir": str(request.relay_dir.expanduser().resolve()),
        "readonly": bool(request.readonly),
        "allow_exec": bool(request.allow_exec),
        "allow_service_tunnels": bool(request.allow_service_tunnels),
    }
    _write_json(profile_path, profile, secret=True, backup=False)

    relay_dir = request.relay_dir.expanduser().resolve()
    entries = {
        client: _entry(install_root, profile_path, python, relay_dir, client)
        for client in request.clients
    }
    generic_entry = {
        **_entry(install_root, profile_path, python, relay_dir, "cc"),
    }
    generic_entry["args"][-1] = "MCP Client"
    configs: list[str] = []
    generic_path = session_dir / "mcp.json"
    entry_path = session_dir / "entry.json"
    _write_json(generic_path, {"mcpServers": {
        request.name: generic_entry}}, backup=False)
    _write_json(entry_path, generic_entry, backup=False)
    configs.extend([str(generic_path), str(entry_path)])
    config_map = {"generic": str(generic_path)}
    if "cc" in entries:
        cc_path = session_dir / "claude.mcp.json"
        _write_json(
            cc_path, {"mcpServers": {request.name: entries["cc"]}},
            backup=False)
        config_map["cc"] = str(cc_path)
        configs.append(str(cc_path))
        cc_settings = session_dir / "claude.settings.json"
        cc_handler = _hook_handler(
            _hook_argv(install_root, profile_path, session_dir, python, "cc"),
            split_args=True)
        _write_json(cc_settings, {"hooks": _hooks(
            cc_handler, ("SessionStart", "UserPromptSubmit", "Stop"))},
            backup=False)
        config_map["cc_settings"] = str(cc_settings)
        configs.append(str(cc_settings))
    if "codex" in entries:
        codex_home = session_dir / "codex-home"
        codex_handler = _hook_handler(
            _hook_argv(
                install_root, profile_path, session_dir, python, "codex"),
            split_args=False)
        codex_hooks = codex_home / "hooks.json"
        _write_json(codex_hooks, {
            "description": "PawFlow published conversation lifecycle bridge",
            "hooks": _hooks(
                codex_handler, ("SessionStart", "UserPromptSubmit", "Stop")),
        }, backup=False)
        config_map["codex_home"] = str(codex_home)
        configs.append(str(codex_hooks))
    if "agy" in entries:
        agy_handler = _hook_handler(
            _hook_argv(install_root, profile_path, session_dir, python, "agy"),
            split_args=False)
        agy_paths = _write_agy_home(
            session_dir, request.name, entries["agy"], agy_handler)
        config_map["agy_home"] = str(session_dir / "agy-home")
        configs.extend(str(path) for path in agy_paths)
    for client, writer, config_key in (
        ("opencode", _write_opencode_home, "opencode_home"),
        ("jcode", _write_jcode_home, "jcode_home"),
        ("hermes", _write_hermes_home, "hermes_home"),
    ):
        if client not in entries:
            continue
        argv = _hook_argv(
            install_root, profile_path, session_dir, python, client)
        paths = writer(
            session_dir, request.name, entries[client], argv)
        config_map[config_key] = str(session_dir / f"{client}-home")
        configs.extend(str(path) for path in paths)
    if "pi" in entries:
        pi_argv = _hook_argv(
            install_root, profile_path, session_dir, python, "pi")
        pi_paths = _write_pi_home(session_dir, pi_argv)
        config_map["pi_home"] = str(session_dir / "pi-home")
        configs.extend(str(path) for path in pi_paths)
    session_path = session_dir / "session.json"
    session = {
        "version": 1,
        "name": request.name,
        "relay_dir": str(relay_dir),
        "clients": list(request.clients),
        "entries": entries,
        "configs": config_map,
        "terminal": {
            "marker_path": str((session_dir / "injected-prompts.jsonl").resolve()),
        },
    }
    _write_json(session_path, session, backup=False)
    configs.append(str(session_path))
    client_launcher = install_root / "run-client.py"
    launch_commands = {
        client: [
            str(Path(python).resolve()), str(client_launcher),
            "--session", str(session_path), "--client", client,
        ]
        for client in request.clients
    }
    return {
        "name": request.name,
        "install_root": str(install_root),
        "profile": str(profile_path),
        "clients": list(request.clients),
        "configs": configs,
        "session": str(session_path),
        "launch_commands": launch_commands,
        "relay_dir": str(relay_dir),
        "readonly": request.readonly,
        "allow_exec": request.allow_exec,
        "allow_service_tunnels": request.allow_service_tunnels,
    }


def _prompt(label: str, default: str = "", *, secret: bool = False,
            required: bool = False) -> str:
    suffix = f" [{default}]" if default and not secret else ""
    while True:
        value = (getpass.getpass if secret else input)(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default:
            return default
        if not required:
            return ""
        print(f"{label} is required.", file=sys.stderr)


def _prompt_bool(label: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        value = input(f"{label} [{suffix}]: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Answer y or n.", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install and configure the PawFlow MCP stdio client")
    parser.add_argument("--name", default="pawflow")
    parser.add_argument("--url", default=os.environ.get("PAWFLOW_MCP_URL", ""))
    parser.add_argument("--relay-dir", default=str(Path.cwd()))
    parser.add_argument(
        "--clients", default="cc,codex,agy,opencode,jcode,pi,hermes")
    parser.add_argument("--readonly", action="store_true")
    parser.add_argument("--allow-exec", action="store_true")
    parser.add_argument("--allow-service-tunnels", action="store_true")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--api-key-env", default="PAWFLOW_MCP_API_KEY")
    parser.add_argument("--gateway-key-env", default="PAWFLOW_GATEWAY_KEY")
    parser.add_argument("--install-dir")
    parser.add_argument("--python", default=sys.executable)
    return parser


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < (3, 10):
        print("Python 3.10 or newer is required.", file=sys.stderr)
        return 2
    args = build_parser().parse_args(argv)
    if args.non_interactive:
        api_key = os.environ.get(args.api_key_env, "")
        gateway_key = os.environ.get(args.gateway_key_env, "")
        name = args.name
        url = args.url
        relay_dir = args.relay_dir
        clients = args.clients
        readonly = args.readonly
        allow_exec = args.allow_exec
        allow_service_tunnels = args.allow_service_tunnels
    else:
        print("PawFlow MCP client guided installation")
        print("Secrets are stored only in a private local profile.")
        name = _prompt("Server name", args.name, required=True)
        url = _prompt("Published PawFlow MCP URL", args.url, required=True)
        api_key = _prompt("PawFlow MCP API key", secret=True, required=True)
        gateway_key = _prompt("Private Gateway key (optional)", secret=True)
        relay_dir = _prompt("Project directory to share", args.relay_dir, required=True)
        clients = _prompt(
            "Clients (cc,codex,agy,opencode,jcode,pi,hermes)",
            args.clients, required=True)
        readonly = _prompt_bool("Make the relay read-only", args.readonly)
        allow_exec = _prompt_bool("Allow shell execution through the relay", args.allow_exec)
        allow_service_tunnels = _prompt_bool(
            "Allow approved FRP service tunnels through the relay",
            args.allow_service_tunnels)
    try:
        request = InstallRequest(
            name=name,
            url=url,
            api_key=api_key,
            gateway_key=gateway_key,
            relay_dir=Path(relay_dir),
            clients=parse_clients(clients),
            readonly=readonly,
            allow_exec=allow_exec,
            allow_service_tunnels=allow_service_tunnels,
        )
        result = install(
            request,
            bundle_root=Path(__file__).resolve().parent,
            install_root=Path(args.install_dir) if args.install_dir else None,
            python=args.python,
        )
    except (OSError, ValueError) as exc:
        print(f"Installation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Installed PawFlow MCP client {result['name']!r}.")
    print(f"Runtime: {result['install_root']}")
    print(f"Private profile: {result['profile']}")
    print(f"Configured clients: {', '.join(result['clients'])}")
    print(f"Shared directory: {result['relay_dir']}")
    print("Session-bound launch commands:")
    for client, command in result["launch_commands"].items():
        print(f"  {client}: {subprocess.list2cmdline(command)}")
    print("Use these launch commands instead of starting the client normally.")
    print("One session bundle maps to exactly one published conversation and agent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
