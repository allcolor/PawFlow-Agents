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
from typing import Dict, Any, List

from core import FlowFile, TaskFactory
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
        self.max_bin_age = int(self.config.get('max_bin_age', 300))
        self.header = self.config.get('header', '').encode('utf-8')
        self.footer = self.config.get('footer', '').encode('utf-8')
        # Bins: correlation_key -> list of FlowFiles
        self._bins: Dict[str, List[FlowFile]] = {}
        self._bin_created: Dict[str, float] = {}
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
            self._bins[key].append(flowfile)
            logger.debug(
                "mergeContent: bin '%s' now %d/%d items (%d bytes)",
                key, len(self._bins[key]), self.min_entries, len(flowfile.get_content()),
            )

            # Flush if ready
            if len(self._bins[key]) >= self.min_entries:
                logger.debug("mergeContent: flushing bin '%s' with %d items", key, len(self._bins[key]))
                return self._flush_bin(key)

        return []

    def _flush_bin(self, key: str) -> List[FlowFile]:
        """Merge all FlowFiles in a bin. Must hold self._lock."""
        buf = self._bins.pop(key, [])
        self._bin_created.pop(key, None)
        if not buf:
            return []

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
                'type': 'integer', 'required': False, 'default': 300,
                'description': 'Max seconds before incomplete bin is discarded (0=no timeout)',
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
