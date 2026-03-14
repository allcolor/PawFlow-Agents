"""Deployment treeview component for Runtime sidebar.

Renders a tree of deployed flow instances grouped by owner,
with status indicators and selection support.
"""

import streamlit as st
from typing import Optional

from gui.i18n import t

# CSS for compact icon buttons (injected once)
_CFG_BTN_CSS = """
<style>
/* Compact icon buttons for tree config (⚙️🔑🔌) */
[data-testid="stExpander"] [data-testid="stHorizontalBlock"]:first-child button[kind="secondary"] {
    background: none !important;
    border: none !important;
    box-shadow: none !important;
    padding: 2px 4px !important;
    min-height: 0 !important;
    line-height: 1.2 !important;
    opacity: 0.65;
    transition: opacity 0.15s, background 0.15s;
}
[data-testid="stExpander"] [data-testid="stHorizontalBlock"]:first-child button[kind="secondary"]:hover {
    opacity: 1;
    background: rgba(128,128,128,0.12) !important;
    border-radius: 6px;
}
</style>
"""

def render_deployment_tree(on_select_key: str = "rt_selected_instance") -> Optional[str]:
    """Render the deployment treeview in the sidebar."""
    from gui.services.deployment_registry import DeploymentRegistry, GLOBAL_OWNER

    # CSS must be injected on every rerun — Streamlit rebuilds HTML each time
    st.markdown(_CFG_BTN_CSS, unsafe_allow_html=True)

    registry = DeploymentRegistry.get_instance()
    grouped = registry.get_grouped()

    selected = st.session_state.get(on_select_key)

    st.markdown(f"#### 📦 {t('runtime.deployed_flows')}")

    if not grouped:
        st.caption(t("runtime.no_deployments"))
    else:
        group_order = []
        if GLOBAL_OWNER in grouped:
            group_order.append(GLOBAL_OWNER)
        for key in sorted(grouped.keys()):
            if key != GLOBAL_OWNER:
                group_order.append(key)

        for group_key in group_order:
            instances = grouped[group_key]
            is_global = group_key == GLOBAL_OWNER
            label = t("runtime.global_flows") if is_global else group_key

            with st.expander(f"📂 {label} ({len(instances)})", expanded=True):
                _render_config_buttons(group_key, is_global)

                for inst in instances:
                    status_icon = _status_icon(inst.status)
                    display = f"{status_icon} {inst.flow_name}"
                    if inst.instance_id != inst.flow_id:
                        display += f" ({inst.instance_id.split('__')[-1]})"

                    is_selected = selected == inst.instance_id
                    btn_type = "primary" if is_selected else "secondary"
                    if st.button(
                        display,
                        key=f"tree_{inst.instance_id}",
                        type=btn_type,
                        width="stretch",
                    ):
                        st.session_state[on_select_key] = inst.instance_id
                        st.rerun()

    st.markdown("---")
    if st.button(
        f"➕ {t('runtime.deploy_new')}",
        key="tree_deploy_new",
        width="stretch",
    ):
        st.session_state[on_select_key] = "__new__"
        st.rerun()

    # Trigger dialog — single flag, only one at a time, auto-clears on next render
    _dlg = st.session_state.pop("_tree_dialog", None)
    if _dlg == "global_params":
        from gui.components.global_config_dialogs import global_params_dialog
        global_params_dialog()
    elif _dlg == "global_secrets":
        from gui.components.global_config_dialogs import global_secrets_dialog
        global_secrets_dialog()
    elif _dlg == "global_services":
        from gui.components.global_services_dialog import global_services_dialog
        global_services_dialog()
    elif _dlg and _dlg.startswith("user_params:"):
        from gui.components.global_config_dialogs import user_params_dialog
        user_params_dialog(_dlg.split(":", 1)[1])
    elif _dlg and _dlg.startswith("user_secrets:"):
        from gui.components.global_config_dialogs import user_secrets_dialog
        user_secrets_dialog(_dlg.split(":", 1)[1])
    elif _dlg and _dlg.startswith("user_services:"):
        from gui.components.user_services_dialog import user_services_dialog
        user_services_dialog(_dlg.split(":", 1)[1])

    return st.session_state.get(on_select_key)


def _render_config_buttons(group_key: str, is_global: bool):
    """Render compact icon-only config buttons via st.button inside a styled container."""
    if is_global:
        cols = st.columns([1, 1, 1, 4], gap="small")
        with cols[0]:
            if st.button("⚙️", key="tree_global_params",
                         help=t("runtime.global_params_title")):
                st.session_state["_tree_dialog"] = "global_params"
        with cols[1]:
            if st.button("🔑", key="tree_global_secrets",
                         help=t("runtime.global_secrets_title")):
                st.session_state["_tree_dialog"] = "global_secrets"
        with cols[2]:
            if st.button("🔌", key="tree_global_services",
                         help=t("runtime.global_services_title")):
                st.session_state["_tree_dialog"] = "global_services"
    else:
        cols = st.columns([1, 1, 1, 4], gap="small")
        with cols[0]:
            if st.button("⚙️", key=f"tree_{group_key}_params",
                         help=t("runtime.user_params_title")):
                st.session_state["_tree_dialog"] = f"user_params:{group_key}"
        with cols[1]:
            if st.button("🔑", key=f"tree_{group_key}_secrets",
                         help=t("runtime.user_secrets_title")):
                st.session_state["_tree_dialog"] = f"user_secrets:{group_key}"
        with cols[2]:
            if st.button("🔌", key=f"tree_{group_key}_services",
                         help=t("runtime.user_services_title")):
                st.session_state["_tree_dialog"] = f"user_services:{group_key}"


def _status_icon(status: str) -> str:
    return {"running": "🟢", "stopped": "🔴", "error": "🔥"}.get(status, "❓")
