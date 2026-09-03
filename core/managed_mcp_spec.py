"""Managed MCP CLI providers: the executable contract in one table.

``cc_mcp``, ``codex_mcp`` and ``agy_mcp`` run the official interactive CLIs
exactly as the ``*-interactive`` providers do (managed container, tmux paste,
PawFlow MCP tools), but read the turn's outcome from the CLI's native lifecycle
hooks instead of a vendor-traffic MITM proxy. This leaf module carries no
imports from the provider package so the pools, the event service and the
actions can consult it without import cycles.

Every capability claim the UI, the API and the docs make about these providers
must come from :data:`MANAGED_MCP_PROVIDERS`; nothing is inferred elsewhere.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

#: Observation mode of a managed interactive session.
MITM_OBSERVATION_MODE = "mitm"
MANAGED_MCP_OBSERVATION_MODE = "managed_mcp"

#: Telemetry values that are honest about their origin.
TELEMETRY_UNAVAILABLE = "unavailable"
FINAL_SOURCE_STOP_HOOK = "stop_hook"

#: Bump when the managed launch/config shape changes in a way that makes a live
#: session incompatible with a new turn (hook set, MCP config, env contract).
#: Stored on the session and compared on reuse so a mismatch recreates it.
MANAGED_MCP_LAUNCH_REVISION = "1"


@dataclass(frozen=True)
class ManagedMcpProviderSpec:
    provider: str
    label: str
    cli: str
    hook_client: str
    pool_family: str
    credential_family: str
    short_alias: str
    terminal_action: str
    terminal_kind: str
    live_preempt: bool
    builtin_tools_visible: bool
    usage_source: str
    context_source: str
    available: bool
    unavailable_reason: str = ""
    thinking_source: str = TELEMETRY_UNAVAILABLE
    final_source: str = FINAL_SOURCE_STOP_HOOK
    manual_capture: bool = True
    attachments: bool = True

    def capabilities(self) -> dict[str, object]:
        data = asdict(self)
        data["observation_mode"] = MANAGED_MCP_OBSERVATION_MODE
        data["launch_revision"] = MANAGED_MCP_LAUNCH_REVISION
        data["text_streaming"] = "final_only"
        return data


MANAGED_MCP_PROVIDERS: dict[str, ManagedMcpProviderSpec] = {
    "cc_mcp": ManagedMcpProviderSpec(
        provider="cc_mcp",
        label="Claude Code \u2014 MCP hooks",
        cli="claude",
        hook_client="cc",
        pool_family="claude-code-interactive",
        credential_family="claude-code",
        short_alias="ccmcp",
        terminal_action="open_cc_interactive_terminal",
        terminal_kind="cci",
        # Live preemption is advertised only once the server-owned request
        # state proves correlation for this CLI (plan section 12). Not yet.
        live_preempt=False,
        # CCI denies every built-in tool, so MCP is the only tool path and
        # every call is visible through ToolRelayService.
        builtin_tools_visible=True,
        usage_source=TELEMETRY_UNAVAILABLE,
        context_source=TELEMETRY_UNAVAILABLE,
        available=True,
    ),
    "codex_mcp": ManagedMcpProviderSpec(
        provider="codex_mcp",
        label="Codex \u2014 MCP hooks",
        cli="codex",
        hook_client="codex",
        pool_family="codex-interactive",
        credential_family="codex-app-server",
        short_alias="codexmcp",
        terminal_action="open_cc_interactive_terminal",
        terminal_kind="codexi",
        live_preempt=False,
        # Codex keeps its own shell and file tools; without a vendor stream
        # nothing reports them. Only PawFlow MCP calls are visible.
        builtin_tools_visible=False,
        # Native rollout ``token_count`` is local session data, not vendor
        # traffic, so the existing reader stays acceptable.
        usage_source="codex_rollout_token_count",
        context_source="codex_rollout_token_count",
        available=True,
    ),
    "agy_mcp": ManagedMcpProviderSpec(
        provider="agy_mcp",
        label="Antigravity \u2014 MCP hooks",
        cli="agy",
        hook_client="agy",
        pool_family="antigravity-interactive",
        credential_family="gemini",
        short_alias="agymcp",
        terminal_action="open_antigravity_interactive_terminal",
        terminal_kind="agy",
        live_preempt=False,
        builtin_tools_visible=False,
        usage_source=TELEMETRY_UNAVAILABLE,
        context_source=TELEMETRY_UNAVAILABLE,
        # Probe-gated: the supported ``agy`` build has not proven a reliable
        # final-answer source through its hooks (see the WP0 probe record in
        # docs/CLAUDE_CODE_INTERACTIVE.md). Registration stays refused until
        # the probe passes in CI.
        available=False,
        unavailable_reason=(
            "agy_mcp is probe-gated: the official agy CLI has not proven a "
            "native final-answer hook field or transcript source"),
    ),
}

MANAGED_MCP_PROVIDER_NAMES = tuple(MANAGED_MCP_PROVIDERS)


def managed_mcp_spec(provider: str) -> ManagedMcpProviderSpec | None:
    return MANAGED_MCP_PROVIDERS.get(str(provider or ""))


def is_managed_mcp_provider(provider: str) -> bool:
    return str(provider or "") in MANAGED_MCP_PROVIDERS


def managed_mcp_observation_mode(provider: str) -> str:
    """Observation mode a pool must launch with for ``provider``."""
    return (MANAGED_MCP_OBSERVATION_MODE if is_managed_mcp_provider(provider)
            else MITM_OBSERVATION_MODE)


def managed_mcp_pool_family(provider: str) -> str:
    """The interactive provider whose pool/session machinery ``provider`` reuses.

    Returns ``provider`` unchanged for anything that is not a managed MCP
    provider, so callers can key on one name for both paths.
    """
    spec = managed_mcp_spec(provider)
    return spec.pool_family if spec else str(provider or "")


def managed_mcp_source_input(provider: str) -> str:
    """The ``source.input`` tag of a message captured from this CLI's tmux."""
    spec = managed_mcp_spec(provider)
    if spec is None:
        return ""
    return f"{spec.provider}_tmux"


def managed_mcp_capability_matrix() -> dict[str, dict[str, object]]:
    """Honest per-provider capability claims for UI, API and docs."""
    return {name: spec.capabilities()
            for name, spec in MANAGED_MCP_PROVIDERS.items()}


def require_available(provider: str) -> ManagedMcpProviderSpec:
    """Return the spec or raise ``ValueError`` when the provider is gated."""
    spec = managed_mcp_spec(provider)
    if spec is None:
        raise ValueError(f"{provider!r} is not a managed MCP provider")
    if not spec.available:
        raise ValueError(spec.unavailable_reason
                         or f"{provider} is not available")
    return spec
