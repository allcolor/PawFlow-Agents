"""Centralized path constants for all PawFlow data and config files."""

from pathlib import Path

# ── Root directories ──────────────────────────────────────────────
DATA_DIR = Path("data")
CONFIG_DIR = DATA_DIR / "config"

# ── Config files ──────────────────────────────────────────────────
AGENTS_FILE = CONFIG_DIR / "agents.json"
AGENT_SECRETS_FILE = CONFIG_DIR / "agent_secrets.json"
AGENT_VARIABLES_FILE = CONFIG_DIR / "agent_variables.json"
GLOBAL_PARAMS_FILE = CONFIG_DIR / "global_parameters.json"
GLOBAL_SECRETS_FILE = CONFIG_DIR / "global_secrets.json"
GLOBAL_SERVICES_FILE = CONFIG_DIR / "global_services.json"
LLM_PROFILES_FILE = CONFIG_DIR / "llm_profiles.json"
MCP_SERVERS_FILE = CONFIG_DIR / "mcp_servers.json"
PROMPTS_FILE = CONFIG_DIR / "prompts.json"
SECRET_KEY_FILE = CONFIG_DIR / "secret.key"
SESSIONS_FILE = CONFIG_DIR / "sessions.json"
SKILLS_FILE = CONFIG_DIR / "skills.json"
TASK_DEFS_FILE = CONFIG_DIR / "task_defs.json"
TASK_TEMPLATES_FILE = CONFIG_DIR / "task_templates.json"
TRIGGERS_FILE = CONFIG_DIR / "triggers.json"
USERS_FILE = CONFIG_DIR / "users.json"
SECURITY_FILE = CONFIG_DIR / "security.json"
SECRETS_FILE = CONFIG_DIR / "secrets.json"
PARAMETER_CONTEXTS_FILE = CONFIG_DIR / "parameter_contexts.json"

# ── Config subdirectories ─────────────────────────────────────────
USER_CONFIG_DIR = CONFIG_DIR / "users"
USER_SERVICES_DIR = CONFIG_DIR / "user_services"
FLOW_VERSIONS_DIR = CONFIG_DIR / "flow_versions"
SSL_DIR = CONFIG_DIR / "ssl"

# ── Data directories ──────────────────────────────────────────────
CONVERSATIONS_DIR = DATA_DIR / "conversations"
DEPLOYMENTS_DIR = DATA_DIR / "deployments"
AGENT_FLOWS_DIR = DATA_DIR / "agent_flows"
AGENT_TEMPLATES_DIR = DATA_DIR / "agent_templates"
DYNAMIC_TOOLS_DIR = DATA_DIR / "dynamic_tools"
FILES_DIR = DATA_DIR / "files"
GRAPHS_DIR = DATA_DIR / "graphs"
KNOWLEDGE_GRAPHS_DIR = DATA_DIR / "knowledge_graphs"
MEMORIES_DIR = DATA_DIR / "memories"
PLANS_DIR = DATA_DIR / "plans"
POLL_SCHEDULE_DIR = DATA_DIR / "poll_schedule"
CLAUDE_SESSIONS_DIR = DATA_DIR / "claude_sessions"

# ── Data files ────────────────────────────────────────────────────
IDENTITY_MAPPINGS_FILE = DATA_DIR / "identity_mappings.json"
TOKEN_USAGE_FILE = DATA_DIR / "token_usage.json"
SERVER_ID_FILE = DATA_DIR / "server_id"
GATEWAY_BANS_FILE = DATA_DIR / "gateway_bans.json"


# ── Defaults (seed data, tracked in git) ──────────────────────
DEFAULTS_DIR = Path("defaults")


def ensure_seed_file(target: Path, default_name: str) -> None:
    """Copy a seed file from defaults/ to target if target doesn't exist."""
    if target.exists():
        return
    source = DEFAULTS_DIR / default_name
    if not source.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy2(source, target)


def user_secrets_path(user_id: str) -> Path:
    """Path to a user's secrets.json."""
    return USER_CONFIG_DIR / user_id / "secrets.json"


def user_params_path(user_id: str) -> Path:
    """Path to a user's parameters.json."""
    return USER_CONFIG_DIR / user_id / "parameters.json"
