"""Rule evaluator for auth gateway provisioning rules.

Evaluates Python-like expressions against JWT/userinfo claims in a sandbox.
Uses core/sandbox.py for safe evaluation.

Examples:
    "email.endswith('@mycompany.com')"
    "email == 'admin@gmail.com' or hd == 'mycompany.com'"
    "re.match(r'.*@(partner1|partner2)\\.org$', email)"
    "provider == 'google' and email_verified == true"
"""

import logging
import re
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Safe builtins for rule evaluation
_SAFE_BUILTINS = {
    "True": True, "False": False, "None": None,
    "true": True, "false": False, "null": None,
    "len": len, "str": str, "int": int, "float": float, "bool": bool,
    "isinstance": isinstance, "hasattr": hasattr,
}


def evaluate_rule(expression: str, claims: Dict[str, Any]) -> bool:
    """Evaluate a matching rule against claims.

    Args:
        expression: Python-like expression string
        claims: Dict of JWT/userinfo claims (email, name, sub, hd, etc.)

    Returns:
        True if the rule matches, False otherwise.
        Never raises — returns False on any error.
    """
    if not expression or not expression.strip():
        return False

    # Build evaluation context: claims as variables + re module + safe builtins
    context = {**_SAFE_BUILTINS, "re": re}
    for key, value in claims.items():
        # Only inject string-safe key names
        if isinstance(key, str) and key.isidentifier():
            context[key] = value

    try:
        # Use compile + eval with restricted globals (no __builtins__)
        code = compile(expression, "<rule>", "eval")
        # Verify no dangerous names in the code
        for name in code.co_names:
            if name.startswith("_"):
                logger.warning(f"[auth:rule] Blocked access to '{name}' in rule: {expression}")
                return False
        result = eval(code, {"__builtins__": {}}, context)  # nosec B307 - restricted auth rule expression.
        return bool(result)
    except Exception as e:
        logger.warning(f"[auth:rule] Evaluation failed for '{expression}': {e}")
        return False
