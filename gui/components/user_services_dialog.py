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

    # Counter to make widget keys unique after each action
    if "_usvc_gen" not in st.session_state:
        st.session_state["_usvc_gen"] = 0
    gen = st.session_state["_usvc_gen"]

    st.markdown(f"**{t('runtime.user_services_desc', username=user_id)}**")

    editing = st.session_state.get("_usvc_edit")

    # --- Existing services ---
    if definitions:
        for sid, sdef in sorted(definitions.items()):
            connected = registry.is_connected(user_id, sid)
            if connected:
                icon = "\U0001f7e2"
            elif not sdef.enabled:
                icon = "\u26ab"
            else:
                icon = "\U0001f7e1"  # enabled, not yet instantiated
            with st.container(border=True):
                hdr_cols = st.columns([5, 1, 1, 1])
                with hdr_cols[0]:
                    st.markdown(f"**{icon} {sid}** (`{sdef.service_type}`)")
                    if sdef.description:
                        st.caption(sdef.description)
                with hdr_cols[1]:
                    if sdef.enabled:
                        def _do_disable(_uid=user_id, _sid=sid):
                            registry.disable(_uid, _sid)
                            st.session_state["_usvc_gen"] = st.session_state.get("_usvc_gen", 0) + 1

                        st.button("\u23f8\ufe0f", key=f"usvc_dis_{user_id}_{sid}_{gen}",
                                  help=t("common.disabled"), on_click=_do_disable)
                    else:
                        def _do_enable(_uid=user_id, _sid=sid):
                            registry.enable(_uid, _sid)
                            st.session_state["_usvc_gen"] = st.session_state.get("_usvc_gen", 0) + 1

                        st.button("\u25b6\ufe0f", key=f"usvc_en_{user_id}_{sid}_{gen}",
                                  help=t("common.enabled"), on_click=_do_enable)
                with hdr_cols[2]:
                    def _do_edit(_sid=sid):
                        st.session_state["_usvc_edit"] = _sid
                        st.session_state["_usvc_gen"] = st.session_state.get("_usvc_gen", 0) + 1

                    st.button("\u2699\ufe0f", key=f"usvc_cfg_{user_id}_{sid}_{gen}",
                              help=t("common.edit"), on_click=_do_edit)
                with hdr_cols[3]:
                    def _do_delete(_uid=user_id, _sid=sid):
                        registry.uninstall(_uid, _sid)
                        st.session_state["_usvc_gen"] = st.session_state.get("_usvc_gen", 0) + 1

                    st.button("\U0001f5d1\ufe0f", key=f"usvc_del_{user_id}_{sid}_{gen}",
                              help=t("common.delete"), on_click=_do_delete)

                # Inline config editor if this service is being edited
                if editing == sid:
                    _render_config_editor(registry, user_id, sdef, gen)
    else:
        st.info(t("runtime.user_services_empty"))

    # --- Install new service ---
    if not editing:
        st.markdown("---")
        st.markdown(f"**{t('runtime.user_services_add')}**")

        service_types = _get_available_service_types()
        if not service_types:
            st.warning("No service types registered.")
        else:
            icols = st.columns([3, 3, 4])
            with icols[0]:
                st.text_input(
                    t("common.name"), key=f"usvc_new_id_{gen}", placeholder="my_postgres"
                )
            with icols[1]:
                st.selectbox(
                    t("common.type"), options=service_types, key=f"usvc_new_type_{gen}"
                )
            with icols[2]:
                st.text_input(
                    "Description", key=f"usvc_new_desc_{gen}", placeholder="My personal DB"
                )

            # Show schema for the selected type
            new_type = st.session_state.get(f"usvc_new_type_{gen}")
            if new_type:
                schema = _get_service_schema(new_type)
                if schema:
                    from gui.components.schema_form import render_schema_fields
                    render_schema_fields(schema, {}, key_prefix=f"usvc_new_cfg_{gen}")
                else:
                    st.caption(t("runtime.global_services_no_schema"))

            def _do_install():
                g = st.session_state.get("_usvc_gen", 0)
                new_id = st.session_state.get(f"usvc_new_id_{g}", "").strip()
                ntype = st.session_state.get(f"usvc_new_type_{g}", "")
                new_desc = st.session_state.get(f"usvc_new_desc_{g}", "")

                if not new_id:
                    st.session_state["_usvc_error"] = t("runtime.service_name_required")
                    return
                if new_id in definitions:
                    st.session_state["_usvc_error"] = t("runtime.global_services_exists")
                    return

                # Read schema config from session_state
                new_config = {}
                if ntype:
                    s = _get_service_schema(ntype)
                    if s:
                        for param_name in s:
                            wk = f"usvc_new_cfg_{g}_{param_name}"
                            if wk in st.session_state:
                                new_config[param_name] = st.session_state[wk]

                try:
                    registry.install(
                        user_id=user_id,
                        service_id=new_id,
                        service_type=ntype,
                        config=new_config if ntype else {},
                        description=new_desc,
                        enabled=True,
                    )
                    st.session_state["_usvc_gen"] = g + 1
                    st.session_state.pop("_usvc_error", None)
                except Exception as e:
                    st.session_state["_usvc_error"] = f"{t('common.error')}: {e}"

            st.button(f"\u2795 {t('runtime.global_services_install')}",
                      key=f"usvc_install_{gen}", type="primary", on_click=_do_install)

            # Show error from previous callback
            if "_usvc_error" in st.session_state:
                st.warning(st.session_state.pop("_usvc_error"))

    # Close button at bottom of dialog
    st.markdown("---")

    def _do_close():
        st.session_state.pop("_show_user_services", None)
        st.session_state.pop("_usvc_edit", None)

    if st.button(t("common.close"), key=f"usvc_close_{gen}", type="primary",
                 on_click=_do_close):
        st.rerun()


