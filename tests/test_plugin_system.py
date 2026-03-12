"""Tests for the plugin system — tasks, services, flows, archives."""

import json
import os
import shutil
import tempfile
import pytest

from tasks import register_all_tasks
register_all_tasks()

from core import TaskFactory, ServiceFactory, FlowFile
from core.plugin import (
    PluginManager, PluginDescriptor, LoadedPlugin,
    create_plugin_archive, get_plugin_manager,
    export_flow_as_plugin,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
SAMPLE_PLUGIN = os.path.join(FIXTURES, "sample_plugin")
EXTERNAL_FLOW = os.path.join(FIXTURES, "external_flow.json")


@pytest.fixture
def plugin_mgr(tmp_path):
    """Create a PluginManager with a temp plugins dir."""
    return PluginManager(plugins_dir=str(tmp_path / "plugins"))


class TestPluginDescriptor:

    def test_from_dict(self):
        data = {
            "id": "com.test.foo",
            "name": "Foo Plugin",
            "version": "2.0.0",
            "tasks": ["tasks/foo.py:FooTask"],
            "services": ["services/bar.py:BarSvc"],
            "flows": ["flows/baz.json"],
        }
        desc = PluginDescriptor.from_dict(data)
        assert desc.id == "com.test.foo"
        assert desc.name == "Foo Plugin"
        assert desc.version == "2.0.0"
        assert len(desc.tasks) == 1
        assert len(desc.services) == 1
        assert len(desc.flows) == 1

    def test_to_dict_roundtrip(self):
        data = {"id": "test", "name": "Test", "version": "1.0.0"}
        desc = PluginDescriptor.from_dict(data)
        d = desc.to_dict()
        assert d["id"] == "test"
        assert d["version"] == "1.0.0"


class TestPluginInstallFromDirectory:

    def test_install_from_directory(self, plugin_mgr):
        desc = plugin_mgr.install(SAMPLE_PLUGIN)
        assert desc.id == "com.test.sample-plugin"
        assert desc.name == "Sample Plugin"

        # Check it's in installed list
        installed = plugin_mgr.list_installed()
        assert len(installed) == 1
        assert installed[0]["id"] == "com.test.sample-plugin"

    def test_load_plugin_tasks(self, plugin_mgr):
        plugin_mgr.install(SAMPLE_PLUGIN)
        results = plugin_mgr.load_all()
        assert len(results) == 1

        loaded = results[0]
        assert "uppercase" in loaded.loaded_tasks

        # Task should be registered in TaskFactory
        assert "uppercase" in TaskFactory.list_types()

        # Task should work
        task_class = TaskFactory.get("uppercase")
        task = task_class({})
        ff = FlowFile(content=b"hello world")
        result = task.execute(ff)
        assert result[0].get_content() == b"HELLO WORLD"

    def test_load_plugin_services(self, plugin_mgr):
        plugin_mgr.install(SAMPLE_PLUGIN)
        results = plugin_mgr.load_all()
        loaded = results[0]
        assert "counter" in loaded.loaded_services

        # Service should be registered in ServiceFactory
        assert "counter" in ServiceFactory.list_types()

        # Service should work
        svc_class = ServiceFactory.get("counter")
        svc = svc_class({"start": "5"})
        svc.connect()
        assert svc.get_count() == 5
        assert svc.increment() == 6

    def test_load_plugin_flows(self, plugin_mgr):
        plugin_mgr.install(SAMPLE_PLUGIN)
        results = plugin_mgr.load_all()
        loaded = results[0]
        assert "plugin_sample_flow" in loaded.loaded_flows

        # Flow should be in registry
        flows = plugin_mgr.list_flows()
        assert len(flows) >= 1
        assert any(f["id"] == "plugin_sample_flow" for f in flows)

        # Get flow dict
        flow_dict = plugin_mgr.get_flow("plugin_sample_flow")
        assert flow_dict is not None
        assert flow_dict["name"] == "Sample Plugin Flow"
        assert "upper" in flow_dict["tasks"]


class TestPluginArchive:

    def test_create_and_install_archive(self, plugin_mgr, tmp_path):
        # Create archive
        archive_path = str(tmp_path / "sample.pfp")
        result_path = create_plugin_archive(SAMPLE_PLUGIN, archive_path)
        assert os.path.exists(result_path)

        # Install from archive
        desc = plugin_mgr.install(result_path)
        assert desc.id == "com.test.sample-plugin"

        # Load and verify
        results = plugin_mgr.load_all()
        assert len(results) == 1
        assert "uppercase" in results[0].loaded_tasks

    def test_archive_excludes_pycache(self, tmp_path):
        # Create a temp plugin with __pycache__
        plugin_dir = tmp_path / "temp_plugin"
        shutil.copytree(SAMPLE_PLUGIN, plugin_dir)
        pycache = plugin_dir / "__pycache__"
        pycache.mkdir()
        (pycache / "junk.pyc").write_bytes(b"fake")

        archive_path = str(tmp_path / "clean.pfp")
        create_plugin_archive(str(plugin_dir), archive_path)

        import zipfile
        with zipfile.ZipFile(archive_path, 'r') as zf:
            names = zf.namelist()
            assert not any("__pycache__" in n for n in names)


class TestPluginUninstall:

    def test_uninstall(self, plugin_mgr):
        plugin_mgr.install(SAMPLE_PLUGIN)
        plugin_mgr.load_all()

        # Verify loaded
        assert plugin_mgr.get_plugin("com.test.sample-plugin") is not None

        # Uninstall
        plugin_mgr.uninstall("com.test.sample-plugin")
        assert plugin_mgr.get_plugin("com.test.sample-plugin") is None
        assert len(plugin_mgr.list_installed()) == 0

    def test_unload_removes_flows(self, plugin_mgr):
        plugin_mgr.install(SAMPLE_PLUGIN)
        plugin_mgr.load_all()
        assert plugin_mgr.get_flow("plugin_sample_flow") is not None

        plugin_mgr.unload_plugin("com.test.sample-plugin")
        assert plugin_mgr.get_flow("plugin_sample_flow") is None


class TestFlowImportExport:

    def test_import_external_flow(self, plugin_mgr):
        flow_dict = plugin_mgr.import_flow(EXTERNAL_FLOW)
        assert flow_dict["id"] == "external_etl_flow"
        assert flow_dict["name"] == "External ETL Flow"

        # Should be in flow registry
        flows = plugin_mgr.list_flows()
        assert any(f["id"] == "external_etl_flow" for f in flows)

    def test_export_flow(self, plugin_mgr, tmp_path):
        flow_dict = plugin_mgr.import_flow(EXTERNAL_FLOW)
        export_path = str(tmp_path / "exported.json")
        plugin_mgr.export_flow(flow_dict, export_path)

        # Read back and verify
        with open(export_path, 'r') as f:
            exported = json.load(f)
        assert exported["id"] == "external_etl_flow"
        # Internal fields should be stripped
        assert "_source" not in exported
        assert "_plugin_id" not in exported

    def test_import_nonexistent_raises(self, plugin_mgr):
        with pytest.raises(FileNotFoundError):
            plugin_mgr.import_flow("/nonexistent/flow.json")


class TestPluginErrors:

    def test_install_missing_descriptor(self, plugin_mgr, tmp_path):
        empty_dir = tmp_path / "empty_plugin"
        empty_dir.mkdir()
        with pytest.raises(ValueError, match="missing plugin.json"):
            plugin_mgr.install(str(empty_dir))

    def test_install_invalid_source(self, plugin_mgr):
        with pytest.raises(ValueError, match="Invalid plugin source"):
            plugin_mgr.install("nonexistent.txt")

    def test_bad_task_reference(self, plugin_mgr, tmp_path):
        # Create a plugin with a bad task reference
        plugin_dir = tmp_path / "bad_plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(json.dumps({
            "id": "com.test.bad",
            "name": "Bad Plugin",
            "tasks": ["tasks/nonexistent.py:NoClass"],
        }))

        plugin_mgr.install(str(plugin_dir))
        results = plugin_mgr.load_all()
        assert len(results) == 1
        assert len(results[0].errors) > 0  # Should have errors but not crash

    def test_reinstall_overwrites(self, plugin_mgr):
        plugin_mgr.install(SAMPLE_PLUGIN)
        # Install again should not raise
        plugin_mgr.install(SAMPLE_PLUGIN)
        installed = plugin_mgr.list_installed()
        assert len(installed) == 1


