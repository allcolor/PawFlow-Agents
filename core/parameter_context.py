# Parameter Context

"""
ParameterContext — porte les paramètres d'un flow et les rend disponibles
aux tâches et services pendant l'exécution.

Supporte :
- Paramètres définis dans le flow JSON (`flow.parameters`)
- Overrides au déploiement (API, CLI, scheduler)
- Merge hiérarchique (parent flow → subflow avec mapping)
- Résolution d'expressions `${flow.parameters.X}` dans les configs de tâches
"""

from typing import Any, Dict, Optional
from core.expression import resolve_expression


class ParameterContext:
    """Contexte de paramètres pour un flow.

    Immutable après construction : les overrides créent un nouveau contexte.

    Usage:
        ctx = ParameterContext({"env": "prod", "batch_size": "100"})
        ctx = ctx.with_overrides({"env": "staging"})
        value = ctx.resolve("${flow.parameters.env}")  # → "staging"
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
        Les expressions ${flow.parameters.X} sont résolues depuis le contexte courant.

        Exemple:
            parent_ctx = ParameterContext({"env": "prod", "key": "abc"})
            mapping = {"sub_env": "${flow.parameters.env}", "mode": "fast"}
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
        """Résoudre les expressions ${flow.parameters.X} dans un template.

        Utilise le moteur d'expressions existant.
        """
        if not isinstance(template, str) or '${' not in template:
            return template
        return resolve_expression(template, parameters=self._params)

    def resolve_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Résoudre toutes les expressions dans un dict de config (récursif).

        Résout les ${flow.parameters.X} dans toutes les valeurs string.
        """
        return self._resolve_dict(config)

    def _resolve_dict(self, d: Dict[str, Any]) -> Dict[str, Any]:
        result = {}
        for key, value in d.items():
            if isinstance(value, str):
                result[key] = self.resolve(value)
            elif isinstance(value, dict):
                result[key] = self._resolve_dict(value)
            elif isinstance(value, list):
                result[key] = [
                    self.resolve(item) if isinstance(item, str) else item
                    for item in value
                ]
            else:
                result[key] = value
        return result

    def __repr__(self) -> str:
        return f"ParameterContext({self._params})"

    def __eq__(self, other):
        if not isinstance(other, ParameterContext):
            return False
        return self._params == other._params

    def __len__(self) -> int:
        return len(self._params)

    def __bool__(self) -> bool:
        return bool(self._params)
