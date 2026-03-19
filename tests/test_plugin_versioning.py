"""Tests for plugin versioning — semver, upgrade/downgrade, dependency tracking."""

import json
import os
import shutil
import zipfile
import pytest

from tasks import register_all_tasks
register_all_tasks()

from core.plugin import PluginManager, PluginDescriptor, PluginVersion

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
SAMPLE_PLUGIN = os.path.join(FIXTURES, "sample_plugin")


@pytest.fixture
def plugin_mgr(tmp_path):
    """Create a PluginManager with a temp plugins dir."""
    return PluginManager(plugins_dir=str(tmp_path / "plugins"))


def _make_plugin_dir(tmp_path, plugin_id, version, deps=None, tasks=None):
    """Helper: create a minimal plugin directory."""
    plugin_dir = tmp_path / f"{plugin_id}-{version}"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    descriptor = {
        "id": plugin_id,
        "name": f"Plugin {plugin_id}",
        "version": version,
        "min_pawflow_version": "1.0.0",
        "tasks": tasks or [],
        "services": [],
        "flows": [],
        "dependencies": deps or {},
    }
    (plugin_dir / "plugin.json").write_text(json.dumps(descriptor), encoding="utf-8")
    return str(plugin_dir)


def _make_plugin_archive(tmp_path, plugin_id, version, deps=None):
    """Helper: create a minimal .pfp archive."""
    plugin_dir = _make_plugin_dir(tmp_path, plugin_id, version, deps)
    archive_path = tmp_path / f"{plugin_id}-{version}.pfp"
    with zipfile.ZipFile(archive_path, 'w') as zf:
        for root, dirs, files in os.walk(plugin_dir):
            for f in files:
                fp = os.path.join(root, f)
                arcname = os.path.relpath(fp, plugin_dir)
                zf.write(fp, arcname)
    return str(archive_path)


# ── PluginVersion parsing ──

class TestPluginVersionParsing:

    def test_parse_basic(self):
        v = PluginVersion("1.2.3")
        assert v.major == 1
        assert v.minor == 2
        assert v.patch == 3
        assert v.pre == ""

    def test_parse_prerelease(self):
        v = PluginVersion("2.0.0-beta")
        assert v.major == 2
        assert v.minor == 0
        assert v.patch == 0
        assert v.pre == "beta"

    def test_str_basic(self):
        assert str(PluginVersion("1.2.3")) == "1.2.3"

    def test_str_prerelease(self):
        assert str(PluginVersion("1.0.0-alpha")) == "1.0.0-alpha"

    def test_parse_major_only(self):
        v = PluginVersion("3")
        assert v.major == 3
        assert v.minor == 0
        assert v.patch == 0

    def test_parse_major_minor(self):
        v = PluginVersion("2.5")
        assert v.major == 2
        assert v.minor == 5
        assert v.patch == 0


class TestPluginVersionComparison:

    def test_equal(self):
        assert PluginVersion("1.0.0") == PluginVersion("1.0.0")

    def test_not_equal(self):
        assert PluginVersion("1.0.0") != PluginVersion("1.0.1")

    def test_less_than_patch(self):
        assert PluginVersion("1.0.0") < PluginVersion("1.0.1")

    def test_less_than_minor(self):
        assert PluginVersion("1.0.9") < PluginVersion("1.1.0")

    def test_less_than_major(self):
        assert PluginVersion("1.9.9") < PluginVersion("2.0.0")

    def test_greater_than(self):
        assert PluginVersion("2.0.0") > PluginVersion("1.9.9")

    def test_prerelease_less_than_release(self):
        assert PluginVersion("1.0.0-alpha") < PluginVersion("1.0.0")

    def test_le_ge(self):
        assert PluginVersion("1.0.0") <= PluginVersion("1.0.0")
        assert PluginVersion("1.0.0") >= PluginVersion("1.0.0")
        assert PluginVersion("1.0.0") <= PluginVersion("1.0.1")
        assert PluginVersion("1.0.1") >= PluginVersion("1.0.0")


class TestPluginVersionCompatibility:

    def test_compatible_same_major(self):
        assert PluginVersion("1.0.0").is_compatible(PluginVersion("1.5.3"))

    def test_incompatible_different_major(self):
        assert not PluginVersion("1.0.0").is_compatible(PluginVersion("2.0.0"))

    def test_satisfies_gte(self):
        assert PluginVersion.satisfies("1.5.0", ">=1.0.0")
        assert not PluginVersion.satisfies("0.9.0", ">=1.0.0")

    def test_satisfies_exact(self):
        assert PluginVersion.satisfies("1.0.0", "1.0.0")
        assert not PluginVersion.satisfies("1.0.1", "1.0.0")


# ── Install with version tracking ──

