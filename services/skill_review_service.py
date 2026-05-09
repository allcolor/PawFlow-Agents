"""Skill review service.

Selects the LLM service used to review untrusted skill content and carries the
policy for when skill create/update/import operations must be reviewed.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core import ServiceError, ServiceFactory
from core.base_service import BaseService

logger = logging.getLogger(__name__)


class SkillReviewService(BaseService):
    """Service selecting the no-tool LLM reviewer for PawFlow skills."""

    TYPE = "skillReview"
    VERSION = "1.0.0"
    NAME = "Skill Review Service"
    DESCRIPTION = "Selects the LLM service and policy used to review skill content"

    def _create_connection(self):
        llm_service = str(self.config.get("llm_service", "") or "")
        if not llm_service:
            raise ServiceError("llm_service is required")
        return {"ready": True, "llm_service": llm_service}

    def _close_connection(self):
        pass

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "llm_service": {
                "type": "service_ref",
                "service_type": "llmConnection",
                "required": True,
                "default": "",
                "description": "LLM service used to review untrusted skill content with no tools",
            },
            "review_on_create": {
                "type": "boolean",
                "default": True,
                "description": "Review skill content before creating a skill",
            },
            "review_on_update": {
                "type": "boolean",
                "default": True,
                "description": "Review skill content before updating a skill",
            },
            "review_on_import": {
                "type": "boolean",
                "default": True,
                "description": "Review normalized external skill packages before import",
            },
            "fail_closed": {
                "type": "boolean",
                "default": True,
                "description": "Block writes when the configured LLM reviewer cannot run",
            },
        }

    def should_review(self, operation: str) -> bool:
        key = {
            "create": "review_on_create",
            "update": "review_on_update",
            "import": "review_on_import",
        }.get(operation, "")
        if not key:
            return True
        return bool(self.config.get(key, True))

    def review_skill(self, skill: Dict[str, Any], *, user_id: str = "",
                     conversation_id: str = "",
                     package_files: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        llm_service = str(self.config.get("llm_service", "") or "")
        if not llm_service:
            raise ServiceError("llm_service is required")
        try:
            from core.skill_review import review_skill
            return review_skill(
                skill,
                reviewer_service_id=llm_service,
                user_id=user_id,
                conversation_id=conversation_id,
                package_files=package_files or {},
            )
        except Exception as exc:
            if bool(self.config.get("fail_closed", True)):
                return {
                    "risk": "block",
                    "allowed": False,
                    "requires_human_review": True,
                    "findings": [{
                        "severity": "block",
                        "category": "reviewer_unavailable",
                        "evidence": llm_service,
                        "reason": f"Configured skill reviewer failed: {exc}",
                    }],
                    "sanitized_summary": "",
                    "recommended_changes": ["Fix or replace the configured skill review LLM service."],
                    "reviewer": self.config.get("llm_service", ""),
                }
            logger.warning("skill review LLM failed; falling back to static review: %s", exc)
            from core.skill_review import static_review_skill
            return static_review_skill(skill, package_files=package_files or {})


ServiceFactory.register(SkillReviewService)
