"""Monitoring router — bulletins, provenance, streaming stats."""

from fastapi import APIRouter, Depends, Query
from typing import Optional

from api.auth import require_permission
from core.bulletin import BulletinBoard
from engine.provenance import get_provenance_repository, ProvenanceEventType
from core.stream import get_spill_tracker

router = APIRouter()


# -- Bulletin Board --

@router.get("/bulletins")
def get_bulletins(
    limit: int = Query(100, ge=1, le=1000),
    level: Optional[str] = Query(None, pattern="^(INFO|WARNING|ERROR)$"),
    _=Depends(require_permission("monitor.view")),
):
    """Get bulletin board messages."""
    bb = BulletinBoard.get_instance()
    return bb.get_messages(limit=limit, level=level)


@router.get("/bulletins/counts")
def get_bulletin_counts(
    _=Depends(require_permission("monitor.view")),
):
    """Get bulletin counts by level."""
    bb = BulletinBoard.get_instance()
    return bb.count_by_level()


@router.delete("/bulletins")
def clear_bulletins(
    _=Depends(require_permission("monitor.clear")),
):
    """Clear all bulletins."""
    bb = BulletinBoard.get_instance()
    bb.clear()
    return {"status": "cleared"}


# -- Provenance --

@router.get("/provenance")
def get_provenance_events(
    flowfile_id: Optional[str] = None,
    task_id: Optional[str] = None,
    event_type: Optional[str] = None,
    flow_id: Optional[str] = None,
    limit: int = Query(100, ge=1, le=10000),
    _=Depends(require_permission("monitor.view")),
):
    """Query provenance events with filters."""
    repo = get_provenance_repository()

    evt_type = None
    if event_type:
        try:
            evt_type = ProvenanceEventType(event_type)
        except ValueError:
            pass

    events = repo.get_events(
        flowfile_id=flowfile_id,
        task_id=task_id,
        event_type=evt_type,
        flow_id=flow_id,
        limit=limit,
    )
    return [e.to_dict() for e in events]


@router.get("/provenance/lineage/{flowfile_id}")
def get_lineage(
    flowfile_id: str,
    _=Depends(require_permission("monitor.view")),
):
    """Get full lineage for a FlowFile."""
    repo = get_provenance_repository()
    events = repo.get_lineage(flowfile_id)
    return [e.to_dict() for e in events]


@router.get("/provenance/stats")
def get_provenance_stats(
    _=Depends(require_permission("monitor.view")),
):
    """Get provenance statistics."""
    repo = get_provenance_repository()
    return repo.to_dict()


@router.delete("/provenance")
def clear_provenance(
    _=Depends(require_permission("monitor.clear")),
):
    """Clear all provenance events."""
    repo = get_provenance_repository()
    repo.clear()
    return {"status": "cleared"}


# -- Streaming / Memory --

@router.get("/streaming")
def get_streaming_stats(
    _=Depends(require_permission("monitor.view")),
):
    """Get SpillTracker and streaming memory stats."""
    tracker = get_spill_tracker()
    return tracker.get_stats()
