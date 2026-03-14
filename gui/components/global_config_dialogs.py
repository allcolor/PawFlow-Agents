"""Dialogs for managing global and user-level parameters and secrets.

Uses @st.dialog for modal CRUD operations, with persistence via ConfigStore:
  - config/global_parameters.json (plaintext key-value, large values spill to sidecar)
  - config/global_secrets.json (encrypted values, large values spill to .enc sidecar)
  - config/users/{username}/parameters.json (user-level params)
  - config/users/{username}/secrets.json (user-level encrypted secrets)
"""

import logging
from pathlib import Path
from typing import Dict

import streamlit as st

from core.config_store import ConfigStore
from core.config_value import ConfigValue
from gui.i18n import t

logger = logging.getLogger(__name__)

_GLOBAL_PARAMS_FILE = Path("config/global_parameters.json")
_GLOBAL_SECRETS_FILE = Path("config/global_secrets.json")
_USER_CONFIG_DIR = Path("config/users")


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
                    key=f"gp_key_{key}", label_visibility="collapsed"
                )
            with cols[1]:
                if cv.is_large:
                    _render_large_value(cv, key, "gp")
                else:
                    new_val = st.text_input(
                        "Value", value=str(cv),
                        key=f"gp_val_{key}", label_visibility="collapsed"
                    )
                    edited[key] = ConfigValue(value=new_val)
            with cols[2]:
                if st.button("🗑️", key=f"gp_del_{key}"):
                    to_delete.append(key)

        if to_delete:
            for k in to_delete:
                edited.pop(k, None)
            ConfigStore.save_params(_GLOBAL_PARAMS_FILE, edited)
            st.rerun()

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
        "Upload large value", key="gp_upload",
        help="Upload a file as a parameter value (certificates, large configs)"
    )

    add_cols = st.columns([3, 5, 1])
    with add_cols[0]:
        new_key = st.text_input(
            t("common.name"), key="gp_new_key", placeholder="my_param"
        )
    with add_cols[1]:
        new_value = st.text_input(
            "Value", key="gp_new_value", placeholder="value"
        )
    with add_cols[2]:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("➕", key="gp_add"):
            if new_key and new_key.strip():
                if upload_file is not None:
                    data = upload_file.read()
                    params[new_key.strip()] = ConfigValue(data=data)
                else:
                    params[new_key.strip()] = ConfigValue(value=new_value)
                ConfigStore.save_params(_GLOBAL_PARAMS_FILE, params)
                st.rerun()
            else:
                st.warning(t("runtime.all_fields_required"))

    st.markdown("---")
    if st.button(t("common.close"), key="gp_close", type="primary"):
        st.session_state.pop("_show_global_params", None)
        st.rerun()


