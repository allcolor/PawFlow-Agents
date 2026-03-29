"""User Services API — CRUD for per-user service definitions."""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from api.auth import get_current_session

logger = logging.getLogger(__name__)

router = APIRouter()


class ServiceCreateRequest(BaseModel):
    service_id: str
    service_type: str
    config: Dict[str, Any] = {}
    description: str = ""
    enabled: bool = True


class ServiceUpdateRequest(BaseModel):
    config: Optional[Dict[str, Any]] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None


@router.get("/llm-profiles")
def list_llm_profiles():
    """List available LLM profiles (provider presets)."""
    from core.llm_profiles import load_profiles
    profiles = load_profiles()
    return {
        "profiles": [
            {
                "name": name,
                "provider": p.get("provider", ""),
                "base_url": p.get("base_url", ""),
                "default_model": p.get("default_model", ""),
                "description": p.get("description", ""),
                "requires_api_key": p.get("requires_api_key", True),
                "models": p.get("models", []),
            }
            for name, p in profiles.items()
        ]
    }


@router.get("")
def list_user_services(session=Depends(get_current_session)):
    """List all services for the authenticated user."""
    from gui.services.user_service_registry import UserServiceRegistry
    ureg = UserServiceRegistry.get_instance()
    user_id = session.username
    defs = ureg.get_all_for_user(user_id)
    return {
        "services": [
            {
                "service_id": sdef.service_id,
                "service_type": sdef.service_type,
                "config": sdef.config,
                "description": sdef.description,
                "enabled": sdef.enabled,
            }
            for sdef in defs.values()
        ]
    }


@router.get("/{service_id}")
def get_user_service(service_id: str, session=Depends(get_current_session)):
    """Get a specific user service definition."""
    from gui.services.user_service_registry import UserServiceRegistry
    ureg = UserServiceRegistry.get_instance()
    user_id = session.username
    sdef = ureg.get_definition(user_id, service_id)
    if not sdef:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' not found")
    return {
        "service_id": sdef.service_id,
        "service_type": sdef.service_type,
        "config": sdef.config,
        "description": sdef.description,
        "enabled": sdef.enabled,
    }


@router.post("", status_code=201)
def create_user_service(req: ServiceCreateRequest, session=Depends(get_current_session)):
    """Create a new user service."""
    from gui.services.user_service_registry import UserServiceRegistry
    ureg = UserServiceRegistry.get_instance()
    user_id = session.username
    try:
        sdef = ureg.install(
            user_id=user_id,
            service_id=req.service_id,
            service_type=req.service_type,
            config=req.config,
            description=req.description,
            enabled=req.enabled,
        )
        return {
            "service_id": sdef.service_id,
            "service_type": sdef.service_type,
            "enabled": sdef.enabled,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{service_id}")
def update_user_service(service_id: str, req: ServiceUpdateRequest, session=Depends(get_current_session)):
    """Update a user service configuration."""
    from gui.services.user_service_registry import UserServiceRegistry
    ureg = UserServiceRegistry.get_instance()
    user_id = session.username
    sdef = ureg.get_definition(user_id, service_id)
    if not sdef:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' not found")
    if req.config is not None:
        ureg.update_config(user_id, service_id, req.config)
    if req.enabled is not None:
        if req.enabled:
            ureg.enable(user_id, service_id)
        else:
            ureg.disable(user_id, service_id)
    return {"ok": True}


@router.delete("/{service_id}")
def delete_user_service(service_id: str, session=Depends(get_current_session)):
    """Delete a user service."""
    from gui.services.user_service_registry import UserServiceRegistry
    ureg = UserServiceRegistry.get_instance()
    user_id = session.username
    sdef = ureg.get_definition(user_id, service_id)
    if not sdef:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' not found")
    ureg.uninstall(user_id, service_id)
    return {"ok": True, "deleted": service_id}