class TestInstallVersionTracking:

    def test_install_records_history(self, plugin_mgr, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path, "test-plugin", "1.0.0")
        plugin_mgr.install(plugin_dir)

        history = plugin_mgr.get_plugin_history("test-plugin")
        assert len(history) == 1
        assert history[0]["action"] == "install"
        assert history[0]["version"] == "1.0.0"
        assert "timestamp" in history[0]

    def test_get_installed_version(self, plugin_mgr, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path, "test-plugin", "2.1.0")
        plugin_mgr.install(plugin_dir)
        assert plugin_mgr.get_installed_version("test-plugin") == "2.1.0"

    def test_get_installed_version_not_installed(self, plugin_mgr):
        assert plugin_mgr.get_installed_version("nonexistent") is None


# ── Upgrade ──

class TestUpgrade:

    def _setup_with_versions(self, plugin_mgr, tmp_path, plugin_id, versions):
        """Install v1, then put other versions in the versions dir."""
        # Install the first version
        plugin_dir = _make_plugin_dir(tmp_path, plugin_id, versions[0])
        plugin_mgr.install(plugin_dir)
        # Backup current so it's in versions dir
        plugin_mgr._backup_current_version(plugin_id)

        # Create archives for other versions
        for v in versions[1:]:
            archive = _make_plugin_archive(tmp_path, plugin_id, v)
            dest = plugin_mgr._versions_dir(plugin_id) / f"{plugin_id}-{v}.pfp"
            shutil.copy2(archive, str(dest))

    def test_upgrade_to_latest(self, plugin_mgr, tmp_path):
        self._setup_with_versions(plugin_mgr, tmp_path, "my-plugin",
                                  ["1.0.0", "1.1.0", "1.2.0"])

        result = plugin_mgr.upgrade("my-plugin")
        assert result.version == "1.2.0"
        assert plugin_mgr.get_installed_version("my-plugin") == "1.2.0"

    def test_upgrade_to_specific_version(self, plugin_mgr, tmp_path):
        self._setup_with_versions(plugin_mgr, tmp_path, "my-plugin",
                                  ["1.0.0", "1.1.0", "1.2.0"])

        result = plugin_mgr.upgrade("my-plugin", "1.1.0")
        assert result.version == "1.1.0"

    def test_upgrade_records_history(self, plugin_mgr, tmp_path):
        self._setup_with_versions(plugin_mgr, tmp_path, "my-plugin",
                                  ["1.0.0", "2.0.0"])

        plugin_mgr.upgrade("my-plugin", "2.0.0")
        history = plugin_mgr.get_plugin_history("my-plugin")
        # install + upgrade
        upgrade_entry = [h for h in history if h["action"] == "upgrade"]
        assert len(upgrade_entry) == 1
        assert upgrade_entry[0]["version"] == "2.0.0"
        assert upgrade_entry[0]["previous_version"] == "1.0.0"

    def test_upgrade_not_installed_raises(self, plugin_mgr):
        with pytest.raises(ValueError, match="not installed"):
            plugin_mgr.upgrade("nonexistent")

    def test_upgrade_no_newer_version_raises(self, plugin_mgr, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path, "my-plugin", "1.0.0")
        plugin_mgr.install(plugin_dir)

        with pytest.raises(ValueError, match="No newer version"):
            plugin_mgr.upgrade("my-plugin")


# ── Downgrade ──

class TestDowngrade:

    def _setup_with_versions(self, plugin_mgr, tmp_path, plugin_id, current, others):
        """Install current version, put others in versions dir."""
        plugin_dir = _make_plugin_dir(tmp_path, plugin_id, current)
        plugin_mgr.install(plugin_dir)
        plugin_mgr._backup_current_version(plugin_id)

        for v in others:
            archive = _make_plugin_archive(tmp_path, plugin_id, v)
            dest = plugin_mgr._versions_dir(plugin_id) / f"{plugin_id}-{v}.pfp"
            shutil.copy2(archive, str(dest))

    def test_downgrade_to_specific(self, plugin_mgr, tmp_path):
        self._setup_with_versions(plugin_mgr, tmp_path, "my-plugin",
                                  "2.0.0", ["1.0.0"])

        result = plugin_mgr.downgrade("my-plugin", "1.0.0")
        assert result.version == "1.0.0"
        assert plugin_mgr.get_installed_version("my-plugin") == "1.0.0"

    def test_downgrade_records_history(self, plugin_mgr, tmp_path):
        self._setup_with_versions(plugin_mgr, tmp_path, "my-plugin",
                                  "2.0.0", ["1.0.0"])

        plugin_mgr.downgrade("my-plugin", "1.0.0")
        history = plugin_mgr.get_plugin_history("my-plugin")
        downgrade_entry = [h for h in history if h["action"] == "downgrade"]
        assert len(downgrade_entry) == 1
        assert downgrade_entry[0]["version"] == "1.0.0"
        assert downgrade_entry[0]["previous_version"] == "2.0.0"

    def test_downgrade_higher_version_raises(self, plugin_mgr, tmp_path):
        self._setup_with_versions(plugin_mgr, tmp_path, "my-plugin",
                                  "1.0.0", ["2.0.0"])

        with pytest.raises(ValueError, match="not lower"):
            plugin_mgr.downgrade("my-plugin", "2.0.0")

    def test_downgrade_not_installed_raises(self, plugin_mgr):
        with pytest.raises(ValueError, match="not installed"):
            plugin_mgr.downgrade("nonexistent", "1.0.0")


