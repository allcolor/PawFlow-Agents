"""Services API — unified CRUD for global, user, and conv services.

All scopes share the same interface. The scope determines storage
and visibility:
    global - shared across all users (admin)
    user   - per-user (owner only)
    conv   - per-conversation (participants only)

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

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel

from api.auth import get_current_session
from core.service_registry import (
    ServiceRegistry, SCOPE_GLOBAL, SCOPE_USER, SCOPE_CONV, VALID_SCOPES,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class ServiceCreateRequest(BaseModel):
    service_id: str
    service_type: str
    config: Dict[str, Any] = {}
    description: str = ""
    enabled: bool = True
    conversation_id: Optional[str] = None


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
        "scope": sdef.scope,
    }


def _resolve_scope_id(scope: str, session, conversation_id: Optional[str] = None) -> str:
    """Resolve scope_id from scope + session."""
    if scope == SCOPE_GLOBAL:
        return ""  # ServiceRegistry normalizes this
    elif scope == SCOPE_USER:
        return session.username
    elif scope == SCOPE_CONV:
        if not conversation_id:
            raise HTTPException(status_code=400,
                                detail="conversation_id required for conv scope")
        return conversation_id
    raise HTTPException(status_code=400,
                        detail=f"Invalid scope '{scope}'. Use: {', '.join(VALID_SCOPES)}")


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
def list_services(scope: str = Path(...),
                  conversation_id: Optional[str] = Query(None),
                  session=Depends(get_current_session)):
    """List all services in the given scope."""
    scope_id = _resolve_scope_id(scope, session, conversation_id)
    reg = ServiceRegistry.get_instance()
    defs = reg.get_all(scope, scope_id)
    return {"services": [_sdef_to_dict(s) for s in defs.values()]}


@router.get("/{scope}/{service_id}")
def get_service(scope: str, service_id: str,
                conversation_id: Optional[str] = Query(None),
                session=Depends(get_current_session)):
    """Get a specific service definition."""
    scope_id = _resolve_scope_id(scope, session, conversation_id)
    reg = ServiceRegistry.get_instance()
    sdef = reg.get_definition(scope, scope_id, service_id)
    if not sdef:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' not found")
    return _sdef_to_dict(sdef)


@router.post("/{scope}", status_code=201)
def create_service(scope: str, req: ServiceCreateRequest,
                   session=Depends(get_current_session)):
    """Create a new service."""
    scope_id = _resolve_scope_id(scope, session, req.conversation_id)
    reg = ServiceRegistry.get_instance()
    try:
        sdef = reg.install(scope, scope_id,
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
                   conversation_id: Optional[str] = Query(None),
                   session=Depends(get_current_session)):
    """Update a service configuration."""
    scope_id = _resolve_scope_id(scope, session, conversation_id)
    reg = ServiceRegistry.get_instance()
    sdef = reg.get_definition(scope, scope_id, service_id)
    if not sdef:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' not found")
    if req.config is not None:
        reg.update_config(scope, scope_id, service_id, req.config)
    if req.enabled is not None:
        if req.enabled:
            reg.enable(scope, scope_id, service_id)
        else:
            reg.disable(scope, scope_id, service_id)
    return {"ok": True}


@router.delete("/{scope}/{service_id}")
def delete_service(scope: str, service_id: str,
                   conversation_id: Optional[str] = Query(None),
                   session=Depends(get_current_session)):
    """Delete a service."""
    scope_id = _resolve_scope_id(scope, session, conversation_id)
    reg = ServiceRegistry.get_instance()
    sdef = reg.get_definition(scope, scope_id, service_id)
    if not sdef:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' not found")
    reg.uninstall(scope, scope_id, service_id)
    return {"ok": True, "deleted": service_id}
