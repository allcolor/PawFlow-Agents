"""Persistent interactive Codex TUI sessions observed through the CCI MITM.

The transport primitives are shared with ``InteractiveClaudeCodePool`` (tmux,
container lifecycle, transparent TLS proxy). Codex-specific startup lives here.
OAuth credentials are deliberately *not* reserved per live session: OpenAI
credentials are safe to share across concurrent Codex agents, while refreshes
remain serialized by ``CodexSessionMixin``'s per-slot refresh lock.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess  # nosec B404
import threading
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import core.paths as _paths
from core._cci_pool_spawn import InteractiveContainer, _InteractiveContainerSpawnMixin
from core.cc_interactive_certs import generate_leaf
from core.claude_code_interactive_pool import InteractiveClaudeCodePool
from core.docker_utils import docker_cmd, get_host_ip

logger = logging.getLogger(__name__)

#: The Codex TUI prints its remaining budget in the status bar:
#: ``gpt-5.6-sol medium  ~  context left 74%``. It is the only place the
#: session's real context window is observable at all -- the Responses API
#: reports prompt sizes but never the window they are measured against.
_CONTEXT_LEFT_RE = re.compile(r"context\s+left\s+(\d+(?:\.\d+)?)\s*%",
                              re.IGNORECASE)

# Below this occupancy the derivation is not worth trusting: the TUI rounds to
# a whole percent, so at 5% used a +-0.5 point error moves the derived window
# by +-10%. At 15% used the same error is worth under 4%.
_MIN_OCCUPANCY_FOR_WINDOW = 0.15
# Replacing the stored window on every turn would make the gauge denominator
# wobble with the TUI's rounding -- the exact defect class this value exists to
# remove. Only a genuine change (a different model, a [1m] variant) moves it.
_WINDOW_REPLACE_TOLERANCE = 0.05


def context_left_fraction(pane: str):
    """Fraction of the Codex context window still free, or None.

    The last match wins: the status bar is redrawn at the bottom of the pane,
    so an older value may still be visible higher up in the transcript.
    """
    matches = _CONTEXT_LEFT_RE.findall(pane or "")
    if not matches:
        return None
    try:
        value = float(matches[-1])
    except (TypeError, ValueError):
        return None
    if not 0.0 <= value <= 100.0:
        return None
    return value / 100.0


def derive_context_window(used_tokens: int, left_fraction, *,
                          previous: int = 0) -> int:
    """Turn (measured prompt size, TUI percentage) into a window in tokens.

    PawFlow measures ``used`` exactly -- it is the ``input_tokens`` the
    Responses API reported on the wire -- and Codex displays what fraction of
    the window is left. The window is the one unknown, so it follows:
    ``window = used / (1 - left)``.

    Returns ``previous`` unchanged when the reading is not trustworthy or when
    the new derivation agrees with it, so the denominator stays put instead of
    breathing with the TUI's rounding.
    """
    try:
        used = int(used_tokens or 0)
    except (TypeError, ValueError):
        used = 0
    prev = max(0, int(previous or 0))
    if used <= 0 or left_fraction is None:
        return prev
    occupancy = 1.0 - float(left_fraction)
    if occupancy < _MIN_OCCUPANCY_FOR_WINDOW:
        return prev
    derived = int(round((used / occupancy) / 1000.0)) * 1000
    if derived <= 0:
        return prev
    if prev and abs(derived - prev) <= prev * _WINDOW_REPLACE_TOLERANCE:
        return prev
    return derived


class _CodexInteractiveSpawnMixin(_InteractiveContainerSpawnMixin):
    @staticmethod
    def _session_root():
        return _paths.CODEX_SESSIONS_DIR

    @staticmethod
    def _container_kind() -> str:
        return "codex-interactive"

    @staticmethod
    def _container_name_tag() -> str:
        return "codexi"

    @staticmethod
    def _container_image() -> str:
        return os.environ.get(
            "PAWFLOW_CODEX_IMAGE", "pawflow-claude-code:latest")

    @staticmethod
    def _codex_base_url(client) -> str:
        base_url = getattr(client, "base_url", "")
        if callable(base_url):
            base_url = base_url()
        elif isinstance(base_url, property):
            base_url = ""
        return str(base_url or "").strip().rstrip("/")

    @classmethod
    def _codex_endpoint(cls, client) -> tuple[str, int, str, str, int]:
        base_url = cls._codex_base_url(client)
        if base_url:
            parsed = urlparse(
                base_url if "//" in base_url else "https://" + base_url)
            scheme = (parsed.scheme or "https").lower()
            if scheme not in ("http", "https"):
                raise ValueError(
                    "codex-interactive MITM requires an HTTP(S) base_url; "
                    f"got {base_url!r}")
            if not parsed.hostname:
                raise ValueError(
                    "codex-interactive MITM base_url requires a hostname; "
                    f"got {base_url!r}")
            host = parsed.hostname
            upstream_port = parsed.port or (443 if scheme == "https" else 80)
            netloc = host
            if parsed.port or scheme == "http":
                netloc = f"{host}:{upstream_port}"
            # Codex always talks TLS to the local MITM. The proxy then uses the
            # configured upstream scheme, so an http:// upstream is supported
            # without weakening the CLI-facing connection.
            codex_base_url = (
                f"https://{netloc}{(parsed.path or '').rstrip('/')}")
            return (host, upstream_port, scheme, codex_base_url.rstrip("/"),
                    upstream_port)

        api_key = getattr(client, "api_key", "")
        if callable(api_key):
            api_key = api_key()
        elif isinstance(api_key, property):
            api_key = ""
        # OAuth/subscription traffic uses ChatGPT's Codex backend. API-key
        # traffic uses the public API. Neither path mutates OPENAI_BASE_URL.
        host = "api.openai.com" if api_key else "chatgpt.com"
        return host, 443, "https", "", 443

    def _start_new(self, client, model: str, user_id: str, conversation_id: str,
                   agent_name: str, key: tuple[str, str, str, str],
                   pool_index: int = -1) -> InteractiveContainer:
        from services.cc_interactive_event_service import (
            get_or_create_cc_interactive_event_service)

        workdir = client._codex_get_session_workdir(
            conversation_id, agent_name, user_id)
        client._codex_setup_credentials(
            workdir, pool_index=pool_index, user_id=user_id,
            conversation_id=conversation_id)
        selected_pool_index = int(
            getattr(client, "_current_pool_index", -1) or 0)
        if getattr(client, "api_key", ""):
            selected_pool_index = -1
        _mcp_path, internal_token = client._codex_setup_mcp_config(
            workdir, user_id, conversation_id, agent_name,
            trusted_project=self._container_workdir(
                user_id, conversation_id, agent_name))

        (upstream_host, upstream_port, upstream_scheme, codex_base_url,
         listen_port) = self._codex_endpoint(client)
        cert_dir = Path(workdir) / ".pawflow_cci" / "certs"
        generate_leaf(cert_dir, common_name=upstream_host)

        event_url, event_token, event_service = (
            get_or_create_cc_interactive_event_service())
        host_ip = get_host_ip()
        event_url = event_url.replace(
            "localhost", host_ip).replace("127.0.0.1", host_ip)
        session_token = uuid.uuid4().hex
        event_service.register_session(
            session_token, user_id=user_id, conversation_id=conversation_id,
            agent_name=agent_name, provider="codex-interactive")

        name = self._spawn_container(
            user_id=user_id, conversation_id=conversation_id,
            agent_name=agent_name, upstream_host=upstream_host)
        physical_workdir = self._physical_container_workdir(
            user_id, conversation_id, agent_name)
        container_workdir = self._container_workdir(
            user_id, conversation_id, agent_name)
        state = InteractiveContainer(
            key=key, name=name, workdir=workdir,
            container_workdir=container_workdir,
            session_token=session_token,
            event_service_id=getattr(event_service, "service_id", ""),
            internal_token=internal_token,
            service_id=getattr(client, "_agent_service", "") or "",
            svc_pool_idx=selected_pool_index, user_id=user_id,
            conv_id=conversation_id)
        try:
            self._write_codex_hooks(workdir)
            self._install_ca(name, physical_workdir)
            self._start_proxy(
                name=name, container_workdir=physical_workdir,
                session_token=session_token, event_url=event_url,
                event_token=event_token, internal_token=internal_token,
                upstream_host=upstream_host, upstream_port=upstream_port,
                upstream_scheme=upstream_scheme, listen_port=listen_port)
            state.proxy_started = True
            self._start_codex_tmux(
                name=name, container_workdir=physical_workdir, model=model,
                effort=client._cfg("effort", "")
                if hasattr(client, "_cfg") else "",
                session_token=session_token, event_url=event_url,
                event_token=event_token, internal_token=internal_token,
                codex_base_url=codex_base_url)
            state.claude_started = True
        except Exception:
            subprocess.run(
                docker_cmd() + ["rm", "-f", name],
                capture_output=True, timeout=15)  # nosec B603
            raise
        return state

    @staticmethod
    def _write_codex_hooks(workdir: str) -> None:
        handler = {
            "type": "command",
            "command": "python3 /opt/pawflow/cc_interactive_hook.py",
            "timeout": 5,
        }
        hooks = {name: [{"hooks": [handler]}] for name in (
            "UserPromptSubmit", "Stop", "PreCompact",
            "PostCompact", "SessionEnd")}
        path = Path(workdir) / ".codex" / "hooks.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "description": "PawFlow Codex interactive lifecycle bridge",
            "hooks": hooks,
        }, indent=2) + "\n", encoding="utf-8")
        os.chmod(path, 0o600)

    def _start_codex_tmux(self, *, name: str, container_workdir: str,
                          model: str, effort: str, session_token: str,
                          event_url: str, event_token: str,
                          internal_token: str, codex_base_url: str = "") -> None:
        parts = container_workdir.lstrip("/").split("/")
        if len(parts) < 3 or parts[0] != "cc_sessions_host":
            raise ValueError(
                "container_workdir must be under /cc_sessions_host/<user>; "
                f"got {container_workdir!r}")
        user_slot = "/cc_sessions_host/" + parts[1]
        ns_workdir = "/cc_sessions/" + "/".join(parts[2:])
        args = [
            "codex",
            "--dangerously-bypass-approvals-and-sandbox",
            "--dangerously-bypass-hook-trust",
            "--no-alt-screen",
        ]
        if model:
            args.extend(["--model", model])
        if effort:
            normalized = {"max": "xhigh", "maximum": "xhigh",
                          "extra": "xhigh"}.get(
                              effort.strip().lower(), effort.strip().lower())
            args.extend(["-c", f'model_reasoning_effort="{normalized}"'])
            args.extend(["-c", 'model_reasoning_summary="auto"'])
        # No `--disable`, exactly like `codex app-server` (see
        # `_codex_app_stream`, which launches codex with no tool blocklist at
        # all). The blocklist `codex exec` carries forces the agent through
        # the MCP bridge, but this provider hands codex a bootstrap it can
        # only reach locally -- `.pawflow_cci/initial_context.md` lives in the
        # session workdir, and the MCP `read` resolves against the relay,
        # which does not serve this root. Blocking every builtin left codex
        # with no way to read its own cold-start context, and no way to open
        # the attachments `_cci_materialize_images` writes next to it.
        # Steering toward PawFlow tools for user work stays a prompt
        # concern, as it already is for app-server.
        quoted = " ".join(shlex.quote(arg) for arg in args)
        drop_privs = (f"setpriv --reuid={self.run_uid} --regid={self.run_gid} "
                      "--clear-groups --")
        endpoint_env = (
            f"OPENAI_BASE_URL={shlex.quote(codex_base_url)} "
            if codex_base_url else "")
        shell = (
            "mkdir -p /cc_sessions && "
            f"mount --bind {shlex.quote(user_slot)} /cc_sessions && "
            f"cd {shlex.quote(ns_workdir)} && "
            f"mkdir -p .codex && chown -R {self.run_uid}:{self.run_gid} .codex && ("
            f"{drop_privs} tmux kill-session -t pawflow 2>/dev/null || true; "
            f"{drop_privs} tmux new-session -d -s pawflow -x 220 -y 50 "
            f"'env HOME={shlex.quote(ns_workdir)} USER=pawflow "
            f"CODEX_HOME={shlex.quote(ns_workdir + '/.codex')} "
            f"PAWFLOW_CCI_SESSION_TOKEN={shlex.quote(session_token)} "
            f"PAWFLOW_CCI_EVENT_URL={shlex.quote(event_url)} "
            f"PAWFLOW_CCI_EVENT_TOKEN={shlex.quote(event_token)} "
            f"PAWFLOW_INTERNAL_TOKEN={shlex.quote(internal_token)} "
            f"PAWFLOW_CCI_INJECTED_PROMPTS={shlex.quote(ns_workdir + '/.pawflow_cci/injected_prompts.jsonl')} "
            f"{endpoint_env}TERM=xterm-256color {quoted}'; "
            f"{drop_privs} tmux set-window-option -t pawflow window-size manual 2>/dev/null || true; "
            f"{drop_privs} tmux set-window-option -t pawflow aggressive-resize off 2>/dev/null || true)")
        result = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "--user", "root", name,
                            "setsid", "--wait", "unshare", "-m",
                            "--propagation", "unchanged", "--",
                            "bash", "-lc", shell],
            capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to start Codex tmux: {result.stderr[:500]}")
        probe = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "--user", self._user_spec(), name,
                            "tmux", "has-session", "-t", "pawflow"],
            capture_output=True, text=True, timeout=10)
        if probe.returncode != 0:
            raise RuntimeError(
                "Codex tmux session exited during startup: "
                f"{(probe.stderr or probe.stdout or '').strip()[:500]}")


class CodexInteractivePool(_CodexInteractiveSpawnMixin,
                           InteractiveClaudeCodePool):
    _instance: Optional["CodexInteractivePool"] = None
    _instance_lock = threading.Lock()

    # `>_ OpenAI Codex (v...)` is the pane's PERMANENT header: it is drawn the
    # instant the TUI starts, long before the input box is interactive. Used as
    # a readiness marker it made the cold-start wait return immediately, and
    # the first paste went into a composer that did not exist yet -- the same
    # header that already fooled the composer scan before beta.72. Every marker
    # here belongs to the input box or its footer.
    _PROMPT_READY_MARKERS = (
        "ask codex", "for shortcuts", "context left")
    _RUNNING_MARKERS = ("esc to interrupt",)
    # The Codex TUI never renders pasted text: it replaces it with an
    # attachment chip. The prompt sitting unsent in the input box therefore
    # looks, to a text probe, exactly like a prompt that was accepted --
    # which is how six pastes stacked up in one composer while every send
    # reported success. The chip is the signal instead.
    _PASTE_CHIP_MARKERS = ("[pasted content", "[image ")
    _COMPOSER_PROMPT_PREFIX = ">"
    # A multi-kilobyte paste takes the Codex TUI longer to ingest than the
    # 0.2s that suits Claude Code; an Enter landing inside that window is
    # swallowed into the attachment as a newline instead of submitting.
    _PASTE_SETTLE_DEFAULT = 1.0

    def _wait_for_prompt_ready(self, name: str, *,
                               timeout: Optional[float] = None) -> bool:
        """Probe Codex readiness once instead of blocking a reused session.

        Codex changes its composer chrome frequently enough that a visual marker
        is not a reliable 45-second gate.  The paste proof below is the actual
        transport check and latches ``prompt_ready`` once the pane reacts.
        Explicit timeouts remain available to diagnostics and tests.
        """
        return super()._wait_for_prompt_ready(
            name, timeout=0.0 if timeout is None else timeout)

    def _pane_diagnostic(self, name: str) -> str:
        """Never copy the Codex pane (and potentially prompts) into logs."""
        return ""

    def _paste_landed(self, state: InteractiveContainer, text: str,
                      before_pane: str = "") -> bool:
        landed = super()._paste_landed(state, text, before_pane)
        if landed:
            # The pane reacting to the paste is stronger evidence than Codex's
            # release-dependent readiness labels.  Keep later turns off the old
            # cold-start gate even when the initial visual probe missed.
            state.prompt_ready = True
        return landed

    def _verify_submitted(self, state: InteractiveContainer, text: str) -> None:
        """Retry Enter until Codex shows submission or a running turn.

        Codex collapses pasted text into a chip, so the inherited
        ``fragment absent == submitted`` rule is never valid here.  When a new
        Codex release boxes the composer and the chip probe cannot locate it,
        absence of a running marker is deliberately treated as inconclusive and
        Enter is retried.  Enter on an empty composer is a no-op; failing to send
        it leaves the real prompt waiting for a human.
        """
        try:
            window = float(os.environ.get(
                "PAWFLOW_CCI_SUBMIT_VERIFY_SECONDS", "6.0") or "6.0")
        except ValueError:
            window = 6.0
        if window <= 0:
            return
        interval = 0.3
        polls = max(1, int(window / interval))
        retries = 0
        for _ in range(polls):
            pane = self._pane_text(state.name)
            holds = self._pane_holds_unsent_paste(pane) if pane else None
            if holds is False or (pane and self._pane_shows_running(pane)):
                return
            if retries >= 3:
                break
            retries += 1
            logger.warning(
                "[codex-interactive] prompt submission not confirmed for %s; "
                "pressing Enter again (retry %d)", state.name, retries)
            if not self.send_keys(state, ["Enter"]):
                break
            time.sleep(interval)
        if retries:
            logger.warning(
                "[codex-interactive] submit verification inconclusive for %s "
                "after %d Enter retries", state.name, retries)

    def __init__(self):
        super().__init__()
        self._idle_ttl = float(os.environ.get(
            "PAWFLOW_CODEX_INTERACTIVE_IDLE_TTL_SECONDS", "1800"))

    def ensure_started(self, client, model: str, user_id: str,
                       conversation_id: str, agent_name: str,
                       before_launch=None) -> InteractiveContainer:
        """Reuse or launch a TUI session without reserving its OAuth slot."""
        idle_ttl = getattr(client, "timeout", None)
        self.ensure_sweeper(
            idle_ttl_seconds=int(idle_ttl) if idle_ttl else None)
        service_id = getattr(client, "_agent_service", "") or ""
        key = (user_id, conversation_id, agent_name, service_id)
        with self._lock:
            existing = self._sessions.get(key)
            if existing and self._is_alive(existing.name):
                existing.last_used = time.time()
                return existing
            if existing:
                self._sessions.pop(key, None)
            if before_launch is not None:
                before_launch()
        state = self._start_new(
            client, model, user_id, conversation_id, agent_name, key,
            pool_index=-1)
        with self._lock:
            self._sessions[key] = state
        return state

    def _recover_container_tokens(self, state: InteractiveContainer) -> None:
        if (not state or state.svc_pool_idx < 0 or not state.service_id
                or not state.workdir):
            return
        try:
            from core.llm_providers._codex_credentials import (
                recover_tokens_from_workdir)
            recover_tokens_from_workdir(
                state.workdir, state.service_id, state.svc_pool_idx,
                user_id=state.user_id, conv_id=state.conv_id)
        except Exception:
            logger.debug(
                "[codex-interactive] token recovery failed", exc_info=True)

    def list_sessions(self, *args, **kwargs) -> list[dict]:
        rows = super().list_sessions(*args, **kwargs)
        for row in rows:
            row["provider"] = "codex-interactive"
        return rows

    def list_sessions_snapshot(self, *args, **kwargs) -> list[dict]:
        rows = super().list_sessions_snapshot(*args, **kwargs)
        for row in rows:
            row["provider"] = "codex-interactive"
        return rows
