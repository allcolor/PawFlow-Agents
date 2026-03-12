"""Triggers router -- manage event triggers."""

from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import require_permission
from engine.triggers import TriggerManager, TriggerType

router = APIRouter()

_trigger_manager: Optional[TriggerManager] = None


def _get_manager() -> TriggerManager:
    global _trigger_manager
    if _trigger_manager is None:
        _trigger_manager = TriggerManager()
    return _trigger_manager


class TriggerCreateRequest(BaseModel):
    trigger_id: str
    trigger_type: str  # file_watcher, webhook, event, polling
    flow_path: str
    name: str = ""
    config: Dict[str, Any] = {}
    parameters: Dict[str, Any] = {}
    enabled: bool = True


@router.get("/")
def list_triggers(_=Depends(require_permission("monitor.view"))):
    return _get_manager().list_triggers()


@router.post("/", status_code=201)
def create_trigger(req: TriggerCreateRequest, _=Depends(require_permission("flow.execute"))):
    tm = _get_manager()
    return tm.create_trigger(
        trigger_id=req.trigger_id,
        trigger_type=TriggerType(req.trigger_type),
        flow_path=req.flow_path,
        name=req.name,
        config=req.config,
        parameters=req.parameters,
        enabled=req.enabled,
    )


@router.get("/history/all")
def all_trigger_history(limit: int = 50, _=Depends(require_permission("monitor.view"))):
    return _get_manager().get_history(limit=limit)


@router.get("/{trigger_id}")
def get_trigger(trigger_id: str, _=Depends(require_permission("monitor.view"))):
    result = _get_manager().get_trigger(trigger_id)
    if not result:
        raise HTTPException(404, f"Trigger '{trigger_id}' not found")
    return result


@router.delete("/{trigger_id}")
def delete_trigger(trigger_id: str, _=Depends(require_permission("flow.execute"))):
    removed = _get_manager().delete_trigger(trigger_id)
    return {"removed": removed}


@router.post("/{trigger_id}/start")
def start_trigger(trigger_id: str, _=Depends(require_permission("flow.execute"))):
    return _get_manager().start_trigger(trigger_id)


@router.post("/{trigger_id}/stop")
def stop_trigger(trigger_id: str, _=Depends(require_permission("flow.execute"))):
    return _get_manager().stop_trigger(trigger_id)


@router.post("/{trigger_id}/pause")
def pause_trigger(trigger_id: str, _=Depends(require_permission("flow.execute"))):
    return _get_manager().pause_trigger(trigger_id)


@router.post("/{trigger_id}/resume")
def resume_trigger(trigger_id: str, _=Depends(require_permission("flow.execute"))):
    return _get_manager().resume_trigger(trigger_id)


@router.get("/{trigger_id}/history")
def trigger_history(trigger_id: str, limit: int = 50, _=Depends(require_permission("monitor.view"))):
    return _get_manager().get_history(trigger_id=trigger_id, limit=limit)
