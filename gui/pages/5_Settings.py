#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Page Paramètres.
Configuration de l'application et du stockage.
"""

import streamlit as st
from pathlib import Path
from typing import Dict, Any

# Configuration de la page
st.set_page_config(
    page_title="Paramètres - PyFi2",
    page_icon="⚙️",
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
from gui.services.storage_service import StorageService


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
            index=4,
        )

        st.markdown("---")
        st.markdown(f"### {t('common.version')}")
        st.caption("PyFi2 v0.1.0")

        return menu


def render_general_settings():
    """Paramètres généraux."""
    st.markdown(f"### ⚙️ {t('settings.general')}")

    # Nom de l'application
    st.session_state.settings["app_name"] = st.text_input(
        t("settings.app_name_label"),
        value=st.session_state.settings.get("app_name", "PyFi2 - Pipeline Framework"),
        key="settings_app_name",
    )

    # Thème
    themes = ["light", "dark", "auto"]
    st.session_state.settings["theme"] = st.selectbox(
        t("settings.theme"),
        options=themes,
        index=themes.index(st.session_state.settings.get("theme", "light")),
        key="settings_theme",
    )

    # Auto-save
    st.session_state.settings["auto_save"] = st.checkbox(
        t("settings.auto_save"),
        value=st.session_state.settings.get("auto_save", True),
        key="settings_auto_save",
    )

    # Auto-validation
    st.session_state.settings["auto_validate"] = st.checkbox(
        t("settings.auto_validate"),
        value=st.session_state.settings.get("auto_validate", True),
        key="settings_auto_validate",
    )

    # Debug mode
    st.session_state.settings["debug_mode"] = st.checkbox(
        t("settings.debug_mode"),
        value=st.session_state.settings.get("debug_mode", False),
        key="settings_debug_mode",
    )

    # Log persistence
    st.markdown("---")
    st.markdown(f"#### 📜 {t('logs.persistence')}")

    st.session_state.settings["log_auto_persist"] = st.checkbox(
        t("logs.auto_persist"),
        value=st.session_state.settings.get("log_auto_persist", False),
        key="settings_log_auto_persist",
    )

    st.session_state.settings["log_dir"] = st.text_input(
        t("logs.log_dir"),
        value=st.session_state.settings.get("log_dir", "logs"),
        key="settings_log_dir",
    )

    st.session_state.settings["log_retention_days"] = st.number_input(
        t("logs.retention_days"),
        min_value=1, max_value=365,
        value=st.session_state.settings.get("log_retention_days", 30),
        key="settings_log_retention",
    )

    if st.button(f"🧹 {t('monitor.clean_orphans')}", key="cleanup_old_logs"):
        from gui.components.log_persistence import LogPersistence
        lp = LogPersistence(
            log_dir=st.session_state.settings.get("log_dir", "logs"),
            retention_days=st.session_state.settings.get("log_retention_days", 30),
        )
        removed = lp.cleanup_old_logs()
        st.success(t("settings.logs_cleaned", count=removed))


def render_storage_settings():
    """Paramètres de stockage."""
    st.markdown("---")
    st.markdown(f"### 💾 {t('settings.storage')}")

    # Type de stockage
    storage_types = ["filesystem", "git", "postgresql", "sqlite"]
    current_storage = st.session_state.settings.get("storage_type", "filesystem")

    storage_type = st.selectbox(
        t("settings.storage_type"),
        options=storage_types,
        index=storage_types.index(current_storage) if current_storage in storage_types else 0,
        key="settings_storage_type",
    )

    st.session_state.settings["storage_type"] = storage_type

    # Configuration selon le type
    if storage_type == "filesystem":
        st.markdown(f"#### {t('settings.storage_filesystem')}")

        st.session_state.settings["filesystem_path"] = st.text_input(
            t("settings.directory_path"),
            value=st.session_state.settings.get("filesystem_path", "./flows"),
            key="settings_fs_path",
        )

        # Créer le répertoire s'il n'existe pas
        if st.button(f"📁 {t('settings.create_directory')}"):
            path = Path(st.session_state.settings["filesystem_path"])
            path.mkdir(parents=True, exist_ok=True)
            st.success(t("settings.directory_created", path=str(path)))

    elif storage_type == "git":
        st.markdown(f"#### {t('settings.storage_git')}")

        st.session_state.settings["git_path"] = st.text_input(
            t("settings.repo_path"),
            value=st.session_state.settings.get("git_path", "./flows.git"),
            key="settings_git_path",
        )

        st.session_state.settings["git_auto_commit"] = st.checkbox(
            t("settings.auto_commit"),
            value=st.session_state.settings.get("git_auto_commit", True),
            key="settings_git_commit",
        )

    elif storage_type == "postgresql":
        st.markdown(f"#### {t('settings.storage_postgresql')}")

        col1, col2 = st.columns(2)

        with col1:
            st.session_state.settings["postgres_host"] = st.text_input(
                t("settings.host"),
                value=st.session_state.settings.get("postgres_host", "localhost"),
                key="settings_pg_host",
            )

            st.session_state.settings["postgres_db"] = st.text_input(
                t("settings.database"),
                value=st.session_state.settings.get("postgres_db", "pyfi2"),
                key="settings_pg_db",
            )

        with col2:
            st.session_state.settings["postgres_port"] = st.number_input(
                "Port",
                value=int(st.session_state.settings.get("postgres_port", 5432)),
                key="settings_pg_port",
                min_value=1,
                max_value=65535,
            )

            st.session_state.settings["postgres_user"] = st.text_input(
                t("settings.users"),
                value=st.session_state.settings.get("postgres_user", "pyfi2"),
                key="settings_pg_user",
            )

    elif storage_type == "sqlite":
        st.markdown(f"#### {t('settings.storage_sqlite')}")

        st.session_state.settings["sqlite_path"] = st.text_input(
            t("settings.db_path"),
            value=st.session_state.settings.get("sqlite_path", "./flows.db"),
            key="settings_sqlite_path",
        )


def render_execution_settings():
    """Paramètres d'exécution."""
    st.markdown("---")
    st.markdown(f"### ▶️ {t('settings.execution_settings')}")

    # Max workers
    st.session_state.settings["max_workers"] = st.number_input(
        t("settings.max_workers"),
        value=int(st.session_state.settings.get("max_workers", 10)),
        key="settings_max_workers",
        min_value=1,
        max_value=100,
    )

    # Max retries
    st.session_state.settings["max_retries"] = st.number_input(
        t("settings.max_retries"),
        value=int(st.session_state.settings.get("max_retries", 3)),
        key="settings_max_retries",
        min_value=0,
        max_value=10,
    )

    # Timeout
    st.session_state.settings["default_timeout"] = st.number_input(
        t("settings.default_timeout"),
        value=int(st.session_state.settings.get("default_timeout", 300)),
        key="settings_timeout",
        min_value=10,
        max_value=3600,
    )


