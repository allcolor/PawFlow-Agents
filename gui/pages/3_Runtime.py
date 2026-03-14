#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Page Exécution de flux.
Permet de lancer et surveiller l'exécution des flux.
"""

import streamlit as st
from pathlib import Path
from typing import Dict, Any, List, Optional
import json as _json
import logging

# Configuration de la page
st.set_page_config(
    page_title="Exécution - PyFi2",
    page_icon="▶️",
    layout="wide",
)

# Import des services
from gui.services.flow_service import FlowService
from engine.continuous_executor import ContinuousFlowExecutor
from engine.debugger import FlowDebugger
from engine.data_preview import DataPreviewManager

# i18n
from gui.i18n import init as i18n_init, t
i18n_init(st.session_state.get("locale", "en"))
from gui.components.theme import inject_theme
inject_theme()

# Auth
from gui.utils.auth import require_auth, render_user_info, check_permission
session = require_auth()
render_user_info()

# Ensure tasks & services are registered
from tasks import register_all_tasks
register_all_tasks()

logger = logging.getLogger(__name__)

# Session state initialization
if "flows" not in st.session_state:
    st.session_state.flows = []
if "execution_results" not in st.session_state:
    st.session_state.execution_results = []


def _get_service_schema_rt(svc_type: str):
    """Get parameter schema for a service type in runtime context."""
    from core import ServiceFactory
    try:
        svc_class = ServiceFactory.get(svc_type)
        instance = object.__new__(svc_class)
        instance.config = {}
        return instance.get_parameter_schema()
    except Exception:
        return {}


def render_sidebar():
    """Barre latérale — deployment treeview only (page nav is handled by Streamlit)."""
    with st.sidebar:
        st.markdown("---")

        # Deployment treeview
        from gui.components.deployment_tree import render_deployment_tree
        render_deployment_tree()


def initialize_state():
    """Initialiser l'état de la page."""
    pass






def _get_executor_registry():
    """Get the executor registry singleton, restoring from disk on first access."""
    from gui.services.executor_registry import ExecutorRegistry
    registry = ExecutorRegistry.get_instance()

    # Restore from disk on first access (e.g. after server restart)
    registry.restore_from_disk()

    # Migrate legacy session_state executors (one-time)
    if "_continuous_executors" in st.session_state:
        for fid, ex in st.session_state._continuous_executors.items():
            if ex is not None and registry.get(fid) is None:
                registry.register(fid, ex)
        del st.session_state._continuous_executors

    legacy = st.session_state.pop("_continuous_executor", None)
    if legacy is not None:
        flow_id = getattr(legacy, '_flow', None)
        fid = flow_id.id if flow_id and hasattr(flow_id, 'id') else "legacy"
        if registry.get(fid) is None:
            registry.register(fid, legacy)

    return registry


def _hot_update_flow(executor: ContinuousFlowExecutor):
    """Reload flow from disk and hot-update the running executor."""
    flow_id = executor._flow.id if hasattr(executor, '_flow') else None
    if not flow_id:
        st.error(t("runtime.cannot_determine_flow_id"))
        return

    # Try to find the flow JSON on disk
    flow_service = FlowService()
    flows_dir = Path("flows")
    found = False
    for jf in flows_dir.glob("*.json"):
        try:
            flow = flow_service.parse_from_file(str(jf))
            if flow.id == flow_id:
                result = executor.update_flow(flow)
                if result is None:
                    # Flow unchanged
                    st.info(t("runtime.flow_unchanged"))
                elif result:
                    flow_ver = executor._flow.version if hasattr(executor._flow, 'version') else executor._flow_version
                    st.success(t("runtime.flow_updated", version=flow_ver))
                    # Show route status for HTTP services
                    for svc_id, svc in executor._flow.services.items():
                        if hasattr(svc, 'get_routes'):
                            routes = svc.get_routes()
                            if routes:
                                st.info(t("runtime.routes_label", svc_id=svc_id, routes=routes))
                            else:
                                st.warning(t("runtime.no_routes", svc_id=svc_id))
                    # Invalidate visualizer cache
                    st.session_state.pop("_rt_fingerprint", None)
                    st.session_state.pop("_rt_state", None)
                else:
                    st.error(t("runtime.flow_update_failed"))
                found = True
                break
        except Exception as e:
            st.error(f"{t('common.error')}: {e}")
            continue

    if not found:
        st.error(t("runtime.flow_file_not_found", flow_id=flow_id))


@st.fragment(run_every=5)
def _sync_deployment_statuses():
    """Periodically sync deployment statuses with running executors."""
    reg = _get_executor_registry()
    reg.cleanup_dead()
    from gui.services.deployment_registry import DeploymentRegistry
    dep_reg = DeploymentRegistry.get_instance()
    dep_reg.sync_with_executors()


def render_continuous_execution():
    """Interface for NiFi-style continuous execution with deployment model."""
    st.markdown(f"### 🔄 {t('runtime.continuous')}")
    st.caption(t("runtime.continuous_desc"))

    # Auto-sync statuses every 5 seconds
    _sync_deployment_statuses()

    registry = _get_executor_registry()
    executors = registry.get_all()

    # Determine what to show based on treeview selection
    selected = st.session_state.get("rt_selected_instance")

    if selected == "__new__":
        _render_new_executor_form()
        return

    if selected:
        # Check if this is a running instance with an executor
        executor = executors.get(selected)
        if executor is not None:
            _render_executor_dashboard(executor, selected, executors)
            return

        # Stopped/error instance — show stopped panel
        from gui.services.deployment_registry import DeploymentRegistry
        dep_reg = DeploymentRegistry.get_instance()
        inst = dep_reg.get(selected)
        if inst is not None:
            _render_stopped_instance_panel(inst)
            return

        # Instance not found (stale selection)
        st.session_state.pop("rt_selected_instance", None)

    # No selection — overview
    if not executors:
        from gui.services.deployment_registry import DeploymentRegistry
        dep_reg = DeploymentRegistry.get_instance()
        all_instances = dep_reg.get_all()
        if not all_instances:
            st.info(t("runtime.no_deployments"))
            st.markdown(t("runtime.deploy_new") + " ⬅️")
        else:
            st.info(f"{len(all_instances)} deployment(s), none running. Select one from the sidebar.")
    else:
        # Quick overview of running executors
        st.markdown("#### " + t('runtime.continuous.status'))
        cols = st.columns(min(len(executors), 4))
        for i, (fid, ex) in enumerate(executors.items()):
            with cols[i % len(cols)]:
                try:
                    s = ex.get_status()
                    icon = "🟢" if s["is_running"] else "🔴"
                    st.markdown(f"{icon} **{fid}**")
                    flow_ver = ex._flow.version if hasattr(ex._flow, 'version') else s['flow_version']
                    st.caption(f"v{flow_ver} | {s['tasks_running']} running | {s['total_queued_flowfiles']} queued")
                except Exception:
                    st.markdown(f"❓ **{fid}**")
        st.caption("Select an instance from the sidebar to view details.")


