#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Documentation page — interactive reference for PyFi2."""

import streamlit as st

st.set_page_config(
    page_title="Documentation - PyFi2",
    page_icon="📚",
    layout="wide",
)

from gui.i18n import init as i18n_init, t
i18n_init(st.session_state.get("locale", "en"))
from gui.components.theme import inject_theme
inject_theme()

from gui.utils.auth import require_auth, render_user_info
session = require_auth()
render_user_info()

from core import TaskFactory, ServiceFactory
from tasks import register_all_tasks
register_all_tasks()


# ============================================================================
# Page content
# ============================================================================

st.markdown(f"# 📚 {t('doc.title')}")

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
    f"🚀 {t('doc.getting_started')}",
    f"📦 {t('doc.task_reference')}",
    f"🔧 {t('doc.expressions')}",
    f"📄 {t('doc.flowfile_doc')}",
    f"🔌 {t('doc.services_doc')}",
    f"🌐 {t('doc.api_doc')}",
    f"⌨️ {t('doc.cli_doc')}",
    f"⌨️ {t('doc.shortcuts_title')}",
])


# --- Getting Started ---
with tab1:
    st.markdown(f"## {t('doc.getting_started')}")
    st.markdown(f"""
1. {t('doc.getting_started_1')}
2. {t('doc.getting_started_2')}
3. {t('doc.getting_started_3')}
4. {t('doc.getting_started_4')}
5. {t('doc.getting_started_5')}
""")

    st.markdown("---")
    st.markdown(f"## {t('doc.architecture')}")
    st.markdown(t('doc.architecture_desc'))
    st.markdown(f"""
- {t('doc.architecture_core')}
- {t('doc.architecture_engine')}
- {t('doc.architecture_tasks')}
- {t('doc.architecture_storage')}
""")

    st.markdown("---")
    st.markdown(f"## {t('doc.relations_doc')}")
    st.markdown(t('doc.relations_desc'))

    col1, col2, col3 = st.columns(3)
    with col1:
        with st.container(border=True):
            st.markdown("**🟢 success**")
            st.caption(t("doc.relation_success_desc"))
    with col2:
        with st.container(border=True):
            st.markdown("**🔴 failure**")
            st.caption(t("doc.relation_failure_desc"))
    with col3:
        with st.container(border=True):
            st.markdown("**🟡 both**")
            st.caption(t("doc.relation_both_desc"))

    st.markdown("---")
    st.markdown(f"## {t('doc.plugins_doc')}")
    st.markdown(t('doc.plugins_desc'))
    st.code("python cli.py plugins install my_plugin.pfp\npython cli.py plugins list", language="bash")


# --- Task Reference ---
with tab2:
    st.markdown(f"## {t('doc.task_reference')}")
    st.caption(t('doc.task_reference_desc'))

    task_search = st.text_input(
        t("common.search"), key="doc_task_search",
        placeholder=t("common.search") + "...",
        label_visibility="collapsed",
    )
    search_lower = task_search.lower().strip() if task_search else ""

    available_types = sorted(TaskFactory.list_types())

    for task_type in available_types:
        if search_lower and search_lower not in task_type.lower():
            continue

        try:
            task_class = TaskFactory.get(task_type)
            instance = object.__new__(task_class)
            instance.config = {}
            schema = instance.get_config_schema() if hasattr(instance, "get_config_schema") else {}
        except Exception:
            schema = {}

        with st.expander(f"**{task_type}**", expanded=bool(search_lower)):
            # Description
            doc = task_class.__doc__
            if doc:
                st.markdown(doc.strip().split("\n")[0])

            if not schema:
                st.caption(t("doc.no_params"))
            else:
                for param_name, param_info in schema.items():
                    if isinstance(param_info, dict):
                        ptype = param_info.get("type", "string")
                        default = param_info.get("default", "")
                        required = param_info.get("required", False)
                        description = param_info.get("description", "")

                        badge = f"🔴 {t('doc.required')}" if required else f"🟢 {t('doc.optional')}"
                        st.markdown(f"- `{param_name}` ({ptype}) — {badge}")
                        if description:
                            st.caption(f"  {description}")
                        if default not in (None, "", []):
                            st.caption(f"  Default: `{default}`")
                    else:
                        st.markdown(f"- `{param_name}`: `{param_info}`")


