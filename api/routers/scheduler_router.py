"""Scheduler router — CRON job management."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, Optional

from api.auth import require_permission
from engine.scheduler import FlowScheduler

router = APIRouter()

# Shared scheduler instance
_scheduler = FlowScheduler()


def get_scheduler() -> FlowScheduler:
    return _scheduler


# -- Models --

class JobCreateRequest(BaseModel):
    job_id: str
    flow_path: str
    cron_expression: str
    enabled: bool = True
    parameters: Optional[Dict[str, Any]] = None


class JobUpdateRequest(BaseModel):
    enabled: Optional[bool] = None
    cron_expression: Optional[str] = None
    flow_path: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None


# -- Endpoints --

@router.get("/")
def list_jobs(
    _=Depends(require_permission("monitor.view")),
    scheduler: FlowScheduler = Depends(get_scheduler),
):
    """List all scheduled jobs."""
    return scheduler.get_jobs()


@router.post("/", status_code=201)
def create_job(
    req: JobCreateRequest,
    _=Depends(require_permission("flow.execute")),
    scheduler: FlowScheduler = Depends(get_scheduler),
):
    """Create a new scheduled job."""
    try:
        job = scheduler.add_job(
            req.job_id, req.flow_path, req.cron_expression, req.enabled,
            parameters=req.parameters,
        )
        return {"job_id": req.job_id, **job}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{job_id}")
def get_job(
    job_id: str,
    _=Depends(require_permission("monitor.view")),
    scheduler: FlowScheduler = Depends(get_scheduler),
):
    """Get a specific job."""
    try:
        return scheduler.get_job(job_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.put("/{job_id}")
def update_job(
    job_id: str,
    req: JobUpdateRequest,
    _=Depends(require_permission("flow.execute")),
    scheduler: FlowScheduler = Depends(get_scheduler),
):
    """Update a job (enable/disable, change schedule)."""
    try:
        if req.enabled is not None:
            if req.enabled:
                scheduler.enable_job(job_id)
            else:
                scheduler.disable_job(job_id)
        # For cron/flow_path/parameters changes, recreate the job
        if req.cron_expression or req.flow_path or req.parameters is not None:
            old = scheduler.get_job(job_id)
            scheduler.remove_job(job_id)
            scheduler.add_job(
                job_id,
                req.flow_path or old["flow_path"],
                req.cron_expression or old["cron_expression"],
                req.enabled if req.enabled is not None else old.get("enabled", True),
                parameters=req.parameters if req.parameters is not None else old.get("parameters"),
            )
        return scheduler.get_job(job_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.delete("/{job_id}")
def delete_job(
    job_id: str,
    _=Depends(require_permission("flow.execute")),
    scheduler: FlowScheduler = Depends(get_scheduler),
):
    """Delete a scheduled job."""
    scheduler.remove_job(job_id)
    return {"status": "deleted", "job_id": job_id}


@router.post("/start")
def start_scheduler(
    _=Depends(require_permission("flow.execute")),
    scheduler: FlowScheduler = Depends(get_scheduler),
):
    """Start the scheduler."""
    scheduler.start()
    return {"status": "started"}


@router.post("/stop")
def stop_scheduler(
    _=Depends(require_permission("flow.execute")),
    scheduler: FlowScheduler = Depends(get_scheduler),
):
    """Stop the scheduler."""
    scheduler.stop()
    return {"status": "stopped"}


@router.post("/save")
def save_jobs(
    _=Depends(require_permission("settings.edit")),
    scheduler: FlowScheduler = Depends(get_scheduler),
):
    """Persist jobs to disk."""
    scheduler.save_jobs()
    return {"status": "saved"}


@router.post("/load")
def load_jobs(
    _=Depends(require_permission("settings.edit")),
    scheduler: FlowScheduler = Depends(get_scheduler),
):
    """Load jobs from disk."""
    count = scheduler.load_jobs()
    return {"status": "loaded", "jobs_count": count}
