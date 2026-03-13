#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Page Éditeur de flux - Visual Drag & Drop Editor.
Canvas interactif avec palette de tasks et panneau de configuration.
"""

import streamlit as st
from typing import Dict, Any, List
import json
import uuid
from datetime import datetime

# Configuration de la page
st.set_page_config(
    page_title="Editeur de flux - PyFi2",
    page_icon="✏️",
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

# Imports
from core import TaskFactory
from gui.components.flow_canvas import FlowCanvas
from gui.components.task_panel import TaskPanel
from gui.components.schema_form import render_schema_fields
from gui.services.flow_service import FlowService
from gui.services.template_service import TemplateService
from engine.flow_diff import FlowDiff

# Force l'initialisation des tasks
from tasks import register_all_tasks
register_all_tasks()


# ============================================================================
# State initialization
# ============================================================================

def initialize_state():
    """Initialiser l'etat de l'editeur."""
    # Convert Flow object to dict if needed (e.g. coming from Dashboard)
    if "current_flow" in st.session_state and st.session_state.current_flow is not None:
        from core import Flow
        if isinstance(st.session_state.current_flow, Flow):
            flow_service = FlowService()
            st.session_state.current_flow = flow_service.flow_to_dict(st.session_state.current_flow)

    # Reload from disk if a saved version exists and is newer
    # This ensures navigating to the Editor always shows the latest saved version
    if "current_flow" in st.session_state and st.session_state.current_flow is not None:
        flow = st.session_state.current_flow
        if isinstance(flow, dict) and flow.get("id"):
            import os
            filepath = f"flows/{flow['id']}.json"
            if os.path.exists(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        disk_flow = json.load(f)
                    # Only reload if disk version is different (newer)
                    if disk_flow.get("version") != flow.get("version"):
                        st.session_state.current_flow = disk_flow
                        # Force canvas rebuild
                        st.session_state.pop('_canvas_state', None)
                        st.session_state.pop('_canvas_fingerprint', None)
                except Exception:
                    pass  # Keep in-memory version on read error

    if "current_flow" not in st.session_state or st.session_state.current_flow is None:
        st.session_state.current_flow = {
            "id": f"flow_{uuid.uuid4().hex[:8]}",
            "name": t("editor.new_flow"),
            "version": "1.0.0",
            "description": "",
            "author": "",
            "parameters": {},
            "entries": [],
            "exits": [],
            "tasks": {},
            "groups": {},
            "relations": [],
            "variables": {},
        }

    if "selected_node" not in st.session_state:
        st.session_state.selected_node = None

    if "selected_edge" not in st.session_state:
        st.session_state.selected_edge = None

    if "connect_mode" not in st.session_state:
        st.session_state.connect_mode = False

    if "connect_from" not in st.session_state:
        st.session_state.connect_from = None

    if "_undo_stack" not in st.session_state:
        st.session_state._undo_stack = []

    if "_redo_stack" not in st.session_state:
        st.session_state._redo_stack = []


# ============================================================================
# Task categories for the palette
# ============================================================================

CATEGORY_I18N_KEYS = {
    "System": "task.categories.system",
    "IO": "task.categories.io",
    "Cloud": "task.categories.cloud",
    "Data": "task.categories.data",
    "Control": "task.categories.control",
    "Messaging": "task.categories.messaging",
    "AI": "task.categories.ai",
}

TASK_CATEGORIES = {
    "System": {
        "icon": "⚙️",
        "tasks": ["log", "updateAttribute", "replace_text", "wait", "fail",
                  "generateFlowFile", "hashContent", "listFiles", "executeScript"],
    },
    "IO": {
        "icon": "📁",
        "tasks": ["getFile", "putFile", "fetchHTTP", "listenHTTP",
                  "getSFTP", "putSFTP", "listSFTP", "getFTP", "putFTP",
                  "httpReceiver", "handleHTTPResponse", "validateHTTPAuth"],
    },
    "Cloud": {
        "icon": "☁️",
        "tasks": ["putS3", "getS3", "putGCS", "getGCS", "putAzureBlob", "getAzureBlob"],
    },
    "Data": {
        "icon": "🔄",
        "tasks": ["transformJSON", "evaluateJSONPath", "extractText",
                  "compressContent", "validateJSON", "convertCharset",
                  "filterContent", "base64Encode", "countText",
                  "convertCSVToJSON", "convertJSONToCSV",
                  "executeSQL", "putSQL", "putCache", "getCache",
                  "fetchDistributedMapCache", "putDistributedMapCache",
                  "detectDuplicate", "attributesToJSON", "splitJSON"],
    },
    "Control": {
        "icon": "🔀",
        "tasks": ["routeOnAttribute", "splitContent", "mergeContent", "duplicateContent",
                  "funnel", "executeFlow", "inputPort", "outputPort", "controlRate"],
    },
    "Messaging": {
        "icon": "📨",
        "tasks": ["publishKafka", "consumeKafka", "publishMQTT", "consumeMQTT",
                  "sendEmail", "notifySlack"],
    },
    "Synchronization": {
        "icon": "🔗",
        "tasks": ["waitForSignal", "notify"],
    },
    "Monitoring": {
        "icon": "📊",
        "tasks": ["reporting"],
    },
    "AI": {
        "icon": "🤖",
        "tasks": ["inferLLM"],
    },
}


# ============================================================================
# Sidebar: Task Palette + Flow metadata + Actions
# ============================================================================

def render_sidebar():
    """Sidebar avec palette de tasks, metadata et actions."""
    with st.sidebar:
        st.markdown(f"# ✏️ {t('editor.title')}")
        st.markdown("---")

        # --- Flow metadata ---
        with st.expander(f"📋 {t('editor.metadata')}", expanded=False):
            flow = st.session_state.current_flow
            flow["name"] = st.text_input(t("common.name"), value=flow.get("name", ""), key="meta_name")
            flow["version"] = st.text_input(t("common.version"), value=flow.get("version", "1.0.0"), key="meta_version")
            flow["description"] = st.text_area(t("common.description"), value=flow.get("description", ""), key="meta_desc", height=68)
            flow["author"] = st.text_input(t("editor.author"), value=flow.get("author", ""), key="meta_author")

        st.markdown("---")

        # --- Task palette ---
        st.markdown(f"### 🧩 {t('editor.task_palette')}")
        st.caption(t("editor.drag_from_canvas"))

        available_types = TaskFactory.list_types()

        # Task search filter
        task_search = st.text_input(
            t("common.search"), key="task_search",
            placeholder=t("common.search") + "...",
            label_visibility="collapsed",
        )
        task_search_lower = task_search.lower().strip() if task_search else ""

        _can_create = check_permission(session, "flow.create")

        for cat_name, cat_info in TASK_CATEGORIES.items():
            cat_label = t(CATEGORY_I18N_KEYS[cat_name]) if cat_name in CATEGORY_I18N_KEYS else cat_name
            # Filter tasks by search
            filtered_tasks = [tt for tt in cat_info["tasks"]
                              if tt in available_types and (not task_search_lower or task_search_lower in tt.lower())]
            if task_search_lower and not filtered_tasks:
                continue
            with st.expander(f"{cat_info['icon']} {cat_label}", expanded=bool(task_search_lower)):
                for task_type in filtered_tasks:
                    if _can_create:
                        if st.button(
                            f"➕ {task_type}",
                            key=f"add_{task_type}",
                            width="stretch",
                        ):
                            _add_task_to_flow(task_type)
                    else:
                        st.button(
                            f"➕ {task_type}",
                            key=f"add_{task_type}",
                            width="stretch",
                            disabled=True,
                        )

        # Plugin tasks (not in any built-in category)
        known_tasks = set()
        for cat_info in TASK_CATEGORIES.values():
            known_tasks.update(cat_info["tasks"])
        plugin_tasks = [tt for tt in available_types if tt not in known_tasks
                        and (not task_search_lower or task_search_lower in tt.lower())]
        if plugin_tasks:
            with st.expander(f"🔌 {t('task.categories.plugins')}", expanded=True):
                for task_type in plugin_tasks:
                    if _can_create:
                        if st.button(
                            f"➕ {task_type}",
                            key=f"add_{task_type}",
                            width="stretch",
                        ):
                            _add_task_to_flow(task_type)
                    else:
                        st.button(
                            f"➕ {task_type}",
                            key=f"add_{task_type}",
                            width="stretch",
                            disabled=True,
                        )

        st.markdown("---")

        # --- Flow Parameters ---
        _render_parameters_section()

        st.markdown("---")

        # --- Flow Variables ---
        _render_variables_section()

        st.markdown("---")

        # --- Flow Services ---
        _render_services_section()

        st.markdown("---")

        # --- Process Groups ---
        _render_groups_section()

        st.markdown("---")

        # --- Actions ---
        st.markdown(f"### 💾 {t('common.actions')}")

        col1, col2 = st.columns(2)
        with col1:
            if check_permission(session, "flow.edit"):
                if st.button(f"💾 {t('common.save')}", width="stretch"):
                    _save_flow()
            else:
                st.button(f"💾 {t('common.save')}", width="stretch", disabled=True,
                          help=t("auth.no_permission"))
        with col2:
            if check_permission(session, "flow.export"):
                if st.button(f"📤 {t('common.export')}", width="stretch"):
                    _export_flow()
            else:
                st.button(f"📤 {t('common.export')}", width="stretch", disabled=True,
                          help=t("auth.no_permission"))

        if st.button(f"✅ {t('editor.validate')}", width="stretch"):
            _validate_flow()

        if check_permission(session, "flow.create"):
            if st.button(f"🗑️ {t('editor.new_flow')}", width="stretch"):
                st.session_state.current_flow = None
                st.session_state.selected_node = None
                st.session_state.node_positions = {}
                st.rerun()
        else:
            st.button(f"🗑️ {t('editor.new_flow')}", width="stretch", disabled=True,
                      help=t("auth.no_permission"))

        st.markdown("---")

        # --- Templates ---
        st.markdown(f"### 📦 {t('editor.templates')}")
        template_service = TemplateService()

        # Search bar
        tpl_search = st.text_input(
            t("editor.search_templates"),
            key="tpl_search_query",
            placeholder=t("editor.search_templates"),
        )

        if tpl_search:
            templates = template_service.search_templates(tpl_search)
        else:
            templates = template_service.list_templates()

        # Category icons
        cat_icons = {
            "ETL": "🔄",
            "Monitoring": "📊",
            "Communication": "📨",
            "Data Processing": "🧮",
            "Integration": "🔗",
            "Custom": "📦",
        }

        # Difficulty badges
        diff_badges = {
            "beginner": "🟢",
            "intermediate": "🟡",
            "advanced": "🔴",
        }

        if templates:
            # Group by category
            categories = {}
            for tpl in templates:
                cat = tpl.get("category", "Custom")
                categories.setdefault(cat, []).append(tpl)

            for cat_name in sorted(categories.keys()):
                cat_templates = categories[cat_name]
                icon = cat_icons.get(cat_name, "📦")
                with st.expander(f"{icon} {cat_name} ({len(cat_templates)})", expanded=False):
                    for tpl in cat_templates:
                        diff_icon = diff_badges.get(tpl.get("difficulty", "intermediate"), "🟡")
                        tags_str = ", ".join(tpl.get("tags", [])[:3])

                        st.markdown(f"**{diff_icon} {tpl['name']}**")
                        if tpl.get("description"):
                            st.caption(tpl["description"])
                        if tags_str:
                            st.caption(f"Tags: {tags_str}")

                        col_preview, col_use = st.columns(2)
                        with col_preview:
                            if st.button(f"👁 {t('editor.preview')}", key=f"preview_{tpl['id']}", width="stretch"):
                                st.session_state._preview_template_id = tpl["id"]
                        with col_use:
                            if st.button(f"📥 {t('editor.use_template')}", key=f"use_{tpl['id']}", width="stretch"):
                                loaded = template_service.load_template(tpl["id"])
                                st.session_state.current_flow = loaded
                                st.session_state.selected_node = None
                                st.session_state.node_positions = {}
                                st.session_state.pop('_canvas_state', None)
                                st.session_state.pop('_canvas_fingerprint', None)
                                st.rerun()
        else:
            st.caption(t("editor.no_templates"))

        # Template preview
        preview_id = st.session_state.get("_preview_template_id")
        if preview_id:
            preview_tpl = template_service.get_template(preview_id)
            if preview_tpl:
                with st.expander(f"{t('editor.preview')}: {preview_tpl['name']}", expanded=True):
                    st.markdown(f"**{preview_tpl.get('description', '')}**")
                    st.caption(f"{t('editor.tpl_author')}: {preview_tpl.get('author', 'N/A')} | "
                               f"{t('editor.tpl_difficulty')}: {preview_tpl.get('difficulty', 'N/A')} | "
                               f"{t('editor.tpl_tasks_count')}: {len(preview_tpl.get('tasks', {}))}")
                    if preview_tpl.get("required_services"):
                        st.caption(f"{t('editor.tpl_required_services')}: {', '.join(preview_tpl['required_services'])}")
                    tasks_list = list(preview_tpl.get("tasks", {}).keys())
                    st.caption(f"{t('editor.tpl_tasks_count')}: {' -> '.join(tasks_list)}")
                    if st.button(t("common.close"), key="close_preview"):
                        st.session_state._preview_template_id = None
                        st.rerun()

        # Save as template
        with st.expander(t("editor.save_as_template"), expanded=False):
            tpl_name = st.text_input(t("common.name"), key="tpl_save_name")
            tpl_desc = st.text_input(t("common.description"), key="tpl_save_desc")
            tpl_category = st.selectbox(
                t("diff.category"),
                options=["Custom", "ETL", "Monitoring", "Communication", "Data Processing", "Integration"],
                key="tpl_save_category",
            )
            tpl_tags = st.text_input(t("editor.template_tags"), key="tpl_save_tags")
            tpl_difficulty = st.selectbox(
                t("editor.template_difficulty"),
                options=["beginner", "intermediate", "advanced"],
                index=1,
                key="tpl_save_difficulty",
            )
            if st.button(f"💾 {t('editor.save_as_template')}", key="save_template", width="stretch"):
                if tpl_name:
                    tags_list = [tag.strip() for tag in tpl_tags.split(",") if tag.strip()] if tpl_tags else []
                    path = template_service.save_as_template(
                        st.session_state.current_flow,
                        tpl_name,
                        tpl_desc,
                        category=tpl_category,
                        tags=tags_list,
                        difficulty=tpl_difficulty,
                    )
                    st.success(f"{t('common.success')}: {path}")
                else:
                    st.warning(t("common.warning") + ": name required")

        # --- Import JSON ---
        st.markdown("---")
        uploaded = st.file_uploader(
            f"📥 {t('editor.import_json')}", type=["json"], key="import_flow_json",
            label_visibility="collapsed",
        )
        if uploaded:
            try:
                import_data = json.loads(uploaded.read().decode("utf-8"))
                if "tasks" in import_data:
                    _push_undo()
                    st.session_state.current_flow = import_data
                    st.session_state.selected_node = None
                    st.session_state.node_positions = {}
                    st.session_state.pop('_canvas_state', None)
                    st.session_state.pop('_canvas_fingerprint', None)
                    st.success(f"{t('common.success')}: {import_data.get('name', 'Flow')}")
                    st.rerun()
                else:
                    st.error(t("runtime.invalid_json", error="invalid structure"))
            except Exception as e:
                st.error(f"{t('common.error')}: {e}")

        # --- Expression Tester ---
        with st.expander(f"🧪 {t('editor.expr_tester')}", expanded=False):
            from core.expression import resolve_expression
            flow = st.session_state.current_flow
            expr_input = st.text_input(
                t("editor.expr_input"),
                placeholder="${flow.parameters.key} or ${attr_name}",
                key="expr_test_input",
            )
            expr_attrs_text = st.text_input(
                t("editor.expr_attrs"),
                placeholder='{"filename": "data.csv"}',
                key="expr_test_attrs",
            )
            if expr_input:
                try:
                    attrs = json.loads(expr_attrs_text) if expr_attrs_text.strip() else {}
                except json.JSONDecodeError:
                    attrs = {}
                params = flow.get("parameters", {})
                result = resolve_expression(expr_input, attributes=attrs, parameters=params)
                if result != expr_input:
                    st.success(f"→ `{result}`")
                else:
                    st.warning(f"→ `{result}` ({t('editor.expr_unresolved')})")

        # --- Canvas options ---
        st.markdown("---")
        with st.expander(f"🖼️ {t('editor.canvas_options')}", expanded=False):
            col_opt1, col_opt2 = st.columns(2)
            with col_opt1:
                show_minimap = st.checkbox(
                    t("editor.show_minimap"),
                    value=st.session_state.get("canvas_show_minimap", False),
                    key="cb_minimap",
                )
                st.session_state.canvas_show_minimap = show_minimap
            with col_opt2:
                show_controls = st.checkbox(
                    t("editor.show_controls"),
                    value=st.session_state.get("canvas_show_controls", True),
                    key="cb_controls",
                )
                st.session_state.canvas_show_controls = show_controls

            # Auto-layout
            layout_options = {
                "manual": t("editor.layout_manual"),
                "layered": t("editor.layout_layered"),
                "hierarchical": t("editor.layout_hierarchical"),
                "compact": t("editor.layout_compact"),
                "pipeline": t("editor.layout_pipeline"),
                "tree": t("editor.layout_tree"),
                "force": t("editor.layout_force"),
            }
            selected_layout = st.selectbox(
                t("editor.layout"),
                options=list(layout_options.keys()),
                format_func=lambda x: layout_options[x],
                key="canvas_layout_select",
                index=0,
            )
            st.session_state.canvas_layout = selected_layout

            if st.button(f"📐 {t('runtime.auto_layout')}", key="editor_auto_layout"):
                st.session_state.node_positions = {}
                st.session_state.canvas_layout = "layered"
                st.session_state.pop("_canvas_state", None)
                st.session_state.pop("_canvas_fingerprint", None)
                st.rerun()

        # --- Color Legend ---
        st.markdown("---")
        with st.expander(f"🎨 {t('editor.color_legend')}", expanded=False):
            from gui.components.color_scheme import get_legend_data
            for item in get_legend_data():
                st.markdown(
                    f'<span style="display:inline-block;width:12px;height:12px;'
                    f'background:{item["color"]};border-radius:3px;margin-right:6px;'
                    f'vertical-align:middle;"></span>'
                    f'{item["icon"]} {item["category"]}',
                    unsafe_allow_html=True,
                )

        # --- Flow Tree ---
        st.markdown("---")
        with st.expander(f"🌳 {t('editor.flow_tree')}", expanded=False):
            from gui.components.flow_tree import render_flow_tree_from_dict
            render_flow_tree_from_dict(
                st.session_state.current_flow,
                selected_task=st.session_state.get("selected_node"),
                key_suffix="sidebar",
            )

        # --- Stats ---
        flow = st.session_state.current_flow
        n_tasks = len(flow.get("tasks", {}))
        n_rels = len(flow.get("relations", []))
        n_undo = len(st.session_state.get("_undo_stack", []))
        st.caption(f"{t('tree.tasks')}: {n_tasks} | {t('editor.connections')}: {n_rels} | {t('editor.undo')}: {n_undo}")

        # Quick download JSON
        flow_json = json.dumps(flow, indent=2, ensure_ascii=False)
        st.download_button(
            f"📥 {t('common.export')} JSON",
            data=flow_json,
            file_name=f"{flow.get('id', 'flow')}.json",
            mime="application/json",
            key="quick_download_json",
            width="stretch",
        )


def _render_parameters_section():
    """Section de gestion des parametres du flow dans la sidebar."""
    st.markdown(f"### 🔧 {t('editor.parameters')}")

    flow = st.session_state.current_flow
    parameters = flow.get("parameters", {})

    if parameters:
        for p_name, p_value in list(parameters.items()):
            col1, col2 = st.columns([3, 1])
            with col1:
                new_val = st.text_input(
                    p_name, value=str(p_value) if p_value else "",
                    key=f"fparam_{p_name}", label_visibility="collapsed",
                    placeholder=p_name,
                )
                flow["parameters"][p_name] = new_val
            with col2:
                if st.button("🗑️", key=f"del_param_{p_name}"):
                    del flow["parameters"][p_name]
                    st.rerun()
            st.caption(f"`${{flow.parameters.{p_name}}}`")
    else:
        st.caption(t("common.none"))

    with st.expander(f"➕ {t('editor.add_parameter')}"):
        new_name = st.text_input(t("common.name"), key="new_param_name")
        new_default = st.text_input(t("params.value"), key="new_param_default")
        if st.button(t("common.create"), key="add_param_btn"):
            if new_name:
                flow.setdefault("parameters", {})[new_name] = new_default
                st.rerun()
            else:
                st.warning(t("runtime.all_fields_required"))


def _render_variables_section():
    """Section de gestion des variables du flow dans la sidebar."""
    st.markdown(f"### 📝 {t('editor.add_variable')}")

    flow = st.session_state.current_flow
    variables = flow.get("variables", {})

    if variables:
        for var_name, var_value in list(variables.items()):
            col1, col2 = st.columns([3, 1])
            with col1:
                st.text(f"{var_name} = {var_value}")
            with col2:
                if st.button("🗑️", key=f"del_var_{var_name}"):
                    del flow["variables"][var_name]
                    st.rerun()
    else:
        st.caption(t("common.none"))

    with st.expander(f"➕ {t('editor.add_parameter')}"):
        new_name = st.text_input(t("common.name"), key="new_var_name")
        new_value = st.text_input(t("params.value"), key="new_var_value")
        if st.button(t("common.create"), key="add_var_btn"):
            if new_name:
                flow.setdefault("variables", {})[new_name] = new_value
                st.rerun()
            else:
                st.warning(t("runtime.all_fields_required"))


def _render_groups_section():
    """Process groups section in sidebar."""
    st.markdown(f"### 📦 {t('editor.groups')}")

    flow = st.session_state.current_flow
    groups = flow.get("groups", {})
    tasks = flow.get("tasks", {})

    GROUP_COLORS = ["#4285f4", "#ea4335", "#fbbc04", "#34a853", "#ff6d01", "#46bdc6", "#7b1fa2", "#c2185b"]

    if groups:
        for group_name, group_data in list(groups.items()):
            color = group_data.get("color", "#4285f4")
            task_list = group_data.get("tasks", [])
            with st.expander(f"🏷️ {group_name} ({len(task_list)})", expanded=False):
                # Color picker
                new_color = st.color_picker(
                    t("editor.group_color"), value=color,
                    key=f"grp_color_{group_name}",
                )
                if new_color != color:
                    group_data["color"] = new_color

                # Show tasks in group
                if task_list:
                    for tid in list(task_list):
                        col1, col2 = st.columns([3, 1])
                        with col1:
                            task_data = tasks.get(tid, {})
                            st.caption(f"⚙️ {task_data.get('name', tid)} ({task_data.get('type', '?')})")
                        with col2:
                            if st.button("✕", key=f"grp_rm_{group_name}_{tid}"):
                                task_list.remove(tid)
                                st.rerun()
                else:
                    st.caption(t("common.none"))

                # Add task to group
                ungrouped = [tid for tid in tasks if tid not in task_list]
                if ungrouped:
                    selected_tid = st.selectbox(
                        t("editor.assign_to_group"),
                        options=ungrouped,
                        key=f"grp_add_task_{group_name}",
                        label_visibility="collapsed",
                    )
                    if st.button(f"➕ {t('editor.assign_to_group')}", key=f"grp_add_btn_{group_name}"):
                        task_list.append(selected_tid)
                        st.rerun()

                # Delete group
                if st.button(f"🗑️ {t('common.delete')}", key=f"grp_del_{group_name}"):
                    del groups[group_name]
                    st.rerun()
    else:
        st.caption(t("editor.no_groups"))

    # Add new group
    with st.expander(f"➕ {t('editor.add_group')}"):
        new_name = st.text_input(t("editor.group_name"), key="new_group_name")
        new_color = st.color_picker(t("editor.group_color"), value=GROUP_COLORS[len(groups) % len(GROUP_COLORS)],
                                     key="new_group_color")
        if st.button(t("common.create"), key="add_group_btn"):
            if new_name:
                flow.setdefault("groups", {})[new_name] = {
                    "color": new_color,
                    "tasks": [],
                }
                st.toast(t("editor.group_created", name=new_name))
                st.rerun()
            else:
                st.warning(t("runtime.all_fields_required"))


def _get_service_schema(svc_type: str) -> Dict[str, Any]:
    """Get parameter schema for a service type, or empty dict if unavailable."""
    from core import ServiceFactory
    try:
        svc_class = ServiceFactory.get(svc_type)
        instance = object.__new__(svc_class)
        instance.config = {}
        return instance.get_parameter_schema()
    except Exception:
        return {}


def _render_services_section():
    """Section de gestion des services du flow dans la sidebar."""
    st.markdown(f"### 🔌 {t('settings.services')}")

    flow = st.session_state.current_flow
    services = flow.get("services", {})

    from core import ServiceFactory

    if services:
        for svc_id, svc_config in list(services.items()):
            svc_type = svc_config.get("type", "unknown")
            with st.expander(f"🔌 {svc_id} ({svc_type})"):
                config = svc_config.get("config", {})

                # Try to get schema from the service class
                schema = _get_service_schema(svc_type)

                if schema:
                    edited = render_schema_fields(schema, config, key_prefix=f"svc_{svc_id}")
                    svc_config["config"] = edited
                else:
                    # Fallback: raw JSON for unknown service types
                    import json as _json
                    updated_json = st.text_area(
                        t("editor.config_json"),
                        value=_json.dumps(config, indent=2, ensure_ascii=False),
                        height=120,
                        key=f"svc_cfg_{svc_id}",
                    )
                    try:
                        svc_config["config"] = _json.loads(updated_json)
                    except _json.JSONDecodeError:
                        pass

                if st.button(f"🗑️ {t('common.delete')}", key=f"del_svc_{svc_id}", width="stretch"):
                    del flow["services"][svc_id]
                    st.rerun()
    else:
        st.caption(t("common.none"))

    with st.expander(f"➕ {t('common.create')}"):
        available_types = ServiceFactory.list_types()

        new_svc_id = st.text_input(t("common.id"), key="new_svc_id",
                                    placeholder="ex: http_listener")
        if available_types:
            new_svc_type = st.selectbox(t("common.type"), available_types, key="new_svc_type")
        else:
            new_svc_type = st.text_input(t("common.type"), key="new_svc_type_input")

        # Schema-based config for the selected type
        new_svc_schema = _get_service_schema(new_svc_type if available_types else "")
        if new_svc_schema:
            new_svc_config = render_schema_fields(new_svc_schema, {}, key_prefix="new_svc")
        else:
            svc_config_json = st.text_area(t("editor.config_json"), key="new_svc_config",
                                            value="{}", height=80)
            new_svc_config = None

        if st.button(t("common.create"), key="add_svc_btn"):
            if new_svc_id and new_svc_type:
                if new_svc_config is None:
                    try:
                        import json
                        new_svc_config = json.loads(svc_config_json)
                    except json.JSONDecodeError:
                        st.error(t("runtime.invalid_json", error=""))
                        return
                flow.setdefault("services", {})[new_svc_id] = {
                    "type": new_svc_type,
                    "config": new_svc_config,
                }
                st.rerun()
            else:
                st.warning(t("runtime.all_fields_required"))


def _add_task_to_flow(task_type: str):
    """Ajouter une task depuis la palette."""
    _push_undo()
    flow = st.session_state.current_flow
    n = len(flow.get("tasks", {})) + 1
    task_id = f"{task_type}_{n}"

    # Eviter les doublons d'ID
    while task_id in flow.get("tasks", {}):
        n += 1
        task_id = f"{task_type}_{n}"

    canvas = FlowCanvas()
    canvas.add_task(flow, task_id, task_type)
    st.session_state.selected_node = task_id
    st.rerun()


def _validate_flow():
    """Valider le flux avec FlowValidator."""
    from engine.validator import FlowValidator
    flow = st.session_state.current_flow
    if not flow:
        st.sidebar.warning(t("common.none"))
        return
    validator = FlowValidator()
    result = validator.validate(flow)
    if result.valid:
        st.sidebar.success(f"✅ {t('editor.validation_ok')} ({len(result.warnings)} {t('common.warning')}(s))")
    else:
        st.sidebar.error(f"❌ {t('editor.validation_errors').replace('{count}', str(len(result.errors)))}")
    for err in result.errors:
        st.sidebar.error(f"• {err}")
    for warn in result.warnings:
        st.sidebar.warning(f"• {warn}")


def _flow_content_fingerprint(flow_dict: dict) -> str:
    """Generate a fingerprint of flow content (ignoring version/metadata)."""
    import copy
    fp = copy.deepcopy(flow_dict)
    # Remove fields that change without structural change
    fp.pop("version", None)
    return json.dumps(fp, sort_keys=True, ensure_ascii=False)


def _save_flow():
    """Sauvegarder le flux. N'incrémente la version que si le contenu a changé."""
    try:
        flow = st.session_state.current_flow
        import os
        os.makedirs("flows", exist_ok=True)

        filepath = f"flows/{flow['id']}.json"

        # Compare with disk version to detect actual changes
        has_changed = True
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    disk_flow = json.load(f)
                has_changed = (
                    _flow_content_fingerprint(flow)
                    != _flow_content_fingerprint(disk_flow)
                )
            except Exception:
                has_changed = True  # Can't read disk → treat as changed

        if has_changed:
            # Auto-increment patch version
            version = flow.get("version", "1.0.0")
            try:
                parts = version.split(".")
                parts[-1] = str(int(parts[-1]) + 1)
                flow["version"] = ".".join(parts)
            except (ValueError, IndexError):
                pass

            # Archive previous version before overwriting
            if os.path.exists(filepath):
                versions_dir = f"flows/versions/{flow['id']}"
                os.makedirs(versions_dir, exist_ok=True)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        old_flow = json.load(f)
                    old_version = old_flow.get("version", "unknown")
                    archive_path = f"{versions_dir}/v{old_version}.json"
                    with open(archive_path, "w", encoding="utf-8") as f:
                        json.dump(old_flow, f, indent=2, ensure_ascii=False)
                except Exception:
                    pass  # Best-effort archiving

        # Save current version
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(flow, f, indent=2, ensure_ascii=False)

        if has_changed:
            st.sidebar.success(f"{t('common.success')} v{flow['version']}: {filepath}")
        else:
            st.sidebar.info(t("editor.no_changes"))
    except Exception as e:
        st.sidebar.error(f"{t('common.error')}: {e}")