def _render_config_editor(registry, user_id: str, sdef, gen):
    """Inline config editor for an existing user service."""
    sid = sdef.service_id
    name_key = f"usvc_rename_{user_id}_{sid}_{gen}"
    desc_key = f"usvc_edesc_{user_id}_{sid}_{gen}"
    prefix = f"usvc_edit_{user_id}_{sid}_{gen}"

    # Check if save was just executed (via callback)
    if st.session_state.pop("_usvc_saved", None) == sid:
        st.success(f"Saved {sid}")
        return

    st.markdown(f"--- *{t('common.edit')}: {sdef.service_id}*")

    # Rename
    st.text_input(
        t("common.name"), value=sdef.service_id, key=name_key
    )

    schema = _get_service_schema(sdef.service_type)
    cfg_keys = []
    if schema:
        from gui.components.schema_form import render_schema_fields
        render_schema_fields(schema, sdef.config, key_prefix=prefix)
    else:
        for cfg_key, cfg_val in sdef.config.items():
            k = f"usvc_ecfg_{user_id}_{sid}_{cfg_key}_{gen}"
            cfg_keys.append((cfg_key, k))
            if isinstance(cfg_val, bool):
                st.checkbox(cfg_key, value=cfg_val, key=k)
            elif isinstance(cfg_val, (int, float)):
                st.number_input(cfg_key, value=cfg_val, key=k)
            else:
                st.text_input(cfg_key, value=str(cfg_val), key=k)

    # Description
    st.text_input(
        "Description", value=sdef.description, key=desc_key
    )

    def _do_save():
        new_name = st.session_state.get(name_key, sid).strip()
        new_desc = st.session_state.get(desc_key, sdef.description)
        edited = {}
        if schema:
            for param_name in schema:
                wk = f"{prefix}_{param_name}"
                if wk in st.session_state:
                    edited[param_name] = st.session_state[wk]
        else:
            for cfg_key, k in cfg_keys:
                if k in st.session_state:
                    edited[cfg_key] = st.session_state[k]
        if new_name and new_name != sid:
            try:
                registry.rename(user_id, sid, new_name)
                registry.update_config(user_id, new_name, edited)
                if new_desc != sdef.description:
                    registry.update_description(user_id, new_name, new_desc)
            except (KeyError, ValueError):
                pass
        else:
            registry.update_config(user_id, sid, edited)
            if new_desc != sdef.description:
                registry.update_description(user_id, sid, new_desc)
        st.session_state.pop("_usvc_edit", None)
        st.session_state["_usvc_saved"] = sid

    save_cols = st.columns([1, 1])
    with save_cols[0]:
        st.button(f"\U0001f4be {t('common.save')}",
                  key=f"usvc_save_{user_id}_{sid}_{gen}",
                  type="primary", on_click=_do_save)
    with save_cols[1]:
        st.button(t("common.cancel"),
                  key=f"usvc_cancel_{user_id}_{sid}_{gen}",
                  on_click=lambda: st.session_state.pop("_usvc_edit", None))