def _render_new_executor_form():
    """Form to deploy and start a new flow instance."""
    st.markdown(f"#### ➕ {t('runtime.deploy_new')}")

    flow_service = FlowService()
    flows_dir = Path("flows")
    if not flows_dir.exists():
        st.warning(t('dashboard.no_flows'))
        return

    json_files = list(flows_dir.glob("*.json"))
    flow_map = {}
    for jf in json_files:
        try:
            flow = flow_service.parse_from_file(str(jf))
            flow_map[f"{flow.name} ({flow.id})"] = (flow, str(jf))
        except Exception:
            pass

    if not flow_map:
        st.warning(t('dashboard.no_flows'))
        return

    # Template selection — no filtering, same template can be deployed N times
    selected = st.selectbox(
        t('runtime.template_source'),
        options=list(flow_map.keys()),
        key="cont_flow_select",
    )

    # Owner selection
    owner_options = [t("runtime.owner_global")]
    try:
        from core.security import SecurityManager
        sm = SecurityManager.get_instance()
        users = sm.list_users() if hasattr(sm, 'list_users') else []
        owner_options += [u.get("username", u) if isinstance(u, dict) else str(u) for u in users]
    except Exception:
        pass
    # Add current user if not in list
    current_user = session.get("username", "") if session else ""
    if current_user and current_user not in owner_options:
        owner_options.append(current_user)

    owner_choice = st.selectbox(
        t("runtime.assign_owner"),
        options=owner_options,
        key="cont_owner",
    )
    owner = None if owner_choice == t("runtime.owner_global") else owner_choice

    col1, col2 = st.columns(2)
    with col1:
        max_workers = st.number_input(t("settings.max_workers"), value=4, min_value=1, max_value=32, key="cont_workers")
    with col2:
        max_retries = st.number_input(t("settings.max_retries"), value=3, min_value=1, max_value=10, key="cont_retries")

    # Flow parameters override
    cont_params = {}
    flow_preview, template_path = flow_map[selected]
    preview_params = flow_preview.parameters if hasattr(flow_preview, 'parameters') else {}
    if preview_params:
        st.markdown("**" + t('runtime.instance_params') + ":**")
        for pname, pdefault in preview_params.items():
            cont_params[pname] = st.text_input(
                f"📎 {pname}", value=str(pdefault), key=f"cont_param_{pname}",
            )

    # Service configuration override (local config or forward to global)
    flow_services = {}
    try:
        raw = _json.loads(Path(template_path).read_text(encoding="utf-8"))
        flow_services = raw.get("services", {})
    except Exception:
        pass

    svc_forwards = {}  # flow_svc_id → prefixed ref (or None for local)
    if flow_services:
        from gui.components.schema_form import render_schema_fields as _render_fields
        from gui.services.global_service_registry import GlobalServiceRegistry
        from gui.services.user_service_registry import UserServiceRegistry
        gsvc_reg = GlobalServiceRegistry.get_instance()
        usvc_reg = UserServiceRegistry.get_instance()

        with st.expander(f"🔌 {t('settings.services')}", expanded=True):
            svc_overrides = {}
            for svc_id, svc_def in flow_services.items():
                svc_type = svc_def.get("type", "?")
                svc_config = svc_def.get("parameters", svc_def.get("config", {}))

                # Build options: local + global + user services
                options = [t("runtime.svc_use_local")]
                option_ids = [None]
                # Global services
                for gs in gsvc_reg.get_compatible(svc_type):
                    label = f"🌐 {gs.service_id}"
                    if gs.description:
                        label += f" — {gs.description}"
                    if not gs.enabled:
                        label += f" ({t('common.disabled')})"
                    options.append(label)
                    option_ids.append(f"global:{gs.service_id}")
                # User services (owner's)
                if owner:
                    for us in usvc_reg.get_compatible(svc_type, owner):
                        label = f"👤 {us.service_id}"
                        if us.description:
                            label += f" — {us.description}"
                        if not us.enabled:
                            label += f" ({t('common.disabled')})"
                        options.append(label)
                        option_ids.append(f"user:{owner}:{us.service_id}")

                choice_idx = st.selectbox(
                    f"**{svc_id}** (`{svc_type}`)",
                    options=range(len(options)),
                    format_func=lambda i, opts=options: opts[i],
                    key=f"rt_svc_mode_{svc_id}",
                )
                chosen_ref = option_ids[choice_idx]
                svc_forwards[svc_id] = chosen_ref

                # Only show local config if not forwarding
                if chosen_ref is None:
                    schema = _get_service_schema_rt(svc_type)
                    if schema:
                        edited_config = _render_fields(schema, svc_config, key_prefix=f"rt_svc_{svc_id}")
                    else:
                        edited_config = {}
                        for cfg_key, cfg_val in svc_config.items():
                            if isinstance(cfg_val, bool):
                                edited_config[cfg_key] = st.checkbox(
                                    cfg_key, value=cfg_val, key=f"rt_svc_{svc_id}_{cfg_key}")
                            elif isinstance(cfg_val, (int, float)):
                                edited_config[cfg_key] = st.number_input(
                                    cfg_key, value=cfg_val, key=f"rt_svc_{svc_id}_{cfg_key}")
                            else:
                                edited_config[cfg_key] = st.text_input(
                                    cfg_key, value=str(cfg_val), key=f"rt_svc_{svc_id}_{cfg_key}")
                    svc_overrides[svc_id] = {"type": svc_type, "config": edited_config}
                else:
                    display_ref = chosen_ref.replace("global:", "🌐 ").replace(f"user:{owner}:", "👤 ")
                    st.caption(f"→ {t('runtime.svc_forwarded_to')} **{display_ref}**")

            st.session_state._cont_svc_overrides = svc_overrides

    # Build service_overrides dict (only non-None forwards)
    service_overrides = {k: v for k, v in svc_forwards.items() if v is not None}

    if check_permission(session, "flow.execute"):
        if st.button(f"🚀 {t('runtime.deploy_and_start')}", type="primary", width="stretch"):
            flow, tmpl_path = flow_map[selected]

            # Deploy via DeploymentRegistry
            from gui.services.deployment_registry import DeploymentRegistry
            dep_reg = DeploymentRegistry.get_instance()
            # Collect local service configs for persistence
            svc_ovr = st.session_state.get("_cont_svc_overrides", {})
            svc_configs_to_save = {}
            for svc_id, svc_def in svc_ovr.items():
                cfg = svc_def.get("config", {})
                if cfg:
                    svc_configs_to_save[svc_id] = cfg

            instance_id = dep_reg.deploy(
                template_path=tmpl_path,
                owner=owner,
                parameters=cont_params if cont_params else None,
                max_workers=max_workers,
                max_retries=max_retries,
                source="gui",
                service_overrides=service_overrides if service_overrides else None,
                service_configs=svc_configs_to_save if svc_configs_to_save else None,
            )

            # Re-parse flow for executor (fresh copy)
            flow = flow_service.parse_from_file(tmpl_path)

            # Apply service config overrides (local services)
            svc_ovr = st.session_state.get("_cont_svc_overrides", {})
            if svc_ovr and hasattr(flow, 'services'):
                for svc_id, svc_def in svc_ovr.items():
                    if svc_id in flow.services:
                        svc_obj = flow.services[svc_id]
                        for k, v in svc_def.get("config", {}).items():
                            if v or v == 0 or v is False or k in svc_obj.config:
                                svc_obj.config[k] = v

            # Apply global service forwards
            _apply_service_forwards(flow, service_overrides)

            ex = ContinuousFlowExecutor(
                flow, max_workers=max_workers, max_retries=max_retries,
                parameters=cont_params if cont_params else None,
            )
            ex.start()
            from gui.services.executor_registry import ExecutorRegistry
            ExecutorRegistry.get_instance().register(instance_id, ex)
            st.session_state["rt_selected_instance"] = instance_id
            st.rerun()
    else:
        st.button(f"🚀 {t('runtime.deploy_and_start')}", width="stretch", disabled=True,
                  help=t("auth.no_permission"))


