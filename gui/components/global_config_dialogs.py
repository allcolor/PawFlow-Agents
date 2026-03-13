"""Dialogs for managing global and user-level parameters and secrets.

Uses @st.dialog for modal CRUD operations, with persistence to:
  - config/global_parameters.json (plaintext key-value)
  - config/global_secrets.json (encrypted values via SecretsManager)
  - config/users/{username}/parameters.json (user-level params)
  - config/users/{username}/secrets.json (user-level encrypted secrets)
"""

import json
import logging
from pathlib import Path
from typing import Dict

import streamlit as st

from gui.i18n import t

logger = logging.getLogger(__name__)

_GLOBAL_PARAMS_FILE = Path("config/global_parameters.json")
_GLOBAL_SECRETS_FILE = Path("config/global_secrets.json")
_USER_CONFIG_DIR = Path("config/users")


# ---- Persistence helpers ----

def _load_global_params() -> Dict[str, str]:
    if not _GLOBAL_PARAMS_FILE.exists():
        return {}
    try:
        return json.loads(_GLOBAL_PARAMS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_global_params(params: Dict[str, str]) -> None:
    _GLOBAL_PARAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _GLOBAL_PARAMS_FILE.write_text(
        json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _load_global_secrets_raw() -> Dict[str, str]:
    """Load raw (encrypted) secret values."""
    if not _GLOBAL_SECRETS_FILE.exists():
        return {}
    try:
        return json.loads(_GLOBAL_SECRETS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_global_secrets_raw(secrets: Dict[str, str]) -> None:
    _GLOBAL_SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _GLOBAL_SECRETS_FILE.write_text(
        json.dumps(secrets, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _encrypt_value(value: str) -> str:
    from core.secrets import get_secrets_manager
    sm = get_secrets_manager()
    return sm.encrypt(value)


def _decrypt_value(value: str) -> str:
    from core.secrets import get_secrets_manager
    sm = get_secrets_manager()
    try:
        return sm.decrypt(value)
    except Exception:
        return value


# ---- Dialogs ----

@st.dialog(t("runtime.global_params_title"), width="large")
def global_params_dialog():
    """CRUD dialog for global parameters."""
    params = _load_global_params()

    st.markdown(f"**{t('runtime.global_params_desc')}**")
    st.caption(t("runtime.global_params_usage"))

    # Existing parameters table
    if params:
        to_delete = []
        edited = dict(params)

        for key in sorted(params.keys()):
            cols = st.columns([3, 5, 1])
            with cols[0]:
                st.text_input(
                    "Key", value=key, disabled=True,
                    key=f"gp_key_{key}", label_visibility="collapsed"
                )
            with cols[1]:
                new_val = st.text_input(
                    "Value", value=params[key],
                    key=f"gp_val_{key}", label_visibility="collapsed"
                )
                edited[key] = new_val
            with cols[2]:
                if st.button("🗑️", key=f"gp_del_{key}"):
                    to_delete.append(key)

        if to_delete:
            for k in to_delete:
                edited.pop(k, None)
            _save_global_params(edited)
            st.rerun()

        # Auto-save edits
        if edited != params:
            _save_global_params(edited)
    else:
        st.info(t("runtime.global_params_empty"))

    # Add new parameter
    st.markdown("---")
    st.markdown(f"**{t('runtime.global_params_add')}**")
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
                params[new_key.strip()] = new_value
                _save_global_params(params)
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
    raw_secrets = _load_global_secrets_raw()

    st.markdown(f"**{t('runtime.global_secrets_desc')}**")
    st.caption(t("runtime.global_secrets_usage"))

    # Existing secrets
    if raw_secrets:
        to_delete = []

        for key in sorted(raw_secrets.keys()):
            cols = st.columns([3, 5, 1])
            with cols[0]:
                st.text_input(
                    "Key", value=key, disabled=True,
                    key=f"gs_key_{key}", label_visibility="collapsed"
                )
            with cols[1]:
                decrypted = _decrypt_value(raw_secrets[key])
                new_val = st.text_input(
                    "Value", value=decrypted, type="password",
                    key=f"gs_val_{key}", label_visibility="collapsed"
                )
                if new_val != decrypted:
                    raw_secrets[key] = _encrypt_value(new_val)
                    _save_global_secrets_raw(raw_secrets)
            with cols[2]:
                if st.button("🗑️", key=f"gs_del_{key}"):
                    to_delete.append(key)

        if to_delete:
            for k in to_delete:
                raw_secrets.pop(k, None)
            _save_global_secrets_raw(raw_secrets)
            st.rerun()
    else:
        st.info(t("runtime.global_secrets_empty"))

    # Add new secret
    st.markdown("---")
    st.markdown(f"**{t('runtime.global_secrets_add')}**")
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
            if new_key and new_key.strip() and new_value:
                raw_secrets[new_key.strip()] = _encrypt_value(new_value)
                _save_global_secrets_raw(raw_secrets)
                st.rerun()
            else:
                st.warning(t("runtime.all_fields_required"))

    st.markdown("---")
    if st.button(t("common.close"), key="gs_close", type="primary"):
        st.session_state.pop("_show_global_secrets", None)
        st.rerun()


# ---- User-level persistence helpers ----

def _load_user_params(username: str) -> Dict[str, str]:
    path = _USER_CONFIG_DIR / username / "parameters.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_user_params(username: str, params: Dict[str, str]) -> None:
    path = _USER_CONFIG_DIR / username / "parameters.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_user_secrets_raw(username: str) -> Dict[str, str]:
    path = _USER_CONFIG_DIR / username / "secrets.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_user_secrets_raw(username: str, secrets: Dict[str, str]) -> None:
    path = _USER_CONFIG_DIR / username / "secrets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(secrets, ensure_ascii=False, indent=2), encoding="utf-8")


# ---- User-level dialogs ----

@st.dialog("User Parameters", width="large")
def user_params_dialog(username: str):
    """CRUD dialog for user-level parameters."""
    params = _load_user_params(username)

    st.markdown(f"**{t('runtime.user_params_desc', username=username)}**")
    st.caption(t("runtime.user_params_usage"))

    if params:
        to_delete = []
        edited = dict(params)

        for key in sorted(params.keys()):
            cols = st.columns([3, 5, 1])
            with cols[0]:
                st.text_input(
                    "Key", value=key, disabled=True,
                    key=f"up_key_{key}", label_visibility="collapsed"
                )
            with cols[1]:
                new_val = st.text_input(
                    "Value", value=params[key],
                    key=f"up_val_{key}", label_visibility="collapsed"
                )
                edited[key] = new_val
            with cols[2]:
                if st.button("🗑️", key=f"up_del_{key}"):
                    to_delete.append(key)

        if to_delete:
            for k in to_delete:
                edited.pop(k, None)
            _save_user_params(username, edited)
            st.rerun()

        if edited != params:
            _save_user_params(username, edited)
    else:
        st.info(t("runtime.user_params_empty"))

    # Add new parameter
    st.markdown("---")
    st.markdown(f"**{t('runtime.global_params_add')}**")
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
                params[new_key.strip()] = new_value
                _save_user_params(username, params)
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
    raw_secrets = _load_user_secrets_raw(username)

    st.markdown(f"**{t('runtime.user_secrets_desc', username=username)}**")
    st.caption(t("runtime.user_secrets_usage"))

    if raw_secrets:
        to_delete = []

        for key in sorted(raw_secrets.keys()):
            cols = st.columns([3, 5, 1])
            with cols[0]:
                st.text_input(
                    "Key", value=key, disabled=True,
                    key=f"us_key_{key}", label_visibility="collapsed"
                )
            with cols[1]:
                decrypted = _decrypt_value(raw_secrets[key])
                new_val = st.text_input(
                    "Value", value=decrypted, type="password",
                    key=f"us_val_{key}", label_visibility="collapsed"
                )
                if new_val != decrypted:
                    raw_secrets[key] = _encrypt_value(new_val)
                    _save_user_secrets_raw(username, raw_secrets)
            with cols[2]:
                if st.button("🗑️", key=f"us_del_{key}"):
                    to_delete.append(key)

        if to_delete:
            for k in to_delete:
                raw_secrets.pop(k, None)
            _save_user_secrets_raw(username, raw_secrets)
            st.rerun()
    else:
        st.info(t("runtime.user_secrets_empty"))

    # Add new secret
    st.markdown("---")
    st.markdown(f"**{t('runtime.global_secrets_add')}**")
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
            if new_key and new_key.strip() and new_value:
                raw_secrets[new_key.strip()] = _encrypt_value(new_value)
                _save_user_secrets_raw(username, raw_secrets)
                st.rerun()
            else:
                st.warning(t("runtime.all_fields_required"))

    st.markdown("---")
    if st.button(t("common.close"), key="us_close", type="primary"):
        st.session_state.pop("_show_user_secrets", None)
        st.rerun()
