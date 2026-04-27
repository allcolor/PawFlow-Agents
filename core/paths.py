"""Centralized path functions for PawFlow storage.

Three top-level areas:
  - repository/  : definitions (agents, skills, tasks, flows, mcps, services, tools)
  - runtime/     : operational state (conversations, deployments, files, memories, ...)
  - system/      : server config (users, sessions, security, secrets, ssl)

Repository scopes:
  - global                        : visible to everyone
  - user   (user_id)              : visible to this user
  - conv   (user_id, conv_id)     : visible in this conversation only
"""

from pathlib import Path

# ── Root ──────────────────────────────────────────────────────────
DATA_DIR = Path("data")
REPOSITORY_DIR = DATA_DIR / "repository"
RUNTIME_DIR = DATA_DIR / "runtime"
SYSTEM_DIR = DATA_DIR / "system"


# ── Repository resource types ────────────────────────────────────
REPO_TYPES = frozenset({
    "agents", "skills", "tasks", "flows",
    "mcps", "services", "tools", "prompts",
    "voice_clones",
})


# ── Repository paths ─────────────────────────────────────────────

def repo_dir(rtype: str, scope: str = "global",
             user_id: str = "", conv_id: str = "") -> Path:
    """Directory for a repository resource type + scope.

    repo_dir("agents", "global")            → data/repository/agents/global/
    repo_dir("agents", "user", "u1")        → data/repository/agents/users/u1/
    repo_dir("agents", "conv", "u1", "c1")  → data/repository/agents/users/u1/c1/
    """
    base = REPOSITORY_DIR / rtype
    if scope == "global":
        return base / "global"
    if scope == "user":
        return base / "users" / user_id
    if scope == "conv":
        return base / "users" / user_id / conv_id
    raise ValueError(f"Invalid scope: {scope!r}")


# Resource types stored as markdown (frontmatter + body)
_MARKDOWN_TYPES = frozenset({"agents", "skills", "prompts"})


def repo_file(rtype: str, name: str, scope: str = "global",
              user_id: str = "", conv_id: str = "") -> Path:
    """Path to a single resource definition file.

    repo_file("agents", "claude", "global")  → data/repository/agents/global/claude.md
    repo_file("mcps", "db", "global")        → data/repository/mcps/global/db.json
    """
    ext = ".md" if rtype in _MARKDOWN_TYPES else ".json"
    return repo_dir(rtype, scope, user_id, conv_id) / f"{name}{ext}"


# ── Flow-specific paths ──────────────────────────────────────────

def flow_package_dir(package: str, scope: str = "global",
                     user_id: str = "", conv_id: str = "") -> Path:
    """Directory for a flow package.

    flow_package_dir("pawflow.demo", "global")
        → data/repository/flows/global/pawflow/demo/
    """
    base = repo_dir("flows", scope, user_id, conv_id)
    return base / package.replace(".", "/")


def flow_dir(package: str, flowname: str, scope: str = "global",
             user_id: str = "", conv_id: str = "") -> Path:
    """Directory for a specific flow (contains latest.json + versions/)."""
    return flow_package_dir(package, scope, user_id, conv_id) / flowname


def flow_latest_file(package: str, flowname: str, scope: str = "global",
                     user_id: str = "", conv_id: str = "") -> Path:
    """Path to latest.json for a flow."""
    return flow_dir(package, flowname, scope, user_id, conv_id) / "latest.json"


def flow_version_file(package: str, flowname: str, version: str,
                      scope: str = "global",
                      user_id: str = "", conv_id: str = "") -> Path:
    """Path to a specific flow version file."""
    return flow_dir(package, flowname, scope, user_id, conv_id) / "versions" / f"{version}.json"


def parse_flow_fqn(fqn: str) -> tuple:
    """Parse 'package.flowname:version' → (package, flowname, version).

    parse_flow_fqn('pawflow.demo.ingest:2.3.1')
        → ('pawflow.demo', 'ingest', '2.3.1')
    parse_flow_fqn('pawflow.demo.ingest')
        → ('pawflow.demo', 'ingest', '')
    """
    version = ""
    if ":" in fqn:
        fqn, version = fqn.rsplit(":", 1)
    package, flowname = fqn.rsplit(".", 1)
    return package, flowname, version