def _render_stopped_snapshot(inst):
    """Show flow view, task states and queue stats — from checkpoint or template."""
    from engine.checkpoint import CheckpointManager

    mgr = CheckpointManager(inst.flow_id)
    data = mgr.load_latest_checkpoint() or {}

    task_states = data.get("task_states", {})
    queue_data = data.get("queues", [])

    # Build queue_stats in the format the visualizer expects
    queue_stats = []
    for qd in queue_data:
        queue_stats.append({
            "source": qd["source"],
            "target": qd["target"],
            "relationship": qd.get("relationship", "success"),
            "queue_size": len(qd.get("flowfiles", [])),
            "max_queue_size": 10000,
            "backpressured": False,
            "flowfiles_in": 0,
            "flowfiles_out": 0,
            "total_bytes": sum(ff.get("size", 0) for ff in qd.get("flowfiles", [])),
        })

    # Load tasks and relations from template to fill gaps
    known_edges = {(qd["source"], qd["target"]) for qd in queue_data}
    if inst.flow_path and Path(inst.flow_path).exists():
        try:
            raw = _json.loads(Path(inst.flow_path).read_text(encoding="utf-8"))
            # Add missing edges from template
            for rel in raw.get("relations", []):
                key = (rel["from"], rel["to"])
                if key not in known_edges:
                    queue_stats.append({
                        "source": rel["from"],
                        "target": rel["to"],
                        "relationship": rel.get("type", "success"),
                        "queue_size": 0,
                        "max_queue_size": 10000,
                        "backpressured": False,
                        "flowfiles_in": 0,
                        "flowfiles_out": 0,
                        "total_bytes": 0,
                    })
            # Add missing tasks from template
            for tid, tconf in raw.get("tasks", {}).items():
                if tid not in task_states:
                    task_states[tid] = {
                        "task_id": tid,
                        "task_type": tconf.get("type", "?"),
                        "state": "stopped",
                        "run_count": 0,
                        "error_count": 0,
                        "flowfiles_in": 0,
                        "flowfiles_out": 0,
                        "bytes_in": 0,
                        "bytes_out": 0,
                    }
        except Exception:
            pass

    if not task_states:
        return

    # -- KPIs --
    st.markdown("---")
    snap_ts = data.get("timestamp")
    if snap_ts:
        st.caption(f"📸 {t('runtime.last_run_snapshot')} ({snap_ts})")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric(t("dashboard.total_tasks"), len(task_states))
    with col2:
        total_runs = sum(ts.get("run_count", 0) for ts in task_states.values())
        st.metric(t("runtime.performance.runs"), total_runs)
    with col3:
        total_errors = sum(ts.get("error_count", 0) for ts in task_states.values())
        st.metric(t("runtime.performance.errors"), total_errors)
    with col4:
        total_queued = sum(qs["queue_size"] for qs in queue_stats)
        st.metric(t("runtime.continuous.queue_stats"), total_queued)

    # -- Flow View --
    _fid = inst.instance_id
    _frozen_key = f"_rt_frozen_{_fid}"

    if _frozen_key not in st.session_state:
        st.session_state[_frozen_key] = True

    is_frozen = st.session_state[_frozen_key]

    # Toolbar placeholder — rendered visually above, filled after canvas
    # so dirty detection uses up-to-date positions.
    toolbar_placeholder = st.empty()

    from gui.components.runtime_visualizer import (
        render_runtime_flow_static, save_layout_to_disk,
        reload_positions_from_disk, is_layout_dirty, _pos_key as viz_pos_key,
        _disk_key as viz_disk_key,
    )

    # Check if auto-layout was requested
    auto_key = f"_rt_do_auto_layout_static_{_fid}"
    do_auto = st.session_state.pop(auto_key, False)

    render_runtime_flow_static(task_states, queue_stats, height=400,
                               use_auto_layout=do_auto, instance_id=_fid,
                               frozen=is_frozen)

    # Now fill toolbar with correct dirty state
    dirty = is_layout_dirty(_fid)
    with toolbar_placeholder.container():
        col_title, col_auto, col_save, col_cancel, col_freeze = st.columns([3, 1, 1, 1, 1])
        with col_title:
            st.markdown(f"#### 🗺️ {t('runtime.flow_view')}")
        with col_auto:
            if st.button(f"📐 {t('runtime.auto_layout')}", key=f"auto_layout_static_{_fid}",
                          disabled=is_frozen):
                st.session_state[auto_key] = True
                st.rerun()
        with col_save:
            if st.button(f"💾 {t('common.save')}", key=f"save_layout_static_{_fid}",
                          disabled=(is_frozen or not dirty)):
                cur = st.session_state.get(viz_pos_key(_fid), {})
                if cur:
                    save_layout_to_disk(_fid, cur)
                    st.session_state[viz_disk_key(_fid)] = dict(cur)
                st.session_state[_frozen_key] = True
                st.session_state.pop("_rt_fp_stopped", None)
                st.session_state.pop("_rt_state_stopped", None)
                st.rerun()
        with col_cancel:
            if st.button(f"↩ {t('common.cancel')}", key=f"cancel_layout_static_{_fid}",
                          disabled=(is_frozen or not dirty)):
                reload_positions_from_disk(_fid, view_suffix="_stopped")
                st.session_state[_frozen_key] = True
                st.rerun()
        with col_freeze:
            if is_frozen:
                if st.button(f"🔓 {t('runtime.unfreeze')}", key=f"unfreeze_static_{_fid}"):
                    st.session_state[_frozen_key] = False
                    st.rerun()
            else:
                if st.button(f"🔒 {t('runtime.freeze')}", key=f"freeze_static_{_fid}"):
                    st.session_state[_frozen_key] = True
                    st.rerun()

    # -- Task Performance --
    with st.expander(f"📊 {t('runtime.performance.title')}", expanded=False):
        perf_data = []
        for task_id, state in task_states.items():
            runs = state.get("run_count", 0)
            errors = state.get("error_count", 0)
            ff_in = state.get("flowfiles_in", 0)
            ff_out = state.get("flowfiles_out", 0)
            perf_data.append({
                "Task": task_id,
                t("common.type"): state.get("task_type", ""),
                t("runtime.performance.runs"): runs,
                t("runtime.performance.errors"): errors,
                "FF In": ff_in,
                "FF Out": ff_out,
            })
        if perf_data:
            import pandas as pd
            df = pd.DataFrame(perf_data)
            st.dataframe(df, width="stretch", hide_index=True)

    # -- Queue Management --
    if queue_stats:
        with st.expander(f"📦 {t('queue.title')}", expanded=False):
            for idx, qs in enumerate(queue_stats):
                q_size = qs["queue_size"]
                src, tgt = qs["source"], qs["target"]
                st.markdown(
                    f"{'🟢' if q_size == 0 else '🟡'} **{src} → {tgt}** : "
                    f"{q_size}/{qs['max_queue_size']} | {qs.get('total_bytes', 0)} bytes"
                )

            # Clear all queues button (clears checkpoint)
            if q_size_total := sum(qs["queue_size"] for qs in queue_stats):
                if st.button(f"🗑️ {t('queue.clear_all')}", key="clear_stopped_queues"):
                    mgr.clear()
                    st.success(t("queue.cleared"))
                    st.rerun()


def _render_stopped_instance_panel(inst):
    """Panel for a stopped/errored deployment instance."""
    from gui.services.deployment_registry import DeploymentRegistry
    import time as _time

    status_icon = "🔴" if inst.status == "stopped" else "🔥"
    st.markdown(f"### {status_icon} {inst.flow_name}")
    st.caption(f"{t('runtime.flow_stopped_info')}")

    # Metadata
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(f"**{t('runtime.template_source')}:** {inst.flow_id}")
        st.markdown(f"**{t('common.status')}:** {inst.status}")
    with col2:
        st.markdown(f"**{t('runtime.assign_owner')}:** {inst.owner or t('runtime.owner_global')}")
        st.markdown(f"**Source:** {inst.source}")
    with col3:
        created = _time.strftime("%Y-%m-%d %H:%M", _time.localtime(inst.created_at))
        st.markdown(f"**Created:** {created}")
        if inst.last_stopped:
            stopped = _time.strftime("%Y-%m-%d %H:%M", _time.localtime(inst.last_stopped))
            st.markdown(f"**Stopped:** {stopped}")

    if inst.error_message:
        st.error(f"Error: {inst.error_message}")

    # Editable parameters section
    _render_instance_parameters(inst, editable=True)

    # Editable services section
    _render_instance_services(inst, editable=True)

    # -- Last run snapshot (from checkpoint) --
    _render_stopped_snapshot(inst)

    st.markdown("---")

    # Actions
    col1, col2, col3 = st.columns(3)
    with col1:
        if check_permission(session, "flow.execute"):
            if st.button(f"▶️ {t('runtime.start')}", type="primary", width="stretch", key="start_stopped"):
                # Re-parse template and start
                try:
                    flow_service = FlowService()
                    flow_path = inst.flow_path
                    if not flow_path or not Path(flow_path).exists():
                        # Try to find it
                        from gui.services.deployment_registry import DeploymentRegistry
                        flow_path = DeploymentRegistry._find_flow_path(inst.flow_id)
                    if not flow_path:
                        st.error(t("runtime.flow_file_not_found", flow_id=inst.flow_id))
                    else:
                        flow = flow_service.parse_from_file(flow_path)
                        # Apply saved service configs (local overrides)
                        if inst.service_configs:
                            _apply_service_configs(flow, inst.service_configs)
                        # Apply global service forwards
                        if inst.service_overrides:
                            _apply_service_forwards(flow, inst.service_overrides)
                        ex = ContinuousFlowExecutor(
                            flow,
                            max_workers=inst.max_workers,
                            max_retries=inst.max_retries,
                            parameters=inst.parameters if inst.parameters else None,
                        )
                        ex.start()
                        from gui.services.executor_registry import ExecutorRegistry
                        ExecutorRegistry.get_instance().register(inst.instance_id, ex)
                        st.rerun()
                except Exception as e:
                    st.error(f"{t('common.error')}: {e}")
        else:
            st.button(f"▶️ {t('runtime.start')}", width="stretch", disabled=True,
                      help=t("auth.no_permission"))

    with col2:
        if check_permission(session, "flow.execute"):
            if st.button(f"🗑️ {t('runtime.undeploy')}", width="stretch", key="undeploy_stopped"):
                dep_reg = DeploymentRegistry.get_instance()
                dep_reg.undeploy(inst.instance_id)
                st.session_state.pop("rt_selected_instance", None)
                st.rerun()
        else:
            st.button(f"🗑️ {t('runtime.undeploy')}", width="stretch", disabled=True,
                      help=t("auth.no_permission"))

    with col3:
        # Reassign owner
        owner_options = [t("runtime.owner_global")]
        try:
            from core.security import SecurityManager
            sm = SecurityManager.get_instance()
            users = sm.list_users() if hasattr(sm, 'list_users') else []
            owner_options += [u.get("username", u) if isinstance(u, dict) else str(u) for u in users]
        except Exception:
            pass
        current_user = session.get("username", "") if session else ""
        if current_user and current_user not in owner_options:
            owner_options.append(current_user)

        current_idx = 0
        if inst.owner and inst.owner in owner_options:
            current_idx = owner_options.index(inst.owner)

        new_owner = st.selectbox(
            t("runtime.assign_owner"),
            options=owner_options,
            index=current_idx,
            key="reassign_owner",
        )
        effective_owner = None if new_owner == t("runtime.owner_global") else new_owner
        if effective_owner != inst.owner:
            if st.button("✅ Save", key="save_owner"):
                dep_reg = DeploymentRegistry.get_instance()
                dep_reg.set_owner(inst.instance_id, effective_owner)
                st.rerun()


