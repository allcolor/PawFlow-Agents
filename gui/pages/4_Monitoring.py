#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Page Monitoring.
Dashboard de monitoring en temps réel des exécutions.
"""

import streamlit as st
from typing import Dict, Any, List
import logging
from datetime import datetime, timedelta

# Configuration de la page
st.set_page_config(
    page_title="Monitoring - OpenPaw",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
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
from gui.services.execution_service import ExecutionService
from gui.components.execution_monitor import ExecutionMonitor
from core.bulletin import BulletinBoard
from engine.provenance import get_provenance_repository, ProvenanceEventType

logger = logging.getLogger(__name__)

# Session state initialization
if "flows" not in st.session_state:
    st.session_state.flows = []
if "execution_results" not in st.session_state:
    st.session_state.execution_results = []
if "current_flow" not in st.session_state:
    st.session_state.current_flow = None
if "selected_flow_id" not in st.session_state:
    st.session_state.selected_flow_id = None


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
            index=3,
        )

        st.markdown("---")
        st.markdown(f"### {t('common.search')}")

        # Filtre par statut
        status_filter = st.multiselect(
            t("common.status"),
            ["running", "success", "failed", "cancelled"],
            default=["running", "success", "failed", "cancelled"],
            key="status_filter",
        )

        st.markdown("---")
        st.markdown(f"🕒 {t('monitor.last_update')}: {datetime.now().strftime('%H:%M:%S')}")

        return menu


def render_global_statistics():
    """Afficher les statistiques globales."""
    st.markdown(f"### 📈 {t('dashboard.title')}")

    execution_service = ExecutionService()
    stats = execution_service.get_statistics()

    # KPIs en colonnes
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            t("monitoring.total_executions"),
            stats.get("total_executions", 0),
            delta=f"+{stats.get('executions_today', 0)} {t('monitor.today')}",
        )

    with col2:
        st.metric(
            t("monitoring.success_rate"),
            stats.get("success_count", 0),
            delta=f"{stats.get('success_rate', 0):.1f}%",
            delta_color="normal" if stats.get("success_rate", 0) > 80 else "inverse",
        )

    with col3:
        st.metric(
            t("common.error"),
            stats.get("failed_count", 0),
            delta=f"{stats.get('failed_rate', 0):.1f}%",
            delta_color="inverse" if stats.get("failed_count", 0) > 5 else "normal",
        )

    with col4:
        avg_duration = stats.get("avg_duration_ms", 0)
        st.metric(
            t("monitoring.avg_duration"),
            f"{avg_duration/1000:.2f}s" if avg_duration > 0 else "N/A",
            delta=f"{stats.get('total_bytes_processed', 0)/1024:.1f} {t('monitor.kb_processed')}",
        )

    # Graphique des exécutions récentes
    st.markdown("---")
    st.markdown(f"### 📊 {t('monitor.execution_trend')}")

    history = execution_service.get_execution_history(limit=20)
    if history:
        # Préparer les données pour le graphique
        labels = []
        success_values = []
        failed_values = []

        for state in history:
            labels.append(state.flow_name[:20])
            if state.status == "success":
                success_values.append(1)
                failed_values.append(0)
            elif state.status == "failed":
                success_values.append(0)
                failed_values.append(1)
            else:
                success_values.append(0)
                failed_values.append(0)

        col1, col2 = st.columns(2)

        with col1:
            st.markdown(f"#### {t('monitor.executions_per_flow')}")
            if labels:
                import pandas as pd
                chart_data = pd.DataFrame({
                    t("common.success"): success_values,
                    t("common.error"): failed_values,
                }, index=labels)
                st.bar_chart(chart_data, width="stretch")

        with col2:
            st.markdown(f"#### {t('monitor.execution_duration')}")
            if history:
                durations = [state.duration_ms for state in history if state.duration_ms > 0]
                if durations:
                    st.line_chart(durations, width="stretch")
                    st.caption(t("monitor.recent_executions"))


def render_active_executions():
    """Afficher les exécutions en cours."""
    st.markdown("---")
    st.markdown(f"### ⏳ {t('monitoring.active_executions')}")

    execution_service = ExecutionService()
    active = execution_service.get_active_executions()

    if not active:
        st.info(t("runtime.no_history"))
        return

    for state in active:
        with st.container(border=True):
            col1, col2, col3, col4 = st.columns(4)

            with col1:
                st.markdown(f"**{state.flow_name}**")
                st.caption(f"ID: {state.execution_id[:8]}...")

            with col2:
                st.metric(t("runtime.duration"), f"{state.duration_ms/1000:.1f}s")

            with col3:
                st.metric(f"{t('queue.stats_in')} → {t('queue.stats_out')}", f"{state.input_flowfiles} → {state.output_flowfiles}")

            with col4:
                st.markdown(f"⏳ {t('runtime.running')}")

            # Bouton d'arrêt
            col_btn, _ = st.columns(2)
            if col_btn.button(f"⏹️ {t('runtime.stop')}", key=f"stop_{state.execution_id}", width="stretch"):
                execution_service.cancel_execution(state.execution_id)
                st.success(t("common.success"))
                st.rerun()


def render_execution_history():
    """Afficher l'historique des exécutions avec cards cliquables."""
    st.markdown("---")
    st.markdown(f"### 📋 {t('runtime.history')}")

    execution_service = ExecutionService()
    history = execution_service.get_execution_history(limit=50)

    if not history:
        st.info(t("runtime.no_history"))
        return

    # Affichage en cards cliquables
    selected_execution = st.session_state.get("selected_execution_id")

    for state in reversed(history):
        status_emoji = {
            "running": "⏳",
            "success": "✅",
            "failed": "❌",
            "cancelled": "🚫",
        }.get(state.status, "❓")

        # Carte d'exécution
        is_selected = selected_execution == state.execution_id

        with st.container(border=True):
            col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 1])

            with col1:
                expanded = st.expander(
                    f"**{state.flow_name}** {status_emoji} {state.status}",
                    expanded=is_selected,
                    key=f"exec_{state.execution_id}",
                )

                if expanded:
                    # Détails de l'exécution
                    st.markdown(f"**ID:** `{state.execution_id[:16]}...`")
                    st.markdown(f"**Date:** {state.start_time.strftime('%Y-%m-%d %H:%M:%S') if state.start_time else 'N/A'}")

                    # Statistiques
                    st.markdown(f"#### {t('monitoring.statistics')}")
                    k1, k2, k3, k4 = st.columns(4)
                    with k1:
                        st.metric(t("queue.stats_in"), state.input_flowfiles)
                    with k2:
                        st.metric(t("queue.stats_out"), state.output_flowfiles)
                    with k3:
                        st.metric(t("runtime.duration"), f"{state.duration_ms/1000:.2f}s")
                    with k4:
                        st.metric(t("queue.flowfile_size"), f"{state.bytes_processed/1024:.1f} KB")

                    # Erreurs
                    if state.errors:
                        st.markdown(f"#### ❌ {t('common.error')}")
                        for error in state.errors:
                            st.error(f"**Error:** {error.get('error', str(error))}")
                            st.caption(f"**Timestamp:** {error.get('timestamp', 'N/A')}")

                    # Bouton de visualisation
                    st.button(f"🔍 {t('monitor.view_full_details')}", key=f"view_{state.execution_id}", width="stretch")

            with col2:
                st.metric(t("runtime.duration"), f"{state.duration_ms/1000:.2f}s")

            with col3:
                st.metric(t("queue.stats_in"), state.input_flowfiles)

            with col4:
                st.metric(t("queue.stats_out"), state.output_flowfiles)

            with col5:
                st.markdown(f"**{t('monitor.date')}**")
                st.caption(state.start_time.strftime("%d/%m %H:%M") if state.start_time else "N/A")