def render_reset():
    """Réinitialisation."""
    st.markdown("---")
    st.markdown(f"### 🗑️ {t('settings.reset_title')}")

    st.warning(t("settings.reset_warning"))

    col1, col2 = st.columns(2)

    with col1:
        if st.button(
            f"🔄 {t('settings.reset_button')}",
            width="stretch",
            type="secondary",
        ):
            st.session_state.settings = {
                "app_name": "PyFi2 - Pipeline Framework",
                "theme": "light",
                "auto_save": True,
                "auto_validate": True,
                "debug_mode": False,
                "storage_type": "filesystem",
                "filesystem_path": "./flows",
                "git_path": "./flows.git",
                "git_auto_commit": True,
                "postgres_host": "localhost",
                "postgres_port": 5432,
                "postgres_db": "pyfi2",
                "postgres_user": "pyfi2",
                "sqlite_path": "./flows.db",
                "max_workers": 10,
                "max_retries": 3,
                "default_timeout": 300,
            }
            st.success(t("settings.reset_success"))
            st.rerun()

    with col2:
        st.caption(t("settings.delete_warning"))


def render_settings_tabs():
    """Afficher les paramètres avec onglets."""
    from tasks import register_all_tasks, get_available_tasks

    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10, tab11, tab12 = st.tabs([
        f"⚙️ {t('settings.general')}", f"📦 {t('dashboard.total_tasks')}", "📥 Import/Export",
        f"🔌 {t('settings.services')}", f"🧩 {t('settings.plugins')}", f"🔐 {t('settings.security')}",
        f"📋 {t('settings.audit')}", f"🔔 {t('settings.webhooks')}", f"🖥️ {t('settings.cluster')}",
        f"🔑 {t('secrets.title')}", f"📎 {t('params.title')}",
        f"🔄 {t('settings.nifi_import')}",
    ])

    with tab1:
        if check_permission(session, "settings.edit"):
            render_general_settings()
            render_storage_settings()
            render_execution_settings()
        else:
            st.info(t("auth.no_permission"))

    with tab2:
        st.markdown(f"### 📦 {t('dashboard.total_tasks')}")

        register_all_tasks()

        tasks = get_available_tasks()

        # Afficher dans un tableau
        if tasks:
            st.dataframe(
                tasks,
                column_config={
                    "type": st.column_config.TextColumn(t("common.type")),
                    "name": st.column_config.TextColumn(t("common.name")),
                    "description": st.column_config.TextColumn(t("common.description")),
                },
                hide_index=True,
                width="stretch",
            )
            st.caption(t("settings.tasks_available", count=len(tasks)))
        else:
            st.info(t("common.none"))

    with tab3:
        st.markdown(f"### 📥 {t('settings.import_export')}")

        # Import
        if check_permission(session, "flow.import"):
            st.markdown(f"#### {t('settings.import_flow')}")
            st.caption(t("settings.import_flow_hint"))

            uploaded_file = st.file_uploader(
                t("settings.choose_json"),
                type=["json"],
                key="import_flow",
                help=t("settings.json_help"),
            )
        else:
            st.info(t("settings.import_permission_needed"))
            uploaded_file = None

        if uploaded_file:
            import json

            try:
                content = json.loads(uploaded_file.read().decode("utf-8"))

                # Créer le répertoire flows si nécessaire
                flows_dir = Path("flows")
                flows_dir.mkdir(exist_ok=True)

                # Sauvegarder le fichier
                flow_id = content.get("id", uploaded_file.name.replace(".json", ""))
                filepath = flows_dir / f"{flow_id}.json"

                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(content, f, indent=2, ensure_ascii=False)

                st.success(f"✅ {t('settings.flow_imported', path=str(filepath))}")

                # Réinitialiser l'upload pour permettre de re-uploader le même fichier
                st.session_state.import_flow = None

            except json.JSONDecodeError as e:
                st.error(f"❌ {t('settings.invalid_json', error=str(e))}")
            except Exception as e:
                st.error(f"❌ {t('settings.import_error', error=str(e))}")

        # Export
        st.markdown(f"#### {t('settings.export_flows')}")
        st.caption(t("settings.export_list"))

        flows_dir = Path("flows")
        if flows_dir.exists():
            json_files = list(flows_dir.glob("*.json"))
            if json_files:
                for f in json_files:
                    st.markdown(f"- `{f.name}` ({f.stat().st_size} bytes)")
            else:
                st.info(t("common.none"))
        else:
            st.info(t("dashboard.no_flows"))

    with tab4:
        st.markdown(f"### 🔌 {t('settings.controller_services')}")

        from core import ServiceFactory

        service_types = ServiceFactory.list_types()

        if service_types:
            for stype in service_types:
                sclass = ServiceFactory.get(stype)
                with st.expander(f"**{sclass.NAME}** (`{sclass.TYPE}`)"):
                    st.caption(sclass.DESCRIPTION)
                    st.markdown(f"**{t('common.version')}:** {sclass.VERSION}")

                    schema = sclass.get_parameter_schema()
                    if schema:
                        st.markdown(f"**{t('runtime.parameters')}:**")
                        for param_name, param_info in schema.items():
                            required = "✅" if param_info.get("required") else "⬜"
                            default = param_info.get("default", "—")
                            st.markdown(
                                f"- {required} `{param_name}` — {param_info.get('description', '')} "
                                f"({t('settings.default_label')}: `{default}`)"
                            )
                    else:
                        st.info(t("common.none"))
        else:
            st.info(t("common.none"))

        st.markdown("---")
        st.markdown(f"#### 🔗 {t('settings.redis_test')}")
        st.caption(t("settings.redis_test_hint"))

        redis_host = st.text_input(t("settings.redis_host"), value="localhost", key="redis_test_host")
        redis_port = st.number_input(t("settings.redis_port"), value=6379, key="redis_test_port", min_value=1, max_value=65535)

        if st.button(f"🔍 {t('settings.redis_test_button')}"):
            try:
                import redis
                r = redis.Redis(host=redis_host, port=redis_port, socket_timeout=3)
                r.ping()
                st.success(f"✅ {t('settings.redis_ok')}")
            except ImportError:
                st.warning(f"⚠️ {t('settings.redis_not_installed')}")
            except Exception as e:
                st.error(f"❌ {t('settings.redis_failed', error=str(e))}")

    with tab5:
        render_plugins_tab()

    with tab6:
        if check_permission(session, "user.manage"):
            from gui.utils.auth import render_security_settings
            render_security_settings()
        else:
            st.info(t("auth.no_permission"))

    with tab7:
        render_audit_tab()

    with tab8:
        if check_permission(session, "settings.edit"):
            render_webhooks_tab()
        else:
            st.info(t("auth.no_permission"))

    with tab9:
        if check_permission(session, "worker.manage"):
            render_cluster_tab()
        else:
            st.info(t("auth.no_permission"))

    with tab10:
        render_secrets_tab()

    with tab11:
        render_parameter_contexts_tab()

    with tab12:
        render_nifi_import_tab()


