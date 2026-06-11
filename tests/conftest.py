"""Global test fixtures — ensure tests NEVER touch real data/.

All path constants in core.paths are redirected to a temporary directory
for the entire test session. This prevents any test from polluting the
user's data/repository, data/runtime, or data/system directories.
"""

import shutil
import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def _neutralize_cci_realtime_waits(monkeypatch):
    """Default the CCI tmux-submit settle/verify waits to 0 in tests.

    `send_text`/`send_interrupt` sleep a settle delay and then poll the tmux
    pane for up to several seconds to confirm submission. Those are real
    wall-clock waits; left at their production defaults they make every test
    that drives a send pay seconds each, ballooning the suite. Tests that
    specifically exercise the settle/verify behaviour set their own values,
    which override these defaults for that test.
    """
    monkeypatch.setenv("PAWFLOW_CCI_PASTE_SETTLE_SECONDS", "0")
    monkeypatch.setenv("PAWFLOW_CCI_SUBMIT_VERIFY_SECONDS", "0")


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
        "CLAUDE_SESSIONS_DIR", "CODEX_SESSIONS_DIR",
        "GEMINI_SESSIONS_DIR", "GRAPHS_DIR", "SPILL_DIR",
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
    paths.CODEX_SESSIONS_DIR = runtime_dir / "sessions" / "codex"
    paths.GEMINI_SESSIONS_DIR = runtime_dir / "sessions" / "gemini"
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

    # Reset module-level caches
    try:
        import core.stream as _stream_mod
        _stream_mod._spill_dir = None
    except Exception:
        pass

    # Reset singletons that cache paths
    for _reset in [
        lambda: __import__('core.plan_store', fromlist=['PlanStore']).PlanStore.__setattr__('_instance', None),
        lambda: __import__('core.conversation_store', fromlist=['ConversationStore']).ConversationStore.reset(),
        lambda: __import__('core.deployment_registry', fromlist=['DeploymentRegistry']).DeploymentRegistry.reset(),
        lambda: __import__('core.file_store', fromlist=['FileStore']).FileStore.reset(),
        lambda: __import__('core.resource_store', fromlist=['ResourceStore']).ResourceStore.reset(),
        lambda: __import__('core.poll_scheduler', fromlist=['PollScheduler']).PollScheduler.reset(),
    ]:
        try:
            _reset()
        except Exception:
            pass

    # Auto-fill missing msg_id/ts in test messages: fixtures predate the
    # strict invariant. Production code paths always create both at
    # message creation — this only patches the validator used by tests.
    import uuid as _uuid_fill
    import time as _time_fill
    from core import conversation_store as _cs_mod

    _orig_validate = _cs_mod.ConversationStore._validate_message

    @staticmethod
    def _validate_with_fill(m):
        # Tests predate the mandatory msg_id + ts producer contract.
        # Fill in defaults inside the session so test message dicts
        # survive _validate_message. seq is NOT filled — it is the
        # on-disk line index, assigned by _stamp_line at write time.
        if m.get("role") != "system":
            if not m.get("msg_id"):
                m["msg_id"] = _uuid_fill.uuid4().hex[:12]
            if not m.get("ts") and not m.get("timestamp"):
                m["ts"] = _time_fill.time()
        return _orig_validate(m)

    _cs_mod.ConversationStore._validate_message = _validate_with_fill

    # Same story for FileStore.store — tests predate mandatory user_id /
    # conversation_id. Provide test defaults only inside this session.
    from core import file_store as _fs_mod

    _orig_store = _fs_mod.FileStore.store

    def _store_with_defaults(self, filename, content,
                              content_type="application/octet-stream",
                              conversation_id="", user_id="", **kw):
        if not user_id:
            user_id = "test_user"
        if not conversation_id:
            conversation_id = "test_conv"
        return _orig_store(self, filename, content, content_type,
                            conversation_id=conversation_id,
                            user_id=user_id, **kw)

    _fs_mod.FileStore.store = _store_with_defaults

    _orig_get = _fs_mod.FileStore.get

    def _get_with_defaults(self, file_id, user_id="", gateway_key=""):
        return _orig_get(self, file_id,
                          user_id=user_id or "test_user",
                          gateway_key=gateway_key)

    _fs_mod.FileStore.get = _get_with_defaults

    # Tests supply SubAgentExecutor(client, registry) with no resolver
    # and rely on the positional `client` as the delegate LLM. Wire a
    # default resolver + default llm_service on AgentTask in tests only.
    from core import agent_executor as _ae_mod

    _orig_exec_init = _ae_mod.SubAgentExecutor.__init__

    def _exec_init_with_resolver(self, client, registry, **kw):
        if "client_resolver" not in kw or kw.get("client_resolver") is None:
            kw["client_resolver"] = lambda _svc, _uid, _c=client: (_c, _svc or "test_svc")
        return _orig_exec_init(self, client, registry, **kw)

    _ae_mod.SubAgentExecutor.__init__ = _exec_init_with_resolver

    # Inject llm_service at execute_agent time (not at AgentTask() creation
    # — that would break tests asserting default="").
    _orig_execute_agent = _ae_mod.SubAgentExecutor.execute_agent

    def _execute_agent_with_default_svc(self, task):
        if not getattr(task, "llm_service", ""):
            task.llm_service = "test_svc"
        return _orig_execute_agent(self, task)

    _ae_mod.SubAgentExecutor.execute_agent = _execute_agent_with_default_svc

    yield tmp

    _cs_mod.ConversationStore._validate_message = _orig_validate
    _fs_mod.FileStore.store = _orig_store
    _fs_mod.FileStore.get = _orig_get
    _ae_mod.SubAgentExecutor.__init__ = _orig_exec_init
    _ae_mod.SubAgentExecutor.execute_agent = _orig_execute_agent

    # Restore originals
    for attr, val in originals.items():
        setattr(paths, attr, val)


# Per-test reset of process-level seq state. tests/ redirects all paths
# to tmpdir so disk state resets naturally — but _msg_seq_persisted is
# a module-level cache in core.llm_client. Without this reset, a test
# that reuses a cid ("c1", "test_conv") sees a counter bootstrapped
# from a prior test's transcript, which has been wiped.
@pytest.fixture(autouse=True)
def _reset_seq_state():
    from core import llm_client as _lc
    _lc._msg_seq_persisted.clear()
    yield
    _lc._msg_seq_persisted.clear()