def _render_instance_services(inst, editable: bool = False):
    """Show service configuration for a deployed instance.

    When editable (stopped): allow choosing local config vs forward to global,
    and editing local service parameters. Saves service_overrides to DeploymentRegistry.
    When read-only (running): show current service config and forwarding status.
    """
    from gui.services.deployment_registry import DeploymentRegistry

    # Load template to get service definitions
    flow_services = {}
    if inst.flow_path and Path(inst.flow_path).exists():
        try:
            raw = _json.loads(Path(inst.flow_path).read_text(encoding="utf-8"))
            flow_services = raw.get("services", {})
        except Exception:
            pass

    if not flow_services:
        return

    with st.expander(f"🔌 {t('settings.services')}", expanded=False):
        if editable:
            from gui.services.global_service_registry import GlobalServiceRegistry
            from gui.services.user_service_registry import UserServiceRegistry
            gsvc_reg = GlobalServiceRegistry.get_instance()
            usvc_reg = UserServiceRegistry.get_instance()

            current_overrides = _migrate_service_overrides(inst.service_overrides)
            new_overrides = {}

            # Track local config fields per service for saving
            svc_field_keys = {}  # svc_id → {cfg_key: session_state_key}

            # Determine owner for user service lookup
            inst_owner = getattr(inst, 'owner', None) or ""

            for svc_id, svc_def in flow_services.items():
                svc_type = svc_def.get("type", "?")
                # Merge: template defaults < saved instance configs
                svc_config = dict(svc_def.get("parameters", svc_def.get("config", {})))
                saved_cfg = (inst.service_configs or {}).get(svc_id, {})
                svc_config.update(saved_cfg)

                # Build options: local + compatible globals + user services
                options = [t("runtime.svc_use_local")]
                option_ids = [None]
                for gs in gsvc_reg.get_compatible(svc_type):
                    label = f"🌐 {gs.service_id}"
                    if gs.description:
                        label += f" — {gs.description}"
                    if not gs.enabled:
                        label += f" ({t('common.disabled')})"
                    options.append(label)
                    option_ids.append(f"global:{gs.service_id}")
                if inst_owner:
                    for us in usvc_reg.get_compatible(svc_type, inst_owner):
                        label = f"👤 {us.service_id}"
                        if us.description:
                            label += f" — {us.description}"
                        if not us.enabled:
                            label += f" ({t('common.disabled')})"
                        options.append(label)
                        option_ids.append(f"user:{inst_owner}:{us.service_id}")

                # Current selection
                current_ref = current_overrides.get(svc_id)
                default_idx = 0
                if current_ref and current_ref in option_ids:
                    default_idx = option_ids.index(current_ref)

                choice_idx = st.selectbox(
                    f"**{svc_id}** (`{svc_type}`)",
                    options=range(len(options)),
                    format_func=lambda i, opts=options: opts[i],
                    index=default_idx,
                    key=f"stopped_svc_mode_{inst.instance_id}_{svc_id}",
                )
                chosen_ref = option_ids[choice_idx]
                if chosen_ref:
                    new_overrides[svc_id] = chosen_ref

                # Show local config fields if not forwarding
                if chosen_ref is None:
                    field_keys = {}
                    schema = _get_service_schema_rt(svc_type)
                    if schema:
                        from gui.components.schema_form import render_schema_fields
                        render_schema_fields(
                            schema, svc_config,
                            key_prefix=f"stopped_svc_{inst.instance_id}_{svc_id}",
                        )
                        # Collect field keys from schema (flat dict: {param_name: {type, ...}})
                        for prop_name in schema:
                            sk = f"stopped_svc_{inst.instance_id}_{svc_id}_{prop_name}"
                            field_keys[prop_name] = sk
                    else:
                        for cfg_key, cfg_val in svc_config.items():
                            sk = f"stopped_svc_{inst.instance_id}_{svc_id}_{cfg_key}"
                            st.text_input(
                                cfg_key, value=str(cfg_val), disabled=False,
                                key=sk,
                            )
                            field_keys[cfg_key] = sk
                    svc_field_keys[svc_id] = field_keys
                else:
                    st.caption(f"→ {t('runtime.svc_forwarded_to')} **{chosen_ref}**")

            # Always show save button in editable mode
            if st.button(f"💾 {t('common.save')} {t('settings.services')}",
                         key=f"save_svc_{inst.instance_id}", type="primary"):
                # Collect local config values from session_state
                new_configs = {}
                for svc_id, field_keys in svc_field_keys.items():
                    cfg = {}
                    for cfg_key, sk in field_keys.items():
                        if sk in st.session_state:
                            cfg[cfg_key] = st.session_state[sk]
                    if cfg:
                        new_configs[svc_id] = cfg

                dep_reg = DeploymentRegistry.get_instance()
                with dep_reg._data_lock:
                    live = dep_reg._instances.get(inst.instance_id)
                    if live:
                        live.service_overrides = new_overrides
                        live.service_configs = new_configs
                dep_reg._save_instance(live or inst)
                st.success(t("common.success"))
                st.rerun()
        else:
            # Read-only: show current service config and forwarding
            current_overrides = _migrate_service_overrides(inst.service_overrides)
            for svc_id, svc_def in flow_services.items():
                svc_type = svc_def.get("type", "?")
                forwarded_to = current_overrides.get(svc_id)

                if forwarded_to:
                    if forwarded_to.startswith("user:"):
                        icon = "👤"
                    else:
                        icon = "🌐"
                    st.markdown(f"**{svc_id}** (`{svc_type}`) → {icon} **{forwarded_to}**")
                else:
                    svc_config = svc_def.get("parameters", svc_def.get("config", {}))
                    st.markdown(f"**{svc_id}** (`{svc_type}`)")
                    for cfg_key, cfg_val in svc_config.items():
                        st.text_input(
                            cfg_key, value=str(cfg_val), disabled=True,
                            key=f"ro_svc_{inst.instance_id}_{svc_id}_{cfg_key}",
                        )


def _apply_service_configs(flow, service_configs: Dict[str, Dict[str, Any]]):
    """Apply saved instance service configs to flow services (local, non-forwarded)."""
    if not service_configs:
        return
    for svc_id, cfg in service_configs.items():
        if svc_id in flow.services and cfg:
            svc = flow.services[svc_id]
            for k, v in cfg.items():
                svc.config[k] = v
            logger.info("Applied custom config to service '%s': %s", svc_id, list(cfg.keys()))


def _migrate_service_overrides(overrides: Optional[Dict[str, str]]) -> Dict[str, str]:
    """Migrate legacy bare IDs to prefixed format (global:)."""
    if not overrides:
        return {}
    result = {}
    for k, v in overrides.items():
        if v and not v.startswith("global:") and not v.startswith("user:"):
            result[k] = f"global:{v}"
        else:
            result[k] = v
    return result


def _apply_service_forwards(flow, service_overrides: Dict[str, str]):
    """Replace flow services with global or user service instances where forwarded.

    Override format (prefixed):
      "global:{svc_id}" → global service
      "user:{user_id}:{svc_id}" → user service
    Legacy bare IDs (no prefix) are treated as global.
    """
    if not service_overrides:
        return
    from gui.services.global_service_registry import GlobalServiceRegistry
    from gui.services.user_service_registry import UserServiceRegistry
    gsvc_reg = GlobalServiceRegistry.get_instance()
    usvc_reg = UserServiceRegistry.get_instance()

    for flow_svc_id, ref in service_overrides.items():
        live = None
        label = ref
        if ref.startswith("user:"):
            parts = ref.split(":", 2)
            if len(parts) == 3:
                _, uid, sid = parts
                live = usvc_reg.get_live_instance(uid, sid)
                label = f"user:{uid}:{sid}"
            else:
                logger.warning("Invalid user service override format: %s", ref)
                continue
        elif ref.startswith("global:"):
            sid = ref.split(":", 1)[1]
            live = gsvc_reg.get_live_instance(sid)
            label = f"global:{sid}"
        else:
            # Legacy bare ID — treat as global
            live = gsvc_reg.get_live_instance(ref)
            label = f"global:{ref}"

        if live is not None and flow_svc_id in flow.services:
            flow.services[flow_svc_id] = live
            logger.info("Forwarded service '%s' → %s", flow_svc_id, label)
        elif live is None:
            logger.warning("Service '%s' not connected, using local for '%s'",
                          label, flow_svc_id)


