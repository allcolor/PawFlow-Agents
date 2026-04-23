"""Tests for ClaudeCode._cc_project_key — the project-bucket derivation.

When CC starts, it derives a project_key from cwd by stripping the
leading slash and replacing every non-alphanum character (including `_`)
with `-`. Sessions get stored under
<CLAUDE_CONFIG_DIR>/projects/<project_key>/<sid>.jsonl.

The per-exec mount-namespace gives CC a cwd of `/cc_sessions/<conv>/<agent>`
(after binding the user's slot over /cc_sessions), so the key is
`-cc-sessions-<conv>-<agent>` — CC maps BOTH `/` and `_` to `-` in its
on-disk project key. Native (non-pool) mode keeps cwd=workdir.

If this derivation drifts, session resume breaks silently — we'd look
for session files at a project key CC never used.
"""

import os
import unittest
from unittest.mock import patch

from core.llm_providers.claude_code import LLMClaudeCodeMixin as _Cls


class TestCCProjectKey(unittest.TestCase):

    def test_pool_key_drops_user_segment(self):
        with patch(
            'core.llm_providers.claude_code._get_sessions_base',
            return_value='/host/data/runtime/sessions/claude',
        ):
            key = _Cls._cc_project_key(
                '/host/data/runtime/sessions/claude/alice/conv123/agent1',
                containerize=True)
        self.assertEqual(key, '-cc-sessions-conv123-agent1')

    def test_pool_key_with_nested_subagent_path(self):
        with patch(
            'core.llm_providers.claude_code._get_sessions_base',
            return_value='/host/sessions',
        ):
            key = _Cls._cc_project_key(
                '/host/sessions/alice/conv1/a/sub',
                containerize=True)
        # All segments after <user> become part of the key
        self.assertEqual(key, '-cc-sessions-conv1-a-sub')

    def test_native_key_uses_workdir_directly(self):
        # Native mode: CC's cwd is the host workdir, so the key derives
        # from the full path.
        key = _Cls._cc_project_key(
            '/some/host/path', containerize=False)
        self.assertEqual(key, '-some-host-path')

    def test_native_key_no_leading_dash_doubling(self):
        # Already-leading-slash paths get one `-` prefix, not two
        key = _Cls._cc_project_key('/x', containerize=False)
        self.assertEqual(key, '-x')
        self.assertFalse(key.startswith('--'))


if __name__ == '__main__':
    unittest.main()
