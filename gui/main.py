#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Application principale Streamlit pour PyFi2.
Point d'entrée de l'interface graphique.
"""

import logging
import os
import sys
from pathlib import Path

# Ensure project root is in sys.path (needed when Streamlit runs from gui/)
_project_root = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _project_root)

# Configure logging so task/engine loggers are visible
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
for _mod in ("engine", "tasks", "services", "core"):
    logging.getLogger(_mod).setLevel(logging.DEBUG)

import streamlit as st

from gui.i18n import init as i18n_init, set_locale, get_locale, t, get_available_locales, SUPPORTED_LOCALES

# Configuration de la page
st.set_page_config(
    page_title="PyFi2 - Pipeline Framework",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Import des services
from gui.services.flow_service import FlowService
from gui.services.storage_service import StorageService

# Force l'initialisation des factory
from tasks import register_all_tasks
register_all_tasks()

# Initialize global log capture for GUI log viewer
from gui.components.log_viewer import LogCapture
LogCapture.get_global()

# Initialisation du session state
if "initialized" not in st.session_state:
    st.session_state.initialized = True
    st.session_state.flows = []
    st.session_state.current_flow = None
    st.session_state.execution_results = []
    st.session_state.selected_flow_id = None

# CSS personnalisé
st.markdown(
    """
    <style>
    .main-header {
        font-size: 2.5rem;
        font-weight: 700;
        background: linear-gradient(90deg, #1f77b4 0%, #2ca02c 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 1rem;
    }
    .sub-header {
        font-size: 1.2rem;
        color: #666;
        margin-bottom: 2rem;
    }
    .flow-card {
        padding: 1rem;
        border-radius: 0.5rem;
        margin-bottom: 1rem;
        background: #f5f5f5;
    }
    .success-card {
        padding: 1rem;
        border-radius: 0.5rem;
        background: #d4edda;
        border-left: 4px solid #28a745;
    }
    .error-card {
        padding: 1rem;
        border-radius: 0.5rem;
        background: #f8d7da;
        border-left: 4px solid #dc3545;
    }
    /* Hide Streamlit deploy button */
    .stDeployButton, [data-testid="stToolbar"] .stDeployButton,
    header [data-testid="stToolbar"] button[kind="header"] {
        display: none !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def render_sidebar():
    """Rendu de la barre latérale avec navigation."""
    with st.sidebar:
        st.markdown(f"# 🚀 {t('app.name')}")

        # Language selector
        locale = st.selectbox(
            "🌐 " + t("language.selector"),
            options=list(SUPPORTED_LOCALES.keys()),
            format_func=lambda x: SUPPORTED_LOCALES[x],
            index=list(SUPPORTED_LOCALES.keys()).index(st.session_state.get("locale", "en")),
            key="locale_selector",
        )
        if locale != st.session_state.get("locale", "en"):
            st.session_state.locale = locale
            set_locale(locale)
            st.rerun()

        # Connection mode selector
        from gui.services.api_client import PyFi2ApiClient

        st.markdown("---")
        mode = st.radio(
            t("connection.mode"),
            [t("connection.direct"), t("connection.api")],
            index=0 if not st.session_state.get("api_mode") else 1,
            key="connection_mode",
        )
        if mode == t("connection.api"):
            api_url = st.text_input(
                t("connection.api_url"),
                value=st.session_state.get("api_url", "http://localhost:8000"),
                key="api_url_input",
            )
            if st.button(t("connection.connect")):
                try:
                    client = PyFi2ApiClient(api_url)
                    health = client.health()
                    st.session_state.api_client = client
                    st.session_state.api_mode = True
                    st.session_state.api_url = api_url
                    st.success(t("connection.connected", url=api_url))
                except Exception as e:
                    st.error(t("connection.failed", error=str(e)))

            if st.session_state.get("api_mode"):
                # Show login if auth is enabled
                client = st.session_state.get("api_client")
                if client:
                    try:
                        sec = client.get_security_status()
                        if sec.get("auth_enabled"):
                            with st.expander(t("connection.login")):
                                user = st.text_input(t("connection.username"), key="api_user")
                                pwd = st.text_input(t("connection.password"), type="password", key="api_pwd")
                                if st.button(t("connection.login"), key="api_login"):
                                    try:
                                        client.login(user, pwd)
                                        st.success(t("connection.logged_in"))
                                    except Exception as e:
                                        st.error(str(e))
                    except Exception:
                        pass
        else:
            st.session_state.api_mode = False
            st.session_state.api_client = None

        st.markdown("---")

        menu = st.selectbox(
            t("common.navigation"),
            [
                f"🏠 {t('nav.dashboard')}",
                f"✏️ {t('nav.editor')}",
                f"▶️ {t('nav.runtime')}",
                f"📊 {t('nav.monitoring')}",
                f"⚙️ {t('nav.settings')}",
                f"📚 {t('nav.documentation')}",
            ],
            index=0,
        )

        st.markdown("---")

        # État du session
        st.markdown(f"### {t('common.status')}")
        st.info(f"{t('dashboard.flows')}: {len(st.session_state.flows)}")

        return menu


def render_dashboard():
    """Rendu du tableau de bord principal."""
    st.markdown(f'<h1 class="main-header">🚀 {t("app.name")}</h1>', unsafe_allow_html=True)
    st.markdown(
        f'<p class="sub-header">{t("app.subtitle")}</p>',
        unsafe_allow_html=True,
    )

    # Initialiser les services
    flow_service = FlowService()
    storage_service = StorageService()

    # Charger les flux existants (reset to avoid duplicates on re-render)
    st.session_state.flows = []
    flows_dir = Path("flows")
    if flows_dir.exists():
        json_files = list(flows_dir.glob("*.json"))
        for json_file in json_files:
            try:
                flow = flow_service.parse_from_file(str(json_file))
                st.session_state.flows.append(flow)
            except Exception as e:
                st.error(f"{t('common.error')}: {json_file}: {e}")

    # Statistiques globales
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(t("dashboard.total_flows"), len(st.session_state.flows))

    with col2:
        st.metric(t("dashboard.total_tasks"), sum(len(f.tasks) for f in st.session_state.flows))

    with col3:
        st.metric(t("monitoring.total_executions"), len(st.session_state.execution_results))

    with col4:
        st.metric(t("monitoring.success_rate"), sum(1 for r in st.session_state.execution_results if getattr(r, 'status', '') == 'success'))

    st.markdown("---")

    # Section Flux récents
    st.markdown(f"### 📁 {t('dashboard.flows')}")

    if not st.session_state.flows:
        st.info(t("dashboard.no_flows"))

        if st.button(f"➕ {t('common.create')}"):
            st.switch_page("pages/2_Editor.py")
    else:
        # Flow search
        flow_search = st.text_input(
            f"🔍 {t('common.search')}",
            key="main_flow_search",
            placeholder=t("dashboard.search_placeholder"),
        )
        search_term = flow_search.lower().strip() if flow_search else ""

        display_flows = [
            f for f in st.session_state.flows
            if not search_term
            or search_term in f.name.lower()
            or search_term in (f.description or "").lower()
            or search_term in f.id.lower()
        ]

        if search_term and not display_flows:
            st.info(t("dashboard.no_search_results"))

        for idx, flow in enumerate(display_flows):
            with st.container():
                col1, col2, col3 = st.columns([3, 1, 1])

                with col1:
                    st.markdown(f"**{flow.name}**")
                    st.caption(f"ID: {flow.id} | Version: {flow.version}")
                    if flow.description:
                        st.caption(flow.description)

                with col2:
                    st.caption(f"{len(flow.tasks)} {t('dashboard.total_tasks')}")

                with col3:
                    col_btn1, col_btn2 = st.columns(2)
                    if col_btn1.button("✏️", key=f"edit_{idx}_{flow.id}", width="stretch"):
                        flow_service = FlowService()
                        st.session_state.current_flow = flow_service.flow_to_dict(flow)
                        st.session_state.selected_flow_id = flow.id
                        st.switch_page("pages/2_Editor.py")
                    if col_btn2.button("▶️", key=f"run_{idx}_{flow.id}", width="stretch"):
                        st.session_state.selected_flow_id = flow.id
                        st.session_state.current_flow = flow
                        st.switch_page("pages/3_Runtime.py")

                st.markdown("---")

    # Section Documentation rapide
    st.markdown("---")
    st.markdown(f"### 📚 {t('doc.title')}")

    with st.expander(f"📖 {t('doc.getting_started')}"):
        st.markdown(
            f"""
            1. {t('doc.getting_started_1')}
            2. {t('doc.getting_started_2')}
            3. {t('doc.getting_started_3')}
            4. {t('doc.getting_started_4')}
            5. {t('doc.getting_started_5')}
            """
        )

    with st.expander(f"🔧 {t('doc.architecture')}"):
        st.markdown(
            f"""
            {t('doc.architecture_desc')}

            - {t('doc.architecture_core')}
            - {t('doc.architecture_engine')}
            - {t('doc.architecture_tasks')}
            - {t('doc.architecture_storage')}
            """
        )


def main():
    """Fonction principale."""
    # Initialize i18n
    i18n_init(st.session_state.get("locale", "en"))

    menu = render_sidebar()

    if menu == f"🏠 {t('nav.dashboard')}":
        render_dashboard()
    elif menu == f"✏️ {t('nav.editor')}":
        st.switch_page("pages/2_Editor.py")
    elif menu == f"▶️ {t('nav.runtime')}":
        st.switch_page("pages/3_Runtime.py")
    elif menu == f"📊 {t('nav.monitoring')}":
        st.switch_page("pages/4_Monitoring.py")
    elif menu == f"⚙️ {t('nav.settings')}":
        st.switch_page("pages/5_Settings.py")
    elif menu == f"📚 {t('nav.documentation')}":
        st.switch_page("pages/6_Documentation.py")


if __name__ == "__main__":
    main()