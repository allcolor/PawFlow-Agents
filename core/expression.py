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


def resolve_expression(template: str, attributes: Optional[Dict[str, str]] = None,
                       parameters: Optional[Dict[str, Any]] = None) -> str:
    """
    Résoudre toutes les expressions ${...} dans un template.

    Ordre de résolution :
    1. ${secrets.key} → secrets chiffrés (config/agent_secrets.json)
    2. ${flow.parameters.key} → paramètres du flow
    3. ${env.VAR} → variables d'environnement
    4. ${attr} → attributs du FlowFile
    5. Non résolu → laissé tel quel

    Args:
        template: Chaîne avec expressions ${...}
        attributes: Attributs du FlowFile
        parameters: Paramètres du flow

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

    def replacer(match):
        nonlocal secrets, variables
        expr = match.group(1)

        # secrets.key_name
        if expr.startswith('secrets.'):
            key = expr[len('secrets.'):]
            if secrets is None:
                secrets = _load_secrets()
            if key in secrets:
                return secrets[key]
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