def render_audit_tab():
    """Audit log viewer."""
    st.markdown(f"### 📋 {t('settings.audit.title')}")
    from core.audit import AuditLog

    audit = AuditLog.get_instance()
    stats = audit.get_stats()

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(t("common.total"), stats.get("total", 0))
    with col2:
        st.metric(t("common.actions"), len(stats.get("actions", {})))
    with col3:
        st.metric(t("auth.users"), len(stats.get("users", {})))

    # Filters
    col1, col2, col3 = st.columns(3)
    with col1:
        filter_action = st.text_input(t("settings.audit_action_filter"), key="audit_action")
    with col2:
        filter_user = st.text_input(t("auth.username"), key="audit_user")
    with col3:
        filter_limit = st.number_input(t("settings.limit"), value=50, min_value=10, max_value=500, key="audit_limit")

    entries = audit.query(
        action=filter_action or None,
        user=filter_user or None,
        limit=filter_limit,
    )

    if entries:
        import pandas as pd
        df = pd.DataFrame(entries)
        if "details" in df.columns:
            df["details"] = df["details"].apply(lambda d: str(d)[:80] if d else "")
        st.dataframe(df, width="stretch", hide_index=True)

        col1, col2 = st.columns(2)
        with col1:
            if st.button(f"📥 {t('settings.audit.export')}", key="audit_export"):
                st.download_button(t("runtime.download_results"), audit.export_json(),
                                   file_name="audit_log.json", mime="application/json",
                                   key="audit_dl")
        with col2:
            if st.button(f"🗑️ {t('settings.audit.clear')}", key="audit_clear"):
                audit.clear()
                st.rerun()
    else:
        st.info(t("settings.audit.no_entries"))


def render_webhooks_tab():
    """Webhook management."""
    st.markdown(f"### 🔔 {t('settings.webhooks.title')}")
    from core.notifications import NotificationManager, EventType

    nm = NotificationManager.get_instance()

    # List existing webhooks
    st.markdown(f"#### {t('settings.webhooks.title')}")
    webhooks = nm._webhooks if hasattr(nm, '_webhooks') else []
    if webhooks:
        for wh in webhooks:
            with st.container(border=True):
                col1, col2, col3 = st.columns([4, 2, 1])
                with col1:
                    st.markdown(f"**{wh['name']}**")
                    st.caption(f"URL: {wh['url']}")
                    events_str = ", ".join(wh.get('events', [])) if wh.get('events') else t("common.all")
                    st.caption(f"{t('settings.webhooks.events')}: {events_str} | {t('common.total')}: {wh.get('call_count', 0)}")
                with col2:
                    if wh.get('last_error'):
                        st.error(f"{t('common.error')}: {wh['last_error'][:50]}")
                with col3:
                    if st.button("🗑️", key=f"wh_del_{wh['id']}"):
                        nm.unregister_webhook(wh['id'])
                        st.rerun()
    else:
        st.info(t("settings.webhooks.no_webhooks"))

    # Add webhook
    st.markdown(f"#### {t('settings.webhooks.add')}")
    wh_name = st.text_input(t("common.name"), key="wh_name", placeholder="Mon webhook")
    wh_url = st.text_input(t("settings.webhook_url"), key="wh_url", placeholder="https://example.com/webhook")

    event_types = [
        EventType.FLOW_STARTED, EventType.FLOW_COMPLETED, EventType.FLOW_FAILED,
        EventType.TASK_FAILED, EventType.SCHEDULER_JOB_FIRED,
        EventType.SYSTEM_ERROR, EventType.PLUGIN_INSTALLED,
    ]
    wh_events = st.multiselect(t("settings.webhooks.events"), event_types, key="wh_events")
    wh_headers_raw = st.text_area(t("settings.webhook_headers"), value="{}", height=60, key="wh_headers")

    if st.button(t("common.create"), type="primary", key="wh_add"):
        if wh_url:
            import json as _json
            try:
                headers = _json.loads(wh_headers_raw) if wh_headers_raw.strip() else {}
            except Exception:
                headers = {}
            nm.register_webhook(wh_url, events=wh_events or None, headers=headers, name=wh_name)
            st.success(f"{t('common.success')}: {wh_name or wh_url}")
            st.rerun()
        else:
            st.warning(t("runtime.all_fields_required"))

    # Notification history
    st.markdown(f"#### {t('runtime.trigger.history')}")
    history = nm._history[-50:] if hasattr(nm, '_history') else []
    if history:
        import pandas as pd
        rows = []
        for h in reversed(history):
            rows.append({
                "Timestamp": h.get("timestamp", "")[:19],
                "Event": h.get("event", ""),
                "Payload": str(h.get("payload", ""))[:80],
            })
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    else:
        st.info(t("common.none"))


