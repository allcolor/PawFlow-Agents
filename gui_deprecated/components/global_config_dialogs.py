"""Dialogs for managing global and user-level parameters and secrets.

Uses @st.dialog for modal CRUD operations, with persistence via ConfigStore:
  - data/config/global_parameters.json (plaintext key-value, large values spill to sidecar)
  - data/config/global_secrets.json (encrypted values, large values spill to .enc sidecar)
  - data/config/users/{username}/parameters.json (user-level params)
  - data/config/users/{username}/secrets.json (user-level encrypted secrets)
"""

import logging
from pathlib import Path
from typing import Dict

import streamlit as st

from core.config_store import ConfigStore
from core.config_value import ConfigValue
from gui.i18n import t

logger = logging.getLogger(__name__)

from core.paths import (
    GLOBAL_PARAMS_FILE as _GLOBAL_PARAMS_FILE,
    GLOBAL_SECRETS_FILE as _GLOBAL_SECRETS_FILE,
    USER_CONFIG_DIR as _USER_CONFIG_DIR,
)


# ---- Encryption helpers (for inline secret editing) ----

def _encrypt_value(value: str) -> str:
    from core.secrets import get_secrets_manager
    return get_secrets_manager().encrypt(value)


def _decrypt_value(value: str) -> str:
    from core.secrets import get_secrets_manager
    try:
        return get_secrets_manager().decrypt(value)
    except Exception:
        return value


