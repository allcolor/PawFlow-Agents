"""Dialog for managing global (shared) services.

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


@st.dialog(t("runtime.global_services_title"), width="large")
def global_services_dialog():
    """Full CRUD dialog for global services."""
    from gui.services.global_service_registry import GlobalServiceRegistry

    registry = GlobalServiceRegistry.get_instance()
    definitions = registry.get_all_definitions()

    st.markdown(f"**{t('runtime.global_services_desc')}**")

    # --- Existing services ---
    if definitions:
        for sid, sdef in sorted(definitions.items()):
            connected = registry.is_connected(sid)
            if connected:
                icon = "🟢"
            elif not sdef.enabled:
                icon = "⚫"
            else:
                icon = "🟡"  # enabled but not yet instantiated (normal for on-demand services)
            with st.container(border=True):
                hdr_cols = st.columns([5, 1, 1, 1])
                with hdr_cols[0]:
                    st.markdown(f"**{icon} {sid}** (`{sdef.service_type}`)")
                    if sdef.description:
                        st.caption(sdef.description)
                with hdr_cols[1]:
                    if sdef.enabled:
                        if st.button("⏸️", key=f"gsvc_dis_{sid}",
                                     help=t("common.disabled")):
                            registry.disable(sid)
                            st.rerun(scope="fragment")
                    else:
                        if st.button("▶️", key=f"gsvc_en_{sid}",
                                     help=t("common.enabled")):
                            registry.enable(sid)
                            st.rerun(scope="fragment")
                with hdr_cols[2]:
                    if st.button("⚙️", key=f"gsvc_cfg_{sid}",
                                 help=t("common.edit")):
                        st.session_state["_gsvc_edit"] = sid
                        st.rerun(scope="fragment")
                with hdr_cols[3]:
                    if st.button("🗑️", key=f"gsvc_del_{sid}",
                                 help=t("common.delete")):
                        registry.uninstall(sid)
                        st.rerun()

                # Inline config editor if this service is being edited
                if st.session_state.get("_gsvc_edit") == sid:
                    _render_config_editor(registry, sdef)
    else:
        st.info(t("runtime.global_services_empty"))

    # --- Install new service ---
    st.markdown("---")
    st.markdown(f"**{t('runtime.global_services_add')}**")

    service_types = _get_available_service_types()
    if not service_types:
        st.warning("No service types registered.")
        return

    icols = st.columns([3, 3, 4])
    with icols[0]:
        new_id = st.text_input(
            t("common.name"), key="gsvc_new_id", placeholder="my_http_listener"
        )
    with icols[1]:
        new_type = st.selectbox(
            t("common.type"), options=service_types, key="gsvc_new_type"
        )
    with icols[2]:
        new_desc = st.text_input(
            "Description", key="gsvc_new_desc", placeholder="Shared HTTP server"
        )

    # Show schema for the selected type
    if new_type:
        schema = _get_service_schema(new_type)
        new_config = {}
        if schema:
            from gui.components.schema_form import render_schema_fields
            new_config = render_schema_fields(schema, {}, key_prefix="gsvc_new_cfg")
        else:
            st.caption(t("runtime.global_services_no_schema"))

    if st.button(f"➕ {t('runtime.global_services_install')}",
                 key="gsvc_install", type="primary"):
        if not new_id or not new_id.strip():
            st.warning(t("runtime.service_name_required"))
        elif new_id.strip() in definitions:
            st.warning(t("runtime.global_services_exists"))
        else:
            try:
                registry.install(
                    service_id=new_id.strip(),
                    service_type=new_type,
                    config=new_config if new_type else {},
                    description=new_desc,
                    enabled=True,
                )
                st.rerun()
            except Exception as e:
                st.error(f"{t('common.error')}: {e}")


def _render_config_editor(registry, sdef):
    """Inline config editor for an existing global service."""
    st.markdown(f"--- *{t('common.edit')}: {sdef.service_id}*")

    # Rename
    new_name = st.text_input(
        t("common.name"), value=sdef.service_id,
        key=f"gsvc_rename_{sdef.service_id}"
    )

    schema = _get_service_schema(sdef.service_type)
    if schema:
        from gui.components.schema_form import render_schema_fields
        edited = render_schema_fields(
            schema, sdef.config, key_prefix=f"gsvc_edit_{sdef.service_id}"
        )
    else:
        edited = {}
        for cfg_key, cfg_val in sdef.config.items():
            if isinstance(cfg_val, bool):
                edited[cfg_key] = st.checkbox(
                    cfg_key, value=cfg_val,
                    key=f"gsvc_ecfg_{sdef.service_id}_{cfg_key}")
            elif isinstance(cfg_val, (int, float)):
                edited[cfg_key] = st.number_input(
                    cfg_key, value=cfg_val,
                    key=f"gsvc_ecfg_{sdef.service_id}_{cfg_key}")
            else:
                edited[cfg_key] = st.text_input(
                    cfg_key, value=str(cfg_val),
                    key=f"gsvc_ecfg_{sdef.service_id}_{cfg_key}")

    # Description
    new_desc = st.text_input(
        "Description", value=sdef.description,
        key=f"gsvc_edesc_{sdef.service_id}"
    )

    save_cols = st.columns([1, 1])
    with save_cols[0]:
        if st.button(f"💾 {t('common.save')}", key=f"gsvc_save_{sdef.service_id}",
                     type="primary"):
            # Rename if changed
            renamed_id = new_name.strip() if new_name else ""
            if renamed_id and renamed_id != sdef.service_id:
                try:
                    registry.rename(sdef.service_id, renamed_id)
                except (KeyError, ValueError) as e:
                    st.error(str(e))
                    return
                registry.update_config(renamed_id, edited)
                if new_desc != sdef.description:
                    registry.update_description(renamed_id, new_desc)
            else:
                registry.update_config(sdef.service_id, edited)
                if new_desc != sdef.description:
                    registry.update_description(sdef.service_id, new_desc)
            st.session_state.pop("_gsvc_edit", None)
            st.rerun(scope="fragment")
    with save_cols[1]:
        if st.button(t("common.cancel"), key=f"gsvc_cancel_{sdef.service_id}"):
            st.session_state.pop("_gsvc_edit", None)
            st.rerun(scope="fragment")

    # Close button at bottom of dialog
    st.markdown("---")
    if st.button(t("common.close"), key="gsvc_close", type="primary"):
        st.session_state.pop("_show_global_services", None)
        st.rerun()
