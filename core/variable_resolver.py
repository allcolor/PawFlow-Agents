# Variable Resolver Mixin

"""
Mixin for variable resolution in configuration.
Avoids code duplication between BaseTask and BaseService.
"""

from typing import Dict, Any, List, Optional
import re


class VariableResolverMixin:
    """
    Mixin for resolving variables in configuration.

    Supporte le format ${variable} et ${var}
    """

    def _resolve_variables(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Resolve variables in configuration.

        Args:
            config: Configuration to resolve

        Returns:
            Configuration with resolved variables
        """
        resolved = {}

        for key, value in config.items():
            if isinstance(value, str):
                resolved[key] = self._resolve_string(value)
            elif isinstance(value, dict):
                resolved[key] = self._resolve_variables(value)
            elif isinstance(value, list):
                resolved[key] = [
                    self._resolve_string(item) if isinstance(item, str) else item
                    for item in value
                ]
            else:
                resolved[key] = value

        return resolved

    def _resolve_string(self, value: str) -> Any:
        """
        Resolve a string containing variables.

        Supporte le format ${variable} et ${var}

        Args:
            value: String to resolve

        Returns:
            Resolved string or original value
        """
        if '${' not in value:
            return value

        def replace_var(match):
            var_path = match.group(1)

            # Cannot be resolved at config time; keep unchanged
            return match.group(0)

        result = re.sub(r'\$\{([^}]+)\}', replace_var, value)
        return result