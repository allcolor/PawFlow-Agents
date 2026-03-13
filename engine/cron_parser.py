"""Simple CRON expression parser.

Used by CronTriggerTask to match CRON schedules.
Format: "minute hour day month weekday"
Supports: exact (5), wildcard (*), interval (*/N), range (1-3)
"""

from datetime import datetime
from typing import Dict


class SimpleCronParser:
    """Parser for CRON expressions."""

    def parse_field(self, field: str, max_value: int) -> set:
        if field.startswith("*/"):
            step = int(field[2:])
            return set(range(0, max_value + 1, step))

        if field == "*":
            return set(range(max_value + 1))

        if "-" in field and not field.startswith("-"):
            start, end = map(int, field.split("-"))
            return set(range(start, end + 1))

        return {int(field)}

    def parse(self, cron_expression: str) -> Dict[str, set]:
        fields = cron_expression.strip().split()
        if len(fields) != 5:
            raise ValueError(f"Invalid CRON expression: {cron_expression}")

        names = ["minute", "hour", "day", "month", "weekday"]
        limits = [59, 23, 31, 12, 6]

        return {
            name: self.parse_field(field, limit)
            for name, field, limit in zip(names, fields, limits)
        }

    def matches(self, cron_expression: str, dt: datetime) -> bool:
        try:
            parsed = self.parse(cron_expression)

            if dt.minute not in parsed["minute"]:
                return False
            if dt.hour not in parsed["hour"]:
                return False
            if dt.day not in parsed["day"]:
                return False
            if dt.month not in parsed["month"]:
                return False

            # Python weekday: mon=0..sun=6 -> CRON: sun=0..sat=6
            cron_weekday = (dt.weekday() + 1) % 7
            if cron_weekday not in parsed["weekday"]:
                return False

            return True
        except (ValueError, IndexError):
            return False
