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


class TestPluginsCommand(unittest.TestCase):
    """Test plugins command."""

    @patch('cli.PluginManager', create=True)
    def test_plugins_list_empty(self, _):
        with patch('core.plugin.PluginManager') as MockPM:
            mock_pm = MagicMock()
            mock_pm.list_plugins.return_value = []
            MockPM.return_value = mock_pm

            args = argparse.Namespace(action='list', path=None, plugin_id=None)
            with patch('builtins.print') as mock_print:
                result = cli.cmd_plugins(args)
            self.assertEqual(result, 0)
            mock_print.assert_called_with("No plugins installed.")

    @patch('core.plugin.PluginManager')
    def test_plugins_list_with_plugins(self, MockPM):
        mock_pm = MagicMock()
        mock_pm.list_plugins.return_value = [
            {'id': 'test-plugin', 'version': '1.0.0', 'description': 'A test plugin'}
        ]
        MockPM.return_value = mock_pm

        args = argparse.Namespace(action='list', path=None, plugin_id=None)
        with patch('builtins.print') as mock_print:
            result = cli.cmd_plugins(args)
        self.assertEqual(result, 0)
        mock_print.assert_called_once()
        call_str = mock_print.call_args[0][0]
        self.assertIn('test-plugin', call_str)

    @patch('core.plugin.PluginManager')
    def test_plugins_install(self, MockPM):
        mock_pm = MagicMock()
        mock_descriptor = MagicMock()
        mock_descriptor.id = 'new-plugin'
        mock_pm.install.return_value = mock_descriptor
        MockPM.return_value = mock_pm

        args = argparse.Namespace(action='install', path='/tmp/plugin.pfp', plugin_id=None)
        with patch('builtins.print') as mock_print:
            result = cli.cmd_plugins(args)
        self.assertEqual(result, 0)
        mock_pm.install.assert_called_once_with('/tmp/plugin.pfp')

    def test_plugins_install_no_path(self):
        args = argparse.Namespace(action='install', path=None, plugin_id=None)
        with patch('builtins.print'):
            result = cli.cmd_plugins(args)
        self.assertEqual(result, 1)

    @patch('core.plugin.PluginManager')
    def test_plugins_remove(self, MockPM):
        mock_pm = MagicMock()
        MockPM.return_value = mock_pm

        args = argparse.Namespace(action='remove', path=None, plugin_id='old-plugin')
        with patch('builtins.print'):
            result = cli.cmd_plugins(args)
        self.assertEqual(result, 0)
        mock_pm.uninstall.assert_called_once_with('old-plugin')

    def test_plugins_remove_no_id(self):
        args = argparse.Namespace(action='remove', path=None, plugin_id=None)
        with patch('builtins.print'):
            result = cli.cmd_plugins(args)
        self.assertEqual(result, 1)


class TestExportCommand(unittest.TestCase):
    """Test export command."""

    @patch('core.plugin.export_flow_as_plugin')
    def test_export_success(self, mock_export):
        import tempfile, os
        # Create a temp flow file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"name": "test", "tasks": {}}, f)
            tmp_path = f.name
        try:
            args = argparse.Namespace(flow_path=tmp_path, output=None)
            with patch('builtins.print') as mock_print:
                result = cli.cmd_export(args)
            self.assertEqual(result, 0)
            mock_export.assert_called_once()
        finally:
            os.unlink(tmp_path)

    def test_export_missing_file(self):
        args = argparse.Namespace(flow_path='/nonexistent/flow.json', output=None)
        with patch('builtins.print'):
            result = cli.cmd_export(args)
        self.assertEqual(result, 1)


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


if __name__ == '__main__':
    unittest.main()
