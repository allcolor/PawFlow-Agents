"""Skill Curator Task — background maintenance report for agent skills.

Reads the skill repository and the ``load_skill`` usage statistics
(``core/skill_stats.py``), flags never-loaded and stale skills, optionally
asks an LLM to review the flagged ones (overlap, obsolescence), and emits
a JSON report with proposed actions (keep / archive / merge).

The task NEVER applies an action. The report is the deliverable; the user
applies changes through the normal resource UI or ``manage_resource``.
Schedule it with a cron trigger for a Hermes-style curator loop that
stays review-first.
"""

import json
import logging
import re
import time
from typing import Any, Dict, List

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)

_REVIEW_PROMPT = """You are curating an agent-skill library. For each flagged skill below, decide: "keep" (still useful), "archive" (obsolete or superseded), or "merge" (overlaps another listed skill — name it in reason).

Return a JSON array: [{"name": "...", "action": "keep|archive|merge", "reason": "one line"}]

Flagged skills:
{flagged}

All skills in the library (for overlap checks):
{all_skills}
"""


class SkillCuratorTask(BaseTask):
    """Produce a curation report for a user's skill library."""

    TYPE = "skillCurator"
    VERSION = "1.0.0"
    NAME = "Skill Curator"
    DESCRIPTION = "Flag stale/unused skills and propose curation actions (report only)"
    ICON = "ai"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "user_id": {
                "type": "string", "required": True,
                "description": "User whose skill library is curated",
            },
            "stale_days": {
                "type": "integer", "required": False, "default": 90,
                "description": "Days without a load before a skill is flagged stale",
            },
            "include_global": {
                "type": "boolean", "required": False, "default": False,
                "description": "Also flag global-scope skills (report only)",
            },
            "provider": {
                "type": "string", "required": False, "default": "",
                "description": "Optional LLM provider for the review pass (openai, anthropic); empty = heuristic report only",
            },
            "api_key": {
                "type": "string", "required": False, "sensitive": True,
                "description": "API key for the review LLM",
            },
            "base_url": {
                "type": "string", "required": False, "default": "",
                "description": "API base URL (for self-hosted or compatible APIs)",
            },
            "model": {
                "type": "string", "required": False, "default": "",
                "description": "Model name (empty = provider default)",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        user_id = str(self.config.get("user_id", "")).strip()
        if not user_id:
            flowfile.set_attribute("skill.curator.error", "user_id is required")
            return [flowfile]
        stale_days = int(self.config.get("stale_days", 90) or 90)
        include_global = bool(self.config.get("include_global", False))

        skills = self._list_skills(user_id, include_global)
        stats = self._stats(user_id)
        now = time.time()
        stale_cutoff = now - stale_days * 86400

        flagged: List[Dict[str, Any]] = []
        entries: List[Dict[str, Any]] = []
        for s in skills:
            name = s["name"]
            st = stats.get(name) or {}
            loads = int(st.get("loads", 0))
            last = float(st.get("last_used_at", 0) or 0)
            if loads == 0:
                status = "never_loaded"
            elif last < stale_cutoff:
                status = "stale"
            else:
                status = "active"
            entry = {
                "name": name,
                "scope": s["scope"],
                "description": s["description"],
                "loads": loads,
                "last_used_at": last,
                "status": status,
            }
            entries.append(entry)
            if status != "active":
                flagged.append(entry)

        reviews = self._llm_review(flagged, entries) if flagged else []
        review_by_name = {r["name"]: r for r in reviews}

        proposed = []
        for entry in flagged:
            review = review_by_name.get(entry["name"])
            proposed.append({
                "name": entry["name"],
                "scope": entry["scope"],
                "status": entry["status"],
                "action": (review or {}).get("action", "review"),
                "reason": (review or {}).get(
                    "reason",
                    f"{entry['status']} for over {stale_days} days"
                    if entry["status"] == "stale" else "never loaded"),
            })

        report = {
            "user_id": user_id,
            "generated_at": now,
            "stale_days": stale_days,
            "total_skills": len(entries),
            "active": sum(1 for e in entries if e["status"] == "active"),
            "flagged": len(flagged),
            "llm_reviewed": bool(reviews),
            "skills": entries,
            "proposed_actions": proposed,
            "note": ("No action has been applied. Apply changes via the "
                     "resource UI or manage_resource after review."),
        }
        flowfile.set_content(json.dumps(report, indent=1).encode("utf-8"))
        flowfile.set_attribute("mime.type", "application/json")
        flowfile.set_attribute("skill.curator.total", str(len(entries)))
        flowfile.set_attribute("skill.curator.flagged", str(len(flagged)))
        return [flowfile]

    def _list_skills(self, user_id: str,
                     include_global: bool) -> List[Dict[str, Any]]:
        from core.resource_store import ResourceStore
        items = ResourceStore.instance().list_all("skill", user_id) or []
        out = []
        for item in items:
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            scope = str(item.get("_scope", "") or "user")
            if scope == "global" and not include_global:
                continue
            out.append({
                "name": name,
                "scope": scope,
                "description":
                    str(item.get("description", "") or "").strip()[:200],
            })
        return out

    def _stats(self, user_id: str) -> Dict[str, Dict[str, Any]]:
        try:
            from core.skill_stats import stats_for_user
            return stats_for_user(user_id)
        except Exception:
            logger.debug("[skill-curator] stats load failed", exc_info=True)
            return {}

    def _llm_review(self, flagged: List[Dict[str, Any]],
                    all_entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        provider = str(self.config.get("provider", "")).strip()
        api_key = str(self.config.get("api_key", "") or "")
        if not provider:
            return []
        try:
            from services.llm_connection import LLMConnectionService, LLMMessage
            svc_config: Dict[str, Any] = {
                "provider": provider, "api_key": api_key, "timeout": 60,
            }
            if self.config.get("base_url"):
                svc_config["base_url"] = self.config["base_url"]
            model = str(self.config.get("model", "") or "")
            if model:
                svc_config["default_model"] = model
            svc = LLMConnectionService(svc_config)
            svc.connect()
            try:
                def _lines(items):
                    return "\n".join(
                        f"- {e['name']} [{e['status']}, {e['loads']} loads]: "
                        f"{e['description']}" for e in items) or "(none)"
                prompt = _REVIEW_PROMPT.format(
                    flagged=_lines(flagged), all_skills=_lines(all_entries))
                _cid = f"_skill_curator:{self.config.get('_service_id', 'curator')}"
                resp = svc.complete(
                    messages=[LLMMessage(role="user", content=prompt,
                                         conversation_id=_cid)],
                    model=model or None,
                    temperature=0.2,
                    max_tokens=2000,
                    response_format="json",
                )
                match = re.search(r"\[.*\]", resp.content or "", re.DOTALL)
                if not match:
                    return []
                data = json.loads(match.group())
                valid = {"keep", "archive", "merge"}
                return [
                    {"name": str(r.get("name", "")).strip(),
                     "action": str(r.get("action", "")).strip().lower(),
                     "reason": str(r.get("reason", "")).strip()[:200]}
                    for r in data
                    if isinstance(r, dict)
                    and str(r.get("action", "")).strip().lower() in valid
                ]
            finally:
                svc.disconnect()
        except Exception:
            logger.debug("[skill-curator] LLM review failed", exc_info=True)
            return []


TaskFactory.register(SkillCuratorTask)