def _export_flow():
    """Exporter le flux en JSON."""
    flow = st.session_state.current_flow
    json_str = json.dumps(flow, indent=2, ensure_ascii=False)
    st.sidebar.download_button(
        t("runtime.download_results"),
        data=json_str,
        file_name=f"{flow['id']}.json",
        mime="application/json",
    )


# ============================================================================
# Undo / Redo
# ============================================================================

def _push_undo():
    """Push current flow state to undo stack."""
    import copy
    flow = st.session_state.current_flow
    if flow is None:
        return
    if "_undo_stack" not in st.session_state:
        st.session_state._undo_stack = []
    if "_redo_stack" not in st.session_state:
        st.session_state._redo_stack = []

    snapshot = json.dumps(flow, ensure_ascii=False)
    # Avoid duplicate consecutive snapshots
    if st.session_state._undo_stack and st.session_state._undo_stack[-1] == snapshot:
        return
    st.session_state._undo_stack.append(snapshot)
    # Cap at 50 entries
    if len(st.session_state._undo_stack) > 50:
        st.session_state._undo_stack = st.session_state._undo_stack[-50:]
    # Clear redo on new action
    st.session_state._redo_stack = []


def _undo():
    """Restore previous flow state."""
    stack = st.session_state.get("_undo_stack", [])
    if not stack:
        st.toast(t("editor.no_undo"))
        return
    # Save current to redo
    if "_redo_stack" not in st.session_state:
        st.session_state._redo_stack = []
    current = json.dumps(st.session_state.current_flow, ensure_ascii=False)
    st.session_state._redo_stack.append(current)
    # Pop and restore
    prev = stack.pop()
    st.session_state.current_flow = json.loads(prev)
    st.session_state.pop('_canvas_state', None)
    st.session_state.pop('_canvas_fingerprint', None)
    st.rerun()