def render_error_summary():
    """Afficher un résumé des erreurs."""
    st.markdown("---")
    st.markdown(f"### ❌ {t('common.error')}")

    execution_service = ExecutionService()
    history = execution_service.get_execution_history(limit=100)

    errors = []

    for state in history:
        if state.errors:
            for error in state.errors:
                errors.append(
                    {
                        "Flow": state.flow_name,
                        t("common.error"): error.get("error", "Unknown error"),
                        "Date": state.start_time.strftime("%Y-%m-%d %H:%M")
                        if state.start_time
                        else "N/A",
                    }
                )

    if not errors:
        st.success(t("common.none"))
        return

    # Afficher les erreurs
    for error in errors[-10:]:  # 10 dernières erreurs
        with st.expander(f"❌ {error['Flow']}"):
            st.error(error[t("common.error")])
            st.caption(f"Date: {error['Date']}")


def render_bulletin_board():
    """Afficher le tableau d'affichage (bulletin board)."""
    st.markdown("---")
    st.markdown(f"### 📋 {t('monitoring.bulletins')}")

    board = BulletinBoard.get_instance()
    counts = board.count_by_level()

    # KPIs
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric(t("monitor.info"), counts.get("INFO", 0))
    with col2:
        st.metric(t("common.warning"), counts.get("WARNING", 0))
    with col3:
        st.metric(t("common.error"), counts.get("ERROR", 0))
    with col4:
        if check_permission(session, "monitor.clear"):
            if st.button(f"🗑️ {t('common.delete')}", key="clear_bulletin"):
                board.clear()
                st.rerun()
        else:
            st.button(f"🗑️ {t('common.delete')}", key="clear_bulletin",
                      disabled=True, help=t("auth.no_permission"))

    # Level filter
    level_filter = st.selectbox(
        t("common.search"),
        [t("common.all"), "INFO", "WARNING", "ERROR"],
        key="bulletin_level_filter",
    )

    # Messages
    messages = board.get_messages(
        limit=50,
        level=level_filter if level_filter != t("common.all") else None,
    )

    if not messages:
        st.info(t("queue.empty"))
        return

    level_icons = {"INFO": "🔵", "WARNING": "🟡", "ERROR": "🔴"}

    for msg in messages:
        icon = level_icons.get(msg["level"], "⚪")
        ts = msg["timestamp"][:19] if msg.get("timestamp") else ""
        st.markdown(
            f"{icon} **[{msg['level']}]** `{msg['source']}` — {msg['message']}  \n"
            f"<small style='color:gray'>{ts}</small>",
            unsafe_allow_html=True,
        )


