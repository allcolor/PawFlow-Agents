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
                        st.button("⏸️", key=f"gsvc_dis_{sid}_{gen}",
                                  help=t("common.disabled"),
                                  on_click=lambda _s=sid: (registry.disable(_s),
                                      st.session_state.update({"_gsvc_gen": gen + 1})))
                    else:
                        st.button("▶️", key=f"gsvc_en_{sid}_{gen}",
                                  help=t("common.enabled"),
                                  on_click=lambda _s=sid: (registry.enable(_s),
                                      st.session_state.update({"_gsvc_gen": gen + 1})))
                with hdr_cols[2]:
                    st.button("⚙️", key=f"gsvc_cfg_{sid}_{gen}",
                              help=t("common.edit"),
                              on_click=lambda _s=sid: st.session_state.update({
                                  "_gsvc_edit": _s}))
                with hdr_cols[3]:
                    st.button("🗑️", key=f"gsvc_del_{sid}_{gen}",
                              help=t("common.delete"),
                              on_click=lambda _s=sid: (registry.uninstall(_s),
                                  st.session_state.update({"_gsvc_gen": gen + 1})))

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

            def _do_install():
                _id = st.session_state.get(f"gsvc_new_id_{gen}", "").strip()
                _type = st.session_state.get(f"gsvc_new_type_{gen}", "")
                _desc = st.session_state.get(f"gsvc_new_desc_{gen}", "")
                if not _id:
                    st.session_state["_gsvc_error"] = t("runtime.service_name_required")
                    return
                if _id in definitions:
                    st.session_state["_gsvc_error"] = t("runtime.global_services_exists")
                    return
                # Collect schema config from session_state
                _cfg = {}
                if new_type:
                    _schema = _get_service_schema(_type)
                    if _schema:
                        for pn in _schema:
                            wk = f"gsvc_new_cfg_{gen}_{pn}"
                            if wk in st.session_state:
                                _cfg[pn] = st.session_state[wk]
                try:
                    registry.install(service_id=_id, service_type=_type,
                                     config=_cfg, description=_desc, enabled=True)
                    st.session_state["_gsvc_gen"] = gen + 1
                except Exception as e:
                    st.session_state["_gsvc_error"] = str(e)

            st.button(f"➕ {t('runtime.global_services_install')}",
                      key=f"gsvc_install_{gen}", type="primary",
                      on_click=_do_install)
            _err = st.session_state.pop("_gsvc_error", None)
            if _err:
                st.error(_err)

    # Close
    st.markdown("---")
    if st.button(t("common.close"), key=f"gsvc_close_{gen}", type="primary"):
        st.session_state.pop("_show_global_services", None)
        st.session_state.pop("_gsvc_edit", None)
        st.rerun()


def _render_config_editor(registry, sdef, gen):
    """Inline config editor for an existing global service."""
    sid = sdef.service_id
    name_key = f"gsvc_rename_{sid}_{gen}"
    desc_key = f"gsvc_edesc_{sid}_{gen}"
    prefix = f"gsvc_edit_{sid}_{gen}"

    # Check if save was just executed (via callback)
    if st.session_state.pop("_gsvc_saved", None) == sid:
        st.success(f"Saved {sid}")
        return

    st.markdown(f"--- *{t('common.edit')}: {sid}*")

    st.text_input(t("common.name"), value=sid, key=name_key)

    schema = _get_service_schema(sdef.service_type)
    cfg_keys = []
    if schema:
        from gui.components.schema_form import render_schema_fields
        render_schema_fields(schema, sdef.config, key_prefix=prefix,
                             ignore_show_when=True)
    else:
        for cfg_key, cfg_val in sdef.config.items():
            k = f"gsvc_ecfg_{sid}_{cfg_key}_{gen}"
            cfg_keys.append((cfg_key, k))
            if isinstance(cfg_val, bool):
                st.checkbox(cfg_key, value=cfg_val, key=k)
            elif isinstance(cfg_val, (int, float)):
                st.number_input(cfg_key, value=cfg_val, key=k)
            else:
                st.text_input(cfg_key, value=str(cfg_val), key=k)

    st.text_input("Description", value=sdef.description, key=desc_key)

    def _do_save():
        print(f"[SAVE] Starting save for {sid}", flush=True)
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
        print(f"[SAVE] Collected config: {list(edited.keys())}", flush=True)
        if new_name and new_name != sid:
            try:
                registry.rename(sid, new_name)
                registry.update_config(new_name, edited)
                if new_desc != sdef.description:
                    registry.update_description(new_name, new_desc)
            except (KeyError, ValueError) as e:
                print(f"[SAVE] Rename error: {e}", flush=True)
        else:
            print(f"[SAVE] Calling update_config...", flush=True)
            registry.update_config(sid, edited)
            print(f"[SAVE] update_config done", flush=True)
            if new_desc != sdef.description:
                registry.update_description(sid, new_desc)
        st.session_state.pop("_gsvc_edit", None)
        st.session_state["_gsvc_saved"] = sid
        print(f"[SAVE] Done", flush=True)

    save_cols = st.columns([1, 1])
    with save_cols[0]:
        st.button(f"💾 {t('common.save')}", key=f"gsvc_save_{sid}_{gen}",
                  type="primary", on_click=_do_save)
    with save_cols[1]:
        st.button(t("common.cancel"), key=f"gsvc_cancel_{sid}_{gen}",
                  on_click=lambda: st.session_state.pop("_gsvc_edit", None))