def _render_instance_parameters(inst, editable: bool = False):
    """Show instance parameters, editable when stopped."""
    from gui.services.deployment_registry import DeploymentRegistry

    # Load template to discover all available parameter keys
    template_params = {}
    if inst.flow_path and Path(inst.flow_path).exists():
        try:
            raw = _json.loads(Path(inst.flow_path).read_text(encoding="utf-8"))
            template_params = raw.get("parameters", {})
        except Exception:
            pass

    # Merge: template defaults + instance overrides
    all_keys = list(template_params.keys())
    for k in (inst.parameters or {}):
        if k not in all_keys:
            all_keys.append(k)

    if not all_keys and not editable:
        return

    with st.expander(f"📎 {t('runtime.instance_params')}", expanded=bool(all_keys)):
        if not all_keys and not editable:
            st.caption(t("runtime.no_instance_params"))
            return

        if editable:
            edited = {}
            to_delete = None
            for pname in all_keys:
                is_extra = pname not in template_params
                current = (inst.parameters or {}).get(pname, template_params.get(pname, ""))
                if is_extra:
                    pcols = st.columns([8, 1])
                    with pcols[0]:
                        edited[pname] = st.text_input(
                            f"📎 {pname}", value=str(current),
                            key=f"stopped_param_{inst.instance_id}_{pname}",
                        )
                    with pcols[1]:
                        st.markdown("<br>", unsafe_allow_html=True)
                        if st.button("🗑️", key=f"delparam_{inst.instance_id}_{pname}"):
                            to_delete = pname
                else:
                    edited[pname] = st.text_input(
                        f"📎 {pname}", value=str(current),
                        key=f"stopped_param_{inst.instance_id}_{pname}",
                    )

            # Handle deletion
            if to_delete:
                edited.pop(to_delete, None)
                dep_reg = DeploymentRegistry.get_instance()
                with dep_reg._data_lock:
                    live = dep_reg._instances.get(inst.instance_id)
                    if live:
                        live.parameters = edited
                dep_reg._save_instance(live or inst)
                st.rerun()

            # Add new parameter
            add_cols = st.columns([4, 4, 1])
            with add_cols[0]:
                new_key = st.text_input(
                    t("common.name"), key=f"stopped_newparam_key_{inst.instance_id}",
                    placeholder="new_param",
                )
            with add_cols[1]:
                new_val = st.text_input(
                    "Value", key=f"stopped_newparam_val_{inst.instance_id}",
                    placeholder="value",
                )
            with add_cols[2]:
                st.markdown("<br>", unsafe_allow_html=True)
                add_clicked = st.button("➕", key=f"stopped_addparam_{inst.instance_id}")

            if add_clicked and new_key and new_key.strip():
                edited[new_key.strip()] = new_val
                # Save immediately
                dep_reg = DeploymentRegistry.get_instance()
                with dep_reg._data_lock:
                    live = dep_reg._instances.get(inst.instance_id)
                    if live:
                        live.parameters = edited
                dep_reg._save_instance(inst)
                st.rerun()

            # Always show save button
            if st.button(f"💾 {t('common.save')} {t('runtime.instance_params')}",
                         key=f"save_params_{inst.instance_id}", type="primary"):
                dep_reg = DeploymentRegistry.get_instance()
                with dep_reg._data_lock:
                    live = dep_reg._instances.get(inst.instance_id)
                    if live:
                        live.parameters = edited
                dep_reg._save_instance(live or inst)
                st.success(t("common.success"))
                st.rerun()
        else:
            # Read-only display
            for pname in all_keys:
                val = (inst.parameters or {}).get(pname, template_params.get(pname, ""))
                st.text_input(
                    f"📎 {pname}", value=str(val), disabled=True,
                    key=f"ro_param_{inst.instance_id}_{pname}",
                )


def _render_live_kpis_and_flow(executor: ContinuousFlowExecutor, instance_id: str):
    """Toolbar + auto-refreshing fragment for KPIs and flow view."""
    from datetime import timedelta
    from gui.components.runtime_visualizer import (
        render_runtime_flow, save_layout_to_disk, reload_positions_from_disk,
        is_layout_dirty, _pos_key as viz_pos_key, _disk_key as viz_disk_key,
    )

    _frozen_key = f"_rt_frozen_{instance_id}"
    if _frozen_key not in st.session_state:
        st.session_state[_frozen_key] = True

    # Toolbar placeholder — filled after fragment so dirty state is correct
    toolbar_placeholder = st.empty()

    # -- Auto-refreshing fragment (KPIs + canvas + selected task info) --
    @st.fragment(run_every=timedelta(seconds=3))
    def _live_fragment():
        _status = executor.get_status()

        # Detect flow stop → full rerun so controls/status outside fragment update
        _was_running_key = f"_rt_was_running_{instance_id}"
        _is_running = _status["is_running"]
        _was_running = st.session_state.get(_was_running_key, _is_running)
        st.session_state[_was_running_key] = _is_running
        if _was_running and not _is_running:
            st.rerun(scope="app")

        c1, c2, c3, c4, c5 = st.columns([1, 1, 1, 1, 1])
        with c1:
            st.metric(t("dashboard.total_tasks"), _status["tasks_total"])
        with c2:
            st.metric(t("runtime.running"), _status["tasks_running"])
        with c3:
            st.metric(t("runtime.performance.errors"), _status["tasks_errored"])
        with c4:
            st.metric(t("runtime.continuous.queue_stats"), _status["total_queued_flowfiles"])
        with c5:
            from datetime import datetime as _dt
            _ts = executor.get_all_task_states()
            _inflight = [tid for tid, s in _ts.items() if s.get("in_flight")]
            st.caption(f"🔄 {_dt.now().strftime('%H:%M:%S')} | ✈ {_inflight or 'none'}")

        auto_key = f"_rt_do_auto_layout_{instance_id}"
        do_auto = st.session_state.pop(auto_key, False)
        _frozen = st.session_state.get(_frozen_key, True)

        selected = render_runtime_flow(executor, height=450,
                                        use_auto_layout=do_auto, frozen=_frozen,
                                        instance_id=instance_id)
        if selected:
            _all = executor.get_all_task_states()
            ts = _all.get(selected, {})
            if ts:
                st.info(
                    f"**{selected}** ({ts.get('task_type', '?')}) — "
                    f"{t('common.status')}: {ts.get('state', '?')} | "
                    f"{t('runtime.performance.runs')}: {ts.get('run_count', 0)} | "
                    f"{t('runtime.performance.errors')}: {ts.get('error_count', 0)} | "
                    f"In: {ts.get('flowfiles_in', 0)} Out: {ts.get('flowfiles_out', 0)}"
                )

    _live_fragment()

    # Fill toolbar now (after canvas updated session_state)
    is_frozen = st.session_state.get(_frozen_key, True)
    dirty = is_layout_dirty(instance_id)
    with toolbar_placeholder.container():
        col_title, col_auto, col_save, col_cancel, col_freeze = st.columns([3, 1, 1, 1, 1])
        with col_title:
            st.markdown(f"#### 🗺️ {t('runtime.flow_view')}")
        with col_auto:
            if st.button(f"📐 {t('runtime.auto_layout')}", key=f"auto_layout_live_{instance_id}",
                          disabled=is_frozen):
                st.session_state[f"_rt_do_auto_layout_{instance_id}"] = True
                st.rerun()
        with col_save:
            if st.button(f"💾 {t('common.save')}", key=f"save_layout_live_{instance_id}",
                          disabled=(is_frozen or not dirty)):
                cur = st.session_state.get(viz_pos_key(instance_id), {})
                if cur:
                    save_layout_to_disk(instance_id, cur)
                    st.session_state[viz_disk_key(instance_id)] = dict(cur)
                st.session_state[_frozen_key] = True
                st.session_state.pop("_rt_fp_live", None)
                st.session_state.pop("_rt_state_live", None)
                st.rerun()
        with col_cancel:
            if st.button(f"↩ {t('common.cancel')}", key=f"cancel_layout_live_{instance_id}",
                          disabled=(is_frozen or not dirty)):
                reload_positions_from_disk(instance_id, view_suffix="_live")
                st.session_state[_frozen_key] = True
                st.rerun()
        with col_freeze:
            if is_frozen:
                if st.button(f"🔓 {t('runtime.unfreeze')}", key=f"unfreeze_live_{instance_id}"):
                    st.session_state[_frozen_key] = False
                    st.rerun()
            else:
                if st.button(f"🔒 {t('runtime.freeze')}", key=f"freeze_live_{instance_id}"):
                    st.session_state[_frozen_key] = True
                    st.rerun()