# --- Expressions ---
with tab3:
    st.markdown(f"## {t('doc.expressions')}")
    st.markdown(t('doc.expressions_desc'))

    st.markdown("---")
    st.markdown(t('doc.expressions_syntax'))
    st.markdown(t('doc.expressions_example'))

    st.markdown("---")
    st.markdown(f"### {t('doc.syntax_reference')}")

    col1, col2 = st.columns(2)
    with col1:
        with st.container(border=True):
            st.markdown(f"**{t('doc.attribute_expressions')}**")
            st.code("${filename}\n${uuid}\n${mime.type}", language="text")

        with st.container(border=True):
            st.markdown(f"**{t('doc.flow_parameters')}**")
            st.code("${flow.parameters.input_dir}\n${flow.parameters.batch_size}", language="text")

    with col2:
        with st.container(border=True):
            st.markdown(f"**{t('doc.environment_variables')}**")
            st.code("${env.HOME}\n${env.DATABASE_URL}", language="text")

        with st.container(border=True):
            st.markdown(f"**{t('doc.nested_combined')}**")
            st.code("${flow.parameters.prefix}_${filename}\noutput/${path}/${filename}", language="text")

    st.markdown("---")
    st.markdown(f"### {t('doc.try_it')}")
    st.caption(t("doc.try_it_hint"))


# --- FlowFile ---
with tab4:
    st.markdown(f"## {t('doc.flowfile_doc')}")
    st.markdown(t('doc.flowfile_desc'))

    st.markdown("---")
    st.markdown(f"### {t('doc.flowfile_attrs')}")

    attrs_data = [
        {"Attribute": "filename", "Description": "Original file name", "Example": "data.csv"},
        {"Attribute": "uuid", "Description": "Unique identifier", "Example": "a1b2c3d4-..."},
        {"Attribute": "path", "Description": "Original file path", "Example": "/input/data/"},
        {"Attribute": "mime.type", "Description": "Content MIME type", "Example": "text/csv"},
        {"Attribute": "content.length", "Description": "Content size in bytes", "Example": "1024"},
    ]
    st.dataframe(attrs_data, hide_index=True, width="stretch")

    st.markdown("---")
    st.markdown(f"### {t('doc.flowfile_lifecycle')}")
    st.markdown("""
1. **Created** by a source task (getFile, httpReceiver, generateFlowFile, etc.)
2. **Routed** through relations between tasks
3. **Transformed** by processing tasks (replaceText, transformJSON, etc.)
4. **Output** by a sink task (putFile, putS3, sendEmail, etc.)

FlowFiles support **streaming** for large content — data is spilled to disk when memory thresholds are exceeded.
""")


# --- Services ---
with tab5:
    st.markdown(f"## {t('doc.services_doc')}")
    st.markdown(t('doc.services_desc'))

    st.markdown("---")

    available_services = sorted(ServiceFactory.list_types())

    for svc_type in available_services:
        try:
            svc_class = ServiceFactory.get(svc_type)
            instance = object.__new__(svc_class)
            instance.config = {}
            schema = instance.get_config_schema() if hasattr(instance, "get_config_schema") else {}
        except Exception:
            schema = {}

        with st.expander(f"**{svc_type}**"):
            doc = svc_class.__doc__
            if doc:
                st.markdown(doc.strip().split("\n")[0])

            if not schema:
                st.caption(t("doc.no_params"))
            else:
                for param_name, param_info in schema.items():
                    if isinstance(param_info, dict):
                        ptype = param_info.get("type", "string")
                        description = param_info.get("description", "")
                        st.markdown(f"- `{param_name}` ({ptype})")
                        if description:
                            st.caption(f"  {description}")
                    else:
                        st.markdown(f"- `{param_name}`: `{param_info}`")


