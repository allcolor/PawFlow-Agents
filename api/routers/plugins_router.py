"""Plugins router — install, uninstall, list, export plugins."""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any
import tempfile
import os

from api.auth import require_permission
from core.plugin import PluginManager, export_flow_as_plugin

router = APIRouter()

_plugin_manager: Optional[PluginManager] = None


def get_plugin_manager() -> PluginManager:
    global _plugin_manager
    if _plugin_manager is None:
        _plugin_manager = PluginManager()
    return _plugin_manager


# -- Endpoints --

@router.get("/")
def list_plugins(
    _=Depends(require_permission("monitor.view")),
    pm: PluginManager = Depends(get_plugin_manager),
):
    """List installed plugins."""
    return pm.list_plugins()  # returns List[Dict]


@router.post("/install")
def install_plugin_from_path(
    path: str,
    _=Depends(require_permission("plugin.install")),
    pm: PluginManager = Depends(get_plugin_manager),
):
    """Install a plugin from a local .pfp file path."""
    try:
        descriptor = pm.install(path)
        return {
            "status": "installed",
            "id": descriptor.id,
            "name": descriptor.name,
            "version": descriptor.version,
        }
    except Exception as e:
        raise HTTPException(400, f"Install failed: {e}")


@router.post("/upload")
def upload_plugin(
    file: UploadFile = File(...),
    _=Depends(require_permission("plugin.install")),
    pm: PluginManager = Depends(get_plugin_manager),
):
    """Upload and install a .pfp plugin file."""
    try:
        # Save to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pfp") as tmp:
            tmp.write(file.file.read())
            tmp_path = tmp.name

        descriptor = pm.install(tmp_path)
        os.unlink(tmp_path)
        return {
            "status": "installed",
            "id": descriptor.id,
            "name": descriptor.name,
            "version": descriptor.version,
        }
    except Exception as e:
        raise HTTPException(400, f"Upload failed: {e}")


@router.delete("/{plugin_id}")
def uninstall_plugin(
    plugin_id: str,
    _=Depends(require_permission("plugin.uninstall")),
    pm: PluginManager = Depends(get_plugin_manager),
):
    """Uninstall a plugin."""
    try:
        pm.uninstall(plugin_id)
        return {"status": "uninstalled", "id": plugin_id}
    except Exception as e:
        raise HTTPException(400, f"Uninstall failed: {e}")


class UpgradeRequest(BaseModel):
    version: Optional[str] = None


class DowngradeRequest(BaseModel):
    version: str


@router.get("/{plugin_id}/versions")
def list_plugin_versions(
    plugin_id: str,
    _=Depends(require_permission("monitor.view")),
    pm: PluginManager = Depends(get_plugin_manager),
):
    """List available versions of a plugin."""
    try:
        versions = pm.list_versions(plugin_id)
        current = pm.get_installed_version(plugin_id)
        return {"plugin_id": plugin_id, "current": current, "versions": versions}
    except Exception as e:
        raise HTTPException(400, f"Failed to list versions: {e}")


@router.post("/{plugin_id}/upgrade")
def upgrade_plugin(
    plugin_id: str,
    req: UpgradeRequest = None,
    _=Depends(require_permission("plugin.install")),
    pm: PluginManager = Depends(get_plugin_manager),
):
    """Upgrade a plugin to a specific or latest version."""
    try:
        target = req.version if req else None
        descriptor = pm.upgrade(plugin_id, target)
        return {
            "status": "upgraded",
            "id": descriptor.id,
            "version": descriptor.version,
        }
    except Exception as e:
        raise HTTPException(400, f"Upgrade failed: {e}")


@router.post("/{plugin_id}/downgrade")
def downgrade_plugin(
    plugin_id: str,
    req: DowngradeRequest = None,
    _=Depends(require_permission("plugin.install")),
    pm: PluginManager = Depends(get_plugin_manager),
):
    """Downgrade a plugin to a specific version."""
    try:
        if not req or not req.version:
            raise ValueError("version is required for downgrade")
        descriptor = pm.downgrade(plugin_id, req.version)
        return {
            "status": "downgraded",
            "id": descriptor.id,
            "version": descriptor.version,
        }
    except Exception as e:
        raise HTTPException(400, f"Downgrade failed: {e}")


@router.get("/{plugin_id}/history")
def plugin_history(
    plugin_id: str,
    _=Depends(require_permission("monitor.view")),
    pm: PluginManager = Depends(get_plugin_manager),
):
    """Get version change history for a plugin."""
    try:
        history = pm.get_plugin_history(plugin_id)
        return {"plugin_id": plugin_id, "history": history}
    except Exception as e:
        raise HTTPException(400, f"Failed to get history: {e}")


class ExportRequest(BaseModel):
    flow_config: Dict[str, Any]
    plugin_id: Optional[str] = None
    plugin_name: Optional[str] = None
    author: str = ""
    description: str = ""


@router.post("/export")
def export_as_plugin(
    req: ExportRequest,
    _=Depends(require_permission("flow.edit")),
):
    """Export a flow (with its custom tasks/services) as a .pfp archive."""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pfp") as tmp:
            tmp_path = tmp.name

        result_path = export_flow_as_plugin(
            flow_config=req.flow_config,
            output_path=tmp_path,
            plugin_id=req.plugin_id,
            plugin_name=req.plugin_name,
            author=req.author,
            description=req.description,
        )

        flow_name = req.flow_config.get("name", "export")
        filename = f"{flow_name.replace(' ', '_')}.pfp"

        return FileResponse(
            result_path,
            media_type="application/zip",
            filename=filename,
        )
    except Exception as e:
        raise HTTPException(400, f"Export failed: {e}")
