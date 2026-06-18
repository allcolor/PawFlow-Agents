"""Shared module-level helpers/consts/thread-local for the tool_relay_service split."""

import logging
import threading
from typing import Any


logger = logging.getLogger(__name__)


_RELAY_TRANSPORT_RETRY_ATTEMPTS = 5
_RELAY_TRANSPORT_RETRY_DELAY_SECONDS = 5.0
_RELAY_TRANSPORT_RETRY_EXHAUSTED_MARKER = "Relay transport retry attempts exhausted"
_RELAY_TRANSPORT_ERROR_MARKERS = (
    "Relay disconnected",
    "Relay not connected",
    "Failed to send to relay",
)
_RELAY_TRANSPORT_RESULT_PREFIXES = (
    "Error reading",
    "Error writing",
    "Error editing",
    "Error copying",
    "Error deleting",
    "Error executing command",
    "Error: Relay disconnected",
    "Error: Relay not connected",
    "Error: Failed to send to relay",
)


def _contains_relay_transport_marker(text: str) -> bool:
    return any(marker in text for marker in _RELAY_TRANSPORT_ERROR_MARKERS)


def _is_relay_transport_result(result: Any) -> bool:
    if not isinstance(result, str):
        return False
    text = result.strip()
    if _RELAY_TRANSPORT_RETRY_EXHAUSTED_MARKER in text:
        return False
    if not _contains_relay_transport_marker(text):
        return False
    return any(text.startswith(prefix) for prefix in _RELAY_TRANSPORT_RESULT_PREFIXES)


def _is_relay_transport_error(exc: Exception) -> bool:
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current)
        if _RELAY_TRANSPORT_RETRY_EXHAUSTED_MARKER in text:
            return False
        if _contains_relay_transport_marker(text):
            return True
        current = current.__cause__ or current.__context__
    return False


def _resolve_vars_in_args(arguments: dict, env: dict, skip_keys: set = None):
    """Resolve $VAR and ${VAR} patterns in all string values of arguments.

    Mutates arguments in-place. Recurses into dicts and lists.
    Skips keys starting with _ (internal params like _secret_env).
    Skips keys in skip_keys (e.g. 'command' for bash — shell resolves itself).
    """
    import re
    _skip = skip_keys or set()
    _pattern = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)')

    def _replace(match):
        name = match.group(1) or match.group(2)
        return env.get(name, env.get(name.upper(), match.group(0)))

    def _resolve(obj):
        if isinstance(obj, str):
            return _pattern.sub(_replace, obj)
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.startswith('_') or k in _skip:
                    continue
                obj[k] = _resolve(v)
            return obj
        if isinstance(obj, list):
            return [_resolve(item) for item in obj]
        return obj

    _resolve(arguments)


def _redact_secrets(text: str, secret_values: set,
                    secret_names: dict = None) -> str:
    """Replace exact occurrences of secret values in text with a redaction marker.

    Only exact matches — no partial prefix/suffix matching (causes false
    positives when secrets are substrings of other data like verification codes).
    """
    if len(text) > 1_000_000 or '\x00' in text:
        return text
    _names = secret_names or {}
    for val in secret_values:
        if val in text:
            _varname = _names.get(val, "")
            _marker = f"<****Redacted — use ${_varname}****>" if _varname else "<****Redacted****>"
            text = text.replace(val, _marker)
    return text