def _render_executor_dashboard(executor: ContinuousFlowExecutor, flow_id: str,
                                executors: Dict[str, ContinuousFlowExecutor]):
    """Render the dashboard for a specific running executor."""
    status = executor.get_status()
    is_running = status["is_running"]

    # Controls (outside fragment — buttons that do full rerun)
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        status_text = "🟢 " + t("runtime.trigger.active") if is_running else "🔴 " + t("runtime.trigger.stopped")
        flow_ver = executor._flow.version if hasattr(executor._flow, 'version') else status['flow_version']
        st.markdown(f"**{t('common.status')}:** {status_text} | **{t('common.version')}:** v{flow_ver}")
    with col2:
        if check_permission(session, "flow.execute"):
            if is_running:
                if st.button(f"⏹️ {t('runtime.stop')}", width="stretch", key=f"stop_{flow_id}"):
                    executor.stop()
                    st.rerun()
            else:
                if st.button(f"▶️ {t('runtime.continuous.start')}", width="stretch", type="primary", key=f"restart_{flow_id}"):
                    executor.start()
                    st.rerun()
        else:
            st.button(f"⏹️ {t('runtime.stop')}" if is_running else f"▶️ {t('runtime.continuous.start')}",
                      width="stretch", disabled=True, help=t("auth.no_permission"), key=f"ctrl_{flow_id}")
    with col3:
        if check_permission(session, "flow.execute"):
            if st.button(f"🔄 {t('common.refresh')}", width="stretch", key=f"refresh_{flow_id}"):
                _hot_update_flow(executor)
        else:
            st.button(f"🔄 {t('common.refresh')}", width="stretch", disabled=True, key=f"refresh_d_{flow_id}")
    with col4:
        if check_permission(session, "flow.execute"):
            if st.button(f"🗑️ {t('runtime.undeploy')}", width="stretch", key=f"del_{flow_id}"):
                executor.stop()
                from gui.services.executor_registry import ExecutorRegistry
                ExecutorRegistry.get_instance().unregister(flow_id)
                from gui.services.deployment_registry import DeploymentRegistry
                DeploymentRegistry.get_instance().undeploy(flow_id)
                st.session_state.pop("rt_selected_instance", None)
                st.rerun()
        else:
            st.button(f"🗑️ {t('runtime.undeploy')}", width="stretch", disabled=True, key=f"del_d_{flow_id}")

    # -- Live KPIs + Flow View (auto-refreshing) --
    st.markdown("---")
    _render_live_kpis_and_flow(executor, flow_id)  # flow_id here is actually instance_id from caller

    # -- Parameters (read-only for running) --
    from gui.services.deployment_registry import DeploymentRegistry
    dep_reg = DeploymentRegistry.get_instance()
    running_inst = dep_reg.get(flow_id)
    if running_inst:
        _render_instance_parameters(running_inst, editable=False)
        _render_instance_services(running_inst, editable=False)

    # -- Services --
    if hasattr(executor, '_flow') and executor._flow.services:
        st.markdown("---")
        with st.expander(f"🔌 {t('settings.services')}", expanded=False):
            for svc_id, svc in executor._flow.services.items():
                svc_type = svc.TYPE if hasattr(svc, 'TYPE') else type(svc).__name__
                connected = svc.is_connected() if hasattr(svc, 'is_connected') else False
                icon = "🟢" if connected else "🔴"
                with st.container(border=True):
                    c1, c2, c3 = st.columns([3, 2, 2])
                    with c1:
                        st.markdown(f"**{icon} {svc_id}** (`{svc_type}`)")
                    with c2:
                        st.caption(t("service.connected") if connected else t("service.disconnected"))
                    with c3:
                        if check_permission(session, "service.manage"):
                            if connected:
                                if st.button(f"⏹️ {t('service.stop')}", key=f"svc_stop_{flow_id}_{svc_id}"):
                                    try:
                                        svc.disconnect()
                                        st.success(f"Service '{svc_id}' {t('runtime.trigger.stopped').lower()}")
                                    except Exception as e:
                                        st.error(str(e))
                                    st.rerun()
                            else:
                                if st.button(f"▶️ {t('service.start')}", key=f"svc_start_{flow_id}_{svc_id}"):
                                    try:
                                        svc.connect()
                                        st.success(f"Service '{svc_id}' {t('runtime.trigger.active').lower()}")
                                    except Exception as e:
                                        st.error(str(e))
                                    st.rerun()
                        else:
                            st.button(f"⏹️ {t('service.stop')}" if connected else f"▶️ {t('service.start')}",
                                      key=f"svc_toggle_{flow_id}_{svc_id}",
                                      disabled=True, help=t("auth.no_permission"))

    # (Live Flow Visualizer is now inside the auto-refreshing fragment above)

    # -- Inject FlowFile --
    st.markdown("---")
    with st.expander(f"📥 {t('runtime.continuous.inject')}", expanded=False):
        inject_content = st.text_area(t("queue.flowfile_content"), value="", height=100, key=f"inject_content_{flow_id}")
        inject_attrs_raw = st.text_area(
            f"{t('queue.flowfile_attrs')} (JSON)", value="{}", height=60, key=f"inject_attrs_{flow_id}",
        )
        task_ids = list(executor.get_all_task_states().keys())
        inject_target = st.selectbox(t("runtime.inject_target"), options=["auto"] + task_ids,
                                     key=f"inject_target_{flow_id}")

        if check_permission(session, "flow.execute"):
            if st.button(f"📥 {t('runtime.continuous.inject')}", width="stretch", key=f"inject_btn_{flow_id}"):
                try:
                    attrs = _json.loads(inject_attrs_raw) if inject_attrs_raw.strip() else {}
                except Exception:
                    attrs = {}
                from core import FlowFile
                ff = FlowFile(content=inject_content.encode("utf-8"), attributes=attrs)
                target = None if inject_target == "auto" else inject_target
                ok = executor.inject(ff, entry_task_id=target)
                if ok:
                    st.success(t("runtime.inject_success"))
                else:
                    st.warning(t("runtime.inject_backpressure"))
                st.rerun()
        else:
            st.button(f"📥 {t('runtime.continuous.inject')}", width="stretch", key=f"inject_btn_{flow_id}",
                      disabled=True, help=t("auth.no_permission"))

    # -- Task States --
    st.markdown("---")
    st.markdown(f"#### {t('dashboard.total_tasks')}")

    all_states = executor.get_all_task_states()
    state_icons = {"running": "🟢", "stopped": "🔴", "error": "🔥", "disabled": "⚫"}

    for task_id, state in all_states.items():
        icon = state_icons.get(state["state"], "❓")
        with st.container(border=True):
            col1, col2, col3, col4 = st.columns([3, 2, 2, 2])
            with col1:
                st.markdown(f"**{icon} {task_id}** ({state.get('task_type', '')})")
                if state.get("error"):
                    st.error(f"{t('common.error')}: {state['error']}")
            with col2:
                st.caption(f"In: {state.get('flowfiles_in', 0)} | Out: {state.get('flowfiles_out', 0)}")
            with col3:
                st.caption(f"Bytes: {state.get('bytes_in', 0)} in / {state.get('bytes_out', 0)} out")
            with col4:
                if check_permission(session, "flow.execute"):
                    if state["state"] == "error":
                        if st.button("🔄 Restart", key=f"restart_{flow_id}_{task_id}"):
                            executor.restart_task(task_id)
                            st.rerun()
                    elif state["state"] == "running":
                        if st.button("⏸️ Stop", key=f"stop_{flow_id}_{task_id}"):
                            executor.stop_task(task_id)
                            st.rerun()
                    elif state["state"] == "stopped":
                        if st.button("▶️ Start", key=f"start_{flow_id}_{task_id}"):
                            executor.start_task(task_id)
                            st.rerun()

    # -- Execution Timeline --
    st.markdown("---")
    with st.expander(f"📈 {t('timeline.title')}", expanded=False):
        try:
            from gui.components.execution_timeline import render_execution_timeline

            # Build task_stats from all_states
            task_stats_map = {}
            for task_id, tstate in all_states.items():
                task_stats_map[task_id] = {
                    "type": tstate.get("task_type", ""),
                    "runs": tstate.get("run_count", 0),
                    "errors": tstate.get("error_count", 0),
                    "ff_in": tstate.get("flowfiles_in", 0),
                    "ff_out": tstate.get("flowfiles_out", 0),
                }

            # Create a minimal execution state for the timeline
            class _MinState:
                duration_ms = 0
                errors = []
                statistics = {}

            render_execution_timeline(_MinState(), task_stats=task_stats_map)
        except Exception:
            pass

    # -- Task Performance --
    st.markdown("---")
    with st.expander(f"📊 {t('runtime.performance.title')}", expanded=False):
        perf_data = []
        for task_id, state in all_states.items():
            runs = state.get("run_count", 0)
            errors = state.get("error_count", 0)
            ff_in = state.get("flowfiles_in", 0)
            ff_out = state.get("flowfiles_out", 0)
            b_in = state.get("bytes_in", 0)
            b_out = state.get("bytes_out", 0)
            perf_data.append({
                "Task": task_id,
                t("common.type"): state.get("task_type", ""),
                t("runtime.performance.runs"): runs,
                t("runtime.performance.errors"): errors,
                "FF In": ff_in,
                "FF Out": ff_out,
                t("runtime.performance.bytes_in"): f"{b_in/1024:.1f} KB" if b_in > 0 else "0",
                t("runtime.performance.bytes_out"): f"{b_out/1024:.1f} KB" if b_out > 0 else "0",
                t("runtime.performance.error_rate"): f"{errors/runs*100:.1f}%" if runs > 0 else "—",
            })
        if perf_data:
            import pandas as pd
            df = pd.DataFrame(perf_data)
            st.dataframe(df, width="stretch", hide_index=True)

            # Top bottleneck: task with highest error rate
            bottleneck = max(perf_data, key=lambda x: x.get(t("runtime.performance.errors"), 0))
            if bottleneck.get(t("runtime.performance.errors"), 0) > 0:
                st.warning(f"⚠️ {t('runtime.performance.bottleneck')}: **{bottleneck['Task']}** ({bottleneck[t('runtime.performance.errors')]} {t('runtime.performance.errors').lower()})")
        else:
            st.info(t("common.none"))

    # -- Queue Management --
    st.markdown("---")
    st.markdown(f"#### {t('queue.title')}")

    queue_stats = status.get("queue_stats", [])
    if queue_stats:
        # Global actions
        col_hdr1, col_hdr2 = st.columns([4, 1])
        with col_hdr2:
            if check_permission(session, "flow.execute"):
                if st.button(f"🗑️ {t('queue.clear_all')}", key=f"clear_all_q_{flow_id}"):
                    executor.clear_all_queues()
                    st.success(t("queue.cleared"))
                    st.rerun()
            else:
                st.button(f"🗑️ {t('queue.clear_all')}", key=f"clear_all_q_{flow_id}",
                          disabled=True, help=t("auth.no_permission"))

        for idx, qs in enumerate(queue_stats):
            max_q = qs.get("max_queue_size", qs.get("max_size", 1)) or 1
            pct = (qs["queue_size"] / max_q * 100) if max_q > 0 else 0
            bp_icon = "🔴" if qs.get("backpressured") else "🟢"
            src, tgt = qs["source"], qs["target"]
            conn_label = f"{src} → {tgt}"

            c1, c2, c3 = st.columns([5, 1, 1])
            with c1:
                st.markdown(
                    f"{bp_icon} **{conn_label}** : "
                    f"{qs['queue_size']}/{max_q} ({pct:.0f}%) "
                    f"| {qs.get('queue_bytes', qs.get('total_bytes', 0))} bytes "
                    f"| {t('queue.stats_in')}: {qs.get('flowfiles_in', 0)} "
                    f"{t('queue.stats_out')}: {qs.get('flowfiles_out', 0)}"
                )
            with c2:
                if st.button("🔍", key=f"inspect_q_{flow_id}_{idx}",
                             help=t("queue.inspect")):
                    st.session_state[f"_inspect_queue_{flow_id}"] = (src, tgt)
            with c3:
                if check_permission(session, "flow.execute"):
                    if st.button("🗑️", key=f"clear_q_{flow_id}_{idx}",
                                 help=t("queue.clear")):
                        executor.clear_task_queue(src, tgt)
                        st.success(t("queue.cleared"))
                        st.rerun()
                else:
                    st.button("🗑️", key=f"clear_q_{flow_id}_{idx}",
                              disabled=True)

            st.progress(min(pct / 100, 1.0))

        # -- Queue Inspector --
        inspect_key = f"_inspect_queue_{flow_id}"
        if inspect_key in st.session_state:
            src, tgt = st.session_state[inspect_key]
            conn = executor.connections.get_connection(src, tgt)
            if conn:
                with st.expander(f"🔍 {t('queue.contents')}: {src} → {tgt}", expanded=True):
                    flowfiles = conn.peek_all(limit=50)
                    if not flowfiles:
                        st.info(t("queue.empty"))
                    else:
                        st.caption(f"{len(flowfiles)} FlowFile(s) (max 50)")
                        for ff_idx, ff in enumerate(flowfiles):
                            with st.container():
                                fc1, fc2, fc3 = st.columns([3, 1, 1])
                                with fc1:
                                    ff_id = ff.process_id[:12] if ff.process_id else "?"
                                    st.markdown(f"**#{ff_idx+1}** `{ff_id}` — {ff.size()} bytes")
                                with fc2:
                                    st.caption(ff.get_attribute("filename") or "")
                                with fc3:
                                    if check_permission(session, "flow.execute"):
                                        if st.button("🗑️", key=f"del_ff_{flow_id}_{idx}_{ff_idx}"):
                                            conn.remove_by_index(ff_idx)
                                            st.rerun()

                                # Attributes
                                attrs = ff.attributes
                                if attrs:
                                    with st.expander(t("queue.flowfile_attrs"), expanded=False):
                                        st.json(attrs)

                                # Content preview (first 500 bytes)
                                try:
                                    content = ff.content
                                    if content:
                                        preview = content[:500]
                                        try:
                                            text = preview.decode("utf-8", errors="replace")
                                        except Exception:
                                            text = repr(preview)
                                        with st.expander(t("queue.flowfile_content"), expanded=False):
                                            st.code(text, language=None)
                                except Exception:
                                    pass

                    if st.button(t("common.close"), key=f"close_inspect_{flow_id}"):
                        del st.session_state[inspect_key]
                        st.rerun()
    else:
        st.info(t("queue.empty"))

    # -- Data Preview --
    st.markdown("---")
    _render_data_preview(executor, flow_id)

    # -- Version History --
    history = executor.get_version_history()
    if history:
        st.markdown("---")
        with st.expander(f"📜 {t('runtime.history')}", expanded=False):
            for entry in reversed(history):
                st.markdown(
                    f"**v{entry['version']}** — {entry['action']} "
                    f"({entry.get('timestamp', '')[:19]})"
                )
                if entry.get("task_id"):
                    st.caption(f"Task: {entry['task_id']}")

    # -- Flow Logs --
    st.markdown("---")
    from gui.components.log_viewer import render_log_viewer_expander, LogCapture
    # Ensure we capture logs for this flow
    flow_capture = LogCapture.get_for_flow(flow_id)
    flow_capture.setLevel(logging.DEBUG)
    for _log_name in ("engine", "tasks", "services", "core"):
        _lg = logging.getLogger(_log_name)
        if flow_capture not in _lg.handlers:
            _lg.addHandler(flow_capture)
        if _lg.level > logging.DEBUG:
            _lg.setLevel(logging.DEBUG)
    render_log_viewer_expander(
        flow_id=flow_id,
        task_ids=list(all_states.keys()),
        key_suffix=flow_id,
    )

    # -- Debug Panel --
    st.markdown("---")
    render_debug_panel(executor)

    # -- Manual refresh --
    if is_running:
        if st.button(f"🔄 {t('common.refresh')}", width="stretch", key=f"refresh_dash_{flow_id}"):
            st.rerun()


