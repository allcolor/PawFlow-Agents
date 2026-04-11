# Execution Monitor

"""
Monitoring en temps réel de l'exécution des flux.
Affiche les statistiques, logs et erreurs.
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
import streamlit as st

from gui.services.execution_service import ExecutionState, ExecutionService
from gui.i18n import t

logger = logging.getLogger(__name__)


class ExecutionMonitor:
    """Moniteur d'exécution des flux."""

    def __init__(self, execution_service: Optional[ExecutionService] = None):
        """
        Initialiser le moniteur.

        Args:
            execution_service: Service d'exécution
        """
        self.execution_service = execution_service or ExecutionService()

    def render_execution_panel(
        self,
        execution_state: Optional[ExecutionState] = None,
    ):
        """
        Afficher le panneau de monitoring d'exécution.

        Args:
            execution_state: État de l'exécution à afficher
        """
        if execution_state:
            self._render_execution_details(execution_state)
        else:
            self._render_execution_list()

    def _render_execution_details(self, state: ExecutionState):
        """
        Afficher les détails d'une exécution.

        Args:
            state: État de l'exécution
        """
        # Titre avec statut
        status_emoji = {
            "running": "⏳",
            "success": "✅",
            "failed": "❌",
            "cancelled": "🚫",
        }.get(state.status, "❓")

        st.markdown(f"### {status_emoji} {t('monitor.execution', id=state.execution_id[:8])}...")

        if state.status == "running":
            st.progress(0.5, text=t("monitor.running_progress"))
        elif state.status == "success":
            st.progress(1.0, text=t("monitor.success_progress"))
        elif state.status == "failed":
            st.progress(0, text=t("monitor.failed_progress"))
        elif state.status == "cancelled":
            st.progress(0, text=t("monitor.cancelled_progress"))

        # Informations principales
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric(t("monitor.status"), self._status_to_display(state.status))

        with col2:
            st.metric(
                t("monitor.duration"),
                f"{state.duration_ms/1000:.2f}s" if state.duration_ms > 0 else "N/A",
            )

        with col3:
            st.metric(
                t("monitor.inputs"),
                state.input_flowfiles,
            )

        with col4:
            st.metric(
                t("monitor.outputs"),
                state.output_flowfiles,
            )

        st.markdown("---")

        # Détails temporels
        col1, col2 = st.columns(2)

        with col1:
            if state.start_time:
                st.markdown(f"**{t('monitor.start')}:** {state.start_time.strftime('%H:%M:%S')}")

        with col2:
            if state.end_time:
                st.markdown(f"**{t('monitor.end')}:** {state.end_time.strftime('%H:%M:%S')}")

        st.markdown("---")

        # Statistiques détaillées
        if state.statistics:
            st.markdown(f"### 📊 {t('monitor.detailed_stats')}")

            for key, value in state.statistics.items():
                if key not in ["input_flowfiles", "output_flowfiles", "bytes_processed"]:
                    st.metric(key.replace("_", " ").title(), value)

        st.markdown("---")

        # Erreurs
        if state.errors:
            st.markdown(f"### ❌ {t('monitor.errors')}")

            for error in state.errors:
                with st.expander(f"🔴 {t('monitor.view_error')}"):
                    error_msg = error.get("error", str(error))
                    error_time = error.get("timestamp", "N/A")

                    st.error(f"**{t('monitor.error_label')}:** {error_msg}")
                    st.caption(f"**{t('monitor.timestamp')}:** {error_time}")

        # Boutons d'action
        st.markdown("---")
        col1, col2, col3 = st.columns(3)

        with col1:
            if st.button(f"🔄 {t('common.refresh')}", key="refresh_execution"):
                st.rerun()

        with col2:
            import json as _json
            result_data = state.to_dict()
            result_json = _json.dumps(result_data, indent=2, ensure_ascii=False, default=str)
            st.download_button(
                f"📥 {t('monitor.download_results')}",
                data=result_json,
                file_name=f"execution_{state.execution_id[:8]}.json",
                mime="application/json",
                key="download_results",
            )

        with col3:
            if st.button(f"🗑️ {t('common.delete')}", key="delete_execution"):
                self.execution_service.delete_execution(state.execution_id)
                st.success(t("monitor.deleted"))
                st.rerun()

    def _status_to_display(self, status: str) -> str:
        """Convertir le statut en affichage lisible."""
        display = {
            "running": t("monitor.status_running"),
            "success": t("monitor.status_success"),
            "failed": t("monitor.status_failed"),
            "cancelled": t("monitor.status_cancelled"),
        }
        return display.get(status, status)

    def _render_execution_list(self):
        """Afficher la liste des exécutions récentes."""
        # Récupérer les exécutions actives et historiques
        active = self.execution_service.get_active_executions()
        history = self.execution_service.get_execution_history(limit=20)

        # Combiner et trier
        all_executions = list(set([e.execution_id for e in active + history]))

        st.markdown(f"### 📋 {t('monitor.recent_executions')}")

        if not all_executions:
            st.info(t("monitor.no_executions"))
            return

        # Afficher les exécutions
        for execution_id in all_executions[:10]:
            state = self.execution_service.get_execution(execution_id)
            if state:
                self._render_execution_card(state)

    def _render_execution_card(self, state: ExecutionState):
        """Afficher une carte d'exécution."""
        status_emoji = {
            "running": "⏳",
            "success": "✅",
            "failed": "❌",
            "cancelled": "🚫",
        }.get(state.status, "❓")

        # Container avec style
        with st.container():
            col1, col2, col3 = st.columns([3, 1, 1])

            with col1:
                st.markdown(f"**{state.flow_name}**")
                st.caption(f"ID: {state.execution_id[:8]}...")

            with col2:
                st.metric(t("monitor.duration"), f"{state.duration_ms/1000:.1f}s" if state.duration_ms else "0s")

            with col3:
                st.metric(t("monitor.status"), self._status_to_display(state.status))

            # Bouton de détails
            if st.button(
                t("monitor.view_details"),
                key=f"details_{state.execution_id}",
                width="stretch",
                type="secondary",
            ):
                with st.expander(f"🔍 {t('monitor.details', id=state.execution_id[:8])}..."):
                    self._render_execution_details(state)

            st.markdown("---")

    def render_statistics_dashboard(self):
        """Afficher le tableau de bord des statistiques."""
        st.markdown(f"### 📈 {t('monitor.global_stats')}")

        # Récupérer les statistiques
        stats = self.execution_service.get_statistics()

        # Cartes de statistiques
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric(
                t("monitor.total_executions"),
                stats.get("total_executions", 0),
            )

        with col2:
            st.metric(
                t("monitor.successes"),
                stats.get("success_count", 0),
                delta=f"{stats.get('success_rate', 0):.1f}% {t('monitor.success_rate')}",
            )

        with col3:
            st.metric(
                t("monitor.failures"),
                stats.get("failed_count", 0),
            )

        with col4:
            st.metric(
                t("monitor.avg_duration"),
                f"{stats.get('avg_duration_ms', 0)/1000:.2f}s",
            )

        st.markdown("---")

        # Graphique des exécutions dans le temps
        st.markdown(f"### 📊 {t('monitor.execution_trend')}")

        history = self.execution_service.get_execution_history(limit=50)
        timed = [e for e in history if e.start_time and e.duration_ms > 0]

        if timed:
            chart_data = {
                "Execution": [e.execution_id[:8] for e in timed],
                "Duration (s)": [e.duration_ms / 1000 for e in timed],
                "Status": [1 if e.status == "success" else 0 for e in timed],
            }
            st.line_chart(chart_data, x="Execution", y="Duration (s)")

            # Success/failure bar
            success = sum(1 for e in timed if e.status == "success")
            failed = sum(1 for e in timed if e.status == "failed")
            col1, col2 = st.columns(2)
            with col1:
                st.metric(t("monitor.successes"), success)
            with col2:
                st.metric(t("monitor.failures"), failed)
        else:
            st.info(t("monitor.no_chart_data"))

    def update_execution(self, execution_id: str) -> Optional[ExecutionState]:
        """
        Mettre à jour l'état d'une exécution.

        Args:
            execution_id: ID de l'exécution

        Returns:
            État mis à jour ou None
        """
        return self.execution_service.get_execution(execution_id)

    def get_active_count(self) -> int:
        """Récupérer le nombre d'exécutions actives."""
        return len(self.execution_service.get_active_executions())