def render_cluster_tab():
    """Cluster and workers management."""
    st.markdown(f"### 🖥️ {t('cluster.title')}")

    from engine.cluster import ClusterState, InstanceRole
    from engine.remote_worker import WorkerCoordinator, WorkerStatus

    cluster = ClusterState()
    instances = cluster.get_instances()

    # -- Cluster Instances --
    st.markdown(f"#### {t('cluster.instances')}")
    if instances:
        for inst in instances:
            alive = inst.is_alive()
            icon = "🟢" if alive else "🔴"
            role_icon = {"coordinator": "👑", "worker": "⚙️", "standby": "💤"}.get(inst.role.value, "❓")
            role_label = t("cluster.leader") if inst.role.value == "coordinator" else t("cluster.follower")
            with st.container(border=True):
                col1, col2, col3, col4 = st.columns([3, 2, 1, 1])
                with col1:
                    st.markdown(f"{icon} {role_icon} **{inst.instance_id[:12]}**")
                    st.caption(f"{role_label} | {inst.host}:{inst.port}")
                with col2:
                    from datetime import datetime as _dt
                    started = _dt.fromtimestamp(inst.started_at).strftime("%Y-%m-%d %H:%M:%S")
                    hb = _dt.fromtimestamp(inst.last_heartbeat).strftime("%H:%M:%S")
                    st.caption(t("monitor.started", time=started))
                    st.caption(f"{t('cluster.heartbeat')}: {hb}")
                with col3:
                    if inst.metadata:
                        st.json(inst.metadata)
                with col4:
                    if not alive:
                        if st.button("🗑️", key=f"rm_inst_{inst.instance_id}"):
                            cluster.remove_instance(inst.instance_id)
                            st.rerun()
    else:
        st.info(t("cluster.no_instances"))
        st.caption("PYFI2_CLUSTER_ENABLED=true")

    # -- Workers --
    st.markdown("---")
    st.markdown(f"#### {t('cluster.workers')}")

    # Get or create WorkerCoordinator in session state
    if "_worker_coordinator" not in st.session_state:
        st.session_state._worker_coordinator = WorkerCoordinator()
    wc = st.session_state._worker_coordinator

    workers = list(wc._workers.values())
    status_icons = {"idle": "🟢", "busy": "🟡", "offline": "🔴", "error": "🔥"}

    if workers:
        for w in workers:
            icon = status_icons.get(w.status.value, "❓")
            with st.container(border=True):
                col1, col2, col3, col4 = st.columns([3, 2, 2, 1])
                with col1:
                    st.markdown(f"{icon} **{w.name}** (`{w.worker_id}`)")
                    st.caption(f"{w.host}:{w.port}" if w.port else w.host)
                with col2:
                    st.caption(f"{t('cluster.status')}: {w.status.value}")
                    st.caption(t("monitor.worker_tasks", current=w.current_tasks, max=w.max_concurrent))
                with col3:
                    st.caption(t("monitor.worker_stats", executed=w.total_executed, errors=w.total_errors))
                    if w.labels:
                        st.caption(f"Labels: {w.labels}")
                with col4:
                    if w.worker_id != "local":
                        if st.button("🗑️", key=f"rm_worker_{w.worker_id}"):
                            wc.unregister_worker(w.worker_id)
                            st.rerun()
    else:
        st.info(t("cluster.no_workers"))

    # Add worker
    with st.expander(f"➕ {t('cluster.add_worker')}"):
        w_name = st.text_input(t("common.name"), key="new_worker_name", placeholder="remote-worker-1")
        w_host = st.text_input(t("settings.host"), key="new_worker_host", value="localhost")
        w_port = st.number_input(t("settings.port"), key="new_worker_port", value=8082, min_value=1, max_value=65535)
        w_max = st.number_input(t("cluster.max_concurrent"), key="new_worker_max", value=4, min_value=1, max_value=64)
        w_labels_raw = st.text_input(t("cluster.labels_json"), key="new_worker_labels", value="{}")

        if st.button(t("common.create"), key="add_worker_btn", type="primary"):
            if w_name:
                import json as _json
                try:
                    labels = _json.loads(w_labels_raw) if w_labels_raw.strip() else {}
                except Exception:
                    labels = {}
                wc.register_worker(w_name, host=w_host, port=w_port,
                                   max_concurrent=w_max, labels=labels)
                st.success(t("cluster.worker_added", name=w_name))
                st.rerun()

    # Environment config display
    st.markdown("---")
    st.markdown(f"#### {t('common.configuration')}")
    import os
    env_vars = {
        "PYFI2_CLUSTER_ENABLED": os.environ.get("PYFI2_CLUSTER_ENABLED", "false"),
        "PYFI2_CLUSTER_HOST": os.environ.get("PYFI2_CLUSTER_HOST", "localhost"),
        "PYFI2_CLUSTER_PORT": os.environ.get("PYFI2_CLUSTER_PORT", "8001"),
        "PYFI2_CORS_ORIGINS": os.environ.get("PYFI2_CORS_ORIGINS", "localhost"),
        "PYFI2_RATE_LIMIT": os.environ.get("PYFI2_RATE_LIMIT", "false"),
        "PYFI2_MAX_BODY_SIZE": os.environ.get("PYFI2_MAX_BODY_SIZE", "10485760"),
        "PYFI2_SECRET_KEY": "***" if os.environ.get("PYFI2_SECRET_KEY") else "(not set)",
    }
    import pandas as pd
    df = pd.DataFrame([{"Variable": k, t("common.status"): v} for k, v in env_vars.items()])
    st.dataframe(df, width="stretch", hide_index=True)


