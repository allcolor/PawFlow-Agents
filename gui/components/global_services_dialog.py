"""Dialog for managing global (shared) services.

Provides CRUD: install, configure, enable/disable, uninstall.
Uses @st.dialog for modal interaction.
"""

import time
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

    # Counter to make widget keys unique after each action (prevents lock)
    if "_gsvc_gen" not in st.session_state:
        st.session_state["_gsvc_gen"] = 0
    gen = st.session_state["_gsvc_gen"]

    st.markdown(f"**{t('runtime.global_services_desc')}**")

    editing = st.session_state.get("_gsvc_edit")

    # --- Existing services ---
    if definitions:
        for sid, sdef in sorted(definitions.items()):
            connected = registry.is_connected(sid)
            if connected:
                icon = "🟢"
            elif not sdef.enabled:
                icon = "⚫"
            else:
                icon = "🟡"
            with st.container(border=True):
                hdr_cols = st.columns([5, 1, 1, 1])
                with hdr_cols[0]:
                    st.markdown(f"**{icon} {sid}** (`{sdef.service_type}`)")
                    if sdef.description:
                        st.caption(sdef.description)
                with hdr_cols[1]:
                    if sdef.enabled:
                        if st.button("⏸️", key=f"gsvc_dis_{sid}_{gen}",
                                     help=t("common.disabled")):
                            registry.disable(sid)
                            st.session_state["_gsvc_gen"] = gen + 1
                            st.rerun(scope="fragment")
                    else:
                        if st.button("▶️", key=f"gsvc_en_{sid}_{gen}",
                                     help=t("common.enabled")):
                            registry.enable(sid)
                            st.session_state["_gsvc_gen"] = gen + 1
                            st.rerun(scope="fragment")
                with hdr_cols[2]:
                    if st.button("⚙️", key=f"gsvc_cfg_{sid}_{gen}",
                                 help=t("common.edit")):
                        st.session_state["_gsvc_edit"] = sid
                        st.session_state["_gsvc_gen"] = gen + 1
                        st.rerun(scope="fragment")
                with hdr_cols[3]:
                    if st.button("🗑️", key=f"gsvc_del_{sid}_{gen}",
                                 help=t("common.delete")):
                        registry.uninstall(sid)
                        st.session_state["_gsvc_gen"] = gen + 1
                        st.rerun(scope="fragment")

                if editing == sid:
                    _render_config_editor(registry, sdef, gen)
    else:
        st.info(t("runtime.global_services_empty"))

    # --- Install new service ---
    if not editing:
        st.markdown("---")
        st.markdown(f"**{t('runtime.global_services_add')}**")

        service_types = _get_available_service_types()
        if not service_types:
            st.warning("No service types registered.")
        else:
            icols = st.columns([3, 3, 4])
            with icols[0]:
                new_id = st.text_input(
                    t("common.name"), key=f"gsvc_new_id_{gen}",
                    placeholder="my_http_listener"
                )
            with icols[1]:
                new_type = st.selectbox(
                    t("common.type"), options=service_types,
                    key=f"gsvc_new_type_{gen}"
                )
            with icols[2]:
                new_desc = st.text_input(
                    "Description", key=f"gsvc_new_desc_{gen}",
                    placeholder="Shared HTTP server"
                )

            if new_type:
                schema = _get_service_schema(new_type)
                new_config = {}
                if schema:
                    from gui.components.schema_form import render_schema_fields
                    new_config = render_schema_fields(
                        schema, {}, key_prefix=f"gsvc_new_cfg_{gen}"
                    )
                else:
                    st.caption(t("runtime.global_services_no_schema"))

            if st.button(f"➕ {t('runtime.global_services_install')}",
                         key=f"gsvc_install_{gen}", type="primary"):
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
                        st.session_state["_gsvc_gen"] = gen + 1
                        st.rerun(scope="fragment")
                    except Exception as e:
                        st.error(f"{t('common.error')}: {e}")

    # Close
    st.markdown("---")
    if st.button(t("common.close"), key=f"gsvc_close_{gen}", type="primary"):
        st.session_state.pop("_show_global_services", None)
        st.session_state.pop("_gsvc_edit", None)
        st.rerun()


def _render_config_editor(registry, sdef, gen):
    """Inline config editor for an existing global service."""
    st.markdown(f"--- *{t('common.edit')}: {sdef.service_id}*")

    new_name = st.text_input(
        t("common.name"), value=sdef.service_id,
        key=f"gsvc_rename_{sdef.service_id}_{gen}"
    )

    schema = _get_service_schema(sdef.service_type)
    if schema:
        from gui.components.schema_form import render_schema_fields
        edited = render_schema_fields(
            schema, sdef.config,
            key_prefix=f"gsvc_edit_{sdef.service_id}_{gen}"
        )
    else:
        edited = {}
        for cfg_key, cfg_val in sdef.config.items():
            if isinstance(cfg_val, bool):
                edited[cfg_key] = st.checkbox(
                    cfg_key, value=cfg_val,
                    key=f"gsvc_ecfg_{sdef.service_id}_{cfg_key}_{gen}")
            elif isinstance(cfg_val, (int, float)):
                edited[cfg_key] = st.number_input(
                    cfg_key, value=cfg_val,
                    key=f"gsvc_ecfg_{sdef.service_id}_{cfg_key}_{gen}")
            else:
                edited[cfg_key] = st.text_input(
                    cfg_key, value=str(cfg_val),
                    key=f"gsvc_ecfg_{sdef.service_id}_{cfg_key}_{gen}")

    new_desc = st.text_input(
        "Description", value=sdef.description,
        key=f"gsvc_edesc_{sdef.service_id}_{gen}"
    )

    save_cols = st.columns([1, 1])
    with save_cols[0]:
        if st.button(f"💾 {t('common.save')}",
                     key=f"gsvc_save_{sdef.service_id}_{gen}",
                     type="primary"):
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
            st.session_state["_gsvc_gen"] = gen + 1
            st.rerun(scope="fragment")
    with save_cols[1]:
        if st.button(t("common.cancel"),
                     key=f"gsvc_cancel_{sdef.service_id}_{gen}"):
            st.session_state.pop("_gsvc_edit", None)
            st.session_state["_gsvc_gen"] = gen + 1
            st.rerun(scope="fragment")