def _redo():
    """Restore next flow state from redo stack."""
    stack = st.session_state.get("_redo_stack", [])
    if not stack:
        st.toast(t("editor.no_redo"))
        return
    # Save current to undo
    if "_undo_stack" not in st.session_state:
        st.session_state._undo_stack = []
    current = json.dumps(st.session_state.current_flow, ensure_ascii=False)
    st.session_state._undo_stack.append(current)
    # Pop and restore
    nxt = stack.pop()
    st.session_state.current_flow = json.loads(nxt)
    st.session_state.pop('_canvas_state', None)
    st.session_state.pop('_canvas_fingerprint', None)
    st.rerun()


def _duplicate_task(flow: dict, task_id: str):
    """Duplicate a task with a new unique ID."""
    _push_undo()
    tasks = flow.get("tasks", {})
    if task_id not in tasks:
        return
    import copy
    config = copy.deepcopy(tasks[task_id])
    task_type = config.get("type", "task")
    n = len(tasks) + 1
    new_id = f"{task_type}_{n}"
    while new_id in tasks:
        n += 1
        new_id = f"{task_type}_{n}"
    tasks[new_id] = config
    # Position offset
    old_pos = st.session_state.node_positions.get(task_id, (200, 200))
    st.session_state.node_positions[new_id] = (old_pos[0] + 50, old_pos[1] + 50)
    st.session_state.pop('_canvas_state', None)
    st.session_state.pop('_canvas_fingerprint', None)
    st.session_state.selected_node = new_id
    st.rerun()