def render_provenance_viewer():
    """Afficher le visualiseur de provenance."""
    st.markdown(f"### 🔍 {t('monitoring.provenance')}")

    provenance_repo = get_provenance_repository()

    # Summary stats
    repo_stats = provenance_repo.to_dict()
    st.markdown(f"#### 📊 {t('monitoring.statistics')}")
    cols = st.columns(len(ProvenanceEventType) + 1)
    with cols[0]:
        st.metric(t("common.total"), repo_stats["total_events"])
    for i, et in enumerate(ProvenanceEventType):
        with cols[i + 1]:
            st.metric(et.value, repo_stats["events_by_type"].get(et.value, 0))

    # Filters
    st.markdown("---")
    st.markdown(f"#### 🔍 {t('common.filters')}")
    col1, col2, col3 = st.columns(3)

    with col1:
        event_type_options = [t("common.all")] + [et.value for et in ProvenanceEventType]
        selected_event_type = st.selectbox(
            t("common.type"), event_type_options, key="prov_event_type"
        )
    with col2:
        task_id_filter = st.text_input(t("monitor.task_id"), key="prov_task_id")
    with col3:
        flowfile_id_filter = st.text_input(t("monitor.flowfile_id"), key="prov_ff_id")

    # Build filter kwargs
    filter_kwargs = {}
    if selected_event_type != t("common.all"):
        filter_kwargs["event_type"] = ProvenanceEventType(selected_event_type)
    if task_id_filter:
        filter_kwargs["task_id"] = task_id_filter
    if flowfile_id_filter:
        filter_kwargs["flowfile_id"] = flowfile_id_filter

    filtered_events = provenance_repo.get_events(limit=500, **filter_kwargs)

    if filtered_events:
        import pandas as pd
        data = []
        for ev in filtered_events:
            data.append({
                "Timestamp": ev.timestamp.strftime("%H:%M:%S.%f")[:-3] if ev.timestamp else "",
                "Type": ev.event_type.value,
                "Task": ev.task_id or "",
                "Task Type": ev.task_type or "",
                "FlowFile": ev.flowfile_id[:8] + "..." if ev.flowfile_id and len(ev.flowfile_id) > 8 else (ev.flowfile_id or ""),
                "Size (B)": ev.content_size,
                "Duration (ms)": round(ev.duration_ms, 1),
                "Details": ev.details or "",
            })
        st.markdown(f"#### {t('monitor.events_count', count=len(filtered_events))}")
        st.dataframe(pd.DataFrame(data), width="stretch", hide_index=True)
    else:
        st.info(t("queue.empty"))

    # Lineage
    st.markdown("---")
    st.markdown(f"#### 🧬 {t('monitor.lineage')}")
    lineage_ff_id = st.text_input(t("queue.flowfile_id"), key="lineage_ff_id")
    if st.button(f"🔍 {t('monitor.trace')}", key="trace_lineage"):
        if lineage_ff_id:
            lineage = provenance_repo.get_lineage(lineage_ff_id)
            if lineage:
                import pandas as pd
                ld = [{
                    "Timestamp": e.timestamp.strftime("%H:%M:%S.%f")[:-3],
                    "Type": e.event_type.value,
                    "Task": e.task_id,
                    "FlowFile": e.flowfile_id,
                    "Details": e.details or "",
                } for e in lineage]
                st.dataframe(pd.DataFrame(ld), width="stretch", hide_index=True)
            else:
                st.info(t("queue.empty"))
        else:
            st.warning(t("common.warning"))

    # Clear
    st.markdown("---")
    if check_permission(session, "monitor.clear"):
        if st.button(f"🗑️ {t('common.delete')}", key="clear_prov", width="stretch"):
            provenance_repo.clear()
            st.success(t("common.success"))
            st.rerun()
    else:
        st.button(f"🗑️ {t('common.delete')}", key="clear_prov", width="stretch",
                  disabled=True, help=t("auth.no_permission"))


