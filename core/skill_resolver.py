"""Skill resolver — resolves skill entries to prompt blocks.

Used by agent_context.py (main conv, task sub-conv) and
agent_executor.py (delegate sub-agents).
"""

import json
import logging
import re
import shlex
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


_MEDIA_KIND_BASES = {
    "image": "services.base_image_generation:BaseImageGenerationService",
    "video": "services.base_video_generation:BaseVideoGenerationService",
    "audio": "services.base_audio_generation:BaseAudioGenerationService",
    "3d": "services.base_capabilities:BaseImage3DService",
    "upscale": "services.base_capabilities:BaseImageUpscaleService",
    "tryon": "services.base_capabilities:BaseTryOnService",
    "lipsync": "services.base_capabilities:BaseLipsyncService",
    "voice": "services.base_voice_clone:BaseVoiceCloneService",
}

_MEDIA_KIND_EXTRA_KEYS = {
    "image": "image_services",
    "video": "video_services",
    "audio": "audio_services",
    "3d": "threed_services",
    "upscale": "upscale_services",
    "tryon": "tryon_services",
    "lipsync": "lipsync_services",
    "voice": "voice_services",
}

_SERVICE_OPS = (
    "generate", "edit_image", "image_to_video", "reference_to_video",
    "frame_to_video", "video_edit", "generate_3d", "upscale",
    "upscale_video", "describe_image", "remix_image", "remove_background",
    "try_on", "lipsync", "train", "speech_to_video", "ensure_voice_id",
    "clone_speak",
)