# ============================================================================
# Flow Diff viewer
# ============================================================================

def _render_flow_diff():
    """Render a flow diff viewer comparing current flow with saved version."""
    flow = st.session_state.current_flow
    if not flow:
        return

    flow_id = flow.get("id", "")
    filepath = f"flows/{flow_id}.json"

    import os
    if not os.path.exists(filepath):
        st.caption(t("diff.no_saved_version"))
        return

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            saved_flow = json.load(f)
    except Exception:
        st.warning(t("editor.read_error"))
        return

    diff = FlowDiff.compare(saved_flow, flow)

    if not diff.has_changes:
        st.success(f"✅ {t('diff.no_changes')}")
        return

    summary = diff.summary
    sc1, sc2, sc3 = st.columns(3)
    with sc1:
        st.metric(f"🟢 {t('diff.added')}", summary.get("added", 0))
    with sc2:
        st.metric(f"🔴 {t('diff.removed')}", summary.get("removed", 0))
    with sc3:
        st.metric(f"🟡 {t('diff.modified')}", summary.get("modified", 0))

    # Category icons
    cat_icons = {"task": "⚙️", "relation": "🔗", "parameter": "🔧", "metadata": "📋"}
    change_colors = {"added": "🟢", "removed": "🔴", "modified": "🟡"}

    for entry in diff.entries:
        icon = cat_icons.get(entry.category, "📄")
        change_icon = change_colors.get(entry.change_type, "❓")
        with st.container():
            st.markdown(f"{change_icon} {icon} **{entry.description}**")
            if entry.change_type == "modified" and entry.old_value is not None:
                col_old, col_new = st.columns(2)
                with col_old:
                    st.caption(f"{t('diff.old_value')}:")
                    st.code(str(entry.old_value), language=None)
                with col_new:
                    st.caption(f"{t('diff.new_value')}:")
                    st.code(str(entry.new_value), language=None)
            elif entry.change_type == "added" and entry.new_value is not None:
                st.caption(str(entry.new_value))


