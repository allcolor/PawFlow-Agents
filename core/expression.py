# Expression Language

"""
Moteur de résolution d'expressions ${...} pour PawFlow.
Résout les variables depuis les attributs FlowFile, les paramètres de flow,
l'environnement, et les secrets chiffrés.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

_SECRETS_FILE = Path("config/agent_secrets.json")
_VARIABLES_FILE = Path("config/agent_variables.json")
_GLOBAL_PARAMS_FILE = Path("config/global_parameters.json")
_GLOBAL_SECRETS_FILE = Path("config/global_secrets.json")
_USER_CONFIG_DIR = Path("config/users")


def _load_variables() -> Dict[str, str]:
    """Load variables from the agent variables store (plaintext)."""
    if not _VARIABLES_FILE.exists():
        return {}
    try:
        raw = json.loads(_VARIABLES_FILE.read_text(encoding="utf-8"))
        return {k: v.get("value", "") if isinstance(v, dict) else str(v)
                for k, v in raw.items()}
    except Exception as e:
        logger.warning(f"Failed to load variables: {e}")
        return {}


def _load_secrets() -> Dict[str, str]:
    """Load and decrypt secrets from the agent secrets store."""
    from core.config_store import ConfigStore
    values = ConfigStore.load_secrets(_SECRETS_FILE)
    return {k: str(v) for k, v in values.items()}


def _load_global_parameters() -> Dict[str, str]:
    """Load global parameters via ConfigStore (supports spilled values)."""
    from core.config_store import ConfigStore
    values = ConfigStore.load_params(_GLOBAL_PARAMS_FILE)
    return values  # Dict[str, ConfigValue] — str() works for small values


def _load_global_secrets() -> Dict[str, str]:
    """Load and decrypt global secrets via ConfigStore."""
    from core.config_store import ConfigStore
    values = ConfigStore.load_secrets(_GLOBAL_SECRETS_FILE)
    return values  # Dict[str, ConfigValue]


def _load_user_parameters(username: str) -> Dict[str, str]:
    """Load user-level parameters via ConfigStore."""
    from core.config_store import ConfigStore
    path = _USER_CONFIG_DIR / username / "parameters.json"
    values = ConfigStore.load_params(path)
    return values  # Dict[str, ConfigValue]


def _load_user_secrets(username: str) -> Dict[str, str]:
    """Load and decrypt user-level secrets via ConfigStore."""
    from core.config_store import ConfigStore
    path = _USER_CONFIG_DIR / username / "secrets.json"
    values = ConfigStore.load_secrets(path)
    return values  # Dict[str, ConfigValue]


def _substitute_expressions(template: str, resolver_fn) -> str:
    """Replace ${...} expressions, supporting nested ${...} in arguments.

    Unlike re.sub with [^}]+, this properly handles balanced braces:
    ${global.x:then(${global.y})} → finds the outer ${...} correctly.
    """
    result = []
    i = 0
    while i < len(template):
        if template[i] == '$' and i + 1 < len(template) and template[i + 1] == '{':
            # Find matching closing brace (balanced)
            depth = 1
            j = i + 2
            while j < len(template) and depth > 0:
                if template[j] == '{' and j > 0 and template[j - 1] == '$':
                    depth += 1
                elif template[j] == '}':
                    depth -= 1
                j += 1
            if depth == 0:
                inner = template[i + 2:j - 1]
                resolved = resolver_fn(inner)
                result.append(str(resolved))
                i = j
            else:
                result.append(template[i])
                i += 1
        else:
            result.append(template[i])
            i += 1
    return "".join(result)


class LazyResolveDict(dict):
    """Dict wrapper that resolves ${...} expressions on every .get() call.

    Use this anywhere config values may contain expressions. Values are
    resolved fresh each time — never cached. Changes to global parameters
    or secrets are picked up immediately.
    """

    def __getitem__(self, key):
        val = super().__getitem__(key)
        return self._resolve(val)

    def get(self, key, default=None):
        try:
            val = super().__getitem__(key)
            return self._resolve(val)
        except KeyError:
            return default

    @staticmethod
    def _resolve(val):
        if isinstance(val, str) and "${" in val:
            try:
                return resolve_expression(val)
            except Exception:
                return val
        return val


def resolve_expression(template: str, attributes: Optional[Dict[str, str]] = None,
                       parameters: Optional[Dict[str, Any]] = None,
                       owner: Optional[str] = None,
                       conversation_id: Optional[str] = None,
                       _depth: int = 0) -> str:
    """
    Résoudre toutes les expressions ${...} dans un template.

    Simplified syntax: ${name} — no scope prefix needed.
    Resolution order: secrets → params → legacy vars → attrs.

    Secrets cascade:  conv → user → global
    Params cascade:   flow → conv → user → global → env
    (env = OS environment variables, last fallback in params cascade)

    Examples:
        ${api_key}           → secrets first, then params, then env
        ${PATH}              → found in env if not in secrets/params
        ${api_key:default("sk-xxx")} → with fallback
        ${PATH:!important(env)} → OS env ONLY, skip other scopes

    Force exact scope:
        ${key:!important(global)}  → global params ONLY
        ${key:!important(user)}    → user params ONLY
        ${key:!important(conv)}    → conv params ONLY
        ${key:!important(env)}     → OS environment ONLY

    No prefixes — ${name} resolves everything through the cascade.

    Résolution récursive : si la valeur résolue contient des ${...},
    elles sont résolues à leur tour (max 10 niveaux).

    Args:
        template: Chaîne avec expressions ${...}
        attributes: Attributs du FlowFile
        parameters: Paramètres du flow
        owner: Owner username for user-level resolution (None = skip user-level)
        conversation_id: Conversation ID for conv-level resolution

    Returns:
        Chaîne avec expressions résolues
    """
    if '${' not in template:
        return template
    if _depth > 10:
        logger.warning("Expression recursion depth exceeded (>10)")
        return template

    attrs = attributes or {}
    params = parameters or {}
    # Lazy-load secrets and variables only if needed
    secrets = None
    variables = None
    global_params = None
    global_secrets = None
    user_params = None
    user_secrets = None
    conv_params = None
    conv_secrets = None

    def _resolve_value(value):
        """Convert a value (possibly ConfigValue) to string for interpolation."""
        from core.config_value import ConfigValue
        if isinstance(value, ConfigValue):
            if value.is_large:
                logger.warning("Large ConfigValue cannot be interpolated in expressions")
                return None  # Signal: skip interpolation
            return str(value)
        return str(value)

    def _get_global_params():
        nonlocal global_params
        if global_params is None:
            global_params = _load_global_parameters()
        return global_params

    def _get_user_params():
        nonlocal user_params
        if user_params is None and owner:
            user_params = _load_user_parameters(owner)
        return user_params or {}

    def _get_global_secrets():
        nonlocal global_secrets
        if global_secrets is None:
            global_secrets = _load_global_secrets()
        return global_secrets

    def _get_user_secrets():
        nonlocal user_secrets
        if user_secrets is None and owner:
            user_secrets = _load_user_secrets(owner)
        return user_secrets or {}

    def _get_conv_params():
        nonlocal conv_params
        if conv_params is None and conversation_id:
            try:
                from core.conversation_store import ConversationStore
                conv_params = ConversationStore.instance().get_extra(
                    conversation_id, "conv_parameters") or {}
            except Exception:
                conv_params = {}
        return conv_params or {}

    def _get_conv_secrets():
        nonlocal conv_secrets
        if conv_secrets is None and conversation_id:
            try:
                from core.conversation_store import ConversationStore
                from core.secrets import SecretsManager
                raw = ConversationStore.instance().get_extra(
                    conversation_id, "conv_secrets") or {}
                sm = SecretsManager.get_instance()
                conv_secrets = {}
                for k, v in raw.items():
                    try:
                        conv_secrets[k] = sm.decrypt(v) if v.startswith("enc:") else v
                    except Exception:
                        conv_secrets[k] = v
            except Exception:
                conv_secrets = {}
        return conv_secrets or {}

    def _cascade_param(key, exact_scope=None):
        """Full cascade: flow → conv → user → global → env. Returns (value, found).

        If exact_scope is set (via :!important(scope)), only look in that scope.
        """
        if exact_scope:
            if exact_scope == "flow":
                if key in params:
                    return str(params[key]), True
            elif exact_scope == "conv":
                cp = _get_conv_params()
                if key in cp:
                    return str(cp[key]), True
            elif exact_scope == "user":
                if owner:
                    up = _get_user_params()
                    if key in up:
                        resolved = _resolve_value(up[key])
                        if resolved is not None:
                            return resolved, True
            elif exact_scope == "global":
                gp = _get_global_params()
                if key in gp:
                    resolved = _resolve_value(gp[key])
                    if resolved is not None:
                        return resolved, True
            elif exact_scope == "env":
                val = os.environ.get(key)
                if val is not None:
                    return val, True
            return None, False

        # Full cascade: flow → conv → user → global → env
        if key in params:
            return str(params[key]), True
        cp = _get_conv_params()
        if key in cp:
            return str(cp[key]), True
        if owner:
            up = _get_user_params()
            if key in up:
                resolved = _resolve_value(up[key])
                if resolved is not None:
                    return resolved, True
        gp = _get_global_params()
        if key in gp:
            resolved = _resolve_value(gp[key])
            if resolved is not None:
                return resolved, True
        # env: OS environment as last fallback
        val = os.environ.get(key)
        if val is not None:
            return val, True
        return None, False

    def _cascade_secret(key, exact_scope=None):
        """Full cascade: conv → user → global secrets. Returns (value, found).

        If exact_scope is set (via :!important), only look in that specific scope.
        No flow-level secrets exist.
        """
        if exact_scope:
            if exact_scope == "conv":
                cs = _get_conv_secrets()
                if key in cs:
                    return str(cs[key]), True
            elif exact_scope == "user":
                if owner:
                    us = _get_user_secrets()
                    if key in us:
                        resolved = _resolve_value(us[key])
                        if resolved is not None:
                            return resolved, True
            elif exact_scope == "global":
                gs = _get_global_secrets()
                if key in gs:
                    resolved = _resolve_value(gs[key])
                    if resolved is not None:
                        return resolved, True
            return None, False

        # Full cascade: conv → user → global
        cs = _get_conv_secrets()
        if key in cs:
            return str(cs[key]), True
        if owner:
            us = _get_user_secrets()
            if key in us:
                resolved = _resolve_value(us[key])
                if resolved is not None:
                    return resolved, True
        gs = _get_global_secrets()
        if key in gs:
            resolved = _resolve_value(gs[key])
            if resolved is not None:
                return resolved, True
        return None, False

    def _parse_important(expr):
        """Parse :!important(scope) suffix. Returns (clean_expr, exact_scope_or_None).

        New syntax: ${key:!important(global)}, ${key:!important(user)}, etc.
        Legacy syntax: ${global.key:!important} (scope from prefix)

        Examples:
            "api_key"                        → ("api_key", None)
            "api_key:!important(global)"     → ("api_key", "global")
            "api_key:!important(user)"       → ("api_key", "user")
            "api_key:!important(conv)"       → ("api_key", "conv")
            "global.mavar:!important"        → ("global.mavar", "global")  # legacy
        """
        # Syntax: ${key:!important(scope)}
        m = re.match(r'^(.+):!important\((\w+)\)$', expr)
        if m:
            return m.group(1), m.group(2)
        return expr, None

    def _resolve_single(expr_inner):
        """Resolve a single ${...} expression (for recursive arg resolution)."""
        m = re.match(r'^\$\{(.+)\}$', expr_inner)
        if m:
            return replacer_core(m.group(1))
        return expr_inner

    def replacer(match):
        return replacer_core(match.group(1))

    def replacer_core(expr):
        nonlocal secrets, variables

        # Parse pipeline operations (e.g. "key:upper:equals("X"):then("Y")")
        from core.expression_pipeline import parse_pipeline, evaluate_pipeline
        scope_key, operations = parse_pipeline(expr)

        # If pure generator (empty scope_key), evaluate directly
        if not scope_key and operations:
            return evaluate_pipeline("", operations, resolve_fn=_resolve_single)

        # Use scope_key for resolution (without pipeline ops)
        expr = scope_key

        def _return_val(val):
            """Apply pipeline operations if any, then return."""
            if operations:
                return evaluate_pipeline(str(val), operations, resolve_fn=_resolve_single)
            return val

        # Parse :!important modifier
        expr, exact_scope = _parse_important(expr)

        key = expr

        # ── Unified resolution: secrets cascade → params cascade ──
        # 1. Secrets: flow(n/a) → conv → user → global
        val, found = _cascade_secret(key, exact_scope=exact_scope)
        if found:
            return _return_val(val)
        # Legacy per-user secrets (agent_secrets.json)
        if secrets is None:
            secrets = _load_secrets()
        if key in secrets:
            resolved = _resolve_value(secrets[key])
            if resolved is not None:
                return _return_val(resolved)

        # 2. Parameters: flow → conv → user → global
        val, found = _cascade_param(key, exact_scope=exact_scope)
        if found:
            return _return_val(val)

        # 3. Legacy variables (agent_variables.json)
        if variables is None:
            variables = _load_variables()
        if key in variables:
            return _return_val(variables[key])

        # 4. FlowFile attributes (for flow expressions like ${http.method})
        if expr in attrs:
            val = attrs[expr]
            return _return_val(val)
        # Also try stripped key in attrs
        if key != expr and key in attrs:
            return _return_val(attrs[key])

        # Not found — try pipeline operations with empty (e.g. ${x:default("fallback")})
        if operations:
            result_val = evaluate_pipeline("", operations, resolve_fn=_resolve_single)
            if result_val:
                return result_val

        return "${" + scope_key + "}"

    # Custom substitution that handles nested ${...} in arguments
    result = _substitute_expressions(template, replacer_core)

    # Recursive resolution: if result still has ${...}, resolve again
    if '${' in result and result != template:
        result = resolve_expression(result, attributes, parameters, owner,
                                    conversation_id=conversation_id,
                                    _depth=_depth + 1)

    return result


def resolve_value(value, owner: Optional[str] = None,
                  conversation_id: Optional[str] = None):
    """Resolve ${...} expressions recursively in any value.

    Works on str, dict, list — returns a NEW object with all strings resolved.
    Use this at the point of USE, never at storage time.

    Examples:
        resolve_value("${secrets.api_key}", owner="bob")
        resolve_value({"url": "${user.mcp_url}", "args": ["--key", "${secrets.key}"]}, owner="bob")
        resolve_value(["${global.x}", "literal"], owner="bob")
    """
    if isinstance(value, str):
        if "${" not in value:
            return value
        return resolve_expression(value, owner=owner,
                                  conversation_id=conversation_id)
    if isinstance(value, dict):
        return {k: resolve_value(v, owner=owner, conversation_id=conversation_id)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        resolved = [resolve_value(v, owner=owner, conversation_id=conversation_id)
                    for v in value]
        return type(value)(resolved)
    # int, float, bool, None — pass through
    return value
