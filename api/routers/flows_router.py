"""Flows router — CRUD, validate, import/export flows + templates."""

import json
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import Optional, Dict, Any, List

from api.auth import require_permission
from core.flow_service import FlowService
from core.template_service import TemplateService

router = APIRouter()

# Shared service instances
_flow_service = FlowService()
_template_service = TemplateService()


def get_flow_service() -> FlowService:
    _flow_service.initialize()
    return _flow_service


def get_template_service() -> TemplateService:
    return _template_service


# -- Models --

class FlowCreateRequest(BaseModel):
    """Create or update a flow from JSON config."""
    config: Dict[str, Any]
    filepath: Optional[str] = None


class TemplateSaveRequest(BaseModel):
    """Save a flow as a template."""
    name: str
    flow_config: Dict[str, Any]
    category: str = "Custom"
    description: str = ""
    tags: List[str] = []
    difficulty: str = "intermediate"
    required_services: List[str] = []


# -- Endpoints --

@router.get("/")
def list_flows(
    _=Depends(require_permission("monitor.view")),
    svc: FlowService = Depends(get_flow_service),
):
    """List all available flows."""
    flow_files = svc.list_flows()
    results = []
    for fp in flow_files:
        try:
            flow = svc.parse_from_file(fp)
            results.append({
                "filepath": fp,
                "id": flow.id,
                "name": flow.name,
                "version": flow.version,
                "description": flow.description,
                "author": flow.author,
                "tasks_count": len(flow.tasks),
                "relations_count": len(flow.relations),
            })
        except Exception as e:
            results.append({"filepath": fp, "error": str(e)})
    return results


@router.get("/{flow_id}")
def get_flow(
    flow_id: str,
    _=Depends(require_permission("monitor.view")),
    svc: FlowService = Depends(get_flow_service),
):
    """Get a flow's full configuration."""
    flow_files = svc.list_flows()
    for fp in flow_files:
        try:
            flow = svc.parse_from_file(fp)
            if flow.id == flow_id:
                return svc.flow_to_dict(flow)
        except Exception:
            continue
    raise HTTPException(404, f"Flow '{flow_id}' not found")


@router.post("/", status_code=201)
def create_flow(
    req: FlowCreateRequest,
    _=Depends(require_permission("flow.create")),
    svc: FlowService = Depends(get_flow_service),
):
    """Create a new flow from JSON config."""
    try:
        flow = svc.parse(req.config)
        filepath = svc.save(flow, req.filepath)
        return {"id": flow.id, "name": flow.name, "filepath": filepath}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.put("/{flow_id}")
def update_flow(
    flow_id: str,
    req: FlowCreateRequest,
    _=Depends(require_permission("flow.edit")),
    svc: FlowService = Depends(get_flow_service),
):
    """Update an existing flow."""
    # Find existing file
    flow_files = svc.list_flows()
    target_path = None
    for fp in flow_files:
        try:
            flow = svc.parse_from_file(fp)
            if flow.id == flow_id:
                target_path = fp
                break
        except Exception:
            continue

    if not target_path:
        raise HTTPException(404, f"Flow '{flow_id}' not found")

    try:
        req.config["id"] = flow_id
        flow = svc.parse(req.config)
        filepath = svc.save(flow, target_path)
        return {"id": flow.id, "name": flow.name, "filepath": filepath}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.delete("/{flow_id}")
def delete_flow(
    flow_id: str,
    _=Depends(require_permission("flow.delete")),
    svc: FlowService = Depends(get_flow_service),
):
    """Delete a flow."""
    flow_files = svc.list_flows()
    for fp in flow_files:
        try:
            flow = svc.parse_from_file(fp)
            if flow.id == flow_id:
                svc.delete(fp)
                return {"status": "deleted", "id": flow_id}
        except Exception:
            continue
    raise HTTPException(404, f"Flow '{flow_id}' not found")


@router.post("/{flow_id}/validate")
def validate_flow(
    flow_id: str,
    _=Depends(require_permission("monitor.view")),
    svc: FlowService = Depends(get_flow_service),
):
    """Validate a flow."""
    flow_files = svc.list_flows()
    for fp in flow_files:
        try:
            flow = svc.parse_from_file(fp)
            if flow.id == flow_id:
                errors = svc.validate(flow)
                return {
                    "valid": len(errors) == 0,
                    "errors": errors,
                }
        except Exception as e:
            raise HTTPException(400, str(e))
    raise HTTPException(404, f"Flow '{flow_id}' not found")


@router.post("/validate")
def validate_flow_config(
    req: FlowCreateRequest,
    _=Depends(require_permission("monitor.view")),
    svc: FlowService = Depends(get_flow_service),
):
    """Validate a flow config without saving."""
    try:
        flow = svc.parse(req.config)
        errors = svc.validate(flow)
        return {"valid": len(errors) == 0, "errors": errors}
    except Exception as e:
        return {"valid": False, "errors": [str(e)]}


@router.post("/import")
def import_flow(
    file: UploadFile = File(...),
    _=Depends(require_permission("flow.import")),
    svc: FlowService = Depends(get_flow_service),
):
    """Import a flow from a JSON file upload."""
    try:
        content = file.file.read()
        config = json.loads(content)
        flow = svc.parse(config)
        filepath = svc.save(flow)
        return {"id": flow.id, "name": flow.name, "filepath": filepath}
    except Exception as e:
        raise HTTPException(400, f"Import failed: {e}")


@router.post("/diff")
def diff_flows(
    body: dict,
    _=Depends(require_permission("monitor.view")),
):
    """Compare two flow configurations."""
    from engine.flow_diff import FlowDiff
    old_flow = body.get("old", {})
    new_flow = body.get("new", {})
    diff = FlowDiff.compare(old_flow, new_flow)
    return diff.to_dict()


@router.get("/{flow_id}/export")
def export_flow(
    flow_id: str,
    _=Depends(require_permission("flow.export")),
    svc: FlowService = Depends(get_flow_service),
):
    """Export a flow as JSON config."""
    flow_files = svc.list_flows()
    for fp in flow_files:
        try:
            flow = svc.parse_from_file(fp)
            if flow.id == flow_id:
                return svc.flow_to_dict(flow)
        except Exception:
            continue
    raise HTTPException(404, f"Flow '{flow_id}' not found")


# ============================================================================
# Template endpoints
# ============================================================================

@router.get("/templates")
def list_templates(
    category: Optional[str] = None,
    search: Optional[str] = None,
    svc: TemplateService = Depends(get_template_service),
):
    """List available flow templates, optionally filtered by category or search query."""
    if search:
        return svc.search_templates(search)
    return svc.list_templates(category=category)


@router.get("/templates/{template_id}")
def get_template(
    template_id: str,
    svc: TemplateService = Depends(get_template_service),
):
    """Get a specific template by ID."""
    template = svc.get_template(template_id)
    if template is None:
        raise HTTPException(404, f"Template '{template_id}' not found")
    return template


@router.post("/templates", status_code=201)
def save_template(
    req: TemplateSaveRequest,
    _=Depends(require_permission("flow.create")),
    svc: TemplateService = Depends(get_template_service),
):
    """Save current flow as a template."""
    try:
        filepath = svc.save_as_template(
            flow_dict=req.flow_config,
            name=req.name,
            description=req.description,
            category=req.category,
            tags=req.tags,
            difficulty=req.difficulty,
            required_services=req.required_services,
        )
        return {"status": "saved", "filepath": filepath}
    except Exception as e:
        raise HTTPException(400, str(e))