# ============================================================================
# Subflow parameter mapping
# ============================================================================

def _render_subflow_mapping(flow: Dict[str, Any], task_id: str, task_config: Dict[str, Any]):
    """Render subflow parameter mapping UI for ExecuteFlowTask."""
    import os

    flow_path = task_config.get("parameters", {}).get("flow_path", "")
    if not flow_path or not os.path.exists(flow_path):
        return

    # Load the subflow to get its parameters
    try:
        with open(flow_path, "r", encoding="utf-8") as f:
            subflow_data = json.load(f)
    except Exception:
        return

    subflow_params = subflow_data.get("parameters", {})
    if not subflow_params:
        return

    st.markdown("---")
    st.markdown(f"##### 🔀 {t('editor.subflow_params')}")
    st.caption(f"Subflow: **{subflow_data.get('name', flow_path)}**")

    # Get parent flow parameters for the dropdown
    parent_params = flow.get("parameters", {})
    parent_param_names = list(parent_params.keys())

    # Source options: parent params + literal + expression
    source_options = ["(defaut subflow)"] + [
        f"${{flow.parameters.{p}}}" for p in parent_param_names
    ] + ["(expression personnalisee)"]

    # Current mapping
    current_mapping = task_config.get("parameters", {}).get("parameter_mapping", {})
    if not isinstance(current_mapping, dict):
        current_mapping = {}

    new_mapping = {}
    all_mapped = True

    for sub_param, sub_default in subflow_params.items():
        current_value = current_mapping.get(sub_param, "")

        # Determine current selection index
        if not current_value:
            idx = 0  # default
        elif current_value in source_options:
            idx = source_options.index(current_value)
        else:
            idx = len(source_options) - 1  # custom expression

        col1, col2 = st.columns([1, 2])
        with col1:
            # Show param name with validation indicator
            if current_value:
                st.markdown(f"✅ **{sub_param}**")
            else:
                st.markdown(f"⚪ **{sub_param}**")
                all_mapped = False
            if sub_default:
                st.caption(f"Defaut: `{sub_default}`")

        with col2:
            choice = st.selectbox(
                f"Source pour {sub_param}",
                options=source_options,
                index=idx,
                key=f"submap_{task_id}_{sub_param}",
                label_visibility="collapsed",
            )

            if choice == "(defaut subflow)":
                pass  # No mapping needed
            elif choice == "(expression personnalisee)":
                expr = st.text_input(
                    "Expression",
                    value=current_value if idx == len(source_options) - 1 else "",
                    key=f"subexpr_{task_id}_{sub_param}",
                    placeholder="${flow.parameters.xxx}",
                )
                if expr:
                    new_mapping[sub_param] = expr
            else:
                new_mapping[sub_param] = choice

    # Save the mapping back
    task_config.setdefault("parameters", {})["parameter_mapping"] = new_mapping

    if not all_mapped and parent_param_names:
        st.caption("⚪ = utilise la valeur par defaut du subflow")