# --- REST API ---
with tab6:
    st.markdown(f"## {t('doc.api_doc')}")
    st.markdown(t('doc.api_desc'))
    st.markdown(t('doc.api_endpoints'))

    st.markdown("---")
    st.markdown(f"### {t('doc.endpoints_overview')}")

    endpoints = [
        {"Group": "Flows", "Methods": "GET, POST, PUT, DELETE", "Path": "/api/flows/*"},
        {"Group": "Execution", "Methods": "POST, GET, DELETE", "Path": "/api/execute/*"},
        {"Group": "Monitoring", "Methods": "GET", "Path": "/api/monitoring/*"},
        {"Group": "Security", "Methods": "GET, POST, PUT, DELETE", "Path": "/api/security/*"},
        {"Group": "Plugins", "Methods": "GET, POST, DELETE", "Path": "/api/plugins/*"},
        {"Group": "Scheduler", "Methods": "GET, POST, DELETE", "Path": "/api/scheduler/*"},
        {"Group": "Cluster", "Methods": "GET, POST", "Path": "/api/cluster/*"},
        {"Group": "Services", "Methods": "GET, POST, PUT, DELETE", "Path": "/api/services/*"},
        {"Group": "Tasks", "Methods": "GET", "Path": "/api/tasks/*"},
        {"Group": "Health", "Methods": "GET", "Path": "/api/health"},
    ]
    st.dataframe(endpoints, hide_index=True, width="stretch")

    st.markdown("---")
    st.markdown(f"### {t('doc.quick_start')}")
    st.code("""# Start the API server
python cli.py serve --port 8000

# Open Swagger UI
# http://localhost:8000/docs

# Execute a flow via API
curl -X POST http://localhost:8000/api/execute/my_flow""", language="bash")


# --- CLI ---
with tab7:
    st.markdown(f"## {t('doc.cli_doc')}")
    st.markdown(t('doc.cli_desc'))

    st.markdown("---")
    st.markdown(t('doc.cli_commands'))

    commands = [
        {"Command": "`run <flow.json>`", "Description": "Execute a flow"},
        {"Command": "`validate <flow.json>`", "Description": "Validate a flow without executing"},
        {"Command": "`list-tasks`", "Description": "List all available task types"},
        {"Command": "`info <flow.json>`", "Description": "Show flow metadata and structure"},
        {"Command": "`serve [--port N]`", "Description": "Start the REST API server"},
        {"Command": "`gui`", "Description": "Launch the Streamlit GUI"},
        {"Command": "`plugins install <file.pfp>`", "Description": "Install a plugin"},
        {"Command": "`plugins list`", "Description": "List installed plugins"},
        {"Command": "`scheduler start`", "Description": "Start the CRON scheduler"},
        {"Command": "`export <flow_id>`", "Description": "Export a flow to JSON"},
        {"Command": "`import <flow.json>`", "Description": "Import a flow from JSON"},
        {"Command": "`cluster status`", "Description": "Show cluster status"},
    ]
    st.dataframe(commands, hide_index=True, width="stretch")

    st.markdown("---")
    st.markdown(f"### {t('doc.examples')}")
    st.code("""# Run a flow
python cli.py run flows/etl_pipeline.json

# Run with parameters
python cli.py run flows/etl_pipeline.json --param input_dir=/data --param output_dir=/output

# Validate before running
python cli.py validate flows/etl_pipeline.json

# List all tasks
python cli.py list-tasks""", language="bash")


# --- Keyboard Shortcuts ---
with tab8:
    st.markdown(f"## {t('doc.shortcuts_title')}")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown(f"### {t('doc.shortcuts_editor')}")
        shortcuts_editor = [
            {"Shortcut": "`Ctrl + S`", t("common.description"): t("editor.save_flow")},
            {"Shortcut": "`Ctrl + Z`", t("common.description"): t("editor.undo")},
            {"Shortcut": "`Ctrl + Y`", t("common.description"): t("editor.redo")},
            {"Shortcut": "`Ctrl + D`", t("common.description"): t("editor.duplicate")},
            {"Shortcut": "`Delete`", t("common.description"): t("editor.delete_task")},
        ]
        st.dataframe(shortcuts_editor, hide_index=True, width="stretch")

    with col2:
        st.markdown(f"### {t('doc.shortcuts_canvas')}")
        shortcuts_canvas = [
            {"Shortcut": "`Scroll`", t("common.description"): "Zoom in/out"},
            {"Shortcut": "`Click + Drag`", t("common.description"): "Pan canvas"},
            {"Shortcut": "`Click node`", t("common.description"): t("editor.click_to_configure")},
            {"Shortcut": "`Drag handle`", t("common.description"): t("editor.connections")},
            {"Shortcut": "`Right-click`", t("common.description"): "Context menu"},
        ]
        st.dataframe(shortcuts_canvas, hide_index=True, width="stretch")

    st.markdown("---")
    st.markdown(f"### {t('doc.shortcuts_general')}")
    shortcuts_general = [
        {"Shortcut": "`Ctrl + F`", t("common.description"): t("common.search")},
        {"Shortcut": "`F5`", t("common.description"): t("common.refresh")},
    ]
    st.dataframe(shortcuts_general, hide_index=True, width="stretch")
