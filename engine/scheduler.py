"""Backward-compatibility shim — SimpleCronParser moved to engine.cron_parser."""

from engine.cron_parser import SimpleCronParser

__all__ = ["SimpleCronParser"]
