"""Authentication helpers for Streamlit GUI.

Provides login page, session management, and permission decorators.

Usage in pages:
    from gui.utils.auth import require_auth, check_permission

    session = require_auth()  # Shows login if needed, returns Session
    if not session:
        st.stop()

    if check_permission(session, "flow.edit"):
        # render editor
"""

import streamlit as st
from typing import Optional
from core.security import SecurityManager, Session, Role
from gui.i18n import t


def get_security() -> SecurityManager:
    """Get the global SecurityManager instance."""
    return SecurityManager.get_instance()


def require_auth() -> Optional[Session]:
    """Check authentication. Shows login form if not authenticated.

    Returns Session if authenticated, None if auth disabled (always allowed).
    Calls st.stop() if login is required but not completed.
    """
    security = get_security()

    # Auth disabled = no login required
    if not security.auth_enabled:
        return None

    # Check existing session
    session_id = st.session_state.get("_session_id")
    if session_id:
        session = security.get_session(session_id)
        if session:
            return session
        # Session expired
        st.session_state.pop("_session_id", None)

    # Show login form
    _render_login_form(security)
    st.stop()
    return None  # unreachable


def _render_login_form(security: SecurityManager):
    """Render the login form."""
    st.markdown(f"## 🔐 {t('auth.login_title')}")
    st.markdown("---")

    tab1, tab2 = st.tabs([t("auth.login"), t("auth.oauth")])

    with tab1:
        with st.form("login_form"):
            username = st.text_input(t("auth.username"))
            password = st.text_input(t("auth.password"), type="password")
            submitted = st.form_submit_button(t("auth.login_button"), width="stretch")

            if submitted:
                session = security.authenticate(username, password)
                if session:
                    st.session_state._session_id = session.session_id
                    st.session_state._username = session.username
                    st.session_state._role = session.role.value
                    st.rerun()
                else:
                    st.error(t("auth.invalid_credentials"))

    with tab2:
        providers = security.list_oauth_providers()
        if providers:
            for provider in providers:
                config = security.get_oauth_config(provider)
                if config:
                    st.markdown(f"**{provider.title()}**")
                    # OAuth2 flow would redirect to authorize_url
                    # For Streamlit, we show the authorize URL as a link
                    authorize_url = config.get("authorize_url", "")
                    client_id = config.get("client_id", "")
                    redirect_uri = config.get("redirect_uri", "")
                    if authorize_url and client_id:
                        import urllib.parse
                        params = urllib.parse.urlencode({
                            "client_id": client_id,
                            "redirect_uri": redirect_uri,
                            "response_type": "code",
                            "scope": "openid email profile",
                        })
                        url = f"{authorize_url}?{params}"
                        st.link_button(
                            f"Se connecter avec {provider.title()}",
                            url,
                            width="stretch",
                        )
        else:
            st.info(t("auth.no_oauth_providers"))
            st.caption(t("auth.configure_oauth_hint"))


def check_permission(session: Optional[Session], permission: str) -> bool:
    """Check if the current session has a permission.

    If auth is disabled (session is None), returns True.
    """
    security = get_security()
    if not security.auth_enabled:
        return True
    if session is None:
        return True  # Auth not enabled
    return security.check_permission(session, permission)


def get_current_user() -> Optional[str]:
    """Get the current username from session state."""
    return st.session_state.get("_username")


def get_current_role() -> Optional[str]:
    """Get the current user's role from session state."""
    return st.session_state.get("_role")


def render_user_info():
    """Render user info in the sidebar."""
    security = get_security()
    if not security.auth_enabled:
        return

    username = get_current_user()
    role = get_current_role()

    if username:
        st.sidebar.markdown("---")
        st.sidebar.markdown(f"👤 **{username}** ({role})")
        if st.sidebar.button(f"🚪 {t('auth.logout')}", width="stretch"):
            session_id = st.session_state.get("_session_id")
            if session_id:
                security.logout(session_id)
            st.session_state.pop("_session_id", None)
            st.session_state.pop("_username", None)
            st.session_state.pop("_role", None)
            st.rerun()


