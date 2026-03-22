"""ToolHandler — abstract base class for agent tool handlers.

Separated from tool_registry to avoid circular imports when handlers
are in core/handlers/ modules.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any


class ToolHandler(ABC):
    """Interface for an executable tool that an agent can call."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique tool name."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description for the LLM."""

    @property
    @abstractmethod
    def parameters_schema(self) -> Dict[str, Any]:
        """JSON Schema describing the tool's input parameters."""

    @abstractmethod
    def execute(self, arguments: Dict[str, Any]) -> str:
        """Execute the tool and return a text result."""
