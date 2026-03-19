#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Page Tableau de bord.
Vue d'ensemble du système et des flux disponibles.
"""

import streamlit as st
from pathlib import Path

# Configuration de la page
st.set_page_config(
    page_title="Tableau de bord - OpenPaw",
    page_icon="🏠",
    layout="wide",
)

# i18n
from gui.i18n import init as i18n_init, t
i18n_init(st.session_state.get("locale", "en"))
from gui.components.theme import inject_theme
inject_theme()

# Auth
from gui.utils.auth import require_auth, render_user_info, check_permission
session = require_auth()
render_user_info()

# Import des services
from gui.services.flow_service import FlowService
from gui.services.execution_service import ExecutionService

# Ensure tasks & services are registered
from tasks import register_all_tasks
register_all_tasks()

# Session state initialization
if "flows" not in st.session_state:
    st.session_state.flows = []
if "execution_results" not in st.session_state:
    st.session_state.execution_results = []
if "current_flow" not in st.session_state:
    st.session_state.current_flow = None
if "selected_flow_id" not in st.session_state:
    st.session_state.selected_flow_id = None


def _get_running_flows():
    """Get set of flow IDs that have a running continuous executor."""
    try:
        from gui.services.executor_registry import ExecutorRegistry
        registry = ExecutorRegistry.get_instance()
        running = {}
        for fid, ex in registry.get_all().items():
            try:
                status = ex.get_status()
                running[fid] = status
            except Exception:
                pass
        return running
    except Exception:
        return {}


def _get_execution_history():
    """Get recent execution history for health badges."""
    try:
        execution_service = ExecutionService()
        return execution_service.get_execution_history(limit=20)
    except Exception:
        return []


def render_sidebar():
    """Barre latérale de navigation."""
    with st.sidebar:
        st.markdown(f"# 🚀 {t('app.name')}")
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
        st.markdown(f"### {t('common.status')}")
        st.info(f"{t('dashboard.flows')}: {len(st.session_state.flows)}")

        # Show running flows count in sidebar
        running = _get_running_flows()
        if running:
            st.success(f"{t('dashboard.running_flows')}: {len(running)}")

        return menu


def render_overview():
    """Rendu de la vue d'ensemble."""
    st.markdown(f'<h1>🏠 {t("dashboard.title")}</h1>', unsafe_allow_html=True)
    st.markdown("---")

    # Initialiser les services
    flow_service = FlowService()
    execution_service = ExecutionService()

    # Charger les flux (reset to avoid duplicates on re-render)
    # Group by flow ID — keep only the latest version per ID
    st.session_state.flows = []
    flows_dir = Path("flows")
    flow_by_id = {}  # id -> (flow, file_path, [all_versions])
    if flows_dir.exists():
        json_files = list(flows_dir.glob("*.json"))
        for json_file in sorted(json_files, key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                flow = flow_service.parse_from_file(str(json_file))
                fid = flow.id or str(json_file)
                if fid not in flow_by_id:
                    flow_by_id[fid] = {"flow": flow, "path": str(json_file), "versions": [flow.version or "1.0.0"]}
                else:
                    flow_by_id[fid]["versions"].append(flow.version or "1.0.0")
                    # Keep the one with the highest version
                    existing_ver = flow_by_id[fid]["flow"].version or "0.0.0"
                    new_ver = flow.version or "0.0.0"
                    if new_ver > existing_ver:
                        flow_by_id[fid]["flow"] = flow
                        flow_by_id[fid]["path"] = str(json_file)
            except Exception as e:
                st.error(f"{t('common.error')}: {json_file}: {e}")

    # Also count archived versions
    for fid, info in flow_by_id.items():
        versions_dir = flows_dir / "versions" / fid
        if versions_dir.exists():
            for vf in versions_dir.glob("v*.json"):
                try:
                    import json as _json
                    vdata = _json.loads(vf.read_text(encoding="utf-8"))
                    v = vdata.get("version", "?")
                    if v not in info["versions"]:
                        info["versions"].append(v)
                except Exception:
                    pass

    st.session_state.flows = [info["flow"] for info in flow_by_id.values()]
    _flow_version_info = flow_by_id  # used below for version badges

    # Get running flows and execution history
    running_flows = _get_running_flows()
    exec_history = _get_execution_history()

    # Build last execution status per flow
    last_exec_status = {}
    for ex in exec_history:
        fname = getattr(ex, "flow_name", "") or ""
        if fname and fname not in last_exec_status:
            last_exec_status[fname] = getattr(ex, "status", "unknown")

    # Success rate
    total_execs = len(st.session_state.execution_results) + len(exec_history)
    success_count = sum(1 for r in st.session_state.execution_results if getattr(r, 'status', '') == 'success')
    success_count += sum(1 for r in exec_history if getattr(r, 'status', '') == 'success')
    success_rate = f"{success_count / total_execs * 100:.0f}%" if total_execs > 0 else "N/A"

    # Statistiques
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(t("dashboard.total_flows"), len(st.session_state.flows))

    with col2:
        st.metric(
            t("dashboard.total_tasks"),
            sum(len(f.tasks) for f in st.session_state.flows),
        )

    with col3:
        st.metric(
            t("dashboard.running_flows"),
            len(running_flows),
            delta=f"{len(running_flows)} active" if running_flows else None,
        )

    with col4:
        st.metric(t("dashboard.success_rate"), success_rate)

    st.markdown("---")

    # Flow list section
    st.markdown(f"### 📁 {t('dashboard.flows')}")

    if not st.session_state.flows:
        st.info(t("dashboard.no_flows"))

        if st.button(f"➕ {t('common.create')}", type="primary"):
            st.switch_page("pages/2_Editor.py")
    else:
        # Search + sort controls
        search_col, sort_col = st.columns([3, 1])

        with search_col:
            flow_search = st.text_input(
                f"🔍 {t('common.search')}",
                key="dashboard_flow_search",
                placeholder=t("dashboard.search_placeholder"),
            )

        with sort_col:
            sort_option = st.selectbox(
                t("dashboard.sort_by"),
                [t("dashboard.sort_recent"), t("dashboard.sort_name"), t("dashboard.sort_tasks")],
                key="dashboard_sort",
            )

        search_term = flow_search.lower().strip() if flow_search else ""

        filtered_flows = [
            f for f in st.session_state.flows
            if not search_term
            or search_term in f.name.lower()
            or search_term in (f.description or "").lower()
            or search_term in f.id.lower()
        ]

        # Sort
        if sort_option == t("dashboard.sort_name"):
            filtered_flows.sort(key=lambda f: f.name.lower())
        elif sort_option == t("dashboard.sort_tasks"):
            filtered_flows.sort(key=lambda f: len(f.tasks), reverse=True)
        # sort_recent: already sorted by mtime from loading

        if search_term and not filtered_flows:
            st.info(t("dashboard.no_search_results"))

        for idx, flow in enumerate(filtered_flows):
            is_running = flow.id in running_flows
            fid = flow.id or ""
            ver_info = _flow_version_info.get(fid, {})
            all_versions = sorted(ver_info.get("versions", [flow.version or "1.0.0"]), reverse=True)
            has_multiple_versions = len(all_versions) > 1

            with st.container(border=True):
                col1, col2, col3, col4 = st.columns([3, 1, 1, 1])

                with col1:
                    # Flow name with running indicator
                    running_badge = " 🟢" if is_running else ""
                    st.markdown(f"#### {flow.name}{running_badge}")
                    version_label = f"v{flow.version}"
                    if has_multiple_versions:
                        version_label += f" ({len(all_versions)} {t('dashboard.versions_available')})"
                    st.caption(f"ID: {flow.id} | {version_label}")
                    if flow.description:
                        st.caption(flow.description)

                with col2:
                    st.metric(t("dashboard.total_tasks"), len(flow.tasks))

                with col3:
                    # Health badge based on last execution
                    flow_status = last_exec_status.get(flow.name, "")
                    if is_running:
                        st.markdown(f"🟢 {t('dashboard.continuous_running')}")
                    elif flow_status == "success":
                        st.markdown(f"✅ {t('dashboard.last_run', status=t('common.success'))}")
                    elif flow_status == "failed":
                        st.markdown(f"❌ {t('dashboard.last_run', status=t('runtime.failed'))}")
                    else:
                        st.caption(t("dashboard.no_executions"))

                with col4:
                    col_btn1, col_btn2 = st.columns(2)
                    if col_btn1.button(f"📖 {t('common.edit')}", key=f"edit_{idx}_{flow.id}", width="stretch"):
                        st.session_state.selected_flow_id = flow.id
                        st.session_state.current_flow = flow
                        st.switch_page("pages/2_Editor.py")
                    if col_btn2.button("▶️", key=f"run_{idx}_{flow.id}", width="stretch"):
                        st.session_state.selected_flow_id = flow.id
                        st.session_state.current_flow = flow
                        st.switch_page("pages/3_Runtime.py")


def render_task_type_chart():
    """Render a chart showing available task types by category."""
    st.markdown("---")
    st.markdown(f"### 📊 {t('dashboard.available_types')}")

    try:
        from core import TaskFactory

        all_types = TaskFactory.list_types()
        if not all_types:
            return

        # Categorize tasks
        categories = {
            "system": [], "io": [], "data": [], "control": [],
            "cloud": [], "messaging": [], "ai": [], "other": [],
        }

        category_map = {
            "log": "system", "generateFlowFile": "system", "wait": "system",
            "fail": "system", "executeScript": "system", "debug": "system",
            "updateAttribute": "system", "executeFlow": "system",
            "putFile": "io", "getFile": "io", "fetchFile": "io",
            "listFile": "io", "putSFTP": "io", "getSFTP": "io",
            "httpReceiver": "io", "handleHTTPResponse": "io",
            "invokeHTTP": "io", "putDatabaseRecord": "io",
            "executeSQLQuery": "io",
            "replace_text": "data", "replaceText": "data",
            "joltTransformJSON": "data", "splitJSON": "data",
            "mergeContent": "data", "convertRecord": "data",
            "evaluateJsonPath": "data", "csvToJson": "data",
            "routeOnAttribute": "control", "routeOnContent": "control",
            "publishKafka": "messaging", "consumeKafka": "messaging",
            "publishMQTT": "messaging", "consumeMQTT": "messaging",
            "invokeLLM": "ai", "generateEmbeddings": "ai",
        }

        for task_type in all_types:
            cat = category_map.get(task_type, "other")
            categories[cat].append(task_type)

        # Build chart data
        cat_labels = {
            "system": t("task.categories.system"),
            "io": t("task.categories.io"),
            "data": t("task.categories.data"),
            "control": t("task.categories.control"),
            "cloud": t("task.categories.cloud"),
            "messaging": t("task.categories.messaging"),
            "ai": t("task.categories.ai"),
            "other": t("task.categories.plugins"),
        }

        chart_data = {}
        for cat, tasks in categories.items():
            if tasks:
                chart_data[cat_labels.get(cat, cat)] = len(tasks)

        if chart_data:
            st.bar_chart(chart_data)

    except Exception:
        pass


def render_quick_templates():
    """Quick-start templates section."""
    st.markdown("---")
    st.markdown(f"### 🧩 {t('editor.templates')}")

    try:
        from gui.services.template_service import TemplateService
        ts = TemplateService()
        templates = ts.list_templates()

        if templates:
            cols = st.columns(min(len(templates), 3))
            for i, tpl in enumerate(templates[:6]):
                with cols[i % 3]:
                    with st.container(border=True):
                        st.markdown(f"**{tpl['name']}**")
                        st.caption(tpl.get('description', '')[:80])
                        if st.button(
                            f"➕ {t('editor.use_template')}",
                            key=f"tpl_quick_{tpl['id']}",
                            width="stretch",
                        ):
                            flow_data = ts.get_template(tpl['id'])
                            if flow_data:
                                st.session_state.current_flow = flow_data
                                st.switch_page("pages/2_Editor.py")
    except Exception:
        pass


def render_system_health():
    """System health panel."""
    st.markdown("---")
    st.markdown(f"### 🏥 {t('dashboard.system_health')}")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        import sys
        st.metric("Python", f"{sys.version_info.major}.{sys.version_info.minor}")

    with col2:
        try:
            flows_dir = Path("flows")
            count = len(list(flows_dir.glob("*.json"))) if flows_dir.exists() else 0
            st.metric(t("dashboard.total_flows"), count)
        except Exception:
            st.metric(t("dashboard.total_flows"), "?")

    with col3:
        try:
            from core import TaskFactory
            st.metric(t("dashboard.task_types"), len(TaskFactory.list_types()))
        except Exception:
            st.metric(t("dashboard.task_types"), "?")

    with col4:
        # Running continuous executors
        running = _get_running_flows()
        st.metric(t("dashboard.running_flows"), len(running))


def main():
    """Fonction principale."""
    menu = render_sidebar()

    if menu == f"🏠 {t('nav.dashboard')}":
        render_overview()
        render_task_type_chart()
        render_quick_templates()
        render_system_health()
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
