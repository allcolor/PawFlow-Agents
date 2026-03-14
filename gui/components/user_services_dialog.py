"""Dialog for managing user-scoped services.

Provides CRUD: install, configure, enable/disable, uninstall.
Uses @st.dialog for modal interaction.
"""

import streamlit as st
from typing import Dict, Any

from gui.i18n import t


def _get_service_schema(svc_type: str) -> dict:
    """Get parameter schema for a service type."""
    from core import ServiceFactory
    try:
        svc_class = ServiceFactory.get(svc_type)
        instance = object.__new__(svc_class)
        instance.config = {}
        return instance.get_parameter_schema()
    except Exception:
        return {}


def _get_available_service_types() -> list:
    """Get list of registered service types."""
    from core import ServiceFactory
    return sorted(ServiceFactory.list_types())


@st.dialog(t("runtime.user_services_title"), width="large")
def user_services_dialog(user_id: str):
    """Full CRUD dialog for user-scoped services."""
    from gui.services.user_service_registry import UserServiceRegistry

    registry = UserServiceRegistry.get_instance()
    definitions = registry.get_all_for_user(user_id)

    st.markdown(f"**{t('runtime.user_services_desc', username=user_id)}**")

    # --- Existing services ---
    if definitions:
        for sid, sdef in sorted(definitions.items()):
            connected = registry.is_connected(user_id, sid)
            icon = "\U0001f7e2" if connected else ("\U0001f534" if sdef.enabled else "\u26ab")
            with st.container(border=True):
                hdr_cols = st.columns([5, 1, 1, 1])
                with hdr_cols[0]:
                    st.markdown(f"**{icon} {sid}** (`{sdef.service_type}`)")
                    if sdef.description:
                        st.caption(sdef.description)
                with hdr_cols[1]:
                    if sdef.enabled:
                        if st.button("\u23f8\ufe0f", key=f"usvc_dis_{user_id}_{sid}",
                                     help=t("common.disabled")):
                            registry.disable(user_id, sid)
                            st.rerun()
                    else:
                        if st.button("\u25b6\ufe0f", key=f"usvc_en_{user_id}_{sid}",
                                     help=t("common.enabled")):
                            registry.enable(user_id, sid)
                            st.rerun()
                with hdr_cols[2]:
                    if st.button("\u2699\ufe0f", key=f"usvc_cfg_{user_id}_{sid}",
                                 help=t("common.edit")):
                        st.session_state["_usvc_edit"] = sid
                        st.rerun()
                with hdr_cols[3]:
                    if st.button("\U0001f5d1\ufe0f", key=f"usvc_del_{user_id}_{sid}",
                                 help=t("common.delete")):
                        registry.uninstall(user_id, sid)
                        st.rerun()

                # Inline config editor if this service is being edited
                if st.session_state.get("_usvc_edit") == sid:
                    _render_config_editor(registry, user_id, sdef)
    else:
        st.info(t("runtime.user_services_empty"))

    # --- Install new service ---
    st.markdown("---")
    st.markdown(f"**{t('runtime.user_services_add')}**")

    service_types = _get_available_service_types()
    if not service_types:
        st.warning("No service types registered.")
        return

    icols = st.columns([3, 3, 4])
    with icols[0]:
        new_id = st.text_input(
            t("common.name"), key="usvc_new_id", placeholder="my_postgres"
        )
    with icols[1]:
        new_type = st.selectbox(
            t("common.type"), options=service_types, key="usvc_new_type"
        )
    with icols[2]:
        new_desc = st.text_input(
            "Description", key="usvc_new_desc", placeholder="My personal DB"
        )

    # Show schema for the selected type
    new_config = {}
    if new_type:
        schema = _get_service_schema(new_type)
        if schema:
            from gui.components.schema_form import render_schema_fields
            new_config = render_schema_fields(schema, {}, key_prefix="usvc_new_cfg")
        else:
            st.caption(t("runtime.global_services_no_schema"))

    if st.button(f"\u2795 {t('runtime.global_services_install')}",
                 key="usvc_install", type="primary"):
        if not new_id or not new_id.strip():
            st.warning(t("runtime.all_fields_required"))
        elif new_id.strip() in definitions:
            st.warning(t("runtime.global_services_exists"))
        else:
            try:
                registry.install(
                    user_id=user_id,
                    service_id=new_id.strip(),
                    service_type=new_type,
                    config=new_config if new_type else {},
                    description=new_desc,
                    enabled=True,
                )
                st.rerun()
            except Exception as e:
                st.error(f"{t('common.error')}: {e}")

    # Close button at bottom of dialog
    st.markdown("---")
    if st.button(t("common.close"), key="usvc_close", type="primary"):
        st.session_state.pop("_show_user_services", None)
        st.rerun()


def _render_config_editor(registry, user_id: str, sdef):
    """Inline config editor for an existing user service."""
    st.markdown(f"--- *{t('common.edit')}: {sdef.service_id}*")

    schema = _get_service_schema(sdef.service_type)
    if schema:
        from gui.components.schema_form import render_schema_fields
        edited = render_schema_fields(
            schema, sdef.config, key_prefix=f"usvc_edit_{user_id}_{sdef.service_id}"
        )
    else:
        edited = {}
        for cfg_key, cfg_val in sdef.config.items():
            if isinstance(cfg_val, bool):
                edited[cfg_key] = st.checkbox(
                    cfg_key, value=cfg_val,
                    key=f"usvc_ecfg_{user_id}_{sdef.service_id}_{cfg_key}")
            elif isinstance(cfg_val, (int, float)):
                edited[cfg_key] = st.number_input(
                    cfg_key, value=cfg_val,
                    key=f"usvc_ecfg_{user_id}_{sdef.service_id}_{cfg_key}")
            else:
                edited[cfg_key] = st.text_input(
                    cfg_key, value=str(cfg_val),
                    key=f"usvc_ecfg_{user_id}_{sdef.service_id}_{cfg_key}")

    # Description
    new_desc = st.text_input(
        "Description", value=sdef.description,
        key=f"usvc_edesc_{user_id}_{sdef.service_id}"
    )

    save_cols = st.columns([1, 1])
    with save_cols[0]:
        if st.button(f"\U0001f4be {t('common.save')}",
                     key=f"usvc_save_{user_id}_{sdef.service_id}",
                     type="primary"):
            registry.update_config(user_id, sdef.service_id, edited)
            if new_desc != sdef.description:
                registry.update_description(user_id, sdef.service_id, new_desc)
            st.session_state.pop("_usvc_edit", None)
            st.rerun()
    with save_cols[1]:
        if st.button(t("common.cancel"),
                     key=f"usvc_cancel_{user_id}_{sdef.service_id}"):
            st.session_state.pop("_usvc_edit", None)
            st.rerun()
