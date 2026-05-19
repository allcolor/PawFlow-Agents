# Git Storage

"""
Git storage implementation.
Uses a local Git repo to version flows.
Each save creates an automatic commit.
"""

import json
import logging
import os
import subprocess  # nosec B404
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)


class GitStorage:
    """Git storage with automatic versioning via commits.

    Stores flows as JSON files in a Git repository.
    Each save_flow/delete_flow creates a commit.
    Git history provides versioning naturally.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize Git storage.

        Args:
            config: Configuration with:
                - repository: path to local repository (default: '.')
                - branch: branch to use (default: 'main')
                - flows_dir: subdirectory for flows (default: 'flows')
                - auto_commit: auto commit after save/delete (default: True)
        """
        self.repository = Path(config.get('repository', '.')).resolve()
        self.branch = config.get('branch', 'main')
        self.flows_dir = config.get('flows_dir', 'flows')
        self.auto_commit = config.get('auto_commit', True)

        self._flows_path = self.repository / self.flows_dir
        self._flows_path.mkdir(parents=True, exist_ok=True)

        # Init repo if needed
        self._ensure_git_repo()

    def _ensure_git_repo(self):
        """Initialize the Git repo if necessary."""
        git_dir = self.repository / '.git'
        if not git_dir.exists():
            self._run_git('init')
            # Ensure user identity is configured for this repo
            result = self._run_git('config', 'user.email', check=False)
            if not result.stdout.strip():
                self._run_git('config', 'user.email', 'pawflow@local')
                self._run_git('config', 'user.name', 'PawFlow')
            # Create initial commit if empty
            self._run_git('add', '.')
            self._run_git('commit', '--allow-empty', '-m', 'Initial commit (PawFlow GitStorage)')
            logger.info(f"Git repo initialized at {self.repository}")

    def _run_git(self, *args, check: bool = True) -> subprocess.CompletedProcess:
        """Run a git command."""
        cmd = ['git', '-C', str(self.repository)] + list(args)
        try:
            result = subprocess.run(  # nosec B603
                cmd, capture_output=True, text=True, timeout=30,
                check=check,
            )
            return result
        except subprocess.CalledProcessError as e:
            logger.error(f"Git error: {' '.join(cmd)}: {e.stderr}")
            raise
        except FileNotFoundError:
            raise RuntimeError("Git is not installed or not in PATH")

    def _commit(self, message: str):
        """Stage all changes and commit."""
        if not self.auto_commit:
            return
        self._run_git('add', '-A')
        # Check if there's anything to commit
        result = self._run_git('status', '--porcelain', check=False)
        if result.stdout.strip():
            self._run_git('commit', '-m', message)

    def save_flow(self, flow_id: str, config: Dict[str, Any]) -> bool:
        """Save a flow to Git."""
        try:
            if 'modified_at' not in config:
                config['modified_at'] = datetime.now().isoformat()

            filepath = self._flows_path / f"{flow_id}.json"
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)

            self._commit(f"Save flow: {flow_id}")
            logger.info(f"Flow saved to Git: {flow_id}")
            return True
        except Exception as e:
            logger.error(f"Error saving flow {flow_id} to Git: {e}")
            return False

    def load_flow(self, flow_id: str) -> Optional[Dict[str, Any]]:
        """Load a flow from Git."""
        try:
            filepath = self._flows_path / f"{flow_id}.json"
            if not filepath.exists():
                return None
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading flow {flow_id} from Git: {e}")
            return None

    def delete_flow(self, flow_id: str) -> bool:
        """Delete a flow from Git."""
        try:
            filepath = self._flows_path / f"{flow_id}.json"
            if filepath.exists():
                filepath.unlink()
                self._commit(f"Delete flow: {flow_id}")
                logger.info(f"Flow deleted from Git: {flow_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"Error deleting flow {flow_id} from Git: {e}")
            return False

    def list_flows(self) -> List[str]:
        """List all flows in Git."""
        try:
            flow_ids = []
            for f in sorted(self._flows_path.glob("*.json")):
                flow_ids.append(f.stem)
            return flow_ids
        except Exception as e:
            logger.error(f"Error listing flows from Git: {e}")
            return []

    def save_task(self, task_type: str, config: Dict[str, Any]) -> bool:
        """Save a task to Git."""
        try:
            task_dir = self.repository / "tasks_config" / task_type
            task_dir.mkdir(parents=True, exist_ok=True)
            filepath = task_dir / f"{config.get('id', 'default')}.json"
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            self._commit(f"Save task config: {task_type}")
            return True
        except Exception as e:
            logger.error(f"Error saving task {task_type} to Git: {e}")
            return False

    def load_service(self, service_type: str, config: Dict[str, Any]) -> bool:
        """Save a service to Git."""
        try:
            svc_dir = self.repository / "services_config" / service_type
            svc_dir.mkdir(parents=True, exist_ok=True)
            filepath = svc_dir / f"{config.get('id', 'default')}.json"
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            self._commit(f"Save service config: {service_type}")
            return True
        except Exception as e:
            logger.error(f"Error saving service {service_type} to Git: {e}")
            return False

    # -- Git-specific methods --

    def get_flow_history(self, flow_id: str) -> List[Dict[str, str]]:
        """Retrieve modification history for a flow.

        Returns:
            List of {"commit": hash, "date": iso, "message": msg}
        """
        try:
            filepath = f"{self.flows_dir}/{flow_id}.json"
            result = self._run_git(
                'log', '--pretty=format:%H|%aI|%s', '--follow', '--', filepath,
                check=False,
            )
            history = []
            for line in result.stdout.strip().split('\n'):
                if '|' in line:
                    parts = line.split('|', 2)
                    history.append({
                        "commit": parts[0],
                        "date": parts[1],
                        "message": parts[2] if len(parts) > 2 else "",
                    })
            return history
        except Exception as e:
            logger.error(f"Error getting history for {flow_id}: {e}")
            return []

    def get_flow_at_commit(self, flow_id: str, commit: str) -> Optional[Dict[str, Any]]:
        """Retrieve a flow at a specific commit."""
        try:
            filepath = f"{self.flows_dir}/{flow_id}.json"
            result = self._run_git('show', f'{commit}:{filepath}', check=False)
            if result.returncode == 0:
                return json.loads(result.stdout)
            return None
        except Exception as e:
            logger.error(f"Error loading flow {flow_id} at {commit}: {e}")
            return None
