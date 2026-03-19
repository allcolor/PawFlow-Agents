"""Plugin System - Dynamic loading of tasks, services, and flows.

Supports .pfp (OpenPaw Plugin) archives — zip files containing:
    plugin.json          # descriptor (required)
    requirements.txt     # pip dependencies (optional)
    tasks/               # task modules (optional)
    services/            # service modules (optional)
    flows/               # flow JSON files (optional)
    assets/              # static files (optional)

plugin.json format:
{
    "id": "com.example.my-plugin",
    "name": "My Plugin",
    "version": "1.0.0",
    "author": "Author",
    "description": "What this plugin does",
    "min_openpaw_version": "1.0.0",
    "tasks": ["tasks/my_task.py:MyTaskClass"],
    "services": ["services/my_svc.py:MySvcClass"],
    "flows": ["flows/my_flow.json"]
}

Also supports loading from plain directories (for development).
"""

import importlib
import importlib.util
import json
import logging
import os
import shutil
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from core import TaskFactory, ServiceFactory, Task, Service, __version__

logger = logging.getLogger(__name__)


class PluginVersion:
    """Semantic versioning for plugins."""

    def __init__(self, version_str: str):
        """Parse '1.2.3' or '1.2.3-beta'."""
        parts = version_str.replace("-", ".").split(".")
        self.major = int(parts[0]) if len(parts) > 0 else 0
        self.minor = int(parts[1]) if len(parts) > 1 else 0
        self.patch = int(parts[2]) if len(parts) > 2 else 0
        self.pre = parts[3] if len(parts) > 3 else ""

    def _tuple(self):
        """Return comparison tuple. Pre-release sorts before release."""
        # (major, minor, patch, has_no_pre, pre)
        # has_no_pre=0 means pre-release (sorts first), 1 means release
        return (self.major, self.minor, self.patch, 0 if self.pre else 1, self.pre)

    def __str__(self):
        base = f"{self.major}.{self.minor}.{self.patch}"
        return f"{base}-{self.pre}" if self.pre else base

    def __repr__(self):
        return f"PluginVersion('{self}')"

    def __lt__(self, other):
        if not isinstance(other, PluginVersion):
            return NotImplemented
        return self._tuple() < other._tuple()

    def __le__(self, other):
        if not isinstance(other, PluginVersion):
            return NotImplemented
        return self._tuple() <= other._tuple()

    def __gt__(self, other):
        if not isinstance(other, PluginVersion):
            return NotImplemented
        return self._tuple() > other._tuple()

    def __ge__(self, other):
        if not isinstance(other, PluginVersion):
            return NotImplemented
        return self._tuple() >= other._tuple()

    def __eq__(self, other):
        if not isinstance(other, PluginVersion):
            return NotImplemented
        return self._tuple() == other._tuple()

    def __hash__(self):
        return hash(self._tuple())

    def is_compatible(self, other) -> bool:
        """Check if same major version (semver compatibility)."""
        if not isinstance(other, PluginVersion):
            return False
        return self.major == other.major

    @staticmethod
    def satisfies(version_str: str, constraint: str) -> bool:
        """Check if version_str satisfies a constraint like '>=1.0.0'."""
        version = PluginVersion(version_str)
        if constraint.startswith(">="):
            return version >= PluginVersion(constraint[2:])
        elif constraint.startswith("<="):
            return version <= PluginVersion(constraint[2:])
        elif constraint.startswith(">"):
            return version > PluginVersion(constraint[1:])
        elif constraint.startswith("<"):
            return version < PluginVersion(constraint[1:])
        elif constraint.startswith("=="):
            return version == PluginVersion(constraint[2:])
        elif constraint.startswith("!="):
            return version != PluginVersion(constraint[2:])
        else:
            return version == PluginVersion(constraint)

# Default directories
PLUGINS_DIR = "plugins"
PLUGINS_INSTALLED_DIR = "plugins/installed"