def render_spill_tracker():
    """Display SpillTracker stats and memory info."""
    st.markdown(f"### 💾 {t('monitoring.streaming')}")

    try:
        from core.stream import get_spill_tracker
        tracker = get_spill_tracker()
        stats = tracker.get_stats()

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric(t("monitor.active_spill_files"), stats.get("active_spill_files", 0))
        with col2:
            total_bytes = stats.get("total_bytes_on_disk", 0)
            st.metric(t("monitor.total_size"), f"{total_bytes / 1024:.1f} KB")
        with col3:
            st.metric(t("monitor.total_spills"), stats.get("total_spill_count", 0))
        with col4:
            st.metric(t("monitor.cleaned_orphans"), stats.get("total_cleaned", 0))

        # Extra info
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric(t("monitor.live_references"), stats.get("live_references", 0))
        with col2:
            st.metric(t("monitor.orphans_detected"), stats.get("orphaned", 0))
        with col3:
            if st.button(f"🧹 {t('monitor.clean_orphans')}", width="stretch"):
                cleaned = tracker.cleanup_orphans()
                st.success(t("monitor.orphans_cleaned", count=cleaned))
                st.rerun()

    except Exception as e:
        st.info(t("monitor.spilltracker_unavailable", error=str(e)))

    # Continuous executor queue stats (if running)
    st.markdown("---")
    st.markdown(f"#### 🔄 {t('queue.title')}")

    executors = st.session_state.get("_continuous_executors", {})
    if executors:
        for fid, executor in executors.items():
            try:
                status = executor.get_status()
            except Exception:
                continue
            st.markdown(
                f"**{fid}** v{status['flow_version']} — "
                f"{'🟢 ' + t('monitor.active') if status['is_running'] else '🔴 ' + t('monitor.stopped')} — "
                f"{t('monitor.queued_flowfiles', count=status['total_queued_flowfiles'])}"
            )

            queue_stats = status.get("queue_stats", [])
            if queue_stats:
                import pandas as pd
                data = []
                for qs in queue_stats:
                    max_q = qs.get("max_queue_size", qs.get("max_size", 1)) or 1
                    pct = (qs["queue_size"] / max_q * 100) if max_q > 0 else 0
                    data.append({
                        "Source": qs["source"],
                        "Target": qs["target"],
                        "Queue": f"{qs['queue_size']}/{max_q}",
                        "Fill %": round(pct, 1),
                        "Bytes": qs.get("queue_bytes", qs.get("total_bytes", 0)),
                        t("queue.backpressure"): t("queue.backpressure_on") if qs.get("backpressured") else t("queue.backpressure_off"),
                    })
                st.dataframe(pd.DataFrame(data), width="stretch", hide_index=True)
    else:
        st.info(t("queue.empty"))

    # Memory info
    st.markdown("---")
    st.markdown(f"#### 🖥️ {t('monitor.memory')}")
    import sys
    import gc
    gc_stats = gc.get_stats()
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(t("monitor.gc_objects", gen=0), gc_stats[0]["collected"])
    with col2:
        st.metric(t("monitor.gc_objects", gen=1), gc_stats[1]["collected"])
    with col3:
        st.metric(t("monitor.gc_objects", gen=2), gc_stats[2]["collected"])