class _PawFlowTemplateContext:
    """Read-only dynamic context exposed to programmable skill templates."""

    def __init__(self, user_id: str = "", conversation_id: str = "",
                 agent_name: str = ""):
        self.user_id = user_id or ""
        self.conversation_id = conversation_id or ""
        self.agent_name = agent_name or ""

    @property
    def conversation(self) -> Dict[str, Any]:
        return {
            "id": self.conversation_id,
            "current_agent": self.agent_name,
            "default_relay": self.default_relay,
        }

    @property
    def relays(self) -> List[Dict[str, Any]]:
        if not self.conversation_id:
            return []
        try:
            from core.relay_bindings import (
                get_default, get_default_local, get_linked,
                list_available_relays,
            )
            linked = get_linked(self.conversation_id, self.agent_name)
            default = get_default(self.conversation_id, self.agent_name) or ""
            available = {
                r.get("relay_id", ""): r for r in list_available_relays(self.user_id)
            }
            rows = []
            for rid in linked:
                raw = available.get(rid, {})
                rows.append({
                    "id": rid,
                    "relay_id": rid,
                    "is_default": rid == default,
                    "connected": bool(raw.get("connected", False)),
                    "root": raw.get("root", ""),
                    "host_root_available": bool(raw.get("host_root", "")),
                    "allow_local": bool(raw.get("allow_local", False)),
                    "allow_local_screen": bool(raw.get("allow_local_screen", False)),
                    "default_local": get_default_local(
                        self.conversation_id, rid, self.agent_name),
                })
            return rows
        except Exception:
            logger.debug("dynamic skill relay snapshot failed", exc_info=True)
            return []

    @property
    def default_relay(self) -> Dict[str, Any]:
        for relay in self.relays:
            if relay.get("is_default"):
                return relay
        return {}

    @property
    def agents(self) -> List[Dict[str, Any]]:
        if not self.conversation_id:
            return []
        try:
            from core.conv_agent_config import get_all_agent_configs
            from core.expression import resolve_value
            from core.resource_store import ResourceStore
            from core.service_registry import ServiceRegistry
            rs = ResourceStore.instance()
            reg = ServiceRegistry.get_instance()
            rows = []
            for name, cfg in (get_all_agent_configs(self.conversation_id) or {}).items():
                agent_def_name = cfg.get("agent", name) or name
                agent_def = rs.get_any("agent", agent_def_name, self.user_id) or {}
                llm_service = resolve_value(
                    cfg.get("llm_service", "") or "",
                    owner=self.user_id,
                    conversation_id=self.conversation_id,
                ) or ""
                provider = ""
                service_type = ""
                if llm_service:
                    try:
                        sdef = reg.resolve_definition(
                            llm_service, user_id=self.user_id,
                            conv_id=self.conversation_id)
                        service_type = getattr(sdef, "service_type", "") or ""
                        provider = ((getattr(sdef, "config", None) or {}).get("provider", "")
                                    if sdef else "")
                    except Exception:
                        provider = ""
                rows.append({
                    "name": name,
                    "definition": agent_def_name,
                    "is_current": name == self.agent_name,
                    "llm_service": llm_service,
                    "service_type": service_type,
                    "provider": provider,
                    "assigned_skills": list(agent_def.get("assigned_skills") or []),
                })
            return rows
        except Exception:
            logger.debug("dynamic skill agent snapshot failed", exc_info=True)
            return []

    @property
    def current_agent(self) -> Dict[str, Any]:
        for agent in self.agents:
            if agent.get("is_current"):
                return agent
        return {"name": self.agent_name}

    def service(self, service_id: str) -> Dict[str, Any]:
        service_id = service_id or ""
        if not service_id:
            return {}
        try:
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            sdef = reg.resolve_definition(
                service_id, user_id=self.user_id, conv_id=self.conversation_id)
            if not sdef:
                return {}
            svc = reg.resolve(service_id, user_id=self.user_id,
                              conv_id=self.conversation_id)
            return self._summarize_service(sdef, svc)
        except Exception:
            logger.debug("dynamic skill service snapshot failed", exc_info=True)
            return {}

    def media_services(self, kind: str = "") -> List[Dict[str, Any]]:
        kind = (kind or "").lower().strip()
        base_path = _MEDIA_KIND_BASES.get(kind)
        if not base_path:
            return []
        try:
            base_class = self._import_symbol(base_path)
            from tasks import _register_all_services
            _register_all_services()
            from core import ServiceFactory
            from core.service_registry import ServiceRegistry
            valid_types = []
            for stype, sclass in ServiceFactory._services.items():
                try:
                    if issubclass(sclass, base_class):
                        valid_types.append(stype)
                except TypeError:
                    pass
            reg = ServiceRegistry.get_instance()
            rows = []
            for stype in sorted(valid_types):
                for sdef in reg.resolve_by_type(stype, user_id=self.user_id):
                    svc = None
                    try:
                        svc = reg.resolve(
                            sdef.service_id, user_id=self.user_id,
                            conv_id=self.conversation_id)
                    except Exception:
                        svc = None
                    rows.append(self._summarize_service(sdef, svc, kind=kind))
            return rows
        except Exception:
            logger.debug("dynamic skill media snapshot failed", exc_info=True)
            return []

    def default_media_service(self, kind: str = "") -> Dict[str, Any]:
        kind = (kind or "").lower().strip()
        services = self.media_services(kind)
        if not services:
            return {}
        extra_key = _MEDIA_KIND_EXTRA_KEYS.get(kind, "")
        if self.conversation_id and extra_key:
            try:
                from core.conversation_store import ConversationStore
                from tasks.ai.agent_utils import _resolve_extra_dict
                prefs = _resolve_extra_dict(
                    ConversationStore.instance(), self.conversation_id,
                    extra_key, self.user_id)
                preferred = (
                    prefs.get(self.agent_name or "agent") or prefs.get("*")
                    if isinstance(prefs, dict) else "")
                if preferred:
                    for svc in services:
                        if svc.get("id") == preferred or svc.get("service_id") == preferred:
                            return svc
            except Exception:
                logger.debug("dynamic skill media default failed", exc_info=True)
        # Keep the same deterministic fallback as the runtime resolver: first
        # compatible service when no explicit/default selector is available.
        return services[0]

    def tool_schema(self, name: str) -> Dict[str, Any]:
        name = name or ""
        if not name:
            return {}
        try:
            from core.handlers.meta_tools import _schema_with_local
            from core.tool_registry import create_default_registry
            handler = create_default_registry().get(name)
            if not handler:
                return {}
            schema = _schema_with_local(handler)
            return {
                "name": handler.name,
                "description": handler.description,
                "parameters": schema,
            }
        except Exception:
            logger.debug("dynamic skill tool schema failed", exc_info=True)
            return {}

    @staticmethod
    def _import_symbol(path: str):
        module_name, attr = path.split(":", 1)
        module = __import__(module_name, fromlist=[attr])
        return getattr(module, attr)

    def _summarize_service(self, sdef, svc=None, kind: str = "") -> Dict[str, Any]:
        cfg = getattr(sdef, "config", {}) or {}
        row = {
            "id": getattr(sdef, "service_id", ""),
            "service_id": getattr(sdef, "service_id", ""),
            "type": getattr(sdef, "service_type", ""),
            "service_type": getattr(sdef, "service_type", ""),
            "scope": getattr(sdef, "scope", ""),
            "kind": kind,
            "provider": cfg.get("provider", ""),
            "default_model": cfg.get("default_model", "") or cfg.get("model", ""),
            "accepts_filestore_urls": bool(
                getattr(svc, "ACCEPTS_FILESTORE_URLS", False)) if svc else False,
            "operations": [op for op in _SERVICE_OPS if svc and hasattr(svc, op)],
            "models": [],
        }
        if svc and hasattr(svc, "get_model_info"):
            try:
                info = svc.get_model_info() or {}
                models = info.get("models") or info.get("available_models") or []
                if isinstance(models, dict):
                    models = [{"name": k, **(v if isinstance(v, dict) else {})}
                              for k, v in models.items()]
                elif isinstance(models, list):
                    models = [m if isinstance(m, dict) else {"name": str(m)}
                              for m in models]
                row["models"] = models[:30]
                if not row["default_model"]:
                    row["default_model"] = info.get("model", "") or info.get("default_model", "")
            except Exception:
                logger.debug("dynamic skill model info failed", exc_info=True)
        return row


