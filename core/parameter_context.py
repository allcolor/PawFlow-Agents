# Parameter Context

"""
ParameterContext — carries flow parameters and makes them available
to tasks and services during execution.

Supports:
- Parameters defined in the flow JSON (`flow.parameters`)
- Deployment overrides (API, CLI, scheduler)
- Hierarchical merge (parent flow -> subflow with mapping)
- Expression resolution `${X}` in task configs
"""

from typing import Any, Dict, Optional
from core.expression import resolve_expression


class ParameterContext:
    """Parameter context for a flow.

    Immutable after construction: overrides create a new context.

    Usage:
        ctx = ParameterContext({"env": "prod", "batch_size": "100"})
        ctx = ctx.with_overrides({"env": "staging"})
        value = ctx.resolve("${env}")  # → "staging"
    """

    def __init__(self, parameters: Optional[Dict[str, Any]] = None):
        self._params: Dict[str, Any] = dict(parameters or {})

    @property
    def parameters(self) -> Dict[str, Any]:
        """Return a copy of the parameters."""
        return dict(self._params)

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a parameter by key."""
        return self._params.get(key, default)

    def get_raw(self, key: str, default: Any = None):
        """Get raw value (ConfigValue if large, else str). No string conversion."""
        return self._params.get(key, default)

    def has(self, key: str) -> bool:
        """Check whether a parameter exists."""
        return key in self._params

    def with_overrides(self, overrides: Dict[str, Any]) -> 'ParameterContext':
        """Create a new context with overrides applied.

        Overrides replace existing values.
        Keys not present in overrides remain unchanged.
        """
        merged = dict(self._params)
        merged.update(overrides)
        return ParameterContext(merged)

    def with_mapping(self, mapping: Dict[str, str]) -> 'ParameterContext':
        """Create a new context for a subflow through a mapping.

        The mapping defines : {subflow_param_name: expression_or_value}
        ${X} expressions are resolved from the current context.

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
        """Resolve ${X} expressions in a template.

        Uses the existing expression engine.
        """
        if not isinstance(template, str) or '${' not in template:
            return template
        return resolve_expression(template, parameters=self._params)

    def resolve_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve all expressions in a config dict recursively.

        Resolves ${X} in all string values.
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