@dataclass
class PluginDescriptor:
    """Parsed plugin.json descriptor."""
    id: str
    name: str
    version: str = "1.0.0"
    author: str = ""
    description: str = ""
    min_openpaw_version: str = "1.0.0"
    tasks: List[str] = field(default_factory=list)
    services: List[str] = field(default_factory=list)
    flows: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> 'PluginDescriptor':
        return cls(
            id=data["id"],
            name=data.get("name", data["id"]),
            version=data.get("version", "1.0.0"),
            author=data.get("author", ""),
            description=data.get("description", ""),
            min_openpaw_version=data.get("min_openpaw_version", "1.0.0"),
            tasks=data.get("tasks", []),
            services=data.get("services", []),
            flows=data.get("flows", []),
            dependencies=data.get("dependencies", []),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "min_openpaw_version": self.min_openpaw_version,
            "tasks": self.tasks,
            "services": self.services,
            "flows": self.flows,
            "dependencies": self.dependencies,
        }


@dataclass
class LoadedPlugin:
    """A plugin that has been loaded into the system."""
    descriptor: PluginDescriptor
    path: Path
    loaded_tasks: List[str] = field(default_factory=list)
    loaded_services: List[str] = field(default_factory=list)
    loaded_flows: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            **self.descriptor.to_dict(),
            "path": str(self.path),
            "loaded_tasks": self.loaded_tasks,
            "loaded_services": self.loaded_services,
            "loaded_flows": self.loaded_flows,
            "errors": self.errors,
        }