class _DynamicSkillPawFlow:
    """Small wrapper so templates can call pawflow.media_services(...)."""

    def __init__(self, ctx: _PawFlowTemplateContext):
        self._ctx = ctx

    @property
    def conversation(self):
        return self._ctx.conversation

    @property
    def relays(self):
        return self._ctx.relays

    @property
    def default_relay(self):
        return self._ctx.default_relay

    @property
    def agents(self):
        return self._ctx.agents

    @property
    def current_agent(self):
        return self._ctx.current_agent

    def media_services(self, kind: str = ""):
        return self._ctx.media_services(kind)

    def default_media_service(self, kind: str = ""):
        return self._ctx.default_media_service(kind)

    def tool_schema(self, name: str):
        return self._ctx.tool_schema(name)

    def service(self, service_id: str):
        return self._ctx.service(service_id)


def normalize_skill_entry(entry) -> Tuple[str, Dict[str, str], str]:
    """Normalize a skill entry to (name, params, condition).

    Accepts:
      - "skill_name"              → ("skill_name", {}, "")
      - {"name": "x", "params": {"k": "v"}, "condition": "${...}"}
        → ("x", {"k": "v"}, "${...}")
    """
    if isinstance(entry, str):
        return entry, {}, ""
    if isinstance(entry, dict):
        return entry.get("name", ""), entry.get("params") or {}, entry.get("condition", "")
    return "", {}, ""


def _evaluate_condition(condition: str, user_id: str) -> bool:
    """Evaluate a condition expression. Returns False if result is empty/false/0."""
    if not condition:
        return True
    from core.expression import resolve_value
    resolved = resolve_value(condition, owner=user_id)
    return bool(resolved) and resolved not in ("false", "False", "0")


def _substitute_params(prompt: str, params: Dict[str, str],
                       defaults: Dict[str, Any]) -> str:
    """Replace ${param_name} in prompt with values from params, falling back to defaults."""
    if not params and not defaults:
        return prompt
    merged = {}
    for k, v in defaults.items():
        if isinstance(v, dict):
            merged[k] = v.get("default", "")
        else:
            merged[k] = str(v)
    merged.update({k: str(v) for k, v in params.items()})
    if not merged:
        return prompt

    def _replace(m):
        key = m.group(1)
        return merged.get(key, m.group(0))

    return re.sub(r'\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}', _replace, prompt)