def _format_size(size_bytes: int) -> str:
    """Format byte size for display."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def _render_large_value(cv: ConfigValue, key: str, prefix: str):
    """Render UI for a large (spilled) value: size + preview + download."""
    st.caption(f"Large value ({_format_size(cv.size)})")
    st.code(cv.preview(200), language=None)
    st.download_button(
        f"Download {key}",
        data=cv.as_bytes(),
        file_name=f"{key}.dat",
        key=f"{prefix}_dl_{key}",
    )


# ---- Dialogs ----

@st.dialog(t("runtime.global_params_title"), width="large")
def global_params_dialog():
    """CRUD dialog for global parameters."""
    params = ConfigStore.load_params(_GLOBAL_PARAMS_FILE)

    # Counter to make widget keys unique after each action
    if "_gp_gen" not in st.session_state:
        st.session_state["_gp_gen"] = 0
    gen = st.session_state["_gp_gen"]

    st.markdown(f"**{t('runtime.global_params_desc')}**")
    st.caption(t("runtime.global_params_usage"))

    # Existing parameters table
    if params:
        to_delete = []
        edited = dict(params)

        for key in sorted(params.keys()):
            cv = params[key]
            cols = st.columns([3, 5, 1])
            with cols[0]:
                st.text_input(
                    "Key", value=key, disabled=True,
                    key=f"gp_key_{key}_{gen}", label_visibility="collapsed"
                )
            with cols[1]:
                if cv.is_large:
                    _render_large_value(cv, key, "gp")
                else:
                    new_val = st.text_input(
                        "Value", value=str(cv),
                        key=f"gp_val_{key}_{gen}", label_visibility="collapsed"
                    )
                    edited[key] = ConfigValue(value=new_val)
            with cols[2]:
                def _do_delete_param(_key=key):
                    p = ConfigStore.load_params(_GLOBAL_PARAMS_FILE)
                    p.pop(_key, None)
                    ConfigStore.save_params(_GLOBAL_PARAMS_FILE, p)
                    st.session_state["_gp_gen"] = st.session_state.get("_gp_gen", 0) + 1

                st.button("\U0001f5d1\ufe0f", key=f"gp_del_{key}_{gen}",
                          on_click=_do_delete_param)

        # Auto-save edits (compare string values for small values)
        if any(str(edited.get(k)) != str(params.get(k))
               for k in edited if not edited[k].is_large):
            ConfigStore.save_params(_GLOBAL_PARAMS_FILE, edited)
    else:
        st.info(t("runtime.global_params_empty"))

    # File upload for large values
    st.markdown("---")
    st.markdown(f"**{t('runtime.global_params_add')}**")

    upload_file = st.file_uploader(
        "Upload large value", key=f"gp_upload_{gen}",
        help="Upload a file as a parameter value (certificates, large configs)"
    )

    add_cols = st.columns([3, 5, 1])
    with add_cols[0]:
        st.text_input(
            t("common.name"), key=f"gp_new_key_{gen}", placeholder="my_param"
        )
    with add_cols[1]:
        st.text_input(
            "Value", key=f"gp_new_value_{gen}", placeholder="value"
        )
    with add_cols[2]:
        st.markdown("<br>", unsafe_allow_html=True)

        def _do_add_param():
            g = st.session_state.get("_gp_gen", 0)
            new_key = st.session_state.get(f"gp_new_key_{g}", "").strip()
            new_value = st.session_state.get(f"gp_new_value_{g}", "")
            up = st.session_state.get(f"gp_upload_{g}")
            if not new_key:
                st.session_state["_gp_error"] = t("runtime.all_fields_required")
                return
            p = ConfigStore.load_params(_GLOBAL_PARAMS_FILE)
            if up is not None:
                data = up.read()
                p[new_key] = ConfigValue(data=data)
            else:
                p[new_key] = ConfigValue(value=new_value)
            ConfigStore.save_params(_GLOBAL_PARAMS_FILE, p)
            st.session_state["_gp_gen"] = g + 1
            st.session_state.pop("_gp_error", None)

        st.button("\u2795", key=f"gp_add_{gen}", on_click=_do_add_param)

        if "_gp_error" in st.session_state:
            st.warning(st.session_state.pop("_gp_error"))

    st.markdown("---")

    def _do_close_gp():
        st.session_state.pop("_show_global_params", None)

    if st.button(t("common.close"), key=f"gp_close_{gen}", type="primary",
                 on_click=_do_close_gp):
        st.rerun()


@st.dialog(t("runtime.global_secrets_title"), width="large")
def global_secrets_dialog():
    """CRUD dialog for global secrets."""
    raw_secrets = ConfigStore.load_secrets_raw(_GLOBAL_SECRETS_FILE)

    # Counter to make widget keys unique after each action
    if "_gs_gen" not in st.session_state:
        st.session_state["_gs_gen"] = 0
    gen = st.session_state["_gs_gen"]

    st.markdown(f"**{t('runtime.global_secrets_desc')}**")
    st.caption(t("runtime.global_secrets_usage"))

    # Existing secrets
    if raw_secrets:
        for key in sorted(raw_secrets.keys()):
            entry = raw_secrets[key]
            cols = st.columns([3, 5, 1])
            with cols[0]:
                st.text_input(
                    "Key", value=key, disabled=True,
                    key=f"gs_key_{key}_{gen}", label_visibility="collapsed"
                )
            with cols[1]:
                # Large spilled secret
                if isinstance(entry, dict) and entry.get("$type") == "spilled":
                    st.caption(f"Large secret ({_format_size(entry.get('size', 0))})")
                    st.info("Encrypted large value \u2014 download to view")
                else:
                    decrypted = _decrypt_value(entry)
                    new_val = st.text_input(
                        "Value", value=decrypted, type="password",
                        key=f"gs_val_{key}_{gen}", label_visibility="collapsed"
                    )
                    if new_val != decrypted:
                        raw_secrets[key] = _encrypt_value(new_val)
                        ConfigStore.save_secrets_raw(_GLOBAL_SECRETS_FILE, raw_secrets)
            with cols[2]:
                def _do_delete_secret(_key=key):
                    rs = ConfigStore.load_secrets_raw(_GLOBAL_SECRETS_FILE)
                    rs.pop(_key, None)
                    ConfigStore.save_secrets_raw(_GLOBAL_SECRETS_FILE, rs)
                    ConfigStore.cleanup_sidecars(
                        _GLOBAL_SECRETS_FILE, set(rs.keys())
                    )
                    st.session_state["_gs_gen"] = st.session_state.get("_gs_gen", 0) + 1

                st.button("\U0001f5d1\ufe0f", key=f"gs_del_{key}_{gen}",
                          on_click=_do_delete_secret)
    else:
        st.info(t("runtime.global_secrets_empty"))

    # Add new secret
    st.markdown("---")
    st.markdown(f"**{t('runtime.global_secrets_add')}**")

    upload_file = st.file_uploader(
        "Upload large secret", key=f"gs_upload_{gen}",
        help="Upload a file as a secret value (certificates, tokens)"
    )

    add_cols = st.columns([3, 5, 1])
    with add_cols[0]:
        st.text_input(
            t("common.name"), key=f"gs_new_key_{gen}", placeholder="api_key"
        )
    with add_cols[1]:
        st.text_input(
            "Value", key=f"gs_new_value_{gen}", type="password", placeholder="secret_value"
        )
    with add_cols[2]:
        st.markdown("<br>", unsafe_allow_html=True)

        def _do_add_secret():
            g = st.session_state.get("_gs_gen", 0)
            new_key = st.session_state.get(f"gs_new_key_{g}", "").strip()
            new_value = st.session_state.get(f"gs_new_value_{g}", "")
            up = st.session_state.get(f"gs_upload_{g}")
            if not new_key:
                st.session_state["_gs_error"] = t("runtime.all_fields_required")
                return
            if up is not None:
                data = up.read()
                secrets_cv = ConfigStore.load_secrets(_GLOBAL_SECRETS_FILE)
                secrets_cv[new_key] = ConfigValue(data=data)
                ConfigStore.save_secrets(_GLOBAL_SECRETS_FILE, secrets_cv)
            elif new_value:
                rs = ConfigStore.load_secrets_raw(_GLOBAL_SECRETS_FILE)
                rs[new_key] = _encrypt_value(new_value)
                ConfigStore.save_secrets_raw(_GLOBAL_SECRETS_FILE, rs)
            else:
                st.session_state["_gs_error"] = t("runtime.all_fields_required")
                return
            st.session_state["_gs_gen"] = g + 1
            st.session_state.pop("_gs_error", None)

        st.button("\u2795", key=f"gs_add_{gen}", on_click=_do_add_secret)

        if "_gs_error" in st.session_state:
            st.warning(st.session_state.pop("_gs_error"))

    st.markdown("---")

    def _do_close_gs():
        st.session_state.pop("_show_global_secrets", None)

    if st.button(t("common.close"), key=f"gs_close_{gen}", type="primary",
                 on_click=_do_close_gs):
        st.rerun()


# ---- User-level dialogs ----

@st.dialog("User Parameters", width="large")
def user_params_dialog(username: str):
    """CRUD dialog for user-level parameters."""
    path = _USER_CONFIG_DIR / username / "parameters.json"
    params = ConfigStore.load_params(path)

    # Counter to make widget keys unique after each action
    if "_up_gen" not in st.session_state:
        st.session_state["_up_gen"] = 0
    gen = st.session_state["_up_gen"]

    st.markdown(f"**{t('runtime.user_params_desc', username=username)}**")
    st.caption(t("runtime.user_params_usage"))

    if params:
        edited = dict(params)

        for key in sorted(params.keys()):
            cv = params[key]
            cols = st.columns([3, 5, 1])
            with cols[0]:
                st.text_input(
                    "Key", value=key, disabled=True,
                    key=f"up_key_{key}_{gen}", label_visibility="collapsed"
                )
            with cols[1]:
                if cv.is_large:
                    _render_large_value(cv, key, "up")
                else:
                    new_val = st.text_input(
                        "Value", value=str(cv),
                        key=f"up_val_{key}_{gen}", label_visibility="collapsed"
                    )
                    edited[key] = ConfigValue(value=new_val)
            with cols[2]:
                def _do_delete_param(_key=key, _path=path):
                    p = ConfigStore.load_params(_path)
                    p.pop(_key, None)
                    ConfigStore.save_params(_path, p)
                    st.session_state["_up_gen"] = st.session_state.get("_up_gen", 0) + 1

                st.button("\U0001f5d1\ufe0f", key=f"up_del_{key}_{gen}",
                          on_click=_do_delete_param)

        if any(str(edited.get(k)) != str(params.get(k))
               for k in edited if not edited[k].is_large):
            ConfigStore.save_params(path, edited)
    else:
        st.info(t("runtime.user_params_empty"))

    # Add new parameter
    st.markdown("---")
    st.markdown(f"**{t('runtime.global_params_add')}**")

    upload_file = st.file_uploader(
        "Upload large value", key=f"up_upload_{gen}",
        help="Upload a file as a parameter value"
    )

    add_cols = st.columns([3, 5, 1])
    with add_cols[0]:
        st.text_input(
            t("common.name"), key=f"up_new_key_{gen}", placeholder="my_param"
        )
    with add_cols[1]:
        st.text_input(
            "Value", key=f"up_new_value_{gen}", placeholder="value"
        )
    with add_cols[2]:
        st.markdown("<br>", unsafe_allow_html=True)

        def _do_add_param():
            g = st.session_state.get("_up_gen", 0)
            new_key = st.session_state.get(f"up_new_key_{g}", "").strip()
            new_value = st.session_state.get(f"up_new_value_{g}", "")
            up = st.session_state.get(f"up_upload_{g}")
            if not new_key:
                st.session_state["_up_error"] = t("runtime.all_fields_required")
                return
            p = ConfigStore.load_params(path)
            if up is not None:
                data = up.read()
                p[new_key] = ConfigValue(data=data)
            else:
                p[new_key] = ConfigValue(value=new_value)
            ConfigStore.save_params(path, p)
            st.session_state["_up_gen"] = g + 1
            st.session_state.pop("_up_error", None)

        st.button("\u2795", key=f"up_add_{gen}", on_click=_do_add_param)

        if "_up_error" in st.session_state:
            st.warning(st.session_state.pop("_up_error"))

    st.markdown("---")

    def _do_close_up():
        st.session_state.pop("_show_user_params", None)

    if st.button(t("common.close"), key=f"up_close_{gen}", type="primary",
                 on_click=_do_close_up):
        st.rerun()


@st.dialog("User Secrets", width="large")
def user_secrets_dialog(username: str):
    """CRUD dialog for user-level secrets."""
    path = _USER_CONFIG_DIR / username / "secrets.json"
    raw_secrets = ConfigStore.load_secrets_raw(path)

    # Counter to make widget keys unique after each action
    if "_us_gen" not in st.session_state:
        st.session_state["_us_gen"] = 0
    gen = st.session_state["_us_gen"]

    st.markdown(f"**{t('runtime.user_secrets_desc', username=username)}**")
    st.caption(t("runtime.user_secrets_usage"))

    if raw_secrets:
        for key in sorted(raw_secrets.keys()):
            entry = raw_secrets[key]
            cols = st.columns([3, 5, 1])
            with cols[0]:
                st.text_input(
                    "Key", value=key, disabled=True,
                    key=f"us_key_{key}_{gen}", label_visibility="collapsed"
                )
            with cols[1]:
                if isinstance(entry, dict) and entry.get("$type") == "spilled":
                    st.caption(f"Large secret ({_format_size(entry.get('size', 0))})")
                    st.info("Encrypted large value \u2014 download to view")
                else:
                    decrypted = _decrypt_value(entry)
                    new_val = st.text_input(
                        "Value", value=decrypted, type="password",
                        key=f"us_val_{key}_{gen}", label_visibility="collapsed"
                    )
                    if new_val != decrypted:
                        raw_secrets[key] = _encrypt_value(new_val)
                        ConfigStore.save_secrets_raw(path, raw_secrets)
            with cols[2]:
                def _do_delete_secret(_key=key, _path=path):
                    rs = ConfigStore.load_secrets_raw(_path)
                    rs.pop(_key, None)
                    ConfigStore.save_secrets_raw(_path, rs)
                    ConfigStore.cleanup_sidecars(_path, set(rs.keys()))
                    st.session_state["_us_gen"] = st.session_state.get("_us_gen", 0) + 1

                st.button("\U0001f5d1\ufe0f", key=f"us_del_{key}_{gen}",
                          on_click=_do_delete_secret)
    else:
        st.info(t("runtime.user_secrets_empty"))

    # Add new secret
    st.markdown("---")
    st.markdown(f"**{t('runtime.global_secrets_add')}**")

    upload_file = st.file_uploader(
        "Upload large secret", key=f"us_upload_{gen}",
        help="Upload a file as a secret value"
    )

    add_cols = st.columns([3, 5, 1])
    with add_cols[0]:
        st.text_input(
            t("common.name"), key=f"us_new_key_{gen}", placeholder="api_key"
        )
    with add_cols[1]:
        st.text_input(
            "Value", key=f"us_new_value_{gen}", type="password", placeholder="secret_value"
        )
    with add_cols[2]:
        st.markdown("<br>", unsafe_allow_html=True)

        def _do_add_secret():
            g = st.session_state.get("_us_gen", 0)
            new_key = st.session_state.get(f"us_new_key_{g}", "").strip()
            new_value = st.session_state.get(f"us_new_value_{g}", "")
            up = st.session_state.get(f"us_upload_{g}")
            if not new_key:
                st.session_state["_us_error"] = t("runtime.all_fields_required")
                return
            if up is not None:
                data = up.read()
                secrets_cv = ConfigStore.load_secrets(path)
                secrets_cv[new_key] = ConfigValue(data=data)
                ConfigStore.save_secrets(path, secrets_cv)
            elif new_value:
                rs = ConfigStore.load_secrets_raw(path)
                rs[new_key] = _encrypt_value(new_value)
                ConfigStore.save_secrets_raw(path, rs)
            else:
                st.session_state["_us_error"] = t("runtime.all_fields_required")
                return
            st.session_state["_us_gen"] = g + 1
            st.session_state.pop("_us_error", None)

        st.button("\u2795", key=f"us_add_{gen}", on_click=_do_add_secret)

        if "_us_error" in st.session_state:
            st.warning(st.session_state.pop("_us_error"))

    st.markdown("---")

    def _do_close_us():
        st.session_state.pop("_show_user_secrets", None)

    if st.button(t("common.close"), key=f"us_close_{gen}", type="primary",
                 on_click=_do_close_us):
        st.rerun()