def _get_data_preview(executor: ContinuousFlowExecutor) -> DataPreviewManager:
    """Get or create a DataPreviewManager attached to the executor."""
    if not hasattr(executor, '_data_preview') or executor._data_preview is None:
        preview = DataPreviewManager()
        preview.attach(executor)
    return executor._data_preview


def _render_data_preview(executor: ContinuousFlowExecutor, flow_id: str):
    """Data preview panel: capture and inspect FlowFile data at connections."""
    import time as _time

    st.markdown(f"#### 👁️ {t('preview.title')}")

    preview = _get_data_preview(executor)

    # Controls
    ctrl1, ctrl2, ctrl3, ctrl4 = st.columns(4)
    with ctrl1:
        if preview._capture_all:
            st.markdown(f"🟢 {t('preview.capture_active')}")
        else:
            enabled_count = len(preview._enabled_connections)
            if enabled_count > 0:
                st.markdown(f"🟡 {enabled_count} {t('preview.connection')}(s)")
            else:
                st.markdown(f"⚪ {t('preview.capture_inactive')}")
    with ctrl2:
        if not preview._capture_all:
            if st.button(f"📡 {t('preview.enable_all')}", key=f"prev_enable_all_{flow_id}"):
                preview.enable_all()
                st.rerun()
        else:
            if st.button(f"⏹️ {t('preview.disable_all')}", key=f"prev_disable_all_{flow_id}"):
                preview.disable_all()
                st.rerun()
    with ctrl3:
        if st.button(f"🗑️ {t('preview.clear')}", key=f"prev_clear_{flow_id}"):
            preview.clear()
            st.rerun()

    # Per-connection toggle (deduplicate src→tgt pairs from multiple relations)
    queue_stats = executor.get_status().get("queue_stats", [])
    if queue_stats:
        # Deduplicate: keep unique (source, target) pairs
        unique_pairs = list(dict.fromkeys((qs["source"], qs["target"]) for qs in queue_stats))
        with st.expander(f"🔧 {t('preview.connection')}s", expanded=False):
            for idx, (src, tgt) in enumerate(unique_pairs):
                is_on = preview.is_enabled(src, tgt)
                col_a, col_b = st.columns([4, 1])
                with col_a:
                    icon = "🟢" if is_on else "⚪"
                    st.markdown(f"{icon} {src} → {tgt}")
                with col_b:
                    if is_on:
                        if st.button(t("preview.disable"), key=f"prev_off_{flow_id}_{idx}"):
                            preview.disable_connection(src, tgt)
                            st.rerun()
                    else:
                        if st.button(t("preview.enable"), key=f"prev_on_{flow_id}_{idx}"):
                            preview.enable_connection(src, tgt)
                            st.rerun()

    # Display captured samples
    connections_data = preview.get_connections_with_data()
    if connections_data:
        for conn_info in connections_data:
            conn_key = conn_info["connection"]
            count = conn_info["sample_count"]
            with st.expander(f"📊 {conn_key} ({count} {t('preview.sample_count').lower()})", expanded=False):
                # Parse source/target from "src -> tgt"
                parts = conn_key.split(" -> ")
                if len(parts) == 2:
                    samples = preview.get_samples(parts[0], parts[1], limit=10)
                else:
                    samples = preview.get_samples(limit=10)

                for sample in reversed(samples):
                    with st.container(border=True):
                        sc1, sc2, sc3 = st.columns([2, 2, 1])
                        with sc1:
                            ts = _time.strftime("%H:%M:%S", _time.localtime(sample["timestamp"]))
                            st.markdown(f"🕐 {ts}")
                        with sc2:
                            ct = sample["content_type"]
                            type_icons = {"json": "📋", "csv": "📊", "xml": "📄", "text": "📝", "binary": "🔢", "empty": "⬜"}
                            st.markdown(f"{type_icons.get(ct, '📄')} {ct} ({sample['content_size']} bytes)")
                        with sc3:
                            st.caption(f"#{sample['index']}")

                        # Attributes
                        if sample.get("attributes"):
                            with st.expander(t("queue.flowfile_attrs"), expanded=False):
                                st.json(sample["attributes"])

                        # Content preview
                        if sample.get("content_preview"):
                            ct = sample["content_type"]
                            lang = {"json": "json", "xml": "xml", "csv": None}.get(ct, None)
                            with st.expander(t("queue.flowfile_content"), expanded=False):
                                st.code(sample["content_preview"][:1000], language=lang)
    else:
        if preview._capture_all or preview._enabled_connections:
            st.info(t("preview.no_samples"))