class PluginManager:
    """Manages plugin installation, loading, and unloading.

    Usage:
        mgr = PluginManager()
        mgr.install("path/to/plugin.pfp")    # install from archive
        mgr.install("path/to/plugin_dir/")    # install from directory
        mgr.load_all()                         # load all installed plugins
        mgr.list_plugins()                     # list loaded plugins
        mgr.uninstall("com.example.my-plugin") # uninstall
    """

    def __init__(self, plugins_dir: str = PLUGINS_DIR):
        self._plugins_dir = Path(plugins_dir)
        self._installed_dir = self._plugins_dir / "installed"
        self._plugins_dir.mkdir(parents=True, exist_ok=True)
        self._installed_dir.mkdir(parents=True, exist_ok=True)

        self._loaded: Dict[str, LoadedPlugin] = {}
        self._flow_registry: Dict[str, dict] = {}  # flow_id -> flow_dict

    # -- Install --

    def install(self, source: str) -> PluginDescriptor:
        """Install a plugin from a .pfp archive or a directory.

        The plugin is extracted/copied to plugins/installed/<plugin_id>/
        """
        source_path = Path(source)

        if source_path.is_file() and (source_path.suffix in ('.pfp', '.zip')):
            descriptor = self._install_from_archive(source_path)
        elif source_path.is_dir():
            descriptor = self._install_from_directory(source_path)
        else:
            raise ValueError(
                f"Invalid plugin source: {source}. "
                f"Expected .pfp/.zip file or directory."
            )

        # Record initial install in version history
        self._record_history(descriptor.id, "install", descriptor.version)
        return descriptor

    def _install_from_archive(self, archive_path: Path) -> PluginDescriptor:
        """Extract a .pfp archive and install."""
        with zipfile.ZipFile(archive_path, 'r') as zf:
            # Read descriptor
            try:
                descriptor_data = json.loads(zf.read("plugin.json"))
            except KeyError:
                raise ValueError(
                    f"Invalid plugin archive: missing plugin.json"
                )

            descriptor = PluginDescriptor.from_dict(descriptor_data)

            # Check version compatibility
            self._check_compatibility(descriptor)

            # Extract to installed dir
            install_dir = self._installed_dir / descriptor.id
            if install_dir.exists():
                shutil.rmtree(install_dir)
            install_dir.mkdir(parents=True)
            zf.extractall(install_dir)

        logger.info(f"Plugin installed: {descriptor.name} v{descriptor.version}")
        return descriptor

    def _install_from_directory(self, dir_path: Path) -> PluginDescriptor:
        """Install from a directory (copy to installed dir)."""
        plugin_json = dir_path / "plugin.json"
        if not plugin_json.exists():
            raise ValueError(
                f"Invalid plugin directory: missing plugin.json in {dir_path}"
            )

        with open(plugin_json, 'r', encoding='utf-8') as f:
            descriptor = PluginDescriptor.from_dict(json.load(f))

        self._check_compatibility(descriptor)

        install_dir = self._installed_dir / descriptor.id
        if install_dir.exists():
            shutil.rmtree(install_dir)

        shutil.copytree(dir_path, install_dir)

        logger.info(f"Plugin installed from directory: {descriptor.name}")
        return descriptor

    def _check_compatibility(self, descriptor: PluginDescriptor):
        """Check if plugin is compatible with current OpenPaw version."""
        # Simple version check (major.minor)
        required = descriptor.min_openpaw_version.split('.')
        current = __version__.split('.')
        try:
            if int(current[0]) < int(required[0]):
                raise ValueError(
                    f"Plugin {descriptor.id} requires OpenPaw >= {descriptor.min_openpaw_version}, "
                    f"current is {__version__}"
                )
        except (ValueError, IndexError):
            pass  # Skip if versions aren't parseable

    # -- Load --

    def load_all(self) -> List[LoadedPlugin]:
        """Load all installed plugins."""
        results = []
        if not self._installed_dir.exists():
            return results

        for plugin_dir in sorted(self._installed_dir.iterdir()):
            if not plugin_dir.is_dir():
                continue
            plugin_json = plugin_dir / "plugin.json"
            if not plugin_json.exists():
                continue
            try:
                loaded = self.load_plugin(plugin_dir)
                results.append(loaded)
            except Exception as e:
                logger.error(f"Failed to load plugin from {plugin_dir}: {e}")

        return results

    def load_plugin(self, plugin_dir: Path) -> LoadedPlugin:
        """Load a single plugin from its install directory."""
        plugin_json = plugin_dir / "plugin.json"
        with open(plugin_json, 'r', encoding='utf-8') as f:
            descriptor = PluginDescriptor.from_dict(json.load(f))

        if descriptor.id in self._loaded:
            logger.warning(f"Plugin {descriptor.id} already loaded, reloading...")
            self.unload_plugin(descriptor.id)

        loaded = LoadedPlugin(descriptor=descriptor, path=plugin_dir)

        # Install pip dependencies if requirements.txt exists
        reqs_file = plugin_dir / "requirements.txt"
        if reqs_file.exists():
            self._install_dependencies(reqs_file, loaded)

        # Load tasks
        for task_ref in descriptor.tasks:
            try:
                task_class = self._load_class(plugin_dir, task_ref, Task)
                TaskFactory.register(task_class)
                loaded.loaded_tasks.append(task_class.TYPE)
                logger.info(f"  Task registered: {task_class.TYPE}")
            except Exception as e:
                err = f"Failed to load task {task_ref}: {e}"
                loaded.errors.append(err)
                logger.error(err)

        # Load services
        for svc_ref in descriptor.services:
            try:
                svc_class = self._load_class(plugin_dir, svc_ref, Service)
                ServiceFactory.register(svc_class)
                loaded.loaded_services.append(svc_class.TYPE)
                logger.info(f"  Service registered: {svc_class.TYPE}")
            except Exception as e:
                err = f"Failed to load service {svc_ref}: {e}"
                loaded.errors.append(err)
                logger.error(err)

        # Load flows
        for flow_ref in descriptor.flows:
            try:
                flow_dict = self._load_flow(plugin_dir, flow_ref)
                flow_id = flow_dict.get("id", flow_ref)
                flow_dict["_plugin_id"] = descriptor.id
                self._flow_registry[flow_id] = flow_dict
                loaded.loaded_flows.append(flow_id)
                logger.info(f"  Flow registered: {flow_id}")
            except Exception as e:
                err = f"Failed to load flow {flow_ref}: {e}"
                loaded.errors.append(err)
                logger.error(err)

        self._loaded[descriptor.id] = loaded
        logger.info(
            f"Plugin loaded: {descriptor.name} v{descriptor.version} "
            f"({len(loaded.loaded_tasks)} tasks, "
            f"{len(loaded.loaded_services)} services, "
            f"{len(loaded.loaded_flows)} flows)"
        )
        return loaded

    def _load_class(self, plugin_dir: Path, ref: str, base_class: type) -> type:
        """Load a class from a plugin reference like 'tasks/my_task.py:MyTask'.

        Args:
            plugin_dir: Root directory of the plugin
            ref: Reference string like 'tasks/my_task.py:MyTaskClass'
            base_class: Expected base class (Task or Service)
        """
        if ':' not in ref:
            raise ValueError(
                f"Invalid reference '{ref}'. Expected 'path/to/file.py:ClassName'"
            )

        file_part, class_name = ref.rsplit(':', 1)
        file_path = plugin_dir / file_part

        if not file_path.exists():
            raise FileNotFoundError(f"Module file not found: {file_path}")

        # Dynamic import
        module_name = f"plugin_{plugin_dir.name}_{file_path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot create module spec for {file_path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        # Get the class
        cls = getattr(module, class_name, None)
        if cls is None:
            raise AttributeError(
                f"Class '{class_name}' not found in {file_path}"
            )

        if not (isinstance(cls, type) and issubclass(cls, base_class)):
            raise TypeError(
                f"'{class_name}' is not a subclass of {base_class.__name__}"
            )

        return cls

    def _load_flow(self, plugin_dir: Path, flow_ref: str) -> dict:
        """Load a flow JSON file from a plugin."""
        flow_path = plugin_dir / flow_ref
        if not flow_path.exists():
            raise FileNotFoundError(f"Flow file not found: {flow_path}")

        with open(flow_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _install_dependencies(self, reqs_file: Path, loaded: LoadedPlugin):
        """Install pip dependencies from requirements.txt."""
        import subprocess
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(reqs_file),
                 "--quiet", "--disable-pip-version-check"],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                err = f"pip install failed: {result.stderr}"
                loaded.errors.append(err)
                logger.warning(err)
        except Exception as e:
            loaded.errors.append(f"Failed to install dependencies: {e}")

    # -- Versioning --

    def _versions_dir(self, plugin_id: str) -> Path:
        """Get the versions directory for a plugin."""
        d = self._plugins_dir / "versions" / plugin_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _history_path(self, plugin_id: str) -> Path:
        """Get the history.json path for a plugin."""
        return self._versions_dir(plugin_id) / "history.json"

    def _load_history(self, plugin_id: str) -> List[dict]:
        """Load version history from disk."""
        path = self._history_path(plugin_id)
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []

    def _save_history(self, plugin_id: str, history: List[dict]):
        """Save version history to disk."""
        path = self._history_path(plugin_id)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(history, f, indent=2, ensure_ascii=False)

    def _record_history(self, plugin_id: str, action: str, version: str,
                        previous_version: str = None):
        """Append an entry to the version history."""
        history = self._load_history(plugin_id)
        entry = {
            "action": action,
            "version": version,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if previous_version:
            entry["previous_version"] = previous_version
        history.append(entry)
        self._save_history(plugin_id, history)

    def _backup_current_version(self, plugin_id: str) -> Optional[str]:
        """Backup the currently installed version as a .pfp in versions dir.

        Returns the version string that was backed up, or None if not installed.
        """
        install_dir = self._installed_dir / plugin_id
        plugin_json = install_dir / "plugin.json"
        if not plugin_json.exists():
            return None

        with open(plugin_json, 'r', encoding='utf-8') as f:
            desc = PluginDescriptor.from_dict(json.load(f))

        versions_dir = self._versions_dir(plugin_id)
        backup_path = versions_dir / f"{plugin_id}-{desc.version}.pfp"

        if not backup_path.exists():
            # Create archive from installed dir
            with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(install_dir):
                    for file in files:
                        file_path = Path(root) / file
                        arc_name = file_path.relative_to(install_dir)
                        if '__pycache__' in str(arc_name) or file.endswith('.pyc'):
                            continue
                        zf.write(file_path, arc_name)

        return desc.version

    def get_installed_version(self, plugin_id: str) -> Optional[str]:
        """Get version of installed plugin."""
        install_dir = self._installed_dir / plugin_id
        plugin_json = install_dir / "plugin.json"
        if not plugin_json.exists():
            return None
        with open(plugin_json, 'r', encoding='utf-8') as f:
            desc = PluginDescriptor.from_dict(json.load(f))
        return desc.version

    def list_versions(self, plugin_id: str) -> List[str]:
        """List all available versions of a plugin (from versions/ dir)."""
        versions_dir = self._versions_dir(plugin_id)
        versions = []
        prefix = f"{plugin_id}-"
        for f in sorted(versions_dir.iterdir()):
            if f.suffix == '.pfp' and f.stem.startswith(prefix):
                ver_str = f.stem[len(prefix):]
                versions.append(ver_str)

        # Sort by semver
        versions.sort(key=lambda v: PluginVersion(v))
        return versions

    def upgrade(self, plugin_id: str, target_version: str = None) -> PluginDescriptor:
        """Upgrade plugin to target version (or latest).

        - Backs up current version
        - Installs new version
        - Validates compatibility
        - Rolls back on failure
        """
        current_version = self.get_installed_version(plugin_id)
        if current_version is None:
            raise ValueError(f"Plugin '{plugin_id}' is not installed")

        # Backup current version
        self._backup_current_version(plugin_id)

        # Find target version
        available = self.list_versions(plugin_id)
        if target_version:
            if target_version not in available:
                raise ValueError(
                    f"Version {target_version} not available for plugin '{plugin_id}'. "
                    f"Available: {available}"
                )
            target = target_version
        else:
            # Find latest version higher than current
            current_pv = PluginVersion(current_version)
            higher = [v for v in available if PluginVersion(v) > current_pv]
            if not higher:
                raise ValueError(
                    f"No newer version available for '{plugin_id}' (current: {current_version})"
                )
            target = higher[-1]  # already sorted

        target_pv = PluginVersion(target)
        current_pv = PluginVersion(current_version)

        if target_pv <= current_pv:
            raise ValueError(
                f"Target version {target} is not higher than current {current_version}. "
                f"Use downgrade() instead."
            )

        # Check compatibility
        if not target_pv.is_compatible(current_pv):
            logger.warning(
                f"Major version change: {current_version} -> {target}. "
                f"This may introduce breaking changes."
            )

        # Install from backup archive
        archive_path = self._versions_dir(plugin_id) / f"{plugin_id}-{target}.pfp"
        try:
            # Unload if loaded
            was_loaded = plugin_id in self._loaded
            if was_loaded:
                self.unload_plugin(plugin_id)

            descriptor = self._install_from_archive(archive_path)
            self._record_history(plugin_id, "upgrade", target, current_version)

            # Reload if it was loaded
            if was_loaded:
                install_dir = self._installed_dir / plugin_id
                self.load_plugin(install_dir)

            logger.info(f"Plugin upgraded: {plugin_id} {current_version} -> {target}")
            return descriptor

        except Exception as e:
            # Rollback: restore previous version
            logger.error(f"Upgrade failed, rolling back: {e}")
            rollback_archive = self._versions_dir(plugin_id) / f"{plugin_id}-{current_version}.pfp"
            if rollback_archive.exists():
                try:
                    self._install_from_archive(rollback_archive)
                    logger.info(f"Rollback successful: restored {current_version}")
                except Exception as re:
                    logger.error(f"Rollback also failed: {re}")
            raise

    def downgrade(self, plugin_id: str, target_version: str) -> PluginDescriptor:
        """Downgrade plugin to specific version."""
        current_version = self.get_installed_version(plugin_id)
        if current_version is None:
            raise ValueError(f"Plugin '{plugin_id}' is not installed")

        # Backup current version
        self._backup_current_version(plugin_id)

        available = self.list_versions(plugin_id)
        if target_version not in available:
            raise ValueError(
                f"Version {target_version} not available for plugin '{plugin_id}'. "
                f"Available: {available}"
            )

        target_pv = PluginVersion(target_version)
        current_pv = PluginVersion(current_version)
        if target_pv >= current_pv:
            raise ValueError(
                f"Target version {target_version} is not lower than current {current_version}. "
                f"Use upgrade() instead."
            )

        archive_path = self._versions_dir(plugin_id) / f"{plugin_id}-{target_version}.pfp"

        # Unload if loaded
        was_loaded = plugin_id in self._loaded
        if was_loaded:
            self.unload_plugin(plugin_id)

        descriptor = self._install_from_archive(archive_path)
        self._record_history(plugin_id, "downgrade", target_version, current_version)

        # Reload if it was loaded
        if was_loaded:
            install_dir = self._installed_dir / plugin_id
            self.load_plugin(install_dir)

        logger.info(f"Plugin downgraded: {plugin_id} {current_version} -> {target_version}")
        return descriptor

    def get_plugin_history(self, plugin_id: str) -> List[dict]:
        """Get version history (installed, upgraded, downgraded timestamps)."""
        return self._load_history(plugin_id)

    def check_dependencies(self, plugin_id: str) -> dict:
        """Check if plugin dependencies are satisfied.

        Returns a dict with:
            - satisfied: bool
            - details: dict mapping dependency_id to status info
        """
        install_dir = self._installed_dir / plugin_id
        plugin_json = install_dir / "plugin.json"
        if not plugin_json.exists():
            return {"satisfied": False, "details": {"_error": "Plugin not installed"}}

        with open(plugin_json, 'r', encoding='utf-8') as f:
            raw = json.load(f)

        deps = raw.get("dependencies", {})
        # Support both list (legacy) and dict (new) formats
        if isinstance(deps, list):
            return {"satisfied": True, "details": {}}

        details = {}
        all_satisfied = True

        for dep_id, constraint in deps.items():
            dep_version = self.get_installed_version(dep_id)
            if dep_version is None:
                details[dep_id] = {
                    "required": constraint,
                    "installed": None,
                    "satisfied": False,
                    "reason": "not installed",
                }
                all_satisfied = False
            else:
                ok = PluginVersion.satisfies(dep_version, constraint)
                details[dep_id] = {
                    "required": constraint,
                    "installed": dep_version,
                    "satisfied": ok,
                }
                if not ok:
                    details[dep_id]["reason"] = (
                        f"installed {dep_version} does not satisfy {constraint}"
                    )
                    all_satisfied = False

        return {"satisfied": all_satisfied, "details": details}

    # -- Unload / Uninstall --

    def unload_plugin(self, plugin_id: str):
        """Unload a plugin (unregister its tasks/services/flows)."""
        loaded = self._loaded.pop(plugin_id, None)
        if not loaded:
            return

        # Note: TaskFactory/ServiceFactory don't have unregister methods
        # We'd need to add them. For now, tasks remain registered until restart.
        for flow_id in loaded.loaded_flows:
            self._flow_registry.pop(flow_id, None)

        logger.info(f"Plugin unloaded: {plugin_id}")

    def uninstall(self, plugin_id: str):
        """Uninstall a plugin (unload + delete files)."""
        self.unload_plugin(plugin_id)

        install_dir = self._installed_dir / plugin_id
        if install_dir.exists():
            shutil.rmtree(install_dir)
            logger.info(f"Plugin uninstalled: {plugin_id}")

    # -- Query --

    def list_plugins(self) -> List[Dict[str, Any]]:
        """List all loaded plugins."""
        return [lp.to_dict() for lp in self._loaded.values()]

    def get_plugin(self, plugin_id: str) -> Optional[LoadedPlugin]:
        """Get a loaded plugin by ID."""
        return self._loaded.get(plugin_id)

    def list_installed(self) -> List[Dict[str, Any]]:
        """List all installed plugins (even if not loaded)."""
        result = []
        if not self._installed_dir.exists():
            return result

        for plugin_dir in sorted(self._installed_dir.iterdir()):
            plugin_json = plugin_dir / "plugin.json"
            if plugin_json.exists():
                try:
                    with open(plugin_json, 'r', encoding='utf-8') as f:
                        desc = PluginDescriptor.from_dict(json.load(f))
                    info = desc.to_dict()
                    info["installed_path"] = str(plugin_dir)
                    info["loaded"] = desc.id in self._loaded
                    result.append(info)
                except Exception as e:
                    result.append({"path": str(plugin_dir), "error": str(e)})

        return result

    # -- Flow registry --

    def get_flow(self, flow_id: str) -> Optional[dict]:
        """Get a registered flow by ID."""
        return self._flow_registry.get(flow_id)

    def list_flows(self) -> List[Dict[str, Any]]:
        """List all registered plugin flows."""
        result = []
        for flow_id, flow_dict in self._flow_registry.items():
            result.append({
                "id": flow_id,
                "name": flow_dict.get("name", flow_id),
                "description": flow_dict.get("description", ""),
                "plugin_id": flow_dict.get("_plugin_id", ""),
                "task_count": len(flow_dict.get("tasks", {})),
            })
        return result

    def import_flow(self, flow_path: str) -> dict:
        """Import a standalone flow JSON file (not from a plugin).

        The flow is registered in the flow registry and can be used
        as a template or executed directly.
        """
        path = Path(flow_path)
        if not path.exists():
            raise FileNotFoundError(f"Flow file not found: {path}")

        with open(path, 'r', encoding='utf-8') as f:
            flow_dict = json.load(f)

        flow_id = flow_dict.get("id", path.stem)
        flow_dict["_source"] = str(path)
        self._flow_registry[flow_id] = flow_dict

        logger.info(f"Flow imported: {flow_id} from {path}")
        return flow_dict

    def export_flow(self, flow_dict: dict, output_path: str):
        """Export a flow to a JSON file."""
        path = Path(output_path)
        # Remove internal metadata before export
        export = {k: v for k, v in flow_dict.items() if not k.startswith('_')}
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(export, f, indent=2, ensure_ascii=False)
        logger.info(f"Flow exported to {path}")


# -- Plugin packaging helper --

def create_plugin_archive(plugin_dir: str, output_path: str = None) -> str:
    """Package a plugin directory into a .pfp archive.

    Args:
        plugin_dir: Path to the plugin directory (must contain plugin.json)
        output_path: Optional output path. Defaults to <plugin_id>.pfp

    Returns:
        Path to the created archive.
    """
    plugin_path = Path(plugin_dir)
    plugin_json = plugin_path / "plugin.json"
    if not plugin_json.exists():
        raise ValueError(f"Missing plugin.json in {plugin_dir}")

    with open(plugin_json, 'r', encoding='utf-8') as f:
        desc = PluginDescriptor.from_dict(json.load(f))

    if output_path is None:
        output_path = f"{desc.id}-{desc.version}.pfp"

    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(plugin_path):
            for file in files:
                file_path = Path(root) / file
                arc_name = file_path.relative_to(plugin_path)
                # Skip __pycache__ and .pyc
                if '__pycache__' in str(arc_name) or file.endswith('.pyc'):
                    continue
                zf.write(file_path, arc_name)

    logger.info(f"Plugin archive created: {output_path}")
    return output_path


def export_flow_as_plugin(
    flow_config: dict,
    output_path: str,
    plugin_id: str = None,
    plugin_name: str = None,
    author: str = "",
    description: str = "",
    include_builtin_tasks: bool = False,
) -> str:
    """Export a flow (with its tasks/services source) as a .pfp archive.

    Inspects the flow config to find which task types and service types are used,
    locates their source .py files, and bundles everything into a .pfp.

    Args:
        flow_config: The flow dict (with "tasks", "services", etc.)
        output_path: Path for the .pfp output file
        plugin_id: Plugin ID (defaults to flow name slugified)
        plugin_name: Human-readable name
        author: Author string
        description: Description string
        include_builtin_tasks: If True, include built-in tasks too (default: only custom/plugin tasks)

    Returns:
        Path to the created .pfp file
    """
    import inspect
    import re

    flow_name = flow_config.get("name", "exported-flow")
    slug = re.sub(r'[^a-z0-9]+', '-', flow_name.lower()).strip('-')
    if not plugin_id:
        plugin_id = f"export.{slug}"
    if not plugin_name:
        plugin_name = flow_name

    # Identify task types used in the flow
    tasks_section = flow_config.get("tasks", {})
    task_types_used = set()
    for task_info in tasks_section.values():
        if isinstance(task_info, dict):
            task_types_used.add(task_info.get("type", ""))
        elif hasattr(task_info, 'TYPE'):
            task_types_used.add(task_info.TYPE)

    # Identify service types
    services_section = flow_config.get("services", {})
    service_types_used = set()
    for svc_info in services_section.values():
        if isinstance(svc_info, dict):
            service_types_used.add(svc_info.get("type", ""))
        elif hasattr(svc_info, 'TYPE'):
            service_types_used.add(svc_info.TYPE)

    # Resolve source files
    task_refs = []  # "tasks/filename.py:ClassName"
    task_sources = {}  # source_path -> archive_path
    builtin_prefix = str(Path("tasks").resolve())

    for ttype in task_types_used:
        if not ttype:
            continue
        try:
            task_class = TaskFactory.get(ttype)
        except Exception:
            continue
        source_file = inspect.getfile(task_class)
        if not include_builtin_tasks and source_file.startswith(builtin_prefix):
            continue
        arc_name = f"tasks/{Path(source_file).name}"
        task_sources[source_file] = arc_name
        task_refs.append(f"{arc_name}:{task_class.__name__}")

    service_refs = []
    service_sources = {}
    builtin_svc_prefix = str(Path("services").resolve())

    for stype in service_types_used:
        if not stype:
            continue
        try:
            svc_class = ServiceFactory.get(stype)
        except Exception:
            continue
        source_file = inspect.getfile(svc_class)
        if not include_builtin_tasks and source_file.startswith(builtin_svc_prefix):
            continue
        arc_name = f"services/{Path(source_file).name}"
        service_sources[source_file] = arc_name
        service_refs.append(f"{arc_name}:{svc_class.__name__}")

    # Build plugin descriptor
    descriptor = {
        "id": plugin_id,
        "name": plugin_name,
        "version": "1.0.0",
        "author": author,
        "description": description or f"Exported flow: {flow_name}",
        "min_openpaw_version": "1.0.0",
        "tasks": task_refs,
        "services": service_refs,
        "flows": ["flows/flow.json"],
    }

    # Create .pfp archive
    out = Path(output_path)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
        # plugin.json
        zf.writestr("plugin.json", json.dumps(descriptor, indent=2, ensure_ascii=False))

        # Flow JSON (strip internal keys)
        flow_export = {k: v for k, v in flow_config.items() if not k.startswith('_')}
        zf.writestr("flows/flow.json", json.dumps(flow_export, indent=2, ensure_ascii=False))

        # Task source files
        for src, arc in task_sources.items():
            zf.write(src, arc)

        # Service source files
        for src, arc in service_sources.items():
            zf.write(src, arc)

    logger.info(f"Flow exported as plugin: {out} ({len(task_refs)} tasks, {len(service_refs)} services)")
    return str(out)


# -- Singleton --

_plugin_manager: Optional[PluginManager] = None


def get_plugin_manager(plugins_dir: str = PLUGINS_DIR) -> PluginManager:
    """Get or create the global PluginManager."""
    global _plugin_manager
    if _plugin_manager is None:
        _plugin_manager = PluginManager(plugins_dir)
    return _plugin_manager