def render_security_settings():
    """Render security configuration in Settings page."""
    security = get_security()

    st.markdown(f"### 🔐 {t('settings.security')}")

    # Enable/disable auth
    auth_enabled = st.checkbox(
        t("settings.auth_enabled"),
        value=security.auth_enabled,
        key="sec_auth_enabled",
    )
    if auth_enabled != security.auth_enabled:
        security.enable_auth(auth_enabled)
        st.rerun()

    if not auth_enabled:
        st.info(t("auth.auth_disabled_info"))
        return

    # User management
    st.markdown("---")
    st.markdown(f"#### 👥 {t('auth.users')}")

    users = security.list_users()
    if users:
        for user_info in users:
            with st.container(border=True):
                col1, col2, col3, col4 = st.columns([2, 2, 1, 1])
                with col1:
                    st.markdown(f"**{user_info['display_name']}** (`{user_info['username']}`)")
                    if user_info.get("email"):
                        st.caption(user_info["email"])
                with col2:
                    roles = [r.value for r in Role]
                    current_idx = roles.index(user_info["role"])
                    new_role = st.selectbox(
                        "Role", roles, index=current_idx,
                        key=f"role_{user_info['username']}",
                        label_visibility="collapsed",
                    )
                    if new_role != user_info["role"]:
                        security.update_user(user_info["username"], role=Role(new_role))
                        st.rerun()
                with col3:
                    enabled = user_info.get("enabled", True)
                    icon = "🟢" if enabled else "🔴"
                    st.markdown(f"{icon} {t('auth.active') if enabled else t('auth.inactive')}")
                with col4:
                    if user_info["username"] != "admin":
                        if st.button("🗑️", key=f"del_user_{user_info['username']}"):
                            security.delete_user(user_info["username"])
                            st.rerun()

    # Add user
    with st.expander(f"➕ {t('auth.add_user')}"):
        new_username = st.text_input(t("auth.username"), key="new_user_name")
        new_password = st.text_input(t("auth.password"), type="password", key="new_user_pass")
        new_email = st.text_input(t("auth.email"), key="new_user_email")
        new_role = st.selectbox(t("auth.role"), [r.value for r in Role], key="new_user_role")
        if st.button(t("auth.create_user"), key="create_user_btn"):
            if new_username and new_password:
                try:
                    security.create_user(
                        new_username, new_password, Role(new_role), email=new_email
                    )
                    st.success(f"{t('auth.create_user')}: {new_username}")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
            else:
                st.warning(f"{t('auth.username')} + {t('auth.password')} required")

    # API Keys
    st.markdown("---")
    st.markdown(f"#### 🔑 {t('auth.api_keys')}")

    api_keys = security.list_api_keys()
    if api_keys:
        for key_info in api_keys:
            st.markdown(f"- `{key_info['key']}` — {key_info['description']}")

    if st.button(t("auth.generate_key"), key="gen_api_key"):
        new_key = security.generate_api_key("Generated from GUI")
        st.code(new_key, language=None)
        st.warning(t("auth.copy_key_warning"))

    # OAuth2 configuration
    st.markdown("---")
    st.markdown(f"#### 🔗 {t('auth.oauth')}")

    providers = security.list_oauth_providers()
    if providers:
        for p in providers:
            config = security.get_oauth_config(p)
            with st.expander(f"{p.title()}"):
                st.json(config)
    else:
        st.info(t("auth.no_oauth_providers"))

    with st.expander(f"➕ {t('auth.configure_oauth')}"):
        oauth_provider = st.text_input(t("auth.provider_name"), key="oauth_provider",
                                       placeholder="google, github, keycloak...")
        oauth_client_id = st.text_input(t("auth.client_id"), key="oauth_client_id")
        oauth_client_secret = st.text_input(t("auth.client_secret"), type="password",
                                            key="oauth_client_secret")
        oauth_auth_url = st.text_input(t("auth.authorize_url"), key="oauth_auth_url")
        oauth_token_url = st.text_input(t("auth.token_url"), key="oauth_token_url")
        oauth_userinfo_url = st.text_input(t("auth.userinfo_url"), key="oauth_userinfo_url")
        oauth_redirect_uri = st.text_input(t("auth.redirect_uri"), key="oauth_redirect_uri",
                                           value="http://localhost:8501")

        if st.button(t("common.save"), key="save_oauth"):
            if oauth_provider and oauth_client_id:
                security.set_oauth_config(oauth_provider, {
                    "client_id": oauth_client_id,
                    "client_secret": oauth_client_secret,
                    "authorize_url": oauth_auth_url,
                    "token_url": oauth_token_url,
                    "userinfo_url": oauth_userinfo_url,
                    "redirect_uri": oauth_redirect_uri,
                })
                st.success(t("auth.provider_saved", name=oauth_provider))
                st.rerun()