def _get_debugger(executor: ContinuousFlowExecutor) -> FlowDebugger:
    """Get or create a debugger attached to the executor."""
    if not hasattr(executor, '_debugger') or executor._debugger is None:
        debugger = FlowDebugger()
        debugger.attach(executor)
    return executor._debugger


def render_debug_panel(executor: ContinuousFlowExecutor):
    """Debug panel: breakpoints, step control, FlowFile inspection."""
    st.markdown(f"#### 🔍 {t('runtime.debug.title')}")

    debugger = _get_debugger(executor)
    dbg_status = debugger.get_status()

    # -- Status indicator --
    if dbg_status["paused"]:
        paused_task = dbg_status["paused_at"] or "unknown"
        st.warning(f"⏸️ {t('runtime.debug.paused_at_task', task=paused_task)}")
    elif dbg_status["step_mode"]:
        st.info(f"🦶 {t('runtime.debug.step_mode')}")
    else:
        bp_count = len(dbg_status["breakpoints"])
        if bp_count > 0:
            st.success(f"🟢 {t('runtime.debug.running_with_bp', count=bp_count)}")
        else:
            st.caption(t("runtime.debug.no_breakpoints"))

    # -- Control buttons --
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button(f"▶️ {t('runtime.debug.continue')}", key="dbg_continue", width="stretch",
                      disabled=not dbg_status["paused"]):
            debugger.continue_execution()
            st.rerun()
    with col2:
        if st.button(f"🦶 {t('runtime.debug.step')}", key="dbg_step", width="stretch",
                      disabled=not dbg_status["paused"]):
            debugger.step()
            st.rerun()
    with col3:
        if st.button(f"⏹️ {t('runtime.debug.stop')}", key="dbg_stop", width="stretch"):
            debugger.stop_debugging()
            st.rerun()

    # -- Breakpoints panel --
    with st.expander(f"🔴 {t('runtime.debug.breakpoints')}", expanded=False):
        all_states = executor.get_all_task_states()
        breakpoints = dbg_status["breakpoints"]

        for task_id in all_states:
            has_bp = task_id in breakpoints
            bp_enabled = breakpoints[task_id]["enabled"] if has_bp else False
            bp_info = breakpoints.get(task_id, {})

            with st.container(border=has_bp):
                col1, col2, col3 = st.columns([3, 1, 1])
                with col1:
                    label = f"{'🔴' if bp_enabled else '⚪'} {task_id}"
                    if has_bp and bp_info.get("hit_count", 0) > 0:
                        label += f" (hits: {bp_info['hit_count']})"
                    st.markdown(label)
                    # Show condition/log if set
                    if has_bp:
                        cond = bp_info.get("condition", "")
                        log_msg = bp_info.get("log_message", "")
                        if cond:
                            st.caption(f"⚡ Condition: `{cond}`")
                        if log_msg:
                            st.caption(f"📝 Log: `{log_msg}`")
                with col2:
                    if has_bp:
                        if st.button("🔄", key=f"bp_toggle_{task_id}",
                                     help="Toggle"):
                            debugger.toggle_breakpoint(task_id)
                            st.rerun()
                with col3:
                    if has_bp:
                        if st.button("✕", key=f"bp_rm_{task_id}",
                                     help=t("common.delete")):
                            debugger.remove_breakpoint(task_id)
                            st.rerun()
                    else:
                        if st.button("🔴", key=f"bp_add_{task_id}",
                                     help=t("runtime.debug.breakpoints")):
                            debugger.add_breakpoint(task_id)
                            st.rerun()

        # Add breakpoint with condition
        st.markdown("---")
        st.caption(t("runtime.debug.advanced_bp"))
        bp_task = st.selectbox(
            t("dashboard.total_tasks"), options=[tid for tid in all_states if tid not in breakpoints],
            key="dbg_bp_task", label_visibility="collapsed",
        )
        bp_cols = st.columns(2)
        with bp_cols[0]:
            bp_condition = st.text_input(
                t("runtime.debug.bp_condition"), key="dbg_bp_cond",
                placeholder="e.g. flowfile.size > 1000",
            )
        with bp_cols[1]:
            bp_log = st.text_input(
                t("runtime.debug.bp_log_message"), key="dbg_bp_log",
                placeholder="Log instead of break",
            )
        if st.button(f"➕ {t('runtime.debug.breakpoints')}", key="dbg_add_adv_bp"):
            if bp_task:
                debugger.add_breakpoint(bp_task, condition=bp_condition, log_message=bp_log)
                st.rerun()

    # -- FlowFile Inspector (when paused) --
    if dbg_status["paused"] and dbg_status["paused_at"]:
        paused_task = dbg_status["paused_at"]
        st.markdown(f"#### 🔎 {t('runtime.debug.inspector')} ({t('runtime.debug.paused_at').replace('{task_id}', paused_task)})")
        snapshots = debugger.get_snapshots(task_id=paused_task, limit=5)
        if snapshots:
            for snap in reversed(snapshots):
                direction_icon = "📥" if snap["direction"] == "input" else "📤"
                st.markdown(f"{direction_icon} **{snap['direction'].upper()}** | "
                           f"Size: {snap['content_size']} bytes | "
                           f"ID: {snap['flowfile_id'][:12]}...")

                # Content preview
                if snap["content_preview"]:
                    st.code(snap["content_preview"], language="text")

                # Attributes table
                if snap["attributes"]:
                    import pandas as pd
                    attr_data = [{"Attribute": k, "Value": str(v)}
                                for k, v in snap["attributes"].items()]
                    st.dataframe(pd.DataFrame(attr_data), width="stretch", hide_index=True)
        else:
            st.info(t("runtime.debug.no_snapshots"))

    # -- Snapshot History --
    with st.expander(f"📜 {t('runtime.debug.snapshots')}", expanded=False):
        all_snapshots = debugger.get_snapshots(limit=50)
        if all_snapshots:
            import pandas as pd
            from datetime import datetime
            rows = []
            for snap in reversed(all_snapshots):
                ts = datetime.fromtimestamp(snap["timestamp"]).strftime("%H:%M:%S")
                preview = snap["content_preview"][:80] + "..." if len(snap["content_preview"]) > 80 else snap["content_preview"]
                rows.append({
                    "Time": ts,
                    "Task": snap["task_id"],
                    "Direction": snap["direction"],
                    "Size": snap["content_size"],
                    "Content": preview,
                })
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        else:
            st.info(t("runtime.debug.no_snapshots_hint"))


def main():
    """Fonction principale."""
    render_sidebar()
    initialize_state()
    render_continuous_execution()


if __name__ == "__main__":
    main()