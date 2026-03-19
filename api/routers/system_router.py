"""System router — health, config, version info, audit log, notifications, metrics."""

import time
from typing import List, Optional
from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from api.auth import require_permission, get_security_manager
from core import __version__, TaskFactory, ServiceFactory
from core.security import SecurityManager
from core.audit import AuditLog
from core.notifications import NotificationManager

router = APIRouter()

# Track start time for uptime metric
_start_time = time.time()

# Cluster support (optional, initialized via init_cluster)
_cluster = None


def init_cluster(config: dict):
    """Initialize cluster mode. Call from app startup if cluster is enabled.

    Args:
        config: dict with keys like host, port, api_port, state_dir, etc.
    """
    global _cluster
    from engine.cluster import ClusterCoordinator
    _cluster = ClusterCoordinator(**config)
    _cluster.start()
    return _cluster


def shutdown_cluster():
    """Shutdown cluster participation. Call from app shutdown."""
    global _cluster
    if _cluster is not None:
        _cluster.stop()
        _cluster = None


@router.get("/health")
def health_check():
    """System health check (no auth required)."""
    return {"status": "healthy", "version": __version__}


@router.get("/info")
def system_info(
    _=Depends(require_permission("monitor.view")),
):
    """Get system information."""
    from tasks import register_all_tasks
    register_all_tasks()

    return {
        "version": __version__,
        "tasks_available": len(TaskFactory.list_types()),
        "services_available": len(ServiceFactory.list_types()),
        "task_types": sorted(TaskFactory.list_types()),
        "service_types": sorted(ServiceFactory.list_types()),
    }


@router.get("/security/status")
def security_status(
    security: SecurityManager = Depends(get_security_manager),
):
    """Get security status (no auth required — tells client if auth is needed)."""
    return {
        "auth_enabled": security.auth_enabled,
        "oauth_providers": security.list_oauth_providers(),
    }


# -- Audit Log --

@router.get("/audit")
def get_audit_log(
    action: Optional[str] = None,
    user: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 100,
    _=Depends(require_permission("admin.audit")),
):
    """Query audit log entries (admin only)."""
    audit = AuditLog.get_instance()
    return audit.query(
        action=action, user=user,
        resource_type=resource_type, resource_id=resource_id,
        since=since, limit=limit,
    )


@router.get("/audit/stats")
def get_audit_stats(
    _=Depends(require_permission("admin.audit")),
):
    """Get audit log statistics."""
    return AuditLog.get_instance().get_stats()


@router.get("/audit/export")
def export_audit(
    _=Depends(require_permission("admin.audit")),
):
    """Export full audit log as JSON."""
    from fastapi.responses import Response
    audit = AuditLog.get_instance()
    return Response(
        content=audit.export_json(),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=audit_log.json"},
    )


# -- Notifications --

class WebhookRequest(BaseModel):
    url: str
    events: Optional[List[str]] = None
    headers: Optional[dict] = None
    name: str = ""


@router.post("/notifications/webhooks")
def register_webhook(
    req: WebhookRequest,
    _=Depends(require_permission("admin.manage")),
):
    """Register a webhook for event notifications."""
    nm = NotificationManager.get_instance()
    webhook_id = nm.register_webhook(
        url=req.url, events=req.events, headers=req.headers, name=req.name,
    )
    return {"id": webhook_id, "status": "registered"}


@router.get("/notifications/webhooks")
def list_webhooks(
    _=Depends(require_permission("admin.manage")),
):
    """List registered webhooks."""
    return NotificationManager.get_instance().list_webhooks()


@router.delete("/notifications/webhooks/{webhook_id}")
def delete_webhook(
    webhook_id: str,
    _=Depends(require_permission("admin.manage")),
):
    """Unregister a webhook."""
    removed = NotificationManager.get_instance().unregister_webhook(webhook_id)
    return {"removed": removed}


@router.get("/notifications/history")
def get_notification_history(
    event_type: Optional[str] = None,
    limit: int = 50,
    _=Depends(require_permission("monitor.view")),
):
    """Get recent notification history."""
    return NotificationManager.get_instance().get_history(event_type=event_type, limit=limit)


@router.get("/notifications/stats")
def get_notification_stats(
    _=Depends(require_permission("monitor.view")),
):
    """Get notification system statistics."""
    return NotificationManager.get_instance().get_stats()


# -- Prometheus-compatible metrics --

@router.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics():
    """Expose metrics in Prometheus text format (no auth for scraping)."""
    from tasks import register_all_tasks
    register_all_tasks()

    lines = []

    def _metric(name, value, help_text="", metric_type="gauge", labels=None):
        if help_text:
            lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {metric_type}")
        if labels:
            label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
            lines.append(f"{name}{{{label_str}}} {value}")
        else:
            lines.append(f"{name} {value}")

    # System info
    _metric("pawflow_info", 1, "PawFlow system info", "gauge",
            labels={"version": __version__})
    _metric("pawflow_uptime_seconds", f"{time.time() - _start_time:.0f}",
            "Seconds since API started")

    # Task/service counts
    _metric("pawflow_tasks_registered_total", len(TaskFactory.list_types()),
            "Number of registered task types")
    _metric("pawflow_services_registered_total", len(ServiceFactory.list_types()),
            "Number of registered service types")

    # Audit log
    try:
        audit = AuditLog.get_instance()
        stats = audit.stats()
        _metric("pawflow_audit_events_total", stats.get("total_events", 0),
                "Total audit events recorded", "counter")
    except Exception:
        pass

    # Notifications
    try:
        nm = NotificationManager.get_instance()
        nm_stats = nm.get_stats()
        _metric("pawflow_notifications_sent_total", nm_stats.get("total_sent", 0),
                "Total notifications sent", "counter")
        _metric("pawflow_webhooks_registered", nm_stats.get("webhooks_count", 0),
                "Number of registered webhooks")
        _metric("pawflow_handlers_registered", nm_stats.get("handlers_count", 0),
                "Number of registered handlers")
    except Exception:
        pass

    # Security
    try:
        sm = get_security_manager()
        _metric("pawflow_active_sessions", len(sm.list_sessions()),
                "Number of active sessions")
        _metric("pawflow_users_total", len(sm.list_users()),
                "Number of registered users")
    except Exception:
        pass

    return "\n".join(lines) + "\n"


# -- Cluster --

@router.get("/cluster/status")
def cluster_status():
    """Get cluster status (no auth — used for discovery)."""
    return {
        "cluster_enabled": _cluster is not None,
        "status": _cluster.get_status() if _cluster else {"message": "Cluster mode not enabled"},
    }


@router.get("/cluster/instances")
def cluster_instances(
    _=Depends(require_permission("monitor.view")),
):
    """List cluster instances."""
    if _cluster is None:
        return []
    return _cluster.get_instances()


@router.post("/cluster/promote")
def cluster_promote(
    _=Depends(require_permission("admin.manage")),
):
    """Promote this instance to coordinator."""
    if _cluster is None:
        return {"error": "Cluster mode not enabled"}
    success = _cluster.promote_to_coordinator()
    return {"promoted": success, "role": _cluster.role.value}


@router.post("/cluster/step-down")
def cluster_step_down(
    _=Depends(require_permission("admin.manage")),
):
    """Step down from coordinator role."""
    if _cluster is None:
        return {"error": "Cluster mode not enabled"}
    _cluster.step_down()
    return {"role": _cluster.role.value}
