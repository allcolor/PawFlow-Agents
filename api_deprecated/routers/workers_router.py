"""Workers router — manage remote workers, health, status."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, List

from api.auth import require_permission
from engine.remote_worker import WorkerCoordinator

router = APIRouter()

# Shared coordinator instance
_coordinator: Optional[WorkerCoordinator] = None


def get_coordinator() -> WorkerCoordinator:
    global _coordinator
    if _coordinator is None:
        _coordinator = WorkerCoordinator()
    return _coordinator


# -- Models --

class RegisterWorkerRequest(BaseModel):
    name: str
    host: str = "localhost"
    port: int = 9000
    capabilities: List[str] = []
    labels: Dict[str, str] = {}
    max_concurrent: int = 4


# -- Endpoints --

@router.get("/")
def list_workers(
    _=Depends(require_permission("monitor.view")),
    coord: WorkerCoordinator = Depends(get_coordinator),
):
    """List all registered workers."""
    return [w.to_dict() for w in coord._workers.values()]


@router.post("/", status_code=201)
def register_worker(
    req: RegisterWorkerRequest,
    _=Depends(require_permission("worker.manage")),
    coord: WorkerCoordinator = Depends(get_coordinator),
):
    """Register a new remote worker."""
    worker = coord.register_worker(
        name=req.name,
        host=req.host,
        port=req.port,
        capabilities=req.capabilities,
        labels=req.labels,
        max_concurrent=req.max_concurrent,
    )
    return worker.to_dict()


@router.delete("/{worker_id}")
def unregister_worker(
    worker_id: str,
    _=Depends(require_permission("worker.manage")),
    coord: WorkerCoordinator = Depends(get_coordinator),
):
    """Unregister a worker."""
    coord.unregister_worker(worker_id)
    return {"status": "unregistered", "worker_id": worker_id}


@router.post("/{worker_id}/reset")
def reset_worker(
    worker_id: str,
    _=Depends(require_permission("worker.manage")),
    coord: WorkerCoordinator = Depends(get_coordinator),
):
    """Reset a worker (clear error state)."""
    try:
        coord.reset_worker(worker_id)
        return {"status": "reset", "worker_id": worker_id}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/health")
def get_health_summary(
    _=Depends(require_permission("monitor.view")),
    coord: WorkerCoordinator = Depends(get_coordinator),
):
    """Get worker health summary."""
    return coord.get_health_summary()