def _safe_skill_path_part(value: str, fallback: str) -> str:
    safe = re.sub(r'[^a-zA-Z0-9_.-]+', '-', str(value or '')).strip('.-')
    return safe or fallback


def skill_mount_dir(skill_name: str, skill_def: Dict[str, Any]) -> str:
    """Return the stable relay-visible path advertised for a skill package."""
    resource_id = (
        skill_def.get("resource_id") or skill_def.get("_resource_id")
        or skill_def.get("id") or f"{skill_def.get('_scope', 'user')}:{skill_name}"
    )
    return (
        "/pawflow/skills/"
        f"{_safe_skill_path_part(resource_id, 'resource')}/"
        f"{_safe_skill_path_part(skill_name, 'skill')}"
    )


def _split_skill_arguments(arguments: str) -> List[str]:
    if not arguments:
        return []
    try:
        return [str(v) for v in shlex.split(arguments)]
    except ValueError:
        return [v for v in arguments.split() if v]


def _declared_param_names(declared_params: Any) -> List[str]:
    if isinstance(declared_params, dict):
        return [str(k) for k in declared_params.keys()]
    if isinstance(declared_params, list):
        names = []
        for item in declared_params:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict) and item.get("name"):
                names.append(str(item["name"]))
        return names
    return []


def _run_params(arguments: str, args: List[str],
                declared_params: Any) -> Dict[str, str]:
    params = {str(idx): value for idx, value in enumerate(args)}
    for name, value in zip(_declared_param_names(declared_params), args):
        params[name] = value
    params["arguments"] = arguments or ""
    return params


def _substitute_run_placeholders(prompt: str, arguments: str,
                                 args: List[str], params: Dict[str, str],
                                 skill_dir: str) -> str:
    """Render Agent Skills style placeholders used by imported skills."""
    replacements = {
        "ARGUMENTS": arguments or "",
        "PAWFLOW_SKILL_DIR": skill_dir,
        "CLAUDE_SKILL_DIR": skill_dir,
        "CODEX_SKILL_DIR": skill_dir,
    }
    for key, value in params.items():
        if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', key):
            replacements.setdefault(key, value)

    def _replace_index(match):
        idx = int(match.group(1))
        return args[idx] if idx < len(args) else match.group(0)

    prompt = re.sub(r'\$ARGUMENTS\[(\d+)\]', _replace_index, prompt)
    prompt = re.sub(r'\$([0-9]+)', _replace_index, prompt)

    def _replace_name(match):
        key = match.group(1) or match.group(2)
        return replacements.get(key, match.group(0))

    return re.sub(
        r'\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}|\$([a-zA-Z_][a-zA-Z0-9_]*)',
        _replace_name,
        prompt,
    )


_MAX_EXTENDS_DEPTH = 5


def _get_skill_any(rs, skill_name: str, user_id: str,
                   conversation_id: str = ""):
    try:
        return rs.get_any(
            "skill", skill_name, user_id, conversation_id=conversation_id)
    except TypeError:
        return rs.get_any("skill", skill_name, user_id)


def _resolve_prompt_chain(skill_name: str, rs, user_id: str,
                          conversation_id: str = "",
                          depth: int = 0) -> str:
    """Resolve a skill's prompt including its extends chain.

    Returns the concatenated prompt: parent first, then child.
    """
    if depth >= _MAX_EXTENDS_DEPTH:
        return ""
    skill_def = _get_skill_any(rs, skill_name, user_id, conversation_id)
    if not skill_def or not skill_def.get("prompt"):
        return ""
    parent_prompt = ""
    extends = skill_def.get("extends", "")
    if extends:
        parent_prompt = _resolve_prompt_chain(
            extends, rs, user_id, conversation_id, depth + 1)
    prompt = skill_def["prompt"]
    if parent_prompt:
        return parent_prompt + "\n\n" + prompt
    return prompt


