"""Review helpers backed by the conversation summarizer service."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional


def review_for_write(subject: Dict[str, Any], *, operation: str,
                     user_id: str = "", conversation_id: str = "",
                     package_files: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Review skill-like content before create/update/import and return metadata."""
    from core.package_review import (
        assert_installable_review, review_hash, review_metadata,
        review_skill_content,
    )
    review = review_skill_content(
        subject,
        operation=operation,
        user_id=user_id,
        conversation_id=conversation_id,
        package_files=package_files or {},
    )
    assert_installable_review(review, force=True, label="Skill")
    meta = review_metadata(
        review,
        service_id=review.get("service_id", ""),
        llm_service=review.get("llm_service", ""),
        subject_hash=review_hash(subject, package_files),
    )
    meta.setdefault("reviewed_at", time.time())
    return meta


def review_now(subject: Dict[str, Any], *, operation: str = "review",
               user_id: str = "", conversation_id: str = "",
               package_files: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    from core.package_review import review_skill_content
    return review_skill_content(
        subject,
        operation=operation,
        user_id=user_id,
        conversation_id=conversation_id,
        package_files=package_files or {},
    )


def attach_review_metadata(subject: Dict[str, Any], review_metadata: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(subject)
    data["review"] = review_metadata
    return data