def render_system_info():
    """Afficher les informations systeme."""
    st.markdown("---")
    st.markdown(f"### 🖥️ {t('monitor.system')}")

    import sys
    import os

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(t("monitor.python_version"), f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    with col2:
        from tasks import register_all_tasks
        register_all_tasks()
        from core import TaskFactory
        st.metric(t("tree.tasks"), len(TaskFactory.list_types()))
    with col3:
        from pathlib import Path
        flows_count = len(list(Path("flows").glob("*.json"))) if Path("flows").exists() else 0
        st.metric(t("dashboard.flows"), flows_count)


def main():
    """Fonction principale."""
    menu = render_sidebar()

    if menu == f"🏠 {t('nav.dashboard')}":
        st.switch_page("pages/1_Dashboard.py")
    elif menu == f"✏️ {t('nav.editor')}":
        st.switch_page("pages/2_Editor.py")
    elif menu == f"▶️ {t('nav.runtime')}":
        st.switch_page("pages/3_Runtime.py")
    elif menu == f"📊 {t('nav.monitoring')}":
        tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
            f"📈 {t('nav.dashboard')}", f"📋 {t('monitoring.bulletins')}", f"📜 {t('runtime.history')}",
            f"🔍 {t('monitoring.provenance')}", f"💾 {t('monitoring.streaming')}",
            f"📜 {t('logs.title')}",
        ])

        with tab1:
            render_global_statistics()
            render_active_executions()
            render_system_info()

        with tab2:
            render_bulletin_board()

        with tab3:
            render_execution_history()
            render_error_summary()

            if check_permission(session, "monitor.clear"):
                if st.button(f"🗑️ {t('common.delete')}", key="clear_history", width="stretch"):
                    execution_service = ExecutionService()
                    execution_service.clear_history()
                    st.success(t("common.success"))
                    st.rerun()
            else:
                st.button(f"🗑️ {t('common.delete')}", key="clear_history", width="stretch",
                          disabled=True, help=t("auth.no_permission"))
        with tab4:
            render_provenance_viewer()

        with tab5:
            render_spill_tracker()

        with tab6:
            from gui.components.log_viewer import render_log_viewer, LogCapture
            # Initialize global capture
            LogCapture.get_global()
            render_log_viewer(key_suffix="global_monitoring")

    elif menu == f"⚙️ {t('nav.settings')}":
        st.switch_page("pages/5_Settings.py")
    elif menu == f"📚 {t('nav.documentation')}":
        st.switch_page("pages/6_Documentation.py")


if __name__ == "__main__":
    main()