def render_secrets_tab():
    """Secrets management UI."""
    st.markdown(f"### 🔑 {t('secrets.title')}")
    from core.secrets import get_secrets_manager

    sm = get_secrets_manager()

    st.info(t("secrets.info"))

    # Manage secrets stored in config/secrets.json
    secrets_path = Path("config/secrets.json")
    secrets_data = {}
    if secrets_path.exists():
        import json as _json
        try:
            secrets_data = _json.loads(secrets_path.read_text(encoding="utf-8"))
        except Exception:
            secrets_data = {}

    if secrets_data:
        for name, value in list(secrets_data.items()):
            with st.container(border=True):
                col1, col2, col3 = st.columns([3, 2, 1])
                with col1:
                    st.markdown(f"**{name}**")
                    if sm.is_encrypted(value):
                        st.caption(f"🔒 {t('secrets.encrypted')}")
                    else:
                        st.caption(f"⚠️ {t('common.not_encrypted')}")
                with col2:
                    if st.button("👁", key=f"reveal_{name}", help=t("secrets.reveal")):
                        try:
                            decrypted = sm.decrypt(value) if sm.is_encrypted(value) else value
                            st.code(decrypted, language=None)
                        except Exception as e:
                            st.error(str(e))
                with col3:
                    if st.button("🗑️", key=f"del_secret_{name}"):
                        del secrets_data[name]
                        import json as _json
                        secrets_path.parent.mkdir(parents=True, exist_ok=True)
                        secrets_path.write_text(_json.dumps(secrets_data, indent=2), encoding="utf-8")
                        st.rerun()
    else:
        st.info(t("secrets.no_secrets"))

    # Add secret
    with st.expander(f"➕ {t('secrets.add')}"):
        s_name = st.text_input(t("secrets.name"), key="new_secret_name")
        s_value = st.text_input(t("secrets.value"), type="password", key="new_secret_value")
        s_encrypt = st.checkbox(t("secrets.encrypted"), value=True, key="new_secret_encrypt")

        if st.button(t("common.create"), key="add_secret_btn", type="primary"):
            if s_name and s_value:
                stored = sm.encrypt(s_value) if s_encrypt else s_value
                secrets_data[s_name] = stored
                import json as _json
                secrets_path.parent.mkdir(parents=True, exist_ok=True)
                secrets_path.write_text(_json.dumps(secrets_data, indent=2), encoding="utf-8")
                st.success(t("secrets.saved", name=s_name))
                st.rerun()

    # Usage reference
    with st.expander(f"📖 {t('common.usage')}"):
        st.markdown("""
Use secrets in flow configurations with the expression syntax:

```
${secrets.my_secret_name}
```

Or in service configs:
```json
{
  "password": "${secrets.db_password}"
}
```

Set `PYFI2_SECRET_KEY` environment variable for production encryption key.
""")


