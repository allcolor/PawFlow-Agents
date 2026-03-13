# Expression Language

"""
Moteur de résolution d'expressions ${...} pour PyFi2.
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
    if not _SECRETS_FILE.exists():
        return {}
    try:
        from core.secrets import get_secrets_manager
        sm = get_secrets_manager()
        raw = json.loads(_SECRETS_FILE.read_text(encoding="utf-8"))
        result = {}
        for key, entry in raw.items():
            encrypted = entry.get("value", "") if isinstance(entry, dict) else entry
            try:
                result[key] = sm.decrypt(encrypted)
            except Exception:
                result[key] = encrypted
        return result
    except Exception as e:
        logger.warning(f"Failed to load secrets: {e}")
        return {}


def _load_global_parameters() -> Dict[str, str]:
    """Load global parameters from config/global_parameters.json."""
    if not _GLOBAL_PARAMS_FILE.exists():
        return {}
    try:
        raw = json.loads(_GLOBAL_PARAMS_FILE.read_text(encoding="utf-8"))
        return {k: str(v) for k, v in raw.items()}
    except Exception as e:
        logger.warning(f"Failed to load global parameters: {e}")
        return {}


def _load_global_secrets() -> Dict[str, str]:
    """Load and decrypt global secrets from config/global_secrets.json."""
    if not _GLOBAL_SECRETS_FILE.exists():
        return {}
    try:
        from core.secrets import get_secrets_manager
        sm = get_secrets_manager()
        raw = json.loads(_GLOBAL_SECRETS_FILE.read_text(encoding="utf-8"))
        result = {}
        for key, entry in raw.items():
            encrypted = entry.get("value", "") if isinstance(entry, dict) else entry
            try:
                result[key] = sm.decrypt(encrypted)
            except Exception:
                result[key] = encrypted
        return result
    except Exception as e:
        logger.warning(f"Failed to load global secrets: {e}")
        return {}


def _load_user_parameters(username: str) -> Dict[str, str]:
    """Load user-level parameters from config/users/{username}/parameters.json."""
    path = _USER_CONFIG_DIR / username / "parameters.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {k: str(v) for k, v in raw.items()}
    except Exception as e:
        logger.warning(f"Failed to load user parameters for {username}: {e}")
        return {}


def _load_user_secrets(username: str) -> Dict[str, str]:
    """Load and decrypt user-level secrets from config/users/{username}/secrets.json."""
    path = _USER_CONFIG_DIR / username / "secrets.json"
    if not path.exists():
        return {}
    try:
        from core.secrets import get_secrets_manager
        sm = get_secrets_manager()
        raw = json.loads(path.read_text(encoding="utf-8"))
        result = {}
        for key, entry in raw.items():
            encrypted = entry.get("value", "") if isinstance(entry, dict) else entry
            try:
                result[key] = sm.decrypt(encrypted)
            except Exception:
                result[key] = encrypted
        return result
    except Exception as e:
        logger.warning(f"Failed to load user secrets for {username}: {e}")
        return {}


def resolve_expression(template: str, attributes: Optional[Dict[str, str]] = None,
                       parameters: Optional[Dict[str, Any]] = None,
                       owner: Optional[str] = None) -> str:
    """
    Résoudre toutes les expressions ${...} dans un template.

    Ordre de résolution :
    1. ${secrets.global.key} → global secrets (config/global_secrets.json)
    2. ${secrets.user.key} → user secrets (config/users/{owner}/secrets.json)
    3. ${secrets.key} → per-user secrets (config/agent_secrets.json)
    4. ${global.key} → global parameters (config/global_parameters.json)
    5. ${user.key} → user parameters (config/users/{owner}/parameters.json)
    6. ${var.key} → plaintext variables (config/agent_variables.json)
    7. ${flow.parameters.key} → paramètres du flow
    8. ${env.VAR} → variables d'environnement
    9. ${attr} → attributs du FlowFile
    10. Non résolu → laissé tel quel

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

    attrs = attributes or {}
    params = parameters or {}
    # Lazy-load secrets and variables only if needed
    secrets = None
    variables = None
    global_params = None
    global_secrets = None
    user_params = None
    user_secrets = None

    def replacer(match):
        nonlocal secrets, variables, global_params, global_secrets
        nonlocal user_params, user_secrets
        expr = match.group(1)

        # secrets.global.key_name → global secrets (separate file)
        if expr.startswith('secrets.global.'):
            key = expr[len('secrets.global.'):]
            if global_secrets is None:
                global_secrets = _load_global_secrets()
            if key in global_secrets:
                return global_secrets[key]
            return match.group(0)

        # secrets.user.key_name → user-level secrets
        if expr.startswith('secrets.user.') and owner:
            key = expr[len('secrets.user.'):]
            if user_secrets is None:
                user_secrets = _load_user_secrets(owner)
            if key in user_secrets:
                return user_secrets[key]
            return match.group(0)

        # secrets.key_name → per-user secrets (legacy agent_secrets)
        if expr.startswith('secrets.'):
            key = expr[len('secrets.'):]
            if secrets is None:
                secrets = _load_secrets()
            if key in secrets:
                return secrets[key]
            return match.group(0)

        # global.key_name → global parameters
        if expr.startswith('global.'):
            key = expr[len('global.'):]
            if global_params is None:
                global_params = _load_global_parameters()
            if key in global_params:
                return global_params[key]
            return match.group(0)

        # user.key_name → user-level parameters
        if expr.startswith('user.') and owner:
            key = expr[len('user.'):]
            if user_params is None:
                user_params = _load_user_parameters(owner)
            if key in user_params:
                return user_params[key]
            return match.group(0)

        # var.key_name (plaintext variables)
        if expr.startswith('var.'):
            key = expr[len('var.'):]
            if variables is None:
                variables = _load_variables()
            if key in variables:
                return variables[key]
            return match.group(0)

        # flow.parameters.key
        if expr.startswith('flow.parameters.'):
            key = expr[len('flow.parameters.'):]
            if key in params:
                return str(params[key])
            return match.group(0)

        # env.VAR
        if expr.startswith('env.'):
            var = expr[len('env.'):]
            return os.environ.get(var, match.group(0))

        # Attribut du FlowFile
        if expr in attrs:
            return attrs[expr]

        return match.group(0)

    return re.sub(r'\$\{([^}]+)\}', replacer, template)
