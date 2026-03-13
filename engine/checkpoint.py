"""Checkpoint System - Queue persistence and crash recovery.

Periodically snapshots the state of all connection queues to disk,
allowing recovery of queued FlowFiles after a crash.

Checkpoint format (JSON):
{
    "timestamp": "...",
    "flow_id": "...",
    "flow_version": 1,
    "queues": [
        {
            "source": "task_a",
            "target": "task_b",
            "relationship": "success",
            "flowfiles": [
                {
                    "process_id": "...",
                    "attributes": {...},
                    "content_b64": "...",       # for small content
                    "content_file": "...",       # for large/spilled content
                    "size": 1234
                }
            ]
        }
    ],
    "task_states": {
        "task_a": {"state": "running", ...}
    }
}
"""

import base64
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

from core import FlowFile

logger = logging.getLogger(__name__)

# Max content size to inline as base64 in checkpoint JSON
INLINE_MAX_BYTES = 256 * 1024  # 256KB

CHECKPOINT_DIR = "checkpoints"


class CheckpointManager:
    """Manages periodic checkpointing and recovery of executor state.

    Usage:
        mgr = CheckpointManager(flow_id="my_flow")

        # Periodic checkpointing (call from executor)
        mgr.save_checkpoint(connections, task_states, flow_version)

        # Recovery on startup
        data = mgr.load_latest_checkpoint()
        if data:
            flowfiles_by_queue = mgr.restore_flowfiles(data)
    """

    def __init__(self, flow_id: str, checkpoint_dir: str = CHECKPOINT_DIR,
                 max_checkpoints: int = 5):
        self._flow_id = flow_id
        self._dir = Path(checkpoint_dir) / flow_id
        self._dir.mkdir(parents=True, exist_ok=True)
        self._max_checkpoints = max_checkpoints
        self._lock = threading.Lock()
        self._counter = 0
        # Directory for large content files
        self._content_dir = self._dir / "content"
        self._content_dir.mkdir(exist_ok=True)

    def save_checkpoint(self, connections, task_states_dict: Dict[str, dict],
                        flow_version: int) -> str:
        """Save a checkpoint of all queue contents and task states.

        Args:
            connections: ConnectionManager or list of Connection objects
            task_states_dict: Dict from get_all_states()
            flow_version: Current flow version number

        Returns:
            Path to the checkpoint file
        """
        if hasattr(connections, '_connections'):
            conn_list = connections._connections
        else:
            conn_list = connections

        checkpoint = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "flow_id": self._flow_id,
            "flow_version": flow_version,
            "queues": [],
            "task_states": task_states_dict,
        }

        for conn in conn_list:
            queue_data = {
                "source": conn.source_id,
                "target": conn.target_id,
                "relationship": conn.relationship,
                "flowfiles": [],
            }

            # Peek all FlowFiles without removing (no stat corruption)
            all_ffs = conn.peek_all(limit=conn.max_queue_size)
            for ff in all_ffs:
                ff_data = self._serialize_flowfile(ff)
                queue_data["flowfiles"].append(ff_data)

            if queue_data["flowfiles"]:
                checkpoint["queues"].append(queue_data)

        # Write checkpoint with unique name
        self._counter += 1
        filename = f"checkpoint_{int(time.time())}_{self._counter:04d}.json"
        filepath = self._dir / filename

        with self._lock:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(checkpoint, f, indent=2, ensure_ascii=False)

            # Trim old checkpoints
            self._trim_checkpoints()

        logger.info(
            f"Checkpoint saved: {filepath} "
            f"({sum(len(q['flowfiles']) for q in checkpoint['queues'])} FlowFiles)"
        )
        return str(filepath)

    def load_latest_checkpoint(self) -> Optional[Dict[str, Any]]:
        """Load the most recent checkpoint.

        Returns:
            Checkpoint dict or None if no checkpoint exists
        """
        checkpoints = sorted(self._dir.glob("checkpoint_*.json"))
        if not checkpoints:
            return None

        latest = checkpoints[-1]
        try:
            with open(latest, 'r', encoding='utf-8') as f:
                data = json.load(f)
            logger.info(f"Loaded checkpoint: {latest}")
            return data
        except Exception as e:
            logger.error(f"Failed to load checkpoint {latest}: {e}")
            return None

    def restore_flowfiles(self, checkpoint: Dict[str, Any]
                          ) -> Dict[tuple, List[FlowFile]]:
        """Restore FlowFiles from a checkpoint.

        Returns:
            Dict mapping (source_id, target_id) -> list of FlowFiles
        """
        result = {}
        for queue_data in checkpoint.get("queues", []):
            key = (queue_data["source"], queue_data["target"])
            flowfiles = []
            for ff_data in queue_data.get("flowfiles", []):
                ff = self._deserialize_flowfile(ff_data)
                if ff:
                    flowfiles.append(ff)
            result[key] = flowfiles

        total = sum(len(ffs) for ffs in result.values())
        logger.info(f"Restored {total} FlowFiles from checkpoint")
        return result

    def list_checkpoints(self) -> List[Dict[str, Any]]:
        """List available checkpoints."""
        result = []
        for cp_file in sorted(self._dir.glob("checkpoint_*.json")):
            try:
                with open(cp_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                total_ffs = sum(len(q["flowfiles"]) for q in data.get("queues", []))
                result.append({
                    "file": str(cp_file),
                    "timestamp": data.get("timestamp", ""),
                    "flow_version": data.get("flow_version", 0),
                    "total_flowfiles": total_ffs,
                })
            except Exception:
                pass
        return result

    def save_layout(self, positions: Dict[str, tuple]):
        """Save node layout positions to a dedicated file."""
        layout_file = self._dir / "layout.json"
        data = {k: {"x": v[0], "y": v[1]} for k, v in positions.items()}
        with self._lock:
            with open(layout_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)

    def load_layout(self) -> Dict[str, tuple]:
        """Load node layout positions. Returns {task_id: (x, y)}."""
        layout_file = self._dir / "layout.json"
        if not layout_file.exists():
            return {}
        try:
            with open(layout_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return {k: (v["x"], v["y"]) for k, v in data.items()}
        except Exception:
            return {}

    def clear(self):
        """Remove all checkpoints."""
        for f in self._dir.glob("checkpoint_*.json"):
            f.unlink()
        # Clean content files
        for f in self._content_dir.iterdir():
            if f.is_file():
                f.unlink()

    def _serialize_flowfile(self, ff: FlowFile) -> Dict[str, Any]:
        """Serialize a FlowFile for checkpointing."""
        data = {
            "process_id": ff.process_id,
            "attributes": ff.get_attributes(),
            "size": ff.size(),
        }

        content = ff.get_content()
        if len(content) <= INLINE_MAX_BYTES:
            data["content_b64"] = base64.b64encode(content).decode("ascii")
        else:
            # Write to content file
            content_file = self._content_dir / f"{ff.process_id}.bin"
            with open(content_file, 'wb') as f:
                f.write(content)
            data["content_file"] = str(content_file)

        return data

    def _deserialize_flowfile(self, data: Dict[str, Any]) -> Optional[FlowFile]:
        """Deserialize a FlowFile from checkpoint data."""
        try:
            if "content_b64" in data:
                content = base64.b64decode(data["content_b64"])
            elif "content_file" in data:
                content_path = Path(data["content_file"])
                if content_path.exists():
                    with open(content_path, 'rb') as f:
                        content = f.read()
                else:
                    logger.warning(f"Content file missing: {content_path}")
                    content = b""
            else:
                content = b""

            ff = FlowFile(
                content=content,
                attributes=data.get("attributes", {}),
            )
            return ff
        except Exception as e:
            logger.error(f"Failed to deserialize FlowFile: {e}")
            return None

    def _trim_checkpoints(self):
        """Keep only the most recent checkpoints."""
        checkpoints = sorted(self._dir.glob("checkpoint_*.json"))
        while len(checkpoints) > self._max_checkpoints:
            old = checkpoints.pop(0)
            old.unlink()