def resolve_secrets_env(user_id: str, conversation_id: str) -> dict:
    """Resolve ALL variables + secrets into a flat dict for env injection.

    Cascade: global → user → conversation (later overrides earlier).
    Both params (variables) AND secrets are included.
    Returns dict of {KEY: value}. Keys are uppercased.
    """
    from core.config_store import ConfigStore

    env = {}

    from core.paths import GLOBAL_PARAMS_FILE, GLOBAL_SECRETS_FILE, USER_CONFIG_DIR

    # ── Global variables ──
    for k, cv in ConfigStore.load_params(GLOBAL_PARAMS_FILE).items():
        env[k.upper()] = cv.value if hasattr(cv, 'value') else str(cv)

    # ── Global secrets ──
    for k, cv in ConfigStore.load_secrets(GLOBAL_SECRETS_FILE).items():
        env[k.upper()] = cv.value if hasattr(cv, 'value') else str(cv)

    # ── User variables (override global) ──
    if user_id:
        for k, cv in ConfigStore.load_params(USER_CONFIG_DIR / user_id / "params.json").items():
            env[k.upper()] = cv.value if hasattr(cv, 'value') else str(cv)

    # ── User secrets (override global) ──
    if user_id:
        for k, cv in ConfigStore.load_secrets(USER_CONFIG_DIR / user_id / "secrets.json").items():
            env[k.upper()] = cv.value if hasattr(cv, 'value') else str(cv)

    # ── Conversation variables + secrets (override user) ──
    if conversation_id:
        try:
            from core.conversation_store import ConversationStore
            from core.secrets import get_secrets_manager
            store = ConversationStore.instance()
            sm = get_secrets_manager()

            # Conv params (variables)
            _conv_params = store.get_extra(conversation_id, "conv_params") or {}
            for k, v in _conv_params.items():
                env[k.upper()] = str(v)

            # Conv secrets
            _conv_secrets = store.get_extra(conversation_id, "conv_secrets") or {}
            for k, v in _conv_secrets.items():
                try:
                    env[k.upper()] = sm.decrypt(v) if isinstance(v, str) and v.startswith("enc:") else str(v)
                except Exception:
                    env[k.upper()] = str(v)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    return env


def resolve_secret_values(user_id: str, conversation_id: str) -> tuple:
    """Resolve ONLY secret values for redaction (not variables).

    Returns (secret_values: set, secret_names: dict{value→key}).
    """
    from core.config_store import ConfigStore

    values = set()
    names = {}

    from core.paths import GLOBAL_SECRETS_FILE, USER_CONFIG_DIR

    # Global secrets
    for k, cv in ConfigStore.load_secrets(GLOBAL_SECRETS_FILE).items():
        v = cv.value if hasattr(cv, 'value') else str(cv)
        if v and len(v) >= 4:
            values.add(v)
            names[v] = k.upper()

    # User secrets
    if user_id:
        for k, cv in ConfigStore.load_secrets(USER_CONFIG_DIR / user_id / "secrets.json").items():
            v = cv.value if hasattr(cv, 'value') else str(cv)
            if v and len(v) >= 4:
                values.add(v)
                names[v] = k.upper()

    # Conversation secrets
    if conversation_id:
        try:
            from core.conversation_store import ConversationStore
            from core.secrets import get_secrets_manager
            _raw = ConversationStore.instance().get_extra(
                conversation_id, "conv_secrets") or {}
            sm = get_secrets_manager()
            for k, v in _raw.items():
                try:
                    v = sm.decrypt(v) if isinstance(v, str) and v.startswith("enc:") else str(v)
                except Exception:
                    v = str(v)
                if v and len(v) >= 4:
                    values.add(v)
                    names[v] = k.upper()
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    return values, names


_thread_local = threading.local()


def _set_current_cancel_event(evt):
    _thread_local.cancel_event = evt


def current_cancel_event():
    """Return the cancel Event for the currently-executing tool, or
    None when not running inside a tool dispatch (tests, direct calls).
    """
    return getattr(_thread_local, "cancel_event", None)


def _set_current_kill_hooks(hooks_list):
    _thread_local.kill_hooks = hooks_list


def register_kill_hook(callback) -> None:
    """Register a callable to be invoked when the current tool is killed.

    Tools that spawn external resources (subprocess.Popen, websockets,
    HTTP sessions) MUST register a hook so FORCE STOP can shut them
    down explicitly. The hook runs from `cancel_agent` and should be
    fast and idempotent (terminate, close, signal — not block).

    No-op when called outside a tool dispatch.
    """
    hooks = getattr(_thread_local, "kill_hooks", None)
    if hooks is None:
        return
    hooks.append(callback)
    cancel_evt = current_cancel_event()
    if cancel_evt is not None and cancel_evt.is_set():
        callback()
