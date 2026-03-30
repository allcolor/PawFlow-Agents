# Variable Resolver Mixin

"""
Mixin pour la résolution de variables dans les configuration.
Évite la duplication de code entre BaseTask et BaseService.
"""

from typing import Dict, Any, List, Optional
import re


class VariableResolverMixin:
    """
    Mixin pour résoudre les variables dans les configurations.

    Supporte le format ${variable} et ${var}
    """

    def _resolve_variables(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Résoudre les variables dans la configuration.

        Args:
            config: Configuration à résoudre

        Returns:
            Configuration avec variables résolues
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
        Résoudre une chaîne contenant des variables.

        Supporte le format ${variable} et ${var}

        Args:
            value: Chaîne à résoudre

        Returns:
            Chaîne résolue ou valeur originale
        """
        if '${' not in value:
            return value

        def replace_var(match):
            var_path = match.group(1)

            # Support pour parameters (legacy flow.parameters. prefix removed)
            if var_path.startswith('flow.parameters.'):
                param_name = var_path.replace('flow.parameters.', '')
                # NOTE: legacy prefix — new expressions use ${var} directly
                # Ceci nécessite un contexte global, retourner la chaîne telle quelle
                return match.group(0)

            # Non résolvable à la config-time, garder tel quel
            return match.group(0)

        result = re.sub(r'\$\{([^}]+)\}', replace_var, value)
        return result