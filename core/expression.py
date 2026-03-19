# Expression Language

"""
Moteur de résolution d'expressions ${...} pour OpenPaw.
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


def resolve_expression(template: str, attributes: Optional[Dict[str, str]] = None,
                       parameters: Optional[Dict[str, Any]] = None,
                       owner: Optional[str] = None,
                       conversation_id: Optional[str] = None,
                       _depth: int = 0) -> str:
    """
    Résoudre toutes les expressions ${...} dans un template.

    Cascade implicite par scope :
    - ${flow.parameters.X} → flow params → user params → global params
    - ${user.X} → user params → global params
    - ${global.X} → global params only
    - ${secrets.user.X} → user secrets → global secrets
    - ${secrets.X} → per-user secrets (legacy)

    Résolution récursive : si la valeur résolue contient des ${...},
    elles sont résolues à leur tour (max 10 niveaux).

    Args:
        template: Chaîne avec expressions ${...}
        attributes: Attributs du FlowFile
        parameters: Paramètres du flow
        owner: Owner username for user-level resolution (None = skip user-level)

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

    def _cascade_param(key):
        """Cascade lookup: conv → user params → global params. Returns (value, found)."""
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
        return None, False

    def _cascade_secret(key):
        """Cascade lookup: conv → user secrets → global secrets. Returns (value, found)."""
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

    def replacer(match):
        nonlocal secrets, variables
        expr = match.group(1)

        # secrets.global.key_name → global secrets only
        if expr.startswith('secrets.global.'):
            key = expr[len('secrets.global.'):]
            gs = _get_global_secrets()
            if key in gs:
                resolved = _resolve_value(gs[key])
                return match.group(0) if resolved is None else resolved
            return match.group(0)

        # secrets.user.key_name → user secrets → global secrets (cascade)
        if expr.startswith('secrets.user.'):
            key = expr[len('secrets.user.'):]
            val, found = _cascade_secret(key)
            if found:
                return val
            return match.group(0)

        # secrets.key_name → per-user secrets (legacy agent_secrets)
        if expr.startswith('secrets.'):
            key = expr[len('secrets.'):]
            if secrets is None:
                secrets = _load_secrets()
            if key in secrets:
                resolved = _resolve_value(secrets[key])
                return match.group(0) if resolved is None else resolved
            return match.group(0)

        # secrets.conv.key_name → conv secrets → user secrets → global secrets (cascade)
        if expr.startswith('secrets.conv.'):
            key = expr[len('secrets.conv.'):]
            val, found = _cascade_secret(key)
            if found:
                return val
            return match.group(0)

        # conv.key_name → conv params → user params → global params (cascade)
        if expr.startswith('conv.'):
            key = expr[len('conv.'):]
            val, found = _cascade_param(key)
            if found:
                return val
            return match.group(0)

        # global.key_name → global parameters only
        if expr.startswith('global.'):
            key = expr[len('global.'):]
            gp = _get_global_params()
            if key in gp:
                resolved = _resolve_value(gp[key])
                return match.group(0) if resolved is None else resolved
            return match.group(0)

        # user.key_name → user params → global params (cascade)
        if expr.startswith('user.'):
            key = expr[len('user.'):]
            val, found = _cascade_param(key)
            if found:
                return val
            return match.group(0)

        # var.key_name (plaintext variables)
        if expr.startswith('var.'):
            key = expr[len('var.'):]
            if variables is None:
                variables = _load_variables()
            if key in variables:
                return variables[key]
            return match.group(0)

        # flow.parameters.key → flow params → user params → global params (cascade)
        if expr.startswith('flow.parameters.'):
            key = expr[len('flow.parameters.'):]
            if key in params:
                return str(params[key])
            # Cascade: try user → global
            val, found = _cascade_param(key)
            if found:
                return val
            return match.group(0)

        # env.VAR
        if expr.startswith('env.'):
            var = expr[len('env.'):]
            return os.environ.get(var, match.group(0))

        # Attribut du FlowFile
        if expr in attrs:
            return attrs[expr]

        return match.group(0)

    result = re.sub(r'\$\{([^}]+)\}', replacer, template)

    # Recursive resolution: if result still has ${...}, resolve again
    if '${' in result and result != template:
        result = resolve_expression(result, attributes, parameters, owner,
                                    _depth=_depth + 1)

    return result