class TestExportFlowAsPlugin:

    def test_export_basic_flow(self, tmp_path):
        """Export a flow with built-in tasks → .pfp with flow only."""
        flow_config = {
            "name": "Test Export Flow",
            "tasks": {
                "log1": {"type": "log", "parameters": {"message": "hello"}},
            },
            "relations": [],
        }
        out = str(tmp_path / "export.pfp")
        result = export_flow_as_plugin(flow_config, out)
        assert os.path.exists(result)

        # Verify archive contents
        import zipfile
        with zipfile.ZipFile(result, 'r') as zf:
            names = zf.namelist()
            assert "plugin.json" in names
            assert "flows/flow.json" in names

            desc = json.loads(zf.read("plugin.json"))
            assert desc["id"] == "export.test-export-flow"
            assert desc["flows"] == ["flows/flow.json"]

            flow = json.loads(zf.read("flows/flow.json"))
            assert flow["name"] == "Test Export Flow"

    def test_export_with_custom_id(self, tmp_path):
        """Export with custom plugin ID and metadata."""
        flow_config = {"name": "My Flow", "tasks": {}}
        out = str(tmp_path / "custom.pfp")
        export_flow_as_plugin(
            flow_config, out,
            plugin_id="com.test.custom",
            plugin_name="Custom Plugin",
            author="Test Author",
            description="Custom description",
        )
        import zipfile
        with zipfile.ZipFile(out, 'r') as zf:
            desc = json.loads(zf.read("plugin.json"))
            assert desc["id"] == "com.test.custom"
            assert desc["name"] == "Custom Plugin"
            assert desc["author"] == "Test Author"
            assert desc["description"] == "Custom description"

    def test_export_strips_internal_keys(self, tmp_path):
        """Internal keys like _plugin_id are stripped from exported flow."""
        flow_config = {
            "name": "Clean Flow",
            "_plugin_id": "should-be-removed",
            "_internal": "also-removed",
            "tasks": {},
        }
        out = str(tmp_path / "clean.pfp")
        export_flow_as_plugin(flow_config, out)

        import zipfile
        with zipfile.ZipFile(out, 'r') as zf:
            flow = json.loads(zf.read("flows/flow.json"))
            assert "_plugin_id" not in flow
            assert "_internal" not in flow
            assert flow["name"] == "Clean Flow"

    def test_export_roundtrip(self, tmp_path, plugin_mgr):
        """Export a flow, then import it as a plugin."""
        flow_config = {
            "name": "Roundtrip Flow",
            "tasks": {"t1": {"type": "log", "parameters": {"message": "test"}}},
            "relations": [],
        }
        out = str(tmp_path / "roundtrip.pfp")
        export_flow_as_plugin(flow_config, out, plugin_id="com.test.roundtrip")

        # Install the exported plugin
        desc = plugin_mgr.install(out)
        assert desc.id == "com.test.roundtrip"
        assert desc.flows == ["flows/flow.json"]

        # Load it
        results = plugin_mgr.load_all()
        assert len(results) == 1
        assert "flows/flow.json" in results[0].loaded_flows or len(results[0].loaded_flows) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