def _render_dynamic_skill_prompt(prompt: str, skill_def: Dict[str, Any],
                                 user_id: str, conversation_id: str,
                                 agent_name: str,
                                 params: Dict[str, str],
                                 args: List[str] = None,
                                 arguments: str = "",
                                 skill_dir: str = "") -> str:
    engine = (skill_def.get("template_engine") or "").lower().strip()
    if engine not in ("jinja", "jinja2"):
        return prompt
    try:
        from jinja2.sandbox import SandboxedEnvironment
        env = SandboxedEnvironment(autoescape=False, trim_blocks=True,
                                   lstrip_blocks=True)
        env.filters["tojson"] = lambda value: json.dumps(value, ensure_ascii=False)
        ctx = _PawFlowTemplateContext(user_id, conversation_id, agent_name)
        return env.from_string(prompt).render(
            pawflow=_DynamicSkillPawFlow(ctx),
            params=params or {},
            args=args or [],
            arguments=arguments or "",
            skill_dir=skill_dir or "",
            PAWFLOW_SKILL_DIR=skill_dir or "",
            CLAUDE_SKILL_DIR=skill_dir or "",
            CODEX_SKILL_DIR=skill_dir or "",
        )
    except Exception as exc:
        logger.warning("Dynamic skill template render failed: %s", exc,
                       exc_info=True)
        return prompt + f"\n\n[Dynamic skill context unavailable: {type(exc).__name__}: {exc}]"


def resolve_skill_prompts(
    skill_entries: List,
    user_id: str,
    conversation_id: str = "",
    agent_name: str = "",
) -> List[str]:
    """Resolve a list of skill entries to formatted prompt blocks.

    Args:
        skill_entries: List of skill names (str) or dicts with name+params.
        user_id: For ResourceStore lookup.
        conversation_id: Optional runtime context for programmable skills.
        agent_name: Optional current agent for programmable skills.

    Returns:
        List of formatted prompt strings ready to inject in system prompt.
    """
    from core.resource_store import ResourceStore
    rs = ResourceStore.instance()
    blocks = []
    seen = set()
    for entry in skill_entries:
        name, params, condition = normalize_skill_entry(entry)
        if not name or name in seen:
            continue
        seen.add(name)
        if condition and not _evaluate_condition(condition, user_id):
            continue
        skill_def = _get_skill_any(rs, name, user_id, conversation_id)
        if not skill_def or not skill_def.get("prompt"):
            continue
        prompt = _resolve_prompt_chain(
            name, rs, user_id, conversation_id=conversation_id)
        declared_params = skill_def.get("parameters") or {}
        if params or declared_params:
            prompt = _substitute_params(prompt, params, declared_params)
        prompt = _render_dynamic_skill_prompt(
            prompt, skill_def, user_id, conversation_id, agent_name, params)
        desc = skill_def.get("description", "")
        blocks.append(
            f"## Skill: {name}\n"
            f"{desc}\n\n"
            f"{prompt}"
        )
    return blocks


def resolve_runnable_skill_prompt(skill_name: str, user_id: str,
                                  conversation_id: str,
                                  agent_name: str,
                                  arguments: str = "") -> str:
    """Resolve a visible skill for immediate one-shot invocation.

    Unlike load_skill, this does not require the skill to be assigned to the
    target agent. It is used for explicit user commands such as
    `/skill run [@agent] name args...`.
    """
    from core.resource_store import ResourceStore
    rs = ResourceStore.instance()
    skill_def = _get_skill_any(rs, skill_name, user_id, conversation_id)
    if not skill_def or not skill_def.get("prompt"):
        return ""
    declared_params = skill_def.get("parameters") or {}
    args = _split_skill_arguments(arguments or "")
    params = _run_params(arguments or "", args, declared_params)
    prompt = _resolve_prompt_chain(
        skill_name, rs, user_id, conversation_id=conversation_id)
    if params or declared_params:
        prompt = _substitute_params(prompt, params, declared_params)
    skill_dir = skill_mount_dir(skill_name, skill_def)
    prompt = _substitute_run_placeholders(
        prompt, arguments or "", args, params, skill_dir)
    prompt = _render_dynamic_skill_prompt(
        prompt, skill_def, user_id, conversation_id, agent_name, params,
        args=args, arguments=arguments or "", skill_dir=skill_dir)
    desc = skill_def.get("description", "")
    arg_line = arguments or ""
    return (
        f"## Skill Invocation: {skill_name}\n"
        f"Target agent: {agent_name}\n"
        f"Arguments: {arg_line}\n"
        f"Skill directory: {skill_dir}\n\n"
        f"{desc}\n\n"
        f"{prompt}\n\n"
        "Run this skill now for the provided arguments. "
        "Use normal PawFlow tools if the skill requires files, commands, or scripts."
    )


