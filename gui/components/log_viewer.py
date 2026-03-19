"""Log Viewer component for the GUI.

Provides a reusable log viewer that shows:
- Python logging output (captured via handler)
- BulletinBoard messages
- Per-flow and per-task filtering
"""

import logging
import threading
from typing import List, Dict, Optional
from datetime import datetime
from collections import deque

import streamlit as st
from gui.i18n import t


class LogCapture(logging.Handler):
    """In-memory logging handler that captures log records for GUI display.

    Thread-safe, singleton per flow_id.
    """

    _instances: Dict[str, "LogCapture"] = {}
    _global_instance: Optional["LogCapture"] = None
    _lock = threading.Lock()

    def __init__(self, max_records: int = 500):
        super().__init__()
        self._records: deque = deque(maxlen=max_records)
        self._record_lock = threading.Lock()

    @classmethod
    def get_global(cls) -> "LogCapture":
        """Get the global log capture handler."""
        if cls._global_instance is None:
            with cls._lock:
                if cls._global_instance is None:
                    cls._global_instance = cls(max_records=1000)
                    # Attach to root logger for openpaw modules
                    for module in ["engine", "core", "tasks", "services"]:
                        logger = logging.getLogger(module)
                        if cls._global_instance not in logger.handlers:
                            logger.addHandler(cls._global_instance)
        return cls._global_instance

    @classmethod
    def get_for_flow(cls, flow_id: str) -> "LogCapture":
        """Get or create a per-flow log capture."""
        if flow_id not in cls._instances:
            with cls._lock:
                if flow_id not in cls._instances:
                    cls._instances[flow_id] = cls(max_records=500)
        return cls._instances[flow_id]

    @classmethod
    def remove_flow(cls, flow_id: str):
        """Remove a per-flow capture."""
        with cls._lock:
            cls._instances.pop(flow_id, None)

    def emit(self, record: logging.LogRecord):
        entry = {
            "timestamp": datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3],
            "level": record.levelname,
            "source": record.name,
            "message": record.getMessage(),
            "task_id": getattr(record, "task_id", None),
            "flow_id": getattr(record, "flow_id", None),
        }
        with self._record_lock:
            self._records.append(entry)

    def get_records(self, limit: int = 100, level: str = None,
                    task_id: str = None) -> List[Dict]:
        """Get log records, newest first."""
        with self._record_lock:
            records = list(self._records)

        if level:
            records = [r for r in records if r["level"] == level]
        if task_id:
            records = [r for r in records if r.get("task_id") == task_id]

        return list(reversed(records))[:limit]

    def clear(self):
        with self._record_lock:
            self._records.clear()

    def count(self) -> int:
        with self._record_lock:
            return len(self._records)


def render_log_viewer(
    flow_id: str = None,
    task_ids: List[str] = None,
    key_suffix: str = "",
    show_header: bool = True,
    max_lines: int = 100,
):
    """Render a log viewer panel.

    Args:
        flow_id: If provided, show per-flow logs. Otherwise show global.
        task_ids: List of task IDs for per-task filter dropdown.
        key_suffix: Unique suffix for streamlit widget keys.
        show_header: Whether to show the section header.
        max_lines: Max log lines to display.
    """
    if show_header:
        st.markdown(f"#### 📜 {t('logs.title')}")

    # Get the right capture
    if flow_id:
        capture = LogCapture.get_for_flow(flow_id)
    else:
        capture = LogCapture.get_global()

    # Controls row 1: filters
    col1, col2, col3, col4 = st.columns([2, 2, 1, 1])

    with col1:
        level_filter = st.selectbox(
            t("logs.level"),
            ["ALL", "DEBUG", "INFO", "WARNING", "ERROR"],
            key=f"log_level_{key_suffix}",
        )

    with col2:
        task_filter = None
        if task_ids:
            options = ["ALL"] + list(task_ids)
            selected_task = st.selectbox(
                t("logs.task_filter"),
                options,
                key=f"log_task_{key_suffix}",
            )
            if selected_task != "ALL":
                task_filter = selected_task

    with col3:
        st.metric(t("logs.count"), capture.count())

    with col4:
        if st.button(f"🗑️ {t('logs.clear')}", key=f"log_clear_{key_suffix}"):
            capture.clear()
            st.rerun()

    # Controls row 2: text search + export + persist
    col_search, col_export, col_persist = st.columns([3, 1, 1])

    with col_search:
        search_text = st.text_input(
            t("common.search"),
            key=f"log_search_{key_suffix}",
            placeholder=t("logs.search_placeholder"),
            label_visibility="collapsed",
        )

    with col_export:
        # Get all records for export
        all_records = capture.get_records(
            limit=max_lines * 10,
            level=level_filter if level_filter != "ALL" else None,
            task_id=task_filter,
        )
        if all_records:
            import json
            export_lines = []
            for rec in all_records:
                task_tag = f" [{rec['task_id']}]" if rec.get("task_id") else ""
                export_lines.append(
                    f"{rec['timestamp']} {rec['level']}{task_tag} "
                    f"{rec['source']} — {rec['message']}"
                )
            export_text = "\n".join(export_lines)
            st.download_button(
                f"📥 {t('logs.export')}",
                data=export_text,
                file_name=f"openpaw_logs{'_' + flow_id if flow_id else ''}.log",
                mime="text/plain",
                key=f"log_export_{key_suffix}",
            )

    with col_persist:
        if capture.count() > 0:
            if st.button(f"💾 {t('logs.persist_to_file')}", key=f"log_persist_{key_suffix}"):
                from gui.components.log_persistence import LogPersistence
                persistence = LogPersistence()
                all_recs = capture.get_records(limit=10000)
                path = persistence.save_records(all_recs, flow_id=flow_id)
                st.success(t("logs.file_saved", path=path))

    # Get records
    records = capture.get_records(
        limit=max_lines,
        level=level_filter if level_filter != "ALL" else None,
        task_id=task_filter,
    )

    # Apply text search filter
    if search_text:
        search_lower = search_text.lower()
        records = [
            r for r in records
            if search_lower in r["message"].lower()
            or search_lower in r.get("source", "").lower()
            or search_lower in r.get("task_id", "").lower()
        ]

    if not records:
        st.info(t("logs.empty"))
        return

    # Render as styled log output
    level_icons = {
        "DEBUG": "⚪",
        "INFO": "🔵",
        "WARNING": "🟡",
        "ERROR": "🔴",
        "CRITICAL": "💀",
    }

    log_lines = []
    for rec in records:
        icon = level_icons.get(rec["level"], "⚪")
        task_tag = f" [{rec['task_id']}]" if rec.get("task_id") else ""
        log_lines.append(
            f"{icon} `{rec['timestamp']}` **{rec['level']}**{task_tag} "
            f"`{rec['source']}` — {rec['message']}"
        )

    # Use a scrollable container
    st.markdown("\n\n".join(log_lines))


def render_log_viewer_expander(
    flow_id: str = None,
    task_ids: List[str] = None,
    key_suffix: str = "",
    expanded: bool = False,
):
    """Render a log viewer inside an expander."""
    count = 0
    if flow_id:
        count = LogCapture.get_for_flow(flow_id).count()
    else:
        count = LogCapture.get_global().count()

    label = f"📜 {t('logs.title')} ({count})"
    with st.expander(label, expanded=expanded):
        render_log_viewer(
            flow_id=flow_id,
            task_ids=task_ids,
            key_suffix=key_suffix,
            show_header=False,
        )
