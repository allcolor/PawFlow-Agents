# MergeContent Task

"""MergeContent — Merge multiple FlowFiles into one, with correlation support.

Two modes:
1. **Correlated** (default): groups FlowFiles by `fragment.identifier` attribute.
   Each group flushes independently when it reaches `min_entries`.
   This prevents mixing FlowFiles from different "waves" through the flow.
2. **Uncorrelated** (correlation_attribute=""): flat buffer, merges the first
   `min_entries` FlowFiles regardless of origin (legacy behavior).

The executor sets `fragment.identifier` automatically when a FlowFile is
cloned to multiple outgoing connections (fan-out). All clones share the
same identifier, so they are grouped together at the merge point.

Config:
    separator: str — separator between merged contents (default: "\\n")
    min_entries: int — minimum FlowFiles per group before flush (default: 2)
    correlation_attribute: str — attribute to group by (default: "fragment.identifier")
        Set to "" for uncorrelated mode (legacy).
    max_bin_age: int — seconds before an incomplete bin is discarded (default: 300, 0=no timeout)
    header: str — prepended to merged content
    footer: str — appended to merged content
"""

import logging
import threading
import time
from typing import Any, Dict, List

from core import FlowFile, TaskError, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


class MergeContentTask(BaseTask):
    """Merge multiple FlowFiles into one, with correlation support."""

    TYPE = "mergeContent"
    VERSION = "2.0.0"
    NAME = "MergeContent"
    DESCRIPTION = "Merge multiple FlowFiles into one (supports correlation)"
    ICON = "merge"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.separator = self.config.get(
            'separator', self.config.get('delimiter', '\n')
        ).encode('utf-8')
        self.min_entries = int(self.config.get('min_entries', 2))
        self.correlation_attribute = self.config.get(
            'correlation_attribute', 'fragment.identifier'
        )
        self.max_bin_age = int(self.config.get('max_bin_age', 0) or 0)
        self.expected_count_attribute = str(
            self.config.get('expected_count_attribute', '') or '')
        self.max_bin_flowfiles = int(self.config.get('max_bin_flowfiles', 0) or 0)
        self.max_bin_bytes = int(self.config.get('max_bin_bytes', 0) or 0)
        self.header = self.config.get('header', '').encode('utf-8')
        self.footer = self.config.get('footer', '').encode('utf-8')
        # Bins: correlation_key -> list of FlowFiles
        self._bins: Dict[str, List[FlowFile]] = {}
        self._bin_created: Dict[str, float] = {}
        self._bin_expected: Dict[str, int] = {}
        self._bin_bytes: Dict[str, int] = {}
        self._lock = threading.Lock()

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Buffer the FlowFile and flush when the bin is complete."""
        # Determine correlation key
        if self.correlation_attribute:
            key = flowfile.get_attribute(self.correlation_attribute) or '_default'
        else:
            key = '_default'

        with self._lock:
            # Expire stale bins first
            self._expire_stale_bins()

            # Add to bin
            if key not in self._bins:
                self._bins[key] = []
                self._bin_created[key] = time.time()
                self._bin_bytes[key] = 0
            expected = self.min_entries
            if self.expected_count_attribute:
                raw_expected = flowfile.get_attribute(self.expected_count_attribute)
                try:
                    expected = int(raw_expected or "")
                except (TypeError, ValueError) as exc:
                    raise TaskError(
                        f"Invalid expected count attribute '{self.expected_count_attribute}'"
                    ) from exc
                if expected < 1:
                    raise TaskError("Expected merge count must be >= 1")
            if self.max_bin_flowfiles > 0 and expected > self.max_bin_flowfiles:
                raise TaskError(
                    f"Expected merge count {expected} exceeds max_bin_flowfiles "
                    f"{self.max_bin_flowfiles}")
            previous_expected = self._bin_expected.setdefault(key, expected)
            if previous_expected != expected:
                raise TaskError(
                    f"Inconsistent expected merge count for bin '{key}': "
                    f"{previous_expected} != {expected}")
            next_count = len(self._bins[key]) + 1
            next_bytes = self._bin_bytes[key] + flowfile.size()
            if self.max_bin_flowfiles > 0 and next_count > self.max_bin_flowfiles:
                raise TaskError(
                    f"Merge bin '{key}' exceeds max_bin_flowfiles "
                    f"{self.max_bin_flowfiles}")
            if self.max_bin_bytes > 0 and next_bytes > self.max_bin_bytes:
                raise TaskError(
                    f"Merge bin '{key}' exceeds max_bin_bytes {self.max_bin_bytes}")
            self._bins[key].append(flowfile)
            self._bin_bytes[key] = next_bytes
            logger.debug(
                "mergeContent: bin '%s' now %d/%d items (%d bytes)",
                key, len(self._bins[key]), expected, flowfile.size(),
            )

            # Flush if ready
            if len(self._bins[key]) >= expected:
                logger.debug("mergeContent: flushing bin '%s' with %d items", key, len(self._bins[key]))
                return self._flush_bin(key)

        return []

    @staticmethod
    def _ordered(buf: List[FlowFile]) -> List[FlowFile]:
        """Restore split order when the fragments carry one.

        splitContent stamps `fragment.index` on every piece it emits, and this
        task never read it: a bin merged in arrival order, so a split -> work
        -> merge round trip could hand the document back with its pieces
        shuffled, the more reliably the more the branches differ in cost.

        Arrival order stays the rule when the fragments carry no usable index.
        A plain executor fan-out tags clones with `fragment.identifier` only,
        and those have no meaningful order to restore -- inventing one there
        would be a different guess, not a fix.
        """
        indexed = []
        for flowfile in buf:
            raw = flowfile.get_attribute('fragment.index')
            if raw is None or str(raw) == '':
                return buf
            try:
                indexed.append((int(raw), flowfile))
            except (TypeError, ValueError):
                return buf
        # Stable: equal indices keep the order they arrived in.
        return [flowfile for _, flowfile in sorted(indexed, key=lambda p: p[0])]

    def _flush_bin(self, key: str) -> List[FlowFile]:
        """Merge all FlowFiles in a bin. Must hold self._lock."""
        buf = self._bins.pop(key, [])
        self._bin_created.pop(key, None)
        self._bin_expected.pop(key, None)
        self._bin_bytes.pop(key, None)
        if not buf:
            return []

        buf = self._ordered(buf)
        contents = [ff.get_content() for ff in buf]
        merged = self.separator.join(contents)

        if self.header:
            merged = self.header + self.separator + merged
        if self.footer:
            merged = merged + self.separator + self.footer

        result = buf[0].clone()
        result.set_content(merged)
        result.set_attribute('merge.count', str(len(buf)))
        result.set_attribute('merge.correlation', key)
        result.set_attribute('fileSize', str(len(merged)))

        return [result]

    def _expire_stale_bins(self):
        """Discard bins older than max_bin_age. Must hold self._lock."""
        if self.max_bin_age <= 0:
            return
        now = time.time()
        expired = [
            k for k, t in self._bin_created.items()
            if now - t > self.max_bin_age
        ]
        for k in expired:
            count = len(self._bins.get(k, []))
            age = now - self._bin_created.get(k, now)
            logger.warning(
                f"mergeContent: discarding stale bin '{k}' "
                f"({count}/{self.min_entries} items, age {age:.0f}s)"
            )
            self._bins.pop(k, None)
            self._bin_created.pop(k, None)
            self._bin_expected.pop(k, None)
            self._bin_bytes.pop(k, None)

    def reset(self):
        """Clear all bins. Called when queues are cleared."""
        with self._lock:
            count = sum(len(b) for b in self._bins.values())
            if count:
                logger.info(
                    f"mergeContent: reset() discarding {count} buffered FlowFiles "
                    f"across {len(self._bins)} bins"
                )
            self._bins.clear()
            self._bin_created.clear()
            self._bin_expected.clear()
            self._bin_bytes.clear()

    def checkpoint_state(self, serialize_flowfile) -> Dict[str, Any]:
        """Return a versioned snapshot of incomplete correlation bins."""
        with self._lock:
            return {
                "schema_version": 1,
                "bins": {
                    key: [serialize_flowfile(flowfile) for flowfile in flowfiles]
                    for key, flowfiles in self._bins.items()
                },
                "bin_created": dict(self._bin_created),
                "bin_expected": dict(self._bin_expected),
            }

    def restore_checkpoint_state(self, state, deserialize_flowfile) -> None:
        """Restore incomplete bins before queue recovery resumes scheduling."""
        if not isinstance(state, dict) or state.get("schema_version") != 1:
            raise ValueError("mergeContent checkpoint schema_version must be 1")
        raw_bins = state.get("bins")
        raw_created = state.get("bin_created")
        raw_expected = state.get("bin_expected", {})
        if (not isinstance(raw_bins, dict) or not isinstance(raw_created, dict)
                or not isinstance(raw_expected, dict)):
            raise ValueError("mergeContent checkpoint bins are invalid")
        bins: Dict[str, List[FlowFile]] = {}
        for key, rows in raw_bins.items():
            if not isinstance(rows, list):
                raise ValueError("mergeContent checkpoint bin must be a list")
            restored = [deserialize_flowfile(row) for row in rows]
            if any(flowfile is None for flowfile in restored):
                raise ValueError("mergeContent checkpoint FlowFile is invalid")
            bins[str(key)] = restored
        created = {}
        expected = {}
        sizes = {}
        for key in bins:
            value = raw_created.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("mergeContent checkpoint timestamp is invalid")
            created[key] = float(value)
            expected_value = raw_expected.get(key, self.min_entries)
            if (isinstance(expected_value, bool) or not isinstance(expected_value, int)
                    or expected_value < 1
                    or (self.max_bin_flowfiles > 0
                        and expected_value > self.max_bin_flowfiles)):
                raise ValueError("mergeContent checkpoint expected count is invalid")
            expected[key] = expected_value
            sizes[key] = sum(flowfile.size() for flowfile in bins[key])
            if self.max_bin_bytes > 0 and sizes[key] > self.max_bin_bytes:
                raise ValueError("mergeContent checkpoint bin exceeds max_bin_bytes")
        with self._lock:
            self._bins = bins
            self._bin_created = created
            self._bin_expected = expected
            self._bin_bytes = sizes

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'separator': {
                'type': 'string', 'required': False, 'default': '\\n',
                'description': 'Separator between merged contents',
            },
            'min_entries': {
                'type': 'integer', 'required': False, 'default': 2,
                'description': 'Minimum FlowFiles per group before merge',
            },
            'correlation_attribute': {
                'type': 'string', 'required': False,
                'default': 'fragment.identifier',
                'description': (
                    'Attribute to group FlowFiles by. FlowFiles with the same '
                    'value are merged together. Set to "" for uncorrelated mode.'
                ),
            },
            'max_bin_age': {
                'type': 'integer', 'required': False, 'default': 0,
                'description': 'Max seconds before incomplete bin is discarded (0=no timeout)',
            },
            'expected_count_attribute': {
                'type': 'string', 'required': False, 'default': '',
                'description': 'FlowFile attribute containing the expected bin size',
            },
            'max_bin_flowfiles': {
                'type': 'integer', 'required': False, 'default': 0,
                'description': 'Maximum FlowFiles accumulated per bin; 0 means unlimited',
            },
            'max_bin_bytes': {
                'type': 'integer', 'required': False, 'default': 0,
                'description': 'Maximum content bytes accumulated per bin; 0 means unlimited',
            },
            'header': {
                'type': 'string', 'required': False,
                'description': 'Header prepended to merged content',
            },
            'footer': {
                'type': 'string', 'required': False,
                'description': 'Footer appended to merged content',
            },
        }


TaskFactory.register(MergeContentTask)
