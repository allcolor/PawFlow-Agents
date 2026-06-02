"""Tests for the expanded PawFlow CLI (P12)."""

import argparse
import json
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open


# We need to mock 'tasks' import before importing cli, since cli.py
# does `import tasks` at module level which triggers task registration.
# The tasks module is already available in the test environment, so we
# import cli directly.
import cli


class TestGuiArgparse(unittest.TestCase):
    """Test gui command argument parsing."""

    def _parse(self, args_list):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest='command')
        sp = sub.add_parser('gui')
        sp.add_argument('--host', default='localhost')
        sp.add_argument('--port', type=int, default=8501)
        sp.add_argument('--headless', action='store_true')
        return parser.parse_args(args_list)

    def test_gui_defaults(self):
        args = self._parse(['gui'])
        self.assertEqual(args.host, 'localhost')
        self.assertEqual(args.port, 8501)
        self.assertFalse(args.headless)

    def test_gui_headless(self):
        args = self._parse(['gui', '--headless', '--port', '9000'])
        self.assertTrue(args.headless)
        self.assertEqual(args.port, 9000)


class TestClusterCommand(unittest.TestCase):
    """Test cluster command."""

    @patch('urllib.request.urlopen')
    def test_cluster_status_enabled(self, mock_urlopen):
        response_data = json.dumps({
            "cluster_enabled": True,
            "status": {
                "total_instances": 3,
                "role": "coordinator",
                "coordinator_host": "host1",
                "instance_id": "i1",
                "instances": [
                    {"id": "i1", "role": "coordinator", "host": "host1"},
                    {"id": "i2", "role": "worker", "host": "host2"},
                ]
            }
        }).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_data
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        args = argparse.Namespace(action='status', api_url=None)
        with patch('builtins.print') as mock_print:
            result = cli.cmd_cluster(args)
        self.assertEqual(result, 0)
        # Check that cluster info was printed
        calls = [str(c) for c in mock_print.call_args_list]
        combined = ' '.join(calls)
        self.assertIn('3 instances', combined)

    @patch('urllib.request.urlopen')
    def test_cluster_status_disabled(self, mock_urlopen):
        response_data = json.dumps({"cluster_enabled": False}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_data
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        args = argparse.Namespace(action='status', api_url=None)
        with patch('builtins.print') as mock_print:
            result = cli.cmd_cluster(args)
        self.assertEqual(result, 0)
        mock_print.assert_called_with("Cluster mode not enabled.")

    @patch('urllib.request.urlopen', side_effect=Exception("Connection refused"))
    def test_cluster_status_unreachable(self, mock_urlopen):
        args = argparse.Namespace(action='status', api_url='http://localhost:9999')
        with patch('builtins.print') as mock_print:
            result = cli.cmd_cluster(args)
        self.assertEqual(result, 0)
        call_str = mock_print.call_args[0][0]
        self.assertIn('Cannot reach API', call_str)


class TestAdminUserCommand(unittest.TestCase):
    def setUp(self):
        import tempfile
        import core.paths as paths
        from core.security import SecurityManager

        self.tmp = tempfile.TemporaryDirectory()
        self._orig_users_file = paths.USERS_FILE
        paths.USERS_FILE = Path(self.tmp.name) / "users.json"
        SecurityManager._instance = None

    def tearDown(self):
        import core.paths as paths
        from core.security import SecurityManager

        SecurityManager._instance = None
        paths.USERS_FILE = self._orig_users_file
        self.tmp.cleanup()

    def test_admin_user_create_from_empty_users_file(self):
        from core.security import SecurityManager

        args = argparse.Namespace(
            action="create",
            username="rescue",
            password="Rescue-password-123",
            password_env="",
            email="rescue@example.com",
            display_name="Rescue Admin",
        )

        with patch('builtins.print'):
            result = cli.cmd_admin_user(args)

        self.assertEqual(result, 0)
        user = SecurityManager.get_instance().get_user("rescue")
        self.assertIsNotNone(user)
        self.assertEqual(user.role.value, "admin")
        self.assertTrue(user.enabled)
        self.assertTrue(user.check_password("Rescue-password-123"))
        self.assertEqual(user.email, "rescue@example.com")

    def test_admin_user_create_repairs_passwordless_existing_admin(self):
        from core.security import SecurityManager, Role

        sm = SecurityManager.get_instance()
        user = sm.create_user("quentin.anciaux", "", Role.ADMIN,
                              email="quentin.anciaux@allcolor.org")
        user.password_hash = ""
        user.enabled = False
        sm._save_users()
        args = argparse.Namespace(
            action="create",
            username="quentin.anciaux",
            password="New-admin-password-123",
            password_env="",
            email="quentin.anciaux@allcolor.org",
            display_name="Quentin Anciaux",
        )

        with patch('builtins.print'):
            result = cli.cmd_admin_user(args)

        self.assertEqual(result, 0)
        repaired = SecurityManager.get_instance().get_user("quentin.anciaux")
        self.assertEqual(repaired.role.value, "admin")
        self.assertTrue(repaired.enabled)
        self.assertTrue(repaired.check_password("New-admin-password-123"))


if __name__ == '__main__':
    unittest.main()
