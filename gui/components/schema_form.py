"""Reusable schema-based form renderer for tasks and services.

Renders Streamlit widgets from a parameter schema dict, handling all types:
string, integer, float, boolean, select, map, object, list, textarea.
"""

import json
from typing import Any, Dict

import streamlit as st


def render_schema_fields(
    schema: Dict[str, Any],
    current_values: Dict[str, Any],
    key_prefix: str,
) -> Dict[str, Any]:
    """Render Streamlit widgets for each parameter in schema.

    Args:
        schema: Parameter schema {name: {type, default, required, description, ...}}
        current_values: Current parameter values (used as defaults)
        key_prefix: Unique prefix for Streamlit widget keys

    Returns:
        Dict of edited parameter values
    """
    result = {}

    for param_name, param_schema in schema.items():
        param_type = param_schema.get("type", "string")
        description = param_schema.get("description", "")
        default = current_values.get(param_name, param_schema.get("default"))
        raw_required = param_schema.get("required", False)
        key = f"{key_prefix}_{param_name}"

        # required can be bool or conditional dict {"field": ["val1", ...]}
        if isinstance(raw_required, dict):
            required = True
            for dep_field, allowed_vals in raw_required.items():
                actual = result.get(dep_field)
                if actual is None:
                    actual = current_values.get(dep_field)
                if actual is None:
                    dep_schema = schema.get(dep_field, {})
                    actual = dep_schema.get("default", "")
                if actual not in allowed_vals:
                    required = False
                    break
        else:
            required = bool(raw_required)
        label = f"{param_name}{'*' if required else ''}"

        # Conditional visibility: show_when = {"field": ["val1", "val2"]}
        show_when = param_schema.get("show_when")
        if show_when:
            visible = True
            for dep_field, allowed_vals in show_when.items():
                # Check rendered result first, then current_values, then schema default
                actual = result.get(dep_field)
                if actual is None:
                    actual = current_values.get(dep_field)
                if actual is None:
                    dep_schema = schema.get(dep_field, {})
                    actual = dep_schema.get("default", "")
                if actual not in allowed_vals:
                    visible = False
                    break
            if not visible:
                continue

        if param_type == "select":
            options = param_schema.get("options", [])
            # If value is an expression ${...}, use text_input so user can edit it
            if isinstance(default, str) and default.startswith("${"):
                result[param_name] = st.text_input(
                    label, value=default, key=key,
                    help=f"{description} (options: {', '.join(options)})" if options else description,
                )
            else:
                idx = options.index(default) if default and default in options else 0
                result[param_name] = st.selectbox(
                    label, options=options, index=idx, key=key, help=description,
                )

        elif param_type == "boolean":
            result[param_name] = st.checkbox(
                label, value=bool(default) if default else False,
                key=key, help=description,
            )

        elif param_type == "integer":
            result[param_name] = st.number_input(
                label, value=int(default) if default is not None else 0,
                step=1, key=key, help=description,
            )

        elif param_type == "float":
            result[param_name] = st.number_input(
                label, value=float(default) if default is not None else 0.0,
                step=0.1, key=key, help=description,
            )

        elif param_type in ("map", "object"):
            st.caption(f"**{label}**" + (f" — {description}" if description else ""))
            raw = st.text_area(
                f"{param_name} (JSON)",
                value=json.dumps(default, indent=2) if default and isinstance(default, dict) else "{}",
                height=100, key=key,
                help='Format JSON: {"key": "value"}',
            )
            try:
                result[param_name] = json.loads(raw)
            except json.JSONDecodeError:
                result[param_name] = default or {}

        elif param_type == "list":
            st.caption(f"**{label}**" + (f" — {description}" if description else ""))
            raw = st.text_area(
                f"{param_name} (JSON)",
                value=json.dumps(default, indent=2) if default and isinstance(default, list) else "[]",
                height=80, key=key,
                help='Format JSON: ["item1", "item2"]',
            )
            try:
                result[param_name] = json.loads(raw)
            except json.JSONDecodeError:
                result[param_name] = default or []

        elif param_type == "textarea":
            result[param_name] = st.text_area(
                label, value=str(default) if default else "",
                height=120, key=key, help=description,
            )

        else:
            # string (default)
            result[param_name] = st.text_input(
                label, value=str(default) if default is not None else "",
                key=key, help=description,
                placeholder=param_schema.get("placeholder", ""),
            )

    return result