def _skill_summary(skill_def: Dict[str, Any]) -> str:
    desc = str(skill_def.get("description", "") or "").strip()
    if desc:
        return desc[:500]
    return "No description provided."


def resolve_skill_manifests(
    skill_entries: List,
    user_id: str,
) -> List[str]:
    """Resolve assigned skills to lightweight availability manifest lines."""
    from core.resource_store import ResourceStore
    rs = ResourceStore.instance()
    lines = []
    seen = set()
    for entry in skill_entries:
        name, _params, condition = normalize_skill_entry(entry)
        if not name or name in seen:
            continue
        seen.add(name)
        if condition and not _evaluate_condition(condition, user_id):
            continue
        skill_def = rs.get_any("skill", name, user_id)
        if not skill_def:
            continue
        summary = _skill_summary(skill_def)
        lines.append(
            f"- {name}: {summary}\n"
            f"  Use `load_skill(name=\"{name}\")` to load the full skill when relevant."
        )
    return lines


def available_skill_context_message(skill_name: str,
                                    skill_def: Dict[str, Any]) -> str:
    """Return the context delta sent when a skill becomes available."""
    summary = _skill_summary(skill_def or {})
    return (
        f"Skill available: {skill_name}\n"
        f"Description: {summary}\n"
        f"Use `load_skill(name=\"{skill_name}\")` to load the full skill when relevant."
    )


def removed_skill_context_message(skill_name: str) -> str:
    """Return the context delta sent when a skill is removed."""
    return (
        f"Skill removed: {skill_name}\n"
        "This skill is no longer available to this agent."
    )


def inject_available_skills_into_prompt(system_prompt: str, skill_entries: List,
                                        user_id: str) -> str:
    """Append only lightweight skill manifests to the provider system prompt."""
    lines = resolve_skill_manifests(skill_entries, user_id)
    if lines:
        system_prompt += "\n\n# Available Skills\n\n" + "\n".join(lines)
    return system_prompt


def _agent_assigned_skill_entry(skill_name: str, user_id: str,
                                conversation_id: str,
                                agent_name: str):
    if not skill_name or not agent_name:
        return None
    from core.resource_store import ResourceStore
    rs = ResourceStore.instance()
    def_name = agent_name
    if conversation_id:
        try:
            from core.conv_agent_config import get_agent_config
            def_name = get_agent_config(conversation_id, agent_name).get("definition") or agent_name
        except Exception:
            def_name = agent_name
    agent_def = rs.get_any("agent", def_name, user_id,
                           conversation_id=conversation_id) or rs.get_any(
                               "agent", def_name, user_id) or {}
    for entry in agent_def.get("assigned_skills") or []:
        name, _params, condition = normalize_skill_entry(entry)
        if name != skill_name:
            continue
        if condition and not _evaluate_condition(condition, user_id):
            return None
        return entry
    return None


def resolve_assigned_skill_prompt(skill_name: str, user_id: str,
                                  conversation_id: str,
                                  agent_name: str) -> str:
    """Resolve a full skill prompt only if assigned to the current agent."""
    entry = _agent_assigned_skill_entry(
        skill_name, user_id, conversation_id, agent_name)
    if not entry:
        return ""
    blocks = resolve_skill_prompts(
        [entry], user_id, conversation_id=conversation_id,
        agent_name=agent_name)
    return blocks[0] if blocks else ""