@st.dialog(t("runtime.global_secrets_title"), width="large")
def global_secrets_dialog():
    """CRUD dialog for global secrets."""
    raw_secrets = ConfigStore.load_secrets_raw(_GLOBAL_SECRETS_FILE)

    st.markdown(f"**{t('runtime.global_secrets_desc')}**")
    st.caption(t("runtime.global_secrets_usage"))

    # Existing secrets
    if raw_secrets:
        to_delete = []

        for key in sorted(raw_secrets.keys()):
            entry = raw_secrets[key]
            cols = st.columns([3, 5, 1])
            with cols[0]:
                st.text_input(
                    "Key", value=key, disabled=True,
                    key=f"gs_key_{key}", label_visibility="collapsed"
                )
            with cols[1]:
                # Large spilled secret
                if isinstance(entry, dict) and entry.get("$type") == "spilled":
                    st.caption(f"Large secret ({_format_size(entry.get('size', 0))})")
                    st.info("Encrypted large value — download to view")
                else:
                    decrypted = _decrypt_value(entry)
                    new_val = st.text_input(
                        "Value", value=decrypted, type="password",
                        key=f"gs_val_{key}", label_visibility="collapsed"
                    )
                    if new_val != decrypted:
                        raw_secrets[key] = _encrypt_value(new_val)
                        ConfigStore.save_secrets_raw(_GLOBAL_SECRETS_FILE, raw_secrets)
            with cols[2]:
                if st.button("🗑️", key=f"gs_del_{key}"):
                    to_delete.append(key)

        if to_delete:
            for k in to_delete:
                raw_secrets.pop(k, None)
            ConfigStore.save_secrets_raw(_GLOBAL_SECRETS_FILE, raw_secrets)
            # Also cleanup orphan sidecars
            ConfigStore.cleanup_sidecars(
                _GLOBAL_SECRETS_FILE, set(raw_secrets.keys())
            )
            st.rerun()
    else:
        st.info(t("runtime.global_secrets_empty"))

    # Add new secret
    st.markdown("---")
    st.markdown(f"**{t('runtime.global_secrets_add')}**")

    upload_file = st.file_uploader(
        "Upload large secret", key="gs_upload",
        help="Upload a file as a secret value (certificates, tokens)"
    )

    add_cols = st.columns([3, 5, 1])
    with add_cols[0]:
        new_key = st.text_input(
            t("common.name"), key="gs_new_key", placeholder="api_key"
        )
    with add_cols[1]:
        new_value = st.text_input(
            "Value", key="gs_new_value", type="password", placeholder="secret_value"
        )
    with add_cols[2]:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("➕", key="gs_add"):
            if new_key and new_key.strip():
                if upload_file is not None:
                    data = upload_file.read()
                    # Save via ConfigStore which handles spill + encryption
                    secrets_cv = ConfigStore.load_secrets(_GLOBAL_SECRETS_FILE)
                    secrets_cv[new_key.strip()] = ConfigValue(data=data)
                    ConfigStore.save_secrets(_GLOBAL_SECRETS_FILE, secrets_cv)
                elif new_value:
                    raw_secrets[new_key.strip()] = _encrypt_value(new_value)
                    ConfigStore.save_secrets_raw(_GLOBAL_SECRETS_FILE, raw_secrets)
                else:
                    st.warning(t("runtime.all_fields_required"))
                    return
                st.rerun()
            else:
                st.warning(t("runtime.all_fields_required"))

    st.markdown("---")
    if st.button(t("common.close"), key="gs_close", type="primary"):
        st.session_state.pop("_show_global_secrets", None)
        st.rerun()


# ---- User-level dialogs ----

@st.dialog("User Parameters", width="large")
def user_params_dialog(username: str):
    """CRUD dialog for user-level parameters."""
    path = _USER_CONFIG_DIR / username / "parameters.json"
    params = ConfigStore.load_params(path)

    st.markdown(f"**{t('runtime.user_params_desc', username=username)}**")
    st.caption(t("runtime.user_params_usage"))

    if params:
        to_delete = []
        edited = dict(params)

        for key in sorted(params.keys()):
            cv = params[key]
            cols = st.columns([3, 5, 1])
            with cols[0]:
                st.text_input(
                    "Key", value=key, disabled=True,
                    key=f"up_key_{key}", label_visibility="collapsed"
                )
            with cols[1]:
                if cv.is_large:
                    _render_large_value(cv, key, "up")
                else:
                    new_val = st.text_input(
                        "Value", value=str(cv),
                        key=f"up_val_{key}", label_visibility="collapsed"
                    )
                    edited[key] = ConfigValue(value=new_val)
            with cols[2]:
                if st.button("🗑️", key=f"up_del_{key}"):
                    to_delete.append(key)

        if to_delete:
            for k in to_delete:
                edited.pop(k, None)
            ConfigStore.save_params(path, edited)
            st.rerun()

        if any(str(edited.get(k)) != str(params.get(k))
               for k in edited if not edited[k].is_large):
            ConfigStore.save_params(path, edited)
    else:
        st.info(t("runtime.user_params_empty"))

    # Add new parameter
    st.markdown("---")
    st.markdown(f"**{t('runtime.global_params_add')}**")

    upload_file = st.file_uploader(
        "Upload large value", key="up_upload",
        help="Upload a file as a parameter value"
    )

    add_cols = st.columns([3, 5, 1])
    with add_cols[0]:
        new_key = st.text_input(
            t("common.name"), key="up_new_key", placeholder="my_param"
        )
    with add_cols[1]:
        new_value = st.text_input(
            "Value", key="up_new_value", placeholder="value"
        )
    with add_cols[2]:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("➕", key="up_add"):
            if new_key and new_key.strip():
                if upload_file is not None:
                    data = upload_file.read()
                    params[new_key.strip()] = ConfigValue(data=data)
                else:
                    params[new_key.strip()] = ConfigValue(value=new_value)
                ConfigStore.save_params(path, params)
                st.rerun()
            else:
                st.warning(t("runtime.all_fields_required"))

    st.markdown("---")
    if st.button(t("common.close"), key="up_close", type="primary"):
        st.session_state.pop("_show_user_params", None)
        st.rerun()