# ── Runtime paths ────────────────────────────────────────────────

# Conversations
CONVERSATIONS_DIR = RUNTIME_DIR / "conversations"

def conversation_dir(user_id: str, conv_id: str) -> Path:
    return CONVERSATIONS_DIR / user_id / conv_id

# Deployments
DEPLOYMENTS_DIR = RUNTIME_DIR / "deployments"

# Files (FileStore)
FILES_DIR = RUNTIME_DIR / "files"

def files_dir(user_id: str, conv_id: str) -> Path:
    return FILES_DIR / user_id / conv_id

# Memories
MEMORIES_DIR = RUNTIME_DIR / "memories"

# Knowledge Graphs
KNOWLEDGE_GRAPHS_DIR = RUNTIME_DIR / "knowledge_graphs"

# Plans
PLANS_DIR = RUNTIME_DIR / "plans"

# Claude Code sessions
CLAUDE_SESSIONS_DIR = RUNTIME_DIR / "sessions" / "claude"

# Codex CLI sessions (per-conv workdir, mirrors CLAUDE_SESSIONS_DIR layout)
CODEX_SESSIONS_DIR = RUNTIME_DIR / "sessions" / "codex"

# Gemini CLI sessions (per-conv workdir, mirrors CLAUDE_SESSIONS_DIR layout)
GEMINI_SESSIONS_DIR = RUNTIME_DIR / "sessions" / "gemini"

# Project graphs (AST cache)
GRAPHS_DIR = RUNTIME_DIR / "graphs"

# Spill (FlowFile large content)
SPILL_DIR = RUNTIME_DIR / "spill"

# Runtime data files
TOKEN_USAGE_FILE = RUNTIME_DIR / "token_usage.json"

# Capability-auth registry (sensitive-route capability tokens, persisted so
# active VNC / terminal / code-server / port-forward sessions survive a
# server restart). See core/capability_auth.py.
CAPABILITIES_FILE = RUNTIME_DIR / "capabilities.json"
# Backward-compatible name for older callers/tests. `capability_auth.init_db()`
# also accepts legacy `.db` paths and maps them to the JSON store.
CAPABILITIES_DB = CAPABILITIES_FILE
POLL_SCHEDULE_FILE = RUNTIME_DIR / "poll_schedule.json"
GATEWAY_BANS_FILE = RUNTIME_DIR / "gateway_bans.json"


# ── System paths ─────────────────────────────────────────────────

USERS_FILE = SYSTEM_DIR / "users.json"
SESSIONS_FILE = SYSTEM_DIR / "sessions.json"
SECURITY_FILE = SYSTEM_DIR / "security.json"
SECRET_KEY_FILE = SYSTEM_DIR / "secret.key"
SERVER_ID_FILE = SYSTEM_DIR / "server_id"
SSL_DIR = SYSTEM_DIR / "ssl"

# System config files (expression resolver, etc.)
GLOBAL_PARAMS_FILE = SYSTEM_DIR / "global_parameters.json"
GLOBAL_SECRETS_FILE = SYSTEM_DIR / "global_secrets.json"
LLM_PROFILES_FILE = SYSTEM_DIR / "llm_profiles.json"
PARAMETER_CONTEXTS_FILE = SYSTEM_DIR / "parameter_contexts.json"
TRIGGERS_FILE = SYSTEM_DIR / "triggers.json"

# User-specific system config (secrets, params, oauth)
USER_CONFIG_DIR = SYSTEM_DIR / "users"

def user_secrets_path(user_id: str) -> Path:
    return USER_CONFIG_DIR / user_id / "secrets.json"

def user_params_path(user_id: str) -> Path:
    return USER_CONFIG_DIR / user_id / "parameters.json"

def user_oauth_path(user_id: str) -> Path:
    return USER_CONFIG_DIR / user_id / "oauth_tokens.json"


# ── Helpers ──────────────────────────────────────────────────────