def _render_subflow_ports(flow: Dict[str, Any], task_id: str, task_config: Dict[str, Any]):
    """Render subflow InputPort/OutputPort mapping for an executeFlow task."""
    import os

    flow_path = task_config.get("parameters", {}).get("flow_path", "")
    if not flow_path:
        return

    # Try to load subflow
    full_path = flow_path
    if not os.path.isabs(flow_path):
        full_path = os.path.join("flows", flow_path) if not flow_path.startswith("flows") else flow_path

    if not os.path.exists(full_path):
        return

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            subflow = json.load(f)
    except (json.JSONDecodeError, OSError):
        return

    # Find input and output ports in subflow
    input_ports: List[Dict[str, str]] = []
    output_ports: List[Dict[str, str]] = []
    for tid, tconf in subflow.get("tasks", {}).items():
        ttype = tconf.get("type", "")
        if ttype == "inputPort":
            port_name = tconf.get("parameters", {}).get("port_name", tid)
            input_ports.append({"id": tid, "name": port_name})
        elif ttype == "outputPort":
            port_name = tconf.get("parameters", {}).get("port_name", tid)
            output_ports.append({"id": tid, "name": port_name})

    if not input_ports and not output_ports:
        return

    st.markdown("---")
    st.markdown(f"##### 🔌 {t('editor.subflow_ports')}")

    # Get current port mapping from task parameters
    params = task_config.get("parameters", {})
    port_mapping = params.get("port_mapping", {"input": {}, "output": {}})
    if not isinstance(port_mapping, dict):
        port_mapping = {"input": {}, "output": {}}
    port_mapping.setdefault("input", {})
    port_mapping.setdefault("output", {})
    changed = False

    # Input ports
    if input_ports:
        st.markdown(f"*{t('editor.subflow_inputs')}*:")
        if len(input_ports) == 1:
            st.caption(f"↳ `{input_ports[0]['name']}` ({input_ports[0]['id']}) — receives all incoming FlowFiles")
            if port_mapping["input"].get("port_task_id") != input_ports[0]["id"]:
                port_mapping["input"]["port_task_id"] = input_ports[0]["id"]
                changed = True
        else:
            # Multiple inputs: let user choose default
            port_ids = [p["id"] for p in input_ports]
            port_labels = [f"{p['name']} ({p['id']})" for p in input_ports]
            current = port_mapping["input"].get("port_task_id", port_ids[0])
            idx = port_ids.index(current) if current in port_ids else 0
            selected = st.selectbox(
                t("editor.subflow_default_input"),
                port_labels,
                index=idx,
                key=f"port_input_{task_id}",
            )
            selected_id = port_ids[port_labels.index(selected)]
            if selected_id != current:
                port_mapping["input"]["port_task_id"] = selected_id
                changed = True

    # Output ports
    if output_ports:
        st.markdown(f"*{t('editor.subflow_outputs')}*:")
        for port in output_ports:
            current_rel = port_mapping["output"].get(port["id"], "success")
            new_rel = st.text_input(
                t("editor.subflow_relationship", name=port['name'], id=port['id']),
                value=current_rel,
                key=f"port_out_{task_id}_{port['id']}",
            )
            if new_rel != current_rel:
                port_mapping["output"][port["id"]] = new_rel
                changed = True

    if changed:
        task_config.setdefault("parameters", {})["port_mapping"] = port_mapping
        st.rerun()


