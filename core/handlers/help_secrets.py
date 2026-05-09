"""Auto-extracted from core/tool_registry.py — see core/handlers/__init__.py"""

import json
import logging
from typing import Dict, Any, List, Optional

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)

class PawFlowHelpHandler(ToolHandler):
    """Query the PawFlow platform catalog and flow-authoring guide.

    Provides dynamic information about available tasks, services, and their
    configuration schemas, plus a static guide on how to build flows.
    """

    @property
    def name(self) -> str:
        return "pawflow_help"

    @property
    def description(self) -> str:
        return (
            "Get information about the PawFlow platform. Topics:\n"
            "- tasks: List all available task types\n"
            "- task:<type>: Get detailed info about a specific task\n"
            "- services: List all available service types\n"
            "- service:<type>: Get detailed info about a specific service\n"
            "- flow_guide: How to create a flow JSON definition\n"
            "- expressions: Expression syntax reference\n"
            "- triggers: Available trigger/scheduling options\n"
            "- resources: Agent/skill/MCP resource management guide"
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": (
                        "Topic to query. Use 'tasks', 'task:<type>', 'services', "
                        "'service:<type>', 'flow_guide', 'expressions', or 'triggers'."
                    ),
                },
            },
            "required": ["topic"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        topic = arguments.get("topic", "").strip()
        if not topic:
            return "Error: topic is required"

        if topic == "tasks":
            return self._list_tasks()
        elif topic.startswith("task:"):
            return self._task_detail(topic[5:].strip())
        elif topic == "services":
            return self._list_services()
        elif topic.startswith("service:"):
            return self._service_detail(topic[8:].strip())
        elif topic in ("flow_guide", "flows", "flow"):
            return self._flow_guide()
        elif topic == "expressions":
            return self._expressions_guide()
        elif topic == "triggers":
            return self._triggers_guide()
        elif topic == "resources":
            return self._resources_guide()
        else:
            return (
                f"Unknown topic '{topic}'. Available: tasks, task:<type>, "
                "services, service:<type>, flow_guide, expressions, triggers, resources"
            )

    def _list_tasks(self) -> str:
        from core import TaskFactory
        types = sorted(TaskFactory.list_types())
        lines = []
        for t in types:
            try:
                cls = TaskFactory.get(t)
                desc = getattr(cls, "DESCRIPTION", "") or ""
                tags = getattr(cls, "TAGS", []) or []
                tag_str = f" [{', '.join(tags)}]" if tags else ""
                lines.append(f"- {t}: {desc}{tag_str}")
            except Exception:
                lines.append(f"- {t}")
        return f"Available tasks ({len(types)}):\n" + "\n".join(lines)

    def _task_detail(self, task_type: str) -> str:
        from core import TaskFactory
        try:
            cls = TaskFactory.get(task_type)
        except Exception:
            return f"Task '{task_type}' not found. Use topic 'tasks' to list available types."

        info = [f"# {task_type}"]
        for attr in ("NAME", "VERSION", "DESCRIPTION", "ICON", "TAGS"):
            val = getattr(cls, attr, None)
            if val:
                info.append(f"{attr}: {val}")

        # Get parameter schema
        schema = {}
        if hasattr(cls, "PARAMETERS") and cls.PARAMETERS:
            schema = cls.PARAMETERS
        else:
            try:
                inst = cls.__new__(cls)
                inst.config = {}
                if hasattr(inst, "get_parameter_schema"):
                    schema = inst.get_parameter_schema()
            except Exception:
                pass

        if schema:
            info.append("\nParameters:")
            for pname, pdef in schema.items():
                ptype = pdef.get("type", "any")
                pdesc = pdef.get("description", "")
                req = " (required)" if pdef.get("required") else ""
                default = pdef.get("default")
                default_str = f" [default: {default}]" if default is not None else ""
                info.append(f"  - {pname}: {ptype} — {pdesc}{req}{default_str}")

        return "\n".join(info)

    def _list_services(self) -> str:
        from core import ServiceFactory
        types = sorted(ServiceFactory.list_types())
        lines = []
        for t in types:
            try:
                cls = ServiceFactory.get(t)
                desc = getattr(cls, "DESCRIPTION", "") or ""
                lines.append(f"- {t}: {desc}")
            except Exception:
                lines.append(f"- {t}")
        return f"Available services ({len(types)}):\n" + "\n".join(lines)

    def _service_detail(self, svc_type: str) -> str:
        from core import ServiceFactory
        try:
            cls = ServiceFactory.get(svc_type)
        except Exception:
            return f"Service '{svc_type}' not found. Use topic 'services' to list available types."

        info = [f"# {svc_type}"]
        for attr in ("NAME", "VERSION", "DESCRIPTION"):
            val = getattr(cls, attr, None)
            if val:
                info.append(f"{attr}: {val}")

        schema = {}
        if hasattr(cls, "PARAMETERS") and cls.PARAMETERS:
            schema = cls.PARAMETERS
        else:
            try:
                inst = cls.__new__(cls)
                inst.config = {}
                if hasattr(inst, "get_parameter_schema"):
                    schema = inst.get_parameter_schema()
            except Exception:
                pass

        if schema:
            info.append("\nParameters:")
            for pname, pdef in schema.items():
                ptype = pdef.get("type", "any")
                pdesc = pdef.get("description", "")
                req = " (required)" if pdef.get("required") else ""
                info.append(f"  - {pname}: {ptype} — {pdesc}{req}")

        return "\n".join(info)

    def _flow_guide(self) -> str:
        return """# PawFlow Flow Authoring Guide

## Flow JSON Structure
```json
{
  "id": "my-flow",
  "name": "My Flow",
  "version": "1.0.0",
  "description": "What this flow does",
  "parameters": {},
  "tasks": {
    "task_id": {
      "type": "<task_type>",
      "parameters": {
        "key": "value"
      }
    }
  },
  "relations": [
    {
      "from": "task_id_1",
      "to": "task_id_2",
      "type": "success"
    }
  ],
  "services": {
    "service_id": {
      "type": "<service_type>",
      "parameters": {
        "key": "value"
      }
    }
  }
}
```

IMPORTANT:
- Relations use "from"/"to" (NOT "source"/"destination")
- The array is called "relations" (NOT "connections")
- Services use "parameters" (NOT "config")
- Relations are a TOP-LEVEL array (NOT inside tasks)

## Key Concepts
- **Tasks** are processing nodes (transform, route, fetch, send data)
- **Services** are shared resources (HTTP listeners, Telegram bots, DB connections)
- **Relations** link tasks: from to with type (success/failure/all)
- **FlowFile** is the data unit flowing between tasks (content bytes + attributes dict)

## Expression Language (EL)
Expressions ${...} support chainable operations:
- String: upper, lower, trim, capitalize, title, reverse, length, substr(s,e), replace(old,new), append(s), prepend(s)
- Conditional: equals(v), not_equals(v), contains(v), starts_with(v), ends_with(v), matches(regex), is_empty, then(v), else(v), default(v)
- Split/Join: split(sep), join(sep), index(n), first, last, count
- Encoding: base64_encode, base64_decode, url_encode, url_decode, hash_md5, hash_sha256, to_int, to_float, to_bool
- JSON: json_get("key.nested")
- Date: now(fmt), format_date(fmt), add_days(n), timestamp
- Generators: uuid, uuid_short, random_int(min,max), random_string(n), now(fmt)
- Syntax: ${scope.key:op1:op2("arg"):op3}
- Nested: ${x:equals("y"):then("A"):else(${z:upper})}
- Generators: ${:uuid}, ${:now("%Y-%m-%d")}
- Multi-pass: resolved values containing ${...} are re-resolved

## Storage (source/destination)
Tools that read/write files support `source` and `destination` params:
- `"filestore"` (default): server FileStore (temporary, downloadable URLs)
- `"fs:<service>"`: filesystem service (relay to user's machine)
- `"<service>"`: shorthand for `"fs:<service>"`

Examples (use the actual filesystem service name from the conversation context):
- `share_file(filename="report.csv", content="...", destination="<fs_service_name>")` - write directly to user's disk
- `read_file(path="src/main.py", source="<fs_service_name>")` - read from user's filesystem
- `generate_image(prompt="...", destination="<fs_service_name>", path="assets/hero.png")` - render directly to filesystem
- `execute_script(code="...", destination="<fs_service_name>")` - execute on user's machine via relay
If only one filesystem service is connected, any name will resolve to it.

## Flow Scope (runtime dependencies)
A flow declares its runtime scope in the JSON: `"scope": "independent" | "user" | "conversation"`
- **independent** (default): no runtime dependencies
- **user**: needs `_user_id` (injected automatically at deploy)
- **conversation**: needs `_user_id` + `_conversation_id` (must be deployed from a conversation)

Conversation-scoped tasks (only work when `_conversation_id` is set):
- **publishMessage**: publish a message into the conversation (SSE + persist)
- **spawnAgent**: spawn an agent sync (wait) or async (fire & forget)
- **assignTaskToAgent**: assign a recurring task to an agent
- **cancelAgentTask**: cancel a running agent task
- **readConversation**: read messages from the conversation

## CRITICAL: Routing & Fan-Out Rules

When a task has MULTIPLE outgoing relations of the same type (e.g. two "success"
relations), EVERY output FlowFile is CLONED to ALL matching connections.

Example: if task A produces 1 FlowFile and has 2 success relations (A→B, A→C),
then B receives 1 FlowFile AND C receives 1 FlowFile (a clone).

### duplicateContent: WRONG way to fan out
DO NOT use `duplicateContent` to split a FlowFile to 2 branches. It produces
N copies as output FlowFiles, and EACH copy is cloned to ALL outgoing relations.

BAD (2 copies × 2 relations = 4 FlowFiles total, 2 per branch):
```
fetchData → duplicateContent(copies=2) → [branchA, branchB]
```

GOOD (1 FlowFile cloned to 2 relations = 1 per branch):
```
fetchData → branchA (success)
fetchData → branchB (success)
```

`duplicateContent` is only useful when you need multiple copies going to the
SAME downstream task (e.g. load testing, batch generation).

### mergeContent: Timing Matters!
`mergeContent` buffers FlowFiles and flushes when `min_entries` is reached.
It merges the FIRST N FlowFiles that arrive, regardless of which branch
they came from. If 2 FlowFiles from the same branch arrive before the other
branch, the merge will contain 2 copies of the same data.

Parameters: `separator` (string, default "\\n"), `min_entries` (int, default 2).
NOTE: the parameter is called "separator", NOT "delimiter".

CORRECT fan-out + merge pattern:
```json
{
  "tasks": {
    "source": { "type": "..." },
    "branchA": { "type": "..." },
    "branchB": { "type": "..." },
    "merge": { "type": "mergeContent", "parameters": { "separator": "\\n---\\n", "min_entries": 2 } },
    "final": { "type": "..." }
  },
  "relations": [
    {"from": "source", "to": "branchA", "type": "success"},
    {"from": "source", "to": "branchB", "type": "success"},
    {"from": "branchA", "to": "merge", "type": "success"},
    {"from": "branchB", "to": "merge", "type": "success"},
    {"from": "merge", "to": "final", "type": "success"}
  ]
}
```
Here `source` output is cloned to both branches (1 FlowFile each).
Each branch processes independently, then merge collects 1 from each.

## Common Patterns

### HTTP API endpoint
tasks: httpReceiver → processData → handleHTTPResponse
services: httpListener (shared port)

### Telegram bot
tasks: telegramReceiver → agentLoop → telegramSend
services: telegramBot

### Deploying existing templates
Use manage_flow with action 'catalog' to see available templates, then 'deploy'
with template_id to create an instance. Override parameters as needed.

### Scheduled pipeline
Use cronTrigger as root task (see pawflow_help topic 'triggers' for details).

### Data transformation
tasks: fetchData → updateAttribute → transformJSON → routeOnAttribute → output

## Agent Tools as Flow Tasks

Every agent tool is also available as a flow task with the prefix `tool.`.
Use these when you need tool functionality in a flow (not in agent context).

Available tool tasks (use `pawflow_help topic='tasks'` for full list):
- `tool.generate_image` — Generate an image via the configured image service
- `tool.generate_video` — Generate a video
- `tool.notify_user` — Send a notification to a user/conversation
- `tool.share_file` — Create a file in the FileStore
- `tool.remember` / `tool.recall` / `tool.forget` — Memory store/retrieve/delete
- `tool.semantic_recall` — Search memories by vector similarity
- `tool.check_duplicate` — Check for duplicate memories before storing
- `tool.memory_navigate` — Browse memory taxonomy (categories)
- `tool.kg_add` / `tool.kg_query` / `tool.kg_invalidate` — Knowledge graph triples
- `tool.kg_timeline` / `tool.kg_stats` — KG timeline and statistics
- `tool.query_graph` — Traverse KG with BFS/DFS
- `tool.kg_god_nodes` — Most connected KG entities
- `tool.diary_write` / `tool.diary_read` — Agent personal diary
- `tool.project_graph` — Build/query code structure graph (AST)
- `tool.fetch` — Fetch a web page
- `tool.web_search` — Web search
- `tool.execute_script` — Run a sandboxed Python script
- `tool.delegate` — Delegate to sub-agents
- `tool.assign_task` — Assign a task to an agent
- `tool.manage_flow` — Create/deploy/manage flows

Tool task parameters match the tool's parameter schema.
Arguments are read from: task config → FlowFile attributes → FlowFile content (JSON).
Output: tool result as FlowFile content, with `tool.name` and `tool.status` attributes.

Example: generate an image from an upstream prompt
```json
{
  "tasks": {
    "prompt": { "type": "inferLLM", "parameters": { "system_prompt": "Generate a Ponyverse image prompt" } },
    "gen": { "type": "tool.generate_image", "parameters": { "negative_prompt": "blurry, deformed" } }
  },
  "relations": [
    { "from": "prompt", "to": "gen", "type": "success" }
  ]
}
```
The prompt task output flows as FlowFile content → tool.generate_image reads `prompt` from it.

## Task Configuration
- Parameters go in the `parameters` key inside the task definition
- Tasks read config via `self.config.get("key")`
- Use expressions like `${attribute_name}` in parameter values
- Use `${key}` for secrets and variables (auto-cascades: flow → conv → user → global)
- Environment variables are in the cascade: ${VAR_NAME} checks env after global
- No scope prefix needed — `${api_key}` finds it wherever it's defined

## IMPORTANT: Before using any task, ALWAYS call pawflow_help with topic 'task:<type>'
to get the EXACT parameter names. DO NOT guess parameter names.

Common mistakes to avoid:
- sendEmail: params are 'to'/'from' (NOT 'to_email'/'from_email'), 'oauth2_client_id' (NOT 'oauth_client_id')
- mergeContent: param is 'separator' (NOT 'delimiter'), no 'strategy' param
- inferLLM: can use 'service' param to reference an llmConnection service instead of inline api_key

## Service References
Tasks can reference a service defined in the flow's `services` section:
```json
{
  "services": {
    "my_llm": {
      "type": "llmConnection",
      "parameters": { "provider": "openai", "api_key": "${openai_key}", "model": "gpt-4o" }
    }
  },
  "tasks": {
    "infer": {
      "type": "inferLLM",
      "parameters": { "service": "my_llm", "system_prompt": "You are helpful." }
    }
  }
}
```
The service parameters are merged into the task config (service = defaults, task = overrides).

## Connection Types
- `success`: Only on successful execution
- `failure`: Only on error
- `all`: Always (default if omitted)

## executeScript
Variables available in scripts:
- `content` (str): FlowFile content decoded as UTF-8
- `attributes` (dict): FlowFile attributes
- `flowfile` / `flow_file`: the FlowFile object
- Set `result` variable to replace FlowFile content (auto-encoded to bytes)
- Or modify `flow_file.content` directly (must be bytes)
- `get_secret('key_name')` — Retrieve a decrypted secret by name (user-scoped)
- `get_variable('key_name')` — Retrieve a plaintext variable by name (user-scoped)
- Standard safe modules: `import json`, `import re`, `import datetime`, `import math`, `import requests`, etc.

## Tips
- Use `updateAttribute` to set/transform FlowFile attributes
- Use `routeOnAttribute` to branch flows conditionally
- Use expressions `${...}` for dynamic values
- Service IDs in task config must match the services section keys
- Each task must have a unique ID within the flow
- To fan out to 2+ branches: add multiple relations from the SAME task (auto-clone)
- Do NOT use duplicateContent to fan out — it multiplies FlowFiles × relations"""

    def _expressions_guide(self) -> str:
        return """# Expression Syntax

PawFlow expressions use `${...}` syntax and are resolved at parse/runtime.

## Global Secrets (shared across all flows)
- `${key_name}` — Encrypted global secret (data/config/global_secrets.json)
- Managed via Runtime UI (🔑 button next to Global in treeview)

## User Secrets (per-user, encrypted at rest)
- `${key_name}` — Encrypted user secret (data/config/users/{username}/secrets.json)
- Store via: `/add-secret name value` in chat or `store_secret` tool
- Managed via Runtime UI (🔑 button next to user group in treeview)
- Use `list_secrets` tool or `/list-secrets` in chat to see available keys

## Global Parameters (shared across all flows)
- `${key_name}` — Global parameter (data/config/global_parameters.json)
- Managed via Runtime UI (⚙️ button next to Global in treeview)

## User Parameters (per-user)
- `${key_name}` — User parameter (data/config/users/{username}/parameters.json)
- Store via: `/add-variable name value` in chat
- Managed via Runtime UI (⚙️ button next to user group in treeview)
- Use `/list-variables` in chat to see available keys

## Attribute References
- `${attribute_name}` — FlowFile attribute value
- `${telegram.chat_id}` — Dotted attribute names work

## Flow Parameters
- `${key}` — From the flow's parameter context

## Environment Variables
- `${VAR_NAME}` — Also checks OS environment (last in cascade)
- `${VAR_NAME:!important(env)}` — Force OS environment only

## Special Variables
- `${now}` — Current ISO timestamp
- `${uuid}` — Random UUID

## Usage
Expressions can be used in most task parameter values:
```json
{
  "url": "${api_base_url}/endpoint",
  "chat_id": "${telegram.chat_id}"
}
```"""

    def _triggers_guide(self) -> str:
        return """# Triggers & Scheduling

## cronTrigger Task (PREFERRED for scheduled flows)
A persistent source task that emits a FlowFile on a CRON schedule.
Use this as the ROOT TASK of any flow that needs to run on a schedule.

```json
{
  "cron": {
    "type": "cronTrigger",
    "parameters": {
      "schedule": "0 7 * * *"
    }
  }
}
```

Then connect it to the first processing task:
```json
{"from": "cron", "to": "first_task", "type": "success"}
```

CRON format: `minute hour day_of_month month day_of_week`
Examples:
- `0 7 * * *` — Every day at 7:00 AM
- `*/5 * * * *` — Every 5 minutes
- `0 0 * * 1` — Every Monday at midnight

The cronTrigger is a persistent source (like httpReceiver), so the
ContinuousFlowExecutor stays alive and fires the flow at each CRON tick.

Output attributes: cron.schedule, cron.fired_at (ISO timestamp).

## IMPORTANT: cronTrigger vs generateFlowFile
- `cronTrigger`: persistent source, keeps flow alive, fires on schedule
- `generateFlowFile`: fires ONCE then flow auto-stops (use for one-shot batch flows only)

For scheduled flows, ALWAYS use cronTrigger as the root task.
Do NOT use generateFlowFile + external CRON — use cronTrigger instead.

## Self-Triggering Tasks (persistent sources)
Tasks with `is_persistent_source = True` and `has_pending_input()`:
- `cronTrigger`: Fires on CRON schedule
- `httpReceiver`: Triggered by incoming HTTP requests
- `telegramReceiver`: Triggered by incoming Telegram messages

## PollScheduler (persistent)
For agent-initiated scheduled checks:
- Use `ScheduleWakeup` tool to schedule a future wake-up
- Persists across restarts (JSON file)
- Supports absolute time or relative delay"""

    def _resources_guide(self) -> str:
        return """# Resource Management

PawFlow supports user-scoped resources: agents, skills, and MCP servers.
Both users (via chat commands) and agents (via tools) can manage them.

## Resource Types

### Agents
Sub-agents with their own system prompts and tool access.
- Create: `manage_resource(action="create", resource_type="agent", name="analyst", data={"prompt": "You are...", "model": "gpt-4", "tools": ["execute_script"]})`
- Fields: prompt (required), model, tools (list), max_depth, timeout, description

### Skills
Prompt modules injected into agents through agent.assigned_skills.
- Create: `manage_resource(action="create", resource_type="skill", name="summarizer", data={"prompt": "Summarize concisely"})`
- Review untrusted content first: `manage_resource(action="review", resource_type="skill", data={"prompt": "..."})`
- Fields: prompt (required), description, parameters, extends, template_engine

### MCP Servers
Model Context Protocol server connections.
- Create: `manage_resource(action="create", resource_type="mcp", name="db", data={"url": "http://localhost:3000"})`
- Fields: url (required), auth

## Using Resources

### manage_resource tool
CRUD operations: create, update, delete, list, get, review, activate, deactivate
Activation scopes compatible resources to the current conversation. Skills are not activated this way; assign them to an agent instead.

### delegate tool
Fire-and-forget delegation to other agents. Returns IMMEDIATELY — you are
not blocked. The target's reply arrives later as a private
`agent_delegate(from, to)` message that wakes or preempts you:
```
delegate(tasks=[
  {"agent": "analyst", "message": "Analyze this data"},
  {"agent": "writer", "message": "Write a report on..."}
])
```
Default `context="shared"` routes the message into the target's own
conversation context. Use `context="isolated"` or `context="last:N"`
only when you need a self-contained sub-agent with an empty workspace.

### show_file tool
Display a file in the chat viewer:
```
show_file(filename="report.pdf")
```

## Chat Slash Commands
- `/agent create` / `/agent list` / `/agent select <name>` / `/agent delete <name>`
- `/add-skill <name> <prompt>` / `/skill list` / `/skill del <name>`
- `/resources` — List all resources with active status
- `/activate <type> <name>` / `/deactivate <type> <name>`
- `/share <type> <name> <conversation_id>` — Share resource to another conversation
- `/view <filename>` — Open file viewer

## Scope Model
- Resource definitions are global (per user) — stored in data/config/*.json
- Activation is per conversation — stored in conversation metadata
- Share = activate a resource in another conversation of the same user"""


class StoreSecretHandler(ToolHandler):
    """Securely store a credential or secret value.

    Uses the SecretsManager to encrypt the value at rest.
    Stores in user-level secrets file: data/config/users/{username}/secrets.json
    Referenced via ${key_name} in flows.
    """

    def __init__(self):
        self._user_id = ""
        self._conversation_id = ""

    @property
    def name(self) -> str:
        return "store_secret"

    @property
    def description(self) -> str:
        return (
            "Securely store a secret (API key, token, password). "
            "The value is encrypted at rest and can be referenced in "
            "flow configs as ${key_name}."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Secret key name (e.g. 'google_calendar_api_key')",
                },
                "value": {
                    "type": "string",
                    "description": "Secret value to store (will be encrypted)",
                },
            },
            "required": ["key", "value"],
        }

    def set_user_id(self, uid: str):
        self._user_id = uid

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def execute(self, arguments: Dict[str, Any]) -> str:
        key = arguments.get("key", "").strip()
        value = arguments.get("value", "")
        if not key or not value:
            return "Error: key and value are required"

        user_id = self._user_id

        try:
            from pathlib import Path
            from core.config_store import ConfigStore
            from core.config_value import ConfigValue

            from core.paths import user_secrets_path; secrets_path = user_secrets_path(user_id)
            secrets = ConfigStore.load_secrets(secrets_path)
            secrets[key] = ConfigValue(value=value)
            ConfigStore.save_secrets(secrets_path, secrets)
            return f"Secret '{key}' stored securely. Reference it as ${{{key}}}"
        except Exception as e:
            return f"Error storing secret: {e}"

    @staticmethod
    def cleanup_conversation(conversation_id: str):
        """No-op: user secrets are permanent and not conversation-scoped."""
        pass


class ListSecretsHandler(ToolHandler):
    """List available secret key names (never values) for the current user."""

    def __init__(self):
        self._user_id = ""

    @property
    def name(self) -> str:
        return "list_secrets"

    @property
    def description(self) -> str:
        return (
            "List available secret names for the current user. "
            "Returns only key names (never values). Use these names "
            "in flow configs as ${key_name}."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def set_user_id(self, uid: str):
        self._user_id = uid

    def execute(self, arguments: Dict[str, Any]) -> str:
        from pathlib import Path
        from core.config_store import ConfigStore

        user_id = self._user_id
        from core.paths import user_secrets_path; secrets_path = user_secrets_path(user_id)
        secrets = ConfigStore.load_secrets(secrets_path)

        if not secrets:
            return "No secrets stored yet. Use store_secret tool or /add-secret in chat."

        lines = [f"Available secrets ({len(secrets)}):"]
        for k in sorted(secrets.keys()):
            cv = secrets[k]
            suffix = f" (large: {cv.size / 1024:.0f}KB)" if cv.is_large else ""
            lines.append(f"- {k}{suffix}  →  ${{{k}}}")
        return "\n".join(lines)


