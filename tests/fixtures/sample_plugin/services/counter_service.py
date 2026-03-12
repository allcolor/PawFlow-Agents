"""Sample plugin service: a simple counter."""

from core import Service
from typing import Dict, Any, List


class CounterService(Service):
    TYPE = "counter"
    VERSION = "1.0.0"
    NAME = "Counter Service"
    DESCRIPTION = "A simple counter service for testing"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._count = 0

    def connect(self):
        self._count = int(self.config.get("start", 0))

    def increment(self) -> int:
        self._count += 1
        return self._count

    def get_count(self) -> int:
        return self._count