# ── List versions ──

class TestListVersions:

    def test_list_versions(self, plugin_mgr, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path, "my-plugin", "1.0.0")
        plugin_mgr.install(plugin_dir)
        plugin_mgr._backup_current_version("my-plugin")

        # Add more versions
        for v in ["1.1.0", "2.0.0"]:
            archive = _make_plugin_archive(tmp_path, "my-plugin", v)
            dest = plugin_mgr._versions_dir("my-plugin") / f"my-plugin-{v}.pfp"
            shutil.copy2(archive, str(dest))

        versions = plugin_mgr.list_versions("my-plugin")
        assert versions == ["1.0.0", "1.1.0", "2.0.0"]

    def test_list_versions_empty(self, plugin_mgr):
        versions = plugin_mgr.list_versions("nonexistent")
        assert versions == []


# ── Dependency checking ──

class TestDependencyChecking:

    def test_dependencies_satisfied(self, plugin_mgr, tmp_path):
        # Install the dependency
        dep_dir = _make_plugin_dir(tmp_path, "dep-plugin", "1.5.0")
        plugin_mgr.install(dep_dir)

        # Install plugin that depends on it
        main_dir = _make_plugin_dir(tmp_path, "main-plugin", "1.0.0",
                                    deps={"dep-plugin": ">=1.0.0"})
        plugin_mgr.install(main_dir)

        result = plugin_mgr.check_dependencies("main-plugin")
        assert result["satisfied"] is True
        assert result["details"]["dep-plugin"]["satisfied"] is True

    def test_dependencies_missing(self, plugin_mgr, tmp_path):
        main_dir = _make_plugin_dir(tmp_path, "main-plugin", "1.0.0",
                                    deps={"missing-plugin": ">=1.0.0"})
        plugin_mgr.install(main_dir)

        result = plugin_mgr.check_dependencies("main-plugin")
        assert result["satisfied"] is False
        assert result["details"]["missing-plugin"]["satisfied"] is False
        assert result["details"]["missing-plugin"]["reason"] == "not installed"

    def test_dependencies_version_mismatch(self, plugin_mgr, tmp_path):
        # Install old version of dependency
        dep_dir = _make_plugin_dir(tmp_path, "dep-plugin", "0.5.0")
        plugin_mgr.install(dep_dir)

        # Install plugin requiring newer
        main_dir = _make_plugin_dir(tmp_path, "main-plugin", "1.0.0",
                                    deps={"dep-plugin": ">=1.0.0"})
        plugin_mgr.install(main_dir)

        result = plugin_mgr.check_dependencies("main-plugin")
        assert result["satisfied"] is False
        assert result["details"]["dep-plugin"]["satisfied"] is False

    def test_dependencies_legacy_list_format(self, plugin_mgr, tmp_path):
        """Legacy plugins used a list for dependencies — should not crash."""
        main_dir = _make_plugin_dir(tmp_path, "legacy-plugin", "1.0.0",
                                    deps=[])
        plugin_mgr.install(main_dir)

        result = plugin_mgr.check_dependencies("legacy-plugin")
        assert result["satisfied"] is True

    def test_dependencies_not_installed(self, plugin_mgr):
        result = plugin_mgr.check_dependencies("nonexistent")
        assert result["satisfied"] is False


# ── Rollback on failed upgrade ──

class TestRollbackOnFailure:

    def test_upgrade_unavailable_version_raises(self, plugin_mgr, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path, "my-plugin", "1.0.0")
        plugin_mgr.install(plugin_dir)

        with pytest.raises(ValueError, match="not available"):
            plugin_mgr.upgrade("my-plugin", "9.9.9")

        # Should still be on original version
        assert plugin_mgr.get_installed_version("my-plugin") == "1.0.0"


# ── Plugin info (integration) ──

class TestPluginInfo:

    def test_full_info_flow(self, plugin_mgr, tmp_path):
        """End-to-end: install, backup, add version, upgrade, check history."""
        # Install v1
        v1_dir = _make_plugin_dir(tmp_path, "info-plugin", "1.0.0")
        plugin_mgr.install(v1_dir)
        plugin_mgr._backup_current_version("info-plugin")

        # Add v2 archive
        v2_archive = _make_plugin_archive(tmp_path, "info-plugin", "2.0.0")
        dest = plugin_mgr._versions_dir("info-plugin") / "info-plugin-2.0.0.pfp"
        shutil.copy2(v2_archive, str(dest))

        # Upgrade
        plugin_mgr.upgrade("info-plugin", "2.0.0")

        # Check version
        assert plugin_mgr.get_installed_version("info-plugin") == "2.0.0"

        # Check history
        history = plugin_mgr.get_plugin_history("info-plugin")
        assert len(history) == 2  # install + upgrade
        assert history[0]["action"] == "install"
        assert history[1]["action"] == "upgrade"

        # Check available versions
        versions = plugin_mgr.list_versions("info-plugin")
        assert "1.0.0" in versions
        assert "2.0.0" in versions


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