def render_parameter_contexts_tab():
    """Parameter contexts management."""
    st.markdown(f"### 📎 {t('params.title')}")

    # Parameter contexts are stored in config/parameter_contexts.json
    ctx_path = Path("config/parameter_contexts.json")
    contexts = {}
    if ctx_path.exists():
        import json as _json
        try:
            contexts = _json.loads(ctx_path.read_text(encoding="utf-8"))
        except Exception:
            contexts = {}

    def _save_contexts():
        import json as _json
        ctx_path.parent.mkdir(parents=True, exist_ok=True)
        ctx_path.write_text(_json.dumps(contexts, indent=2), encoding="utf-8")

    if contexts:
        for ctx_name, ctx_data in list(contexts.items()):
            params = ctx_data.get("parameters", {})
            inherits = ctx_data.get("inherits_from", "")
            with st.expander(f"📎 **{ctx_name}** ({len(params)} params)" +
                             (f" ← {inherits}" if inherits else ""), expanded=False):
                if inherits:
                    st.caption(f"{t('params.inherit_from')}: {inherits}")

                # Show parameters
                for p_name, p_info in list(params.items()):
                    col1, col2, col3, col4 = st.columns([2, 3, 1, 1])
                    with col1:
                        st.text(p_name)
                    with col2:
                        is_sensitive = p_info.get("sensitive", False)
                        if is_sensitive:
                            st.text("••••••••")
                        else:
                            new_val = st.text_input(
                                t("params.value"), value=str(p_info.get("value", "")),
                                key=f"ctx_{ctx_name}_{p_name}", label_visibility="collapsed",
                            )
                            if new_val != str(p_info.get("value", "")):
                                params[p_name]["value"] = new_val
                                _save_contexts()
                    with col3:
                        if is_sensitive:
                            st.caption(f"🔒 {t('params.sensitive')}")
                    with col4:
                        if st.button("🗑️", key=f"del_ctx_p_{ctx_name}_{p_name}"):
                            del params[p_name]
                            _save_contexts()
                            st.rerun()

                # Add parameter to this context
                st.markdown("---")
                ac1, ac2, ac3, ac4 = st.columns([2, 3, 1, 1])
                with ac1:
                    np_name = st.text_input(t("common.name"), key=f"new_p_{ctx_name}_name",
                                            label_visibility="collapsed", placeholder=t("common.name"))
                with ac2:
                    np_value = st.text_input(t("params.value"), key=f"new_p_{ctx_name}_val",
                                             label_visibility="collapsed", placeholder=t("params.value"))
                with ac3:
                    np_sensitive = st.checkbox(t("params.sensitive"), key=f"new_p_{ctx_name}_sens")
                with ac4:
                    if st.button("➕", key=f"add_p_{ctx_name}"):
                        if np_name:
                            params[np_name] = {"value": np_value, "sensitive": np_sensitive}
                            _save_contexts()
                            st.rerun()

                # Delete context
                if st.button(f"🗑️ {t('common.delete')} {ctx_name}", key=f"del_ctx_{ctx_name}"):
                    del contexts[ctx_name]
                    _save_contexts()
                    st.rerun()
    else:
        st.info(t("params.no_contexts"))

    # Add new context
    with st.expander(f"➕ {t('params.add_context')}"):
        new_ctx_name = st.text_input(t("params.context_name"), key="new_ctx_name")
        inherit_options = [""] + list(contexts.keys())
        new_ctx_inherit = st.selectbox(t("params.inherit_from"), inherit_options, key="new_ctx_inherit")

        if st.button(t("common.create"), key="add_ctx_btn", type="primary"):
            if new_ctx_name:
                contexts[new_ctx_name] = {
                    "parameters": {},
                    "inherits_from": new_ctx_inherit or "",
                }
                _save_contexts()
                st.success(t("params.context_created", name=new_ctx_name))
                st.rerun()

    # Usage reference
    with st.expander(f"📖 {t('common.usage')}"):
        st.markdown("""
Parameter contexts provide named sets of parameters that can be applied to flows.

**In flow JSON:**
```json
{
  "parameter_context": "production",
  "tasks": { ... }
}
```

**Expression syntax:**
```
${flow.parameters.my_param}
```

**Inheritance:** A context can inherit from another, overriding specific values.
""")


def render_plugins_tab():
    """Gestion des plugins."""
    st.markdown(f"### 🧩 {t('settings.plugins')}")

    from core.plugin import get_plugin_manager

    pm = get_plugin_manager()

    # Install plugin
    st.markdown(f"#### {t('settings.install_plugin')}")
    if check_permission(session, "plugin.install"):
        col1, col2 = st.columns(2)

        with col1:
            plugin_source = st.text_input(
                "Source (chemin ou .pfp)",
                placeholder="./my_plugin/ ou plugin.pfp",
                key="plugin_source",
            )
            if st.button("📦 Installer", width="stretch"):
                if plugin_source:
                    try:
                        desc = pm.install(plugin_source)
                        pm.load_all()
                        st.success(f"{t('common.success')}: {desc.name} ({desc.id})")
                        st.rerun()
                    except Exception as e:
                        st.error(f"{t('common.error')}: {e}")

        with col2:
            uploaded_plugin = st.file_uploader(
                "Ou uploader un .pfp",
                type=["pfp", "zip"],
                key="plugin_upload",
            )
            if uploaded_plugin:
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".pfp", delete=False) as tmp:
                    tmp.write(uploaded_plugin.read())
                    tmp_path = tmp.name
                try:
                    desc = pm.install(tmp_path)
                    pm.load_all()
                    st.success(f"{t('common.success')}: {desc.name} ({desc.id})")
                    st.rerun()
                except Exception as e:
                    st.error(f"{t('common.error')}: {e}")
                finally:
                    import os
                    os.unlink(tmp_path)
    else:
        st.info(t("auth.no_permission"))

    # List installed plugins
    st.markdown("---")
    st.markdown(f"#### {t('settings.plugins')}")

    installed = pm.list_installed()
    if installed:
        for plugin_info in installed:
            with st.container(border=True):
                col1, col2, col3 = st.columns([3, 2, 1])
                with col1:
                    st.markdown(f"**{plugin_info.get('name', plugin_info['id'])}**")
                    st.caption(f"ID: `{plugin_info['id']}` | v{plugin_info.get('version', '?')}")
                    if plugin_info.get("description"):
                        st.caption(plugin_info["description"])
                with col2:
                    tasks = plugin_info.get("tasks", [])
                    services = plugin_info.get("services", [])
                    flows = plugin_info.get("flows", [])
                    st.caption(f"Tasks: {len(tasks)} | Services: {len(services)} | Flows: {len(flows)}")
                with col3:
                    if check_permission(session, "plugin.uninstall"):
                        if st.button("🗑️", key=f"uninstall_{plugin_info['id']}"):
                            pm.uninstall(plugin_info["id"])
                            st.success(t("settings.plugin_uninstalled", id=plugin_info['id']))
                            st.rerun()
                    else:
                        st.button("🗑️", key=f"uninstall_{plugin_info['id']}",
                                  disabled=True, help=t("auth.no_permission"))
    else:
        st.info(t("common.none"))

    # Plugin flows
    st.markdown("---")
    st.markdown(f"#### {t('settings.plugin_flows')}")
    flows = pm.list_flows()
    if flows:
        for flow_info in flows:
            st.markdown(f"- **{flow_info.get('name', flow_info['id'])}** (`{flow_info['id']}`)")
    else:
        st.info(t("common.none"))

    # Import external flow
    st.markdown("---")
    st.markdown(f"#### {t('settings.import_external_flow')}")
    flow_file = st.file_uploader(t("settings.flow_json_file"), type=["json"], key="import_ext_flow")
    if flow_file:
        import tempfile, json
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode='w') as tmp:
            content = json.loads(flow_file.read().decode("utf-8"))
            json.dump(content, tmp)
            tmp_path = tmp.name
        try:
            flow_dict = pm.import_flow(tmp_path)
            st.success(t("settings.flow_imported_name", name=flow_dict.get('name', flow_dict['id'])))
        except Exception as e:
            st.error(f"{t('common.error')}: {e}")
        finally:
            import os
            os.unlink(tmp_path)


