"""Tasks & Services router — list types, get parameter schemas."""

from fastapi import APIRouter, Depends, HTTPException

from api.auth import require_permission
from core import TaskFactory, ServiceFactory
from core.flow_service import FlowService

router = APIRouter()

_flow_service = FlowService()


def get_flow_service() -> FlowService:
    _flow_service.initialize()
    return _flow_service


@router.get("/")
def list_task_types(
    _=Depends(require_permission("monitor.view")),
    svc: FlowService = Depends(get_flow_service),
):
    """List all available task types."""
    types = svc.get_available_tasks()
    result = []
    for t in types:
        try:
            cls = TaskFactory.get(t)
            result.append({
                "type": t,
                "name": getattr(cls, "NAME", t),
                "version": getattr(cls, "VERSION", "1.0.0"),
                "description": getattr(cls, "DESCRIPTION", ""),
                "icon": getattr(cls, "ICON", ""),
            })
        except Exception:
            result.append({"type": t})
    return result


@router.get("/{task_type}/schema")
def get_task_schema(
    task_type: str,
    _=Depends(require_permission("monitor.view")),
    svc: FlowService = Depends(get_flow_service),
):
    """Get the parameter schema for a task type."""
    schema = svc.get_task_schema(task_type)
    if not schema and task_type not in svc.get_available_tasks():
        raise HTTPException(404, f"Task type '{task_type}' not found")
    return {"type": task_type, "parameters": schema}


@router.get("/services")
def list_service_types(
    _=Depends(require_permission("monitor.view")),
    svc: FlowService = Depends(get_flow_service),
):
    """List all available service types."""
    types = svc.get_available_services()
    result = []
    for t in types:
        try:
            cls = ServiceFactory.get(t)
            result.append({
                "type": t,
                "name": getattr(cls, "NAME", t),
                "version": getattr(cls, "VERSION", "1.0.0"),
                "description": getattr(cls, "DESCRIPTION", ""),
            })
        except Exception:
            result.append({"type": t})
    return result


@router.get("/services/{service_type}/schema")
def get_service_schema(
    service_type: str,
    _=Depends(require_permission("monitor.view")),
    svc: FlowService = Depends(get_flow_service),
):
    """Get the parameter schema for a service type."""
    try:
        cls = ServiceFactory.get(service_type)
        instance = cls({})
        schema = instance.get_parameter_schema()
        return {"type": service_type, "parameters": schema}
    except Exception as e:
        raise HTTPException(404, f"Service type '{service_type}' not found: {e}")