@st.dialog("User Secrets", width="large")
def user_secrets_dialog(username: str):
    """CRUD dialog for user-level secrets."""
    path = _USER_CONFIG_DIR / username / "secrets.json"
    raw_secrets = ConfigStore.load_secrets_raw(path)

    st.markdown(f"**{t('runtime.user_secrets_desc', username=username)}**")
    st.caption(t("runtime.user_secrets_usage"))

    if raw_secrets:
        to_delete = []

        for key in sorted(raw_secrets.keys()):
            entry = raw_secrets[key]
            cols = st.columns([3, 5, 1])
            with cols[0]:
                st.text_input(
                    "Key", value=key, disabled=True,
                    key=f"us_key_{key}", label_visibility="collapsed"
                )
            with cols[1]:
                if isinstance(entry, dict) and entry.get("$type") == "spilled":
                    st.caption(f"Large secret ({_format_size(entry.get('size', 0))})")
                    st.info("Encrypted large value — download to view")
                else:
                    decrypted = _decrypt_value(entry)
                    new_val = st.text_input(
                        "Value", value=decrypted, type="password",
                        key=f"us_val_{key}", label_visibility="collapsed"
                    )
                    if new_val != decrypted:
                        raw_secrets[key] = _encrypt_value(new_val)
                        ConfigStore.save_secrets_raw(path, raw_secrets)
            with cols[2]:
                if st.button("🗑️", key=f"us_del_{key}"):
                    to_delete.append(key)

        if to_delete:
            for k in to_delete:
                raw_secrets.pop(k, None)
            ConfigStore.save_secrets_raw(path, raw_secrets)
            ConfigStore.cleanup_sidecars(path, set(raw_secrets.keys()))
            st.rerun()
    else:
        st.info(t("runtime.user_secrets_empty"))

    # Add new secret
    st.markdown("---")
    st.markdown(f"**{t('runtime.global_secrets_add')}**")

    upload_file = st.file_uploader(
        "Upload large secret", key="us_upload",
        help="Upload a file as a secret value"
    )

    add_cols = st.columns([3, 5, 1])
    with add_cols[0]:
        new_key = st.text_input(
            t("common.name"), key="us_new_key", placeholder="api_key"
        )
    with add_cols[1]:
        new_value = st.text_input(
            "Value", key="us_new_value", type="password", placeholder="secret_value"
        )
    with add_cols[2]:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("➕", key="us_add"):
            if new_key and new_key.strip():
                if upload_file is not None:
                    data = upload_file.read()
                    secrets_cv = ConfigStore.load_secrets(path)
                    secrets_cv[new_key.strip()] = ConfigValue(data=data)
                    ConfigStore.save_secrets(path, secrets_cv)
                elif new_value:
                    raw_secrets[new_key.strip()] = _encrypt_value(new_value)
                    ConfigStore.save_secrets_raw(path, raw_secrets)
                else:
                    st.warning(t("runtime.all_fields_required"))
                    return
                st.rerun()
            else:
                st.warning(t("runtime.all_fields_required"))

    st.markdown("---")
    if st.button(t("common.close"), key="us_close", type="primary"):
        st.session_state.pop("_show_user_secrets", None)
        st.rerun()