# ============================================================================
# Main area: Canvas + Config panel
# ============================================================================

def render_main():
    """Zone principale avec canvas et panneau de config."""
    flow = st.session_state.current_flow

    # Header
    st.markdown(f"### 🔧 {flow.get('name', t('editor.new_flow'))}")

    if not flow.get("tasks"):
        st.info(t("editor.empty_canvas_hint"))
        _render_empty_canvas()
        return

    # Layout: canvas (large) + config panel (right)
    if st.session_state.selected_node and st.session_state.selected_node in flow.get("tasks", {}):
        col_canvas, col_config = st.columns([3, 1])
    else:
        col_canvas, col_config = st.columns([4, 1])

    # --- Canvas ---
    with col_canvas:
        _render_canvas(flow)

    # --- Config panel ---
    with col_config:
        _render_config_panel(flow)


def _render_empty_canvas():
    """Canvas vide avec message."""
    canvas = FlowCanvas()
    canvas.render(st.session_state.current_flow, height=500)


def _render_canvas(flow: Dict[str, Any]):
    """Rendre le canvas interactif."""
    # Toolbar au-dessus du canvas
    toolbar_cols = st.columns([1, 1, 1, 1, 1, 1, 1, 2])

    with toolbar_cols[0]:
        connect_mode = st.toggle(f"🔗 {t('editor.connections')}", value=st.session_state.connect_mode, key="toggle_connect")
        st.session_state.connect_mode = connect_mode

    with toolbar_cols[1]:
        if check_permission(session, "flow.edit"):
            if st.button(f"🗑️ {t('common.delete')}", key="delete_selected"):
                _push_undo()
                _delete_selected(flow)
        else:
            st.button(f"🗑️ {t('common.delete')}", key="delete_selected", disabled=True)

    with toolbar_cols[2]:
        if st.button(f"📋 {t('editor.json_preview')}", key="toggle_json"):
            st.session_state._show_json = not st.session_state.get("_show_json", False)

    with toolbar_cols[3]:
        undo_count = len(st.session_state.get("_undo_stack", []))
        if st.button(f"↩️ {t('editor.undo')}", key="undo_btn", disabled=(undo_count == 0)):
            _undo()

    with toolbar_cols[4]:
        redo_count = len(st.session_state.get("_redo_stack", []))
        if st.button(f"↪️ {t('editor.redo')}", key="redo_btn", disabled=(redo_count == 0)):
            _redo()

    with toolbar_cols[5]:
        selected_node = st.session_state.get("selected_node")
        can_dup = selected_node and selected_node in flow.get("tasks", {})
        if st.button(f"📋 {t('editor.duplicate')}", key="dup_btn", disabled=not can_dup):
            if can_dup:
                _duplicate_task(flow, selected_node)

    with toolbar_cols[6]:
        if st.button(f"📊 {t('diff.title')}", key="toggle_diff"):
            st.session_state._show_diff = not st.session_state.get("_show_diff", False)

    # Canvas with in-canvas drag palette (NiFi-style)
    canvas = FlowCanvas()
    available_types = set(TaskFactory.list_types())
    selected = canvas.render(
        flow, height=500,
        enable_drag_palette=True,
        available_task_types=available_types,
    )

    # Handle selection
    if selected:
        if selected in flow.get("tasks", {}):
            # Node clicked
            if st.session_state.connect_mode:
                _handle_connection(flow, selected)
            else:
                st.session_state.selected_node = selected
                st.session_state.selected_edge = None
        else:
            # Edge clicked
            st.session_state.selected_edge = selected
            st.session_state.selected_node = None

    # Inline auto-validation (if auto_validate enabled in settings)
    if st.session_state.get("settings", {}).get("auto_validate", True):
        try:
            from engine.validator import FlowValidator
            validator = FlowValidator()
            result = validator.validate(flow)
            if not result.valid:
                with st.container():
                    for err in result.errors[:5]:
                        st.error(f"• {err}", icon="❌")
            elif result.warnings:
                for warn in result.warnings[:3]:
                    st.warning(f"• {warn}", icon="⚠️")
        except Exception:
            pass

    # JSON preview
    if st.session_state.get("_show_json", False):
        with st.expander(f"📄 {t('editor.json_preview')}", expanded=True):
            st.code(json.dumps(flow, indent=2, ensure_ascii=False), language="json")

    # Flow diff
    if st.session_state.get("_show_diff", False):
        with st.expander(f"📊 {t('diff.title')}", expanded=True):
            _render_flow_diff()


