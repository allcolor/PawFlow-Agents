"""Continuous-executor result/statistics dataclasses.

Split out of continuous_executor.py to keep each module <=800 lines.
Re-exported from engine.continuous_executor (invariant 1: import-path stability).
"""

from typing import Dict, Any, List
from dataclasses import dataclass, field
from datetime import datetime

from core import FlowFile


@dataclass
class TaskStats:
    """Per-task execution statistics."""
    task_id: str
    task_type: str
    invocations: int = 0
    success_count: int = 0
    error_count: int = 0
    flowfiles_in: int = 0
    flowfiles_out: int = 0
    bytes_in: int = 0
    bytes_out: int = 0
    total_duration_ms: float = 0.0
    avg_duration_ms: float = 0.0


@dataclass
class ExecutionResult:
    """Result of a flow execution (batch mode)."""
    flow_id: str
    success: bool
    output_flowfiles: List[FlowFile] = field(default_factory=list)
    statistics: Dict[str, Any] = field(default_factory=dict)
    errors: List[Dict[str, Any]] = field(default_factory=list)
    duration_ms: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)
    task_statistics: Dict[str, TaskStats] = field(default_factory=dict)