def render_nifi_import_tab():
    """Import NiFi flow — upload, preview, script conversion, validation."""
    import json
    from engine.nifi_converter import NiFiConverter
    from engine.nifi_script_converter import NiFiScriptConverter

    st.markdown(f"### 🔄 {t('settings.nifi_import')}")
    st.caption(t("settings.nifi_caption"))

    # --- Step 1: Upload ---
    uploaded = st.file_uploader(
        t("settings.nifi_file"),
        type=["xml", "json", "gz"],
        key="nifi_upload",
        help=t("settings.nifi_help"),
    )

    if not uploaded:
        st.info(t("settings.nifi_upload_hint"))
        return

    # Parse the uploaded file
    content = uploaded.read().decode("utf-8", errors="replace")

    converter = NiFiConverter()
    result = converter.convert(content)

    if not result.success:
        st.error(t("settings.nifi_conversion_error"))
        for w in result.warnings:
            st.error(f"- {w.message}")
        return

    # Store result in session state
    st.session_state["_nifi_result"] = result
    st.session_state.setdefault("_nifi_scripts", {})

    flow = result.flow

    # --- Step 2: Preview ---
    st.markdown("---")
    st.markdown(f"#### 📋 {t('settings.nifi_preview')}")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric(t("dashboard.total_tasks"), len(flow.get("tasks", {})))
    with col2:
        st.metric(t("editor.connections"), len(flow.get("relations", [])))
    with col3:
        st.metric(t("runtime.parameters"), len(flow.get("parameters", {})))
    with col4:
        st.metric("Subflows", len(result.subflows))

    # Editable flow name
    flow["name"] = st.text_input(t("editor.flow_name"), value=flow.get("name", ""), key="nifi_flow_name")

    # Warnings
    if result.warnings:
        with st.expander(f"⚠️ {t('settings.nifi_warnings', count=len(result.warnings))}", expanded=False):
            for w in result.warnings:
                icon = {"info": "ℹ️", "warning": "⚠️", "error": "❌"}.get(w.level, "⚠️")
                st.markdown(f"{icon} **{w.processor_id}** (`{w.processor_type}`): {w.message}")

    # Unmapped processors
    if result.unmapped_processors:
        with st.expander(f"🔴 {t('settings.nifi_unmapped', count=len(result.unmapped_processors))}", expanded=True):
            for proc_type in result.unmapped_processors:
                st.markdown(f"- `{proc_type}` → {t('settings.nifi_unmapped_hint')}")

    # Task list
    with st.expander(f"📦 {t('settings.nifi_converted_tasks')}", expanded=False):
        for tid, tconfig in flow.get("tasks", {}).items():
            st.markdown(f"- **{tid}** → `{tconfig['type']}`")

    # Flow JSON preview
    with st.expander(f"📄 {t('settings.nifi_flow_json')}", expanded=False):
        st.code(json.dumps(flow, indent=2, ensure_ascii=False), language="json")

    # --- Step 3: Script conversion ---
    if result.script_processors:
        st.markdown("---")
        st.markdown(f"#### 🔧 {t('settings.nifi_script_conversion', count=len(result.script_processors))}")

        # LLM config (persisted to config/llm_config.json — API key stays in session only)
        from gui.services.llm_config_service import load_llm_config, save_llm_config

        saved_llm = load_llm_config()

        with st.expander(f"🤖 {t('settings.nifi_llm_config')}", expanded=False):
            providers = ["openai", "anthropic"]
            saved_provider = saved_llm.get("provider", "openai")
            llm_provider = st.selectbox(
                t("common.provider"), providers,
                index=providers.index(saved_provider) if saved_provider in providers else 0,
                key="nifi_llm_provider",
            )
            llm_key = st.text_input(t("common.api_key"), type="password", key="nifi_llm_key")
            llm_url = st.text_input(
                t("common.base_url"), key="nifi_llm_url",
                value=saved_llm.get("base_url", ""),
                placeholder="https://api.openai.com",
            )
            llm_model = st.text_input(
                t("common.model"), key="nifi_llm_model",
                value=saved_llm.get("default_model", ""),
                placeholder="gpt-4o-mini",
            )

            if st.button(f"💾 {t('common.save')}", key="save_llm_config"):
                save_llm_config({
                    "provider": llm_provider,
                    "base_url": llm_url,
                    "default_model": llm_model,
                })
                st.success(t("common.success"))

            st.caption(t("settings.llm_api_key_note"))

        llm_config = None
        if llm_key:
            llm_config = {
                "provider": llm_provider,
                "api_key": llm_key,
                "base_url": llm_url or saved_llm.get("base_url", ""),
                "default_model": llm_model or saved_llm.get("default_model", ""),
            }

        script_converter = NiFiScriptConverter(llm_config)

        for i, sp in enumerate(result.script_processors):
            task_id = sp["task_id"]
            groovy_script = sp["script"]
            language = sp["language"]

            st.markdown(f"---")
            st.markdown(f"##### Script: **{task_id}** ({language})")

            col_left, col_right = st.columns(2)

            with col_left:
                st.markdown("**Groovy original:**")
                st.code(groovy_script or "(vide)", language="groovy")

            with col_right:
                st.markdown("**Python converti:**")

                # Check if already converted
                script_key = f"_nifi_script_{task_id}"
                if script_key not in st.session_state._nifi_scripts:
                    st.session_state._nifi_scripts[script_key] = ""

                converted = st.session_state._nifi_scripts.get(script_key, "")

                if not converted:
                    if groovy_script and st.button(f"🔄 {t('settings.converting')}", key=f"convert_{i}"):
                        with st.spinner(t("settings.converting")):
                            conv_result = script_converter.convert(groovy_script)
                        st.session_state._nifi_scripts[script_key] = conv_result.converted_python
                        if conv_result.warnings:
                            for w in conv_result.warnings:
                                st.warning(f"⚠️ {w}")
                        if conv_result.used_llm:
                            st.caption(f"🤖 LLM ({conv_result.llm_tokens_used} tokens)")
                        else:
                            st.caption("📐 Regex")
                        st.rerun()
                    elif not groovy_script:
                        st.caption(t("settings.nifi_empty_script"))
                else:
                    # Show editable converted script
                    edited = st.text_area(
                        "Python (editable)",
                        value=converted,
                        height=300,
                        key=f"edit_script_{i}",
                    )
                    st.session_state._nifi_scripts[script_key] = edited

                    # Re-submit with feedback
                    if llm_config:
                        feedback = st.text_input(
                            "Feedback",
                            key=f"feedback_{i}",
                            placeholder="Fix X, add Y...",
                        )
                        if feedback and st.button(f"🔄 LLM", key=f"resubmit_{i}"):
                            with st.spinner(t("settings.converting")):
                                conv_result = script_converter.convert_with_feedback(
                                    groovy_script, edited, feedback,
                                )
                            if conv_result.success:
                                st.session_state._nifi_scripts[script_key] = conv_result.converted_python
                                st.success(t("common.success"))
                                st.rerun()
                            else:
                                st.error(conv_result.error)

                    # Apply converted script to the flow
                    if edited:
                        flow["tasks"][task_id]["parameters"]["script"] = edited
                        flow["tasks"][task_id]["parameters"]["language"] = "python"

    # --- Step 4: Import ---
    st.markdown("---")
    st.markdown(f"#### ✅ {t('settings.import_flow')}")

    col1, col2 = st.columns(2)
    with col1:
        if st.button(f"📥 {t('settings.import_flow')}", type="primary", width="stretch"):
            flows_dir = Path("flows")
            flows_dir.mkdir(exist_ok=True)

            # Save subflows first
            saved_subflows = []
            for sf in result.subflows:
                sf_id = sf.get("id", "subflow")
                sf_path = flows_dir / f"{sf_id}.json"
                with open(sf_path, "w", encoding="utf-8") as f:
                    json.dump(sf, f, indent=2, ensure_ascii=False)
                saved_subflows.append(sf_path)

            # Save main flow
            flow_id = flow.get("id", "nifi_import")
            filepath = flows_dir / f"{flow_id}.json"
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(flow, f, indent=2, ensure_ascii=False)

            st.success(f"✅ {t('settings.nifi_flow_imported', path=str(filepath))}")
            if saved_subflows:
                st.info(f"📂 {len(saved_subflows)} subflow(s): {', '.join(str(p) for p in saved_subflows)}")
            st.balloons()

    with col2:
        if st.button(f"✏️ {t('dashboard.open_editor')}", width="stretch"):
            st.session_state.current_flow = flow
            st.session_state.selected_node = None
            st.session_state.node_positions = {}
            st.switch_page("pages/2_Editor.py")