def _handle_connection(flow: Dict[str, Any], clicked_node: str):
    """Gerer le mode connexion."""
    if st.session_state.connect_from is None:
        st.session_state.connect_from = clicked_node
        st.toast(f"Source: {clicked_node} → ?")
    else:
        from_id = st.session_state.connect_from
        to_id = clicked_node

        if from_id != to_id:
            _push_undo()
            canvas = FlowCanvas()
            canvas.add_connection(flow, from_id, to_id)
            st.toast(f"🔗 {from_id} → {to_id}")

        st.session_state.connect_from = None
        st.session_state.connect_mode = False
        st.rerun()


def _delete_selected(flow: Dict[str, Any]):
    """Supprimer l'element selectionne."""
    canvas = FlowCanvas()

    if st.session_state.selected_node:
        canvas.remove_task(flow, st.session_state.selected_node)
        st.session_state.selected_node = None
        st.rerun()
    elif st.session_state.selected_edge:
        canvas.remove_connection(flow, st.session_state.selected_edge)
        st.session_state.selected_edge = None
        st.rerun()


def _render_config_panel(flow: Dict[str, Any]):
    """Panneau de configuration de la task selectionnee."""
    selected = st.session_state.selected_node

    if not selected or selected not in flow.get("tasks", {}):
        st.markdown(f"#### 📌 {t('editor.selection')}")
        st.caption(t("editor.click_to_configure"))

        # Liste des connexions avec type editable
        if flow.get("relations"):
            st.markdown("---")
            st.markdown(f"#### 🔗 {t('editor.connections')}")
            rel_types = ["success", "failure", "retry", "original", "matched", "unmatched"]
            for i, rel in enumerate(flow["relations"]):
                col1, col2, col3 = st.columns([2, 2, 1])
                with col1:
                    st.caption(f"{rel['from']} → {rel['to']}")
                with col2:
                    cur_type = rel.get("type", "success")
                    idx = rel_types.index(cur_type) if cur_type in rel_types else 0
                    new_type = st.selectbox(
                        t("common.type"), rel_types, index=idx,
                        key=f"rel_type_{i}", label_visibility="collapsed",
                    )
                    if new_type != cur_type:
                        rel["type"] = new_type
                with col3:
                    if st.button("✕", key=f"del_rel_{i}"):
                        flow["relations"].pop(i)
                        st.rerun()
        return

    task_config = flow["tasks"][selected]
    task_type = task_config.get("type", "unknown")

    st.markdown(f"#### ⚙️ {selected}")
    st.caption(f"{t('common.type')}: **{task_type}**")

    # Task comment/annotation
    annotations = flow.setdefault("annotations", {})
    current_note = annotations.get(selected, "")
    new_note = st.text_input(
        f"💬 {t('editor.annotation')}",
        value=current_note,
        key=f"note_{selected}",
        placeholder=t("editor.annotation_placeholder"),
    )
    if new_note != current_note:
        if new_note:
            annotations[selected] = new_note
        elif selected in annotations:
            del annotations[selected]

    # Group assignment
    groups = flow.get("groups", {})
    if groups:
        current_group = None
        for gname, gdata in groups.items():
            if selected in gdata.get("tasks", []):
                current_group = gname
                break
        group_options = [t("common.none")] + list(groups.keys())
        current_idx = group_options.index(current_group) if current_group in group_options else 0
        new_group = st.selectbox(
            f"🏷️ {t('editor.assign_to_group')}",
            options=group_options,
            index=current_idx,
            key=f"task_group_{selected}",
        )
        if new_group != (current_group or t("common.none")):
            # Remove from old group
            if current_group and current_group in groups:
                groups[current_group]["tasks"] = [
                    t for t in groups[current_group]["tasks"] if t != selected
                ]
            # Add to new group
            if new_group != t("common.none") and new_group in groups:
                groups[new_group].setdefault("tasks", []).append(selected)
            st.rerun()

    # Bouton supprimer
    if check_permission(session, "flow.edit"):
        if st.button(f"🗑️ {t('editor.delete_task')}", key="del_task_panel", width="stretch"):
            _push_undo()
            canvas = FlowCanvas()
            canvas.remove_task(flow, selected)
            st.session_state.selected_node = None
            st.rerun()
    else:
        st.button(f"🗑️ {t('editor.delete_task')}", key="del_task_panel", width="stretch",
                  disabled=True, help=t("auth.no_permission"))

    st.markdown("---")

    # Schema-based parameter editing
    try:
        task_class = TaskFactory.get(task_type)
        try:
            instance = object.__new__(task_class)
            instance.config = {}
            instance._original_config = {}
            schema = instance.get_parameter_schema()
        except Exception:
            schema = {}
    except Exception:
        schema = {}

    if not schema:
        st.caption(t("common.none"))
        return

    params = task_config.get("parameters", {})

    # Filter out keys handled by dedicated UI (executeFlow subflow mapping)
    render_schema = schema
    if task_type == "executeFlow":
        render_schema = {k: v for k, v in schema.items()
                         if k not in ("parameter_mapping", "port_mapping")}

    edited = render_schema_fields(render_schema, params, key_prefix=f"cfg_{selected}")
    params.update(edited)
    task_config["parameters"] = params

    # Subflow parameter mapping (only for executeFlow tasks)
    if task_type == "executeFlow":
        _render_subflow_mapping(flow, selected, task_config)
        _render_subflow_ports(flow, selected, task_config)

    # Connexions de cette task
    st.markdown("---")
    st.markdown(f"##### 🔗 {t('editor.connections')}")

    # Sortantes
    outgoing = [r for r in flow.get("relations", []) if r["from"] == selected]
    incoming = [r for r in flow.get("relations", []) if r["to"] == selected]

    if incoming:
        for r in incoming:
            st.caption(f"⬅️ {r['from']}")
    if outgoing:
        for r in outgoing:
            st.caption(f"➡️ {r['to']}")

    if not incoming and not outgoing:
        st.caption(t("common.none"))


# ============================================================================
# Main
# ============================================================================

def main():
    initialize_state()
    render_sidebar()
    render_main()


if __name__ == "__main__":
    main()
