# Parameter Context

"""
ParameterContext — porte les paramètres d'un flow et les rend disponibles
aux tâches et services pendant l'exécution.

Supporte :
- Paramètres définis dans le flow JSON (`flow.parameters`)
- Overrides au déploiement (API, CLI, scheduler)
- Merge hiérarchique (parent flow → subflow avec mapping)
- Résolution d'expressions `${X}` dans les configs de tâches
"""

from typing import Any, Dict, Optional
from core.expression import resolve_expression


class ParameterContext:
    """Contexte de paramètres pour un flow.

    Immutable après construction : les overrides créent un nouveau contexte.

    Usage:
        ctx = ParameterContext({"env": "prod", "batch_size": "100"})
        ctx = ctx.with_overrides({"env": "staging"})
        value = ctx.resolve("${env}")  # → "staging"
    """

    def __init__(self, parameters: Optional[Dict[str, Any]] = None):
        self._params: Dict[str, Any] = dict(parameters or {})

    @property
    def parameters(self) -> Dict[str, Any]:
        """Retourne une copie des paramètres."""
        return dict(self._params)

    def get(self, key: str, default: Any = None) -> Any:
        """Récupérer un paramètre par clé."""
        return self._params.get(key, default)

    def get_raw(self, key: str, default: Any = None):
        """Get raw value (ConfigValue if large, else str). No string conversion."""
        return self._params.get(key, default)

    def has(self, key: str) -> bool:
        """Vérifier si un paramètre existe."""
        return key in self._params

    def with_overrides(self, overrides: Dict[str, Any]) -> 'ParameterContext':
        """Créer un nouveau contexte avec des overrides appliqués.

        Les overrides écrasent les valeurs existantes.
        Les clés non présentes dans overrides restent inchangées.
        """
        merged = dict(self._params)
        merged.update(overrides)
        return ParameterContext(merged)

    def with_mapping(self, mapping: Dict[str, str]) -> 'ParameterContext':
        """Créer un nouveau contexte pour un subflow via un mapping.

        Le mapping définit : {subflow_param_name: expression_or_value}
        Les expressions ${X} sont résolues depuis le contexte courant.

        Exemple:
            parent_ctx = ParameterContext({"env": "prod", "key": "abc"})
            mapping = {"sub_env": "${env}", "mode": "fast"}
            child_ctx = parent_ctx.with_mapping(mapping)
            # child_ctx.get("sub_env") == "prod"
            # child_ctx.get("mode") == "fast"
        """
        child_params = {}
        for child_key, value_expr in mapping.items():
            if isinstance(value_expr, str):
                child_params[child_key] = self.resolve(value_expr)
            else:
                child_params[child_key] = value_expr
        return ParameterContext(child_params)

    def resolve(self, template: str) -> str:
        """Résoudre les expressions ${X} dans un template.

        Utilise le moteur d'expressions existant.
        """
        if not isinstance(template, str) or '${' not in template:
            return template
        return resolve_expression(template, parameters=self._params)

    def resolve_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Résoudre toutes les expressions dans un dict de config (récursif).

        Résout les ${X} dans toutes les valeurs string.
        """
        return self._resolve_dict(config)

    def _resolve_dict(self, d: Dict[str, Any]) -> Dict[str, Any]:
        from core.config_value import ConfigValue
        result = {}
        for key, value in d.items():
            # Skip large ConfigValues — pass through without expression resolution
            if isinstance(value, ConfigValue) and value.is_large:
                result[key] = value
            elif isinstance(value, str):
                resolved = self.resolve(value)
                # Second pass for cascading: ${x} → ${y} → actual value
                if isinstance(resolved, str) and '${' in resolved:
                    resolved = self.resolve(resolved)
                result[key] = resolved
            elif isinstance(value, dict):
                result[key] = self._resolve_dict(value)
            elif isinstance(value, list):
                result[key] = [
                    self._resolve_cascade(item) if isinstance(item, str) else item
                    for item in value
                ]
            else:
                result[key] = value
        return result

    def _resolve_cascade(self, value: str) -> str:
        """Resolve with a second pass for cascading expressions."""
        resolved = self.resolve(value)
        if isinstance(resolved, str) and '${' in resolved:
            resolved = self.resolve(resolved)
        return resolved

    def __repr__(self) -> str:
        from core.config_value import ConfigValue
        display = {}
        for k, v in self._params.items():
            if isinstance(v, ConfigValue) and v.is_large:
                mb = v.size / (1024 * 1024)
                display[k] = f"<large:{mb:.1f}MB>"
            else:
                display[k] = v
        return f"ParameterContext({display})"

    def __eq__(self, other):
        if not isinstance(other, ParameterContext):
            return False
        return self._params == other._params

    def __len__(self) -> int:
        return len(self._params)

    def __bool__(self) -> bool:
        return bool(self._params)
