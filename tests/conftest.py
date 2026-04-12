"""Global test fixtures — ensure tests NEVER touch real data/.

All path constants in core.paths are redirected to a temporary directory
for the entire test session. This prevents any test from polluting the
user's data/repository, data/runtime, or data/system directories.
"""

import shutil
import pytest
from pathlib import Path


@pytest.fixture(autouse=True, scope="session")
def _isolate_data_dir(tmp_path_factory):
    """Redirect ALL data paths to a session-scoped temp directory."""
    import core.paths as paths

    tmp = tmp_path_factory.mktemp("pawflow_test_data")

    # Save originals
    originals = {}
    path_attrs = [
        "DATA_DIR", "REPOSITORY_DIR", "RUNTIME_DIR", "SYSTEM_DIR",
        "CONVERSATIONS_DIR", "DEPLOYMENTS_DIR", "FILES_DIR",
        "MEMORIES_DIR", "KNOWLEDGE_GRAPHS_DIR", "PLANS_DIR",
        "CLAUDE_SESSIONS_DIR", "GRAPHS_DIR", "SPILL_DIR",
        "TOKEN_USAGE_FILE", "POLL_SCHEDULE_FILE", "GATEWAY_BANS_FILE",
        "USERS_FILE", "SESSIONS_FILE", "SECURITY_FILE",
        "SECRET_KEY_FILE", "SERVER_ID_FILE", "SSL_DIR",
        "GLOBAL_PARAMS_FILE", "GLOBAL_SECRETS_FILE",
        "LLM_PROFILES_FILE", "PARAMETER_CONTEXTS_FILE",
        "TRIGGERS_FILE", "USER_CONFIG_DIR",
    ]

    for attr in path_attrs:
        if hasattr(paths, attr):
            originals[attr] = getattr(paths, attr)

    # Compute new paths relative to tmp
    data_dir = tmp / "data"
    repo_dir = data_dir / "repository"
    runtime_dir = data_dir / "runtime"
    system_dir = data_dir / "system"

    paths.DATA_DIR = data_dir
    paths.REPOSITORY_DIR = repo_dir
    paths.RUNTIME_DIR = runtime_dir
    paths.SYSTEM_DIR = system_dir
    paths.CONVERSATIONS_DIR = runtime_dir / "conversations"
    paths.DEPLOYMENTS_DIR = runtime_dir / "deployments"
    paths.FILES_DIR = runtime_dir / "files"
    paths.MEMORIES_DIR = runtime_dir / "memories"
    paths.KNOWLEDGE_GRAPHS_DIR = runtime_dir / "knowledge_graphs"
    paths.PLANS_DIR = runtime_dir / "plans"
    paths.CLAUDE_SESSIONS_DIR = runtime_dir / "sessions" / "claude"
    paths.GRAPHS_DIR = runtime_dir / "graphs"
    paths.SPILL_DIR = runtime_dir / "spill"
    paths.TOKEN_USAGE_FILE = runtime_dir / "token_usage.json"
    paths.POLL_SCHEDULE_FILE = runtime_dir / "poll_schedule.json"
    paths.GATEWAY_BANS_FILE = runtime_dir / "gateway_bans.json"
    paths.USERS_FILE = system_dir / "users.json"
    paths.SESSIONS_FILE = system_dir / "sessions.json"
    paths.SECURITY_FILE = system_dir / "security.json"
    paths.SECRET_KEY_FILE = system_dir / "secret.key"
    paths.SERVER_ID_FILE = system_dir / "server_id"
    paths.SSL_DIR = system_dir / "ssl"
    paths.GLOBAL_PARAMS_FILE = system_dir / "global_parameters.json"
    paths.GLOBAL_SECRETS_FILE = system_dir / "global_secrets.json"
    paths.LLM_PROFILES_FILE = system_dir / "llm_profiles.json"
    paths.PARAMETER_CONTEXTS_FILE = system_dir / "parameter_contexts.json"
    paths.TRIGGERS_FILE = system_dir / "triggers.json"
    paths.USER_CONFIG_DIR = system_dir / "users"

    # Create essential directories
    for d in [repo_dir, runtime_dir, system_dir,
              paths.USER_CONFIG_DIR, paths.CONVERSATIONS_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    # Copy global repository definitions so structural tests work
    import shutil as _shutil
    real_repo = originals["REPOSITORY_DIR"]
    if real_repo.exists():
        for sub in real_repo.iterdir():
            if sub.is_dir():
                dst = repo_dir / sub.name
                if not dst.exists():
                    _shutil.copytree(sub, dst,
                                     ignore=_shutil.ignore_patterns("users"))

    # Reset singletons that cache paths
    try:
        from core.plan_store import PlanStore
        PlanStore._instance = None
    except Exception:
        pass
    try:
        from core.conversation_store import ConversationStore
        ConversationStore.reset()
    except Exception:
        pass

    yield tmp

    # Restore originals
    for attr, val in originals.items():
        setattr(paths, attr, val)