def render_about():
    """À propos."""
    st.markdown("---")
    st.markdown(f"### ℹ️ {t('settings.about')}")

    import sys
    import importlib

    from core import __version__ as pyfi2_version
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    try:
        streamlit_version = importlib.metadata.version("streamlit")
    except importlib.metadata.PackageNotFoundError:
        streamlit_version = "N/A"

    try:
        sf_version = importlib.metadata.version("streamlit-flow-component")
    except importlib.metadata.PackageNotFoundError:
        sf_version = "N/A"

    st.markdown(
        f"""
        {t('doc.architecture_desc')}

        #### {t('common.version')}
        - **PyFi2:** v{pyfi2_version}
        - **Python:** {python_version}
        - **Streamlit:** {streamlit_version}
        - **Streamlit Flow:** {sf_version}

        #### {t('doc.architecture')}
        - {t('doc.architecture_core')}
        - {t('doc.architecture_engine')}
        - {t('doc.architecture_tasks')}
        - {t('doc.architecture_storage')}

        #### License
        MIT License
        """
    )


def main():
    """Fonction principale."""
    menu = render_sidebar()

    # Initialiser les settings si nécessaire
    if "settings" not in st.session_state:
        st.session_state.settings = {
            "app_name": "PyFi2 - Pipeline Framework",
            "theme": "light",
            "auto_save": True,
            "auto_validate": True,
            "debug_mode": False,
            "storage_type": "filesystem",
            "filesystem_path": "./flows",
            "git_path": "./flows.git",
            "git_auto_commit": True,
            "postgres_host": "localhost",
            "postgres_port": 5432,
            "postgres_db": "pyfi2",
            "postgres_user": "pyfi2",
            "sqlite_path": "./flows.db",
            "max_workers": 10,
            "max_retries": 3,
            "default_timeout": 300,
        }

    if menu == f"🏠 {t('nav.dashboard')}":
        st.switch_page("pages/1_Dashboard.py")
    elif menu == f"✏️ {t('nav.editor')}":
        st.switch_page("pages/2_Editor.py")
    elif menu == f"▶️ {t('nav.runtime')}":
        st.switch_page("pages/3_Runtime.py")
    elif menu == f"📊 {t('nav.monitoring')}":
        st.switch_page("pages/4_Monitoring.py")
    elif menu == f"⚙️ {t('nav.settings')}":
        render_settings_tabs()
        render_about()
        if check_permission(session, "settings.edit"):
            render_reset()
    elif menu == f"📚 {t('nav.documentation')}":
        st.switch_page("pages/6_Documentation.py")


if __name__ == "__main__":
    main()