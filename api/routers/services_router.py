"""Services API - unified CRUD for global and user services.

All scopes share the same interface. The scope determines storage
and visibility:
    global - shared across all users (admin)
    user   - per-user (owner only)

Routes:
    GET    /llm-profiles                - list LLM provider presets
    GET    /{scope}                     - list services
    GET    /{scope}/{service_id}        - get a service
    POST   /{scope}                     - create a service
    PUT    /{scope}/{service_id}        - update a service
    DELETE /{scope}/{service_id}        - delete a service
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel

from api.auth import get_current_session

logger = logging.getLogger(__name__)

router = APIRouter()

_VALID_SCOPES = ("global", "user")


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


def _sdef_to_dict(sdef) -> dict:
    return {
        "service_id": sdef.service_id,
        "service_type": sdef.service_type,
        "config": sdef.config,
        "description": sdef.description,
        "enabled": sdef.enabled,
    }


def _get_registry(scope: str, user_id: str = ""):
    """Return (registry, kwargs) for the given scope.

    kwargs contains the extra arguments needed for user-scoped calls
    (user_id). Global calls need no extra args.
    """
    if scope == "global":
        from gui.services.global_service_registry import GlobalServiceRegistry
        return GlobalServiceRegistry.get_instance(), {}
    elif scope == "user":
        from gui.services.user_service_registry import UserServiceRegistry
        return UserServiceRegistry.get_instance(), {"user_id": user_id}
    raise HTTPException(
        status_code=400,
        detail=f"Invalid scope '{scope}'. Use: {', '.join(_VALID_SCOPES)}")


def _registry_call(reg, method: str, kwargs: dict, **extra):
    """Call a registry method, merging scope kwargs with extra args."""
    return getattr(reg, method)(**kwargs, **extra)


# -- LLM Profiles --

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


# -- Scoped CRUD --

@router.get("/{scope}")
def list_services(scope: str = Path(...), session=Depends(get_current_session)):
    """List all services in the given scope."""
    reg, kw = _get_registry(scope, session.username)
    if scope == "global":
        defs = reg.get_all_definitions()
    else:
        defs = reg.get_all_for_user(session.username)
    return {"services": [_sdef_to_dict(s) for s in defs.values()]}


@router.get("/{scope}/{service_id}")
def get_service(scope: str, service_id: str, session=Depends(get_current_session)):
    """Get a specific service definition."""
    reg, kw = _get_registry(scope, session.username)
    sdef = _registry_call(reg, "get_definition", kw, service_id=service_id)
    if not sdef:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' not found")
    return _sdef_to_dict(sdef)


@router.post("/{scope}", status_code=201)
def create_service(scope: str, req: ServiceCreateRequest,
                   session=Depends(get_current_session)):
    """Create a new service."""
    reg, kw = _get_registry(scope, session.username)
    try:
        sdef = _registry_call(reg, "install", kw,
                              service_id=req.service_id,
                              service_type=req.service_type,
                              config=req.config,
                              description=req.description,
                              enabled=req.enabled)
        return _sdef_to_dict(sdef)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{scope}/{service_id}")
def update_service(scope: str, service_id: str, req: ServiceUpdateRequest,
                   session=Depends(get_current_session)):
    """Update a service configuration."""
    reg, kw = _get_registry(scope, session.username)
    sdef = _registry_call(reg, "get_definition", kw, service_id=service_id)
    if not sdef:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' not found")
    if req.config is not None:
        _registry_call(reg, "update_config", kw,
                       service_id=service_id, config=req.config)
    if req.enabled is not None:
        method = "enable" if req.enabled else "disable"
        _registry_call(reg, method, kw, service_id=service_id)
    return {"ok": True}


@router.delete("/{scope}/{service_id}")
def delete_service(scope: str, service_id: str,
                   session=Depends(get_current_session)):
    """Delete a service."""
    reg, kw = _get_registry(scope, session.username)
    sdef = _registry_call(reg, "get_definition", kw, service_id=service_id)
    if not sdef:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' not found")
    _registry_call(reg, "uninstall", kw, service_id=service_id)
    return {"ok": True, "deleted": service_id}
