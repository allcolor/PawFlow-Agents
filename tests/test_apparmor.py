"""Tests for core.apparmor — pool-container AppArmor profile resolution."""

from unittest.mock import patch

import core.apparmor as apparmor


def setup_function(_fn):
    apparmor._reset_for_tests()


def teardown_function(_fn):
    apparmor._reset_for_tests()


def test_profile_used_when_probe_succeeds(monkeypatch):
    monkeypatch.delenv("PAWFLOW_APPARMOR_PROFILE", raising=False)
    with patch.object(apparmor, "_profile_usable", return_value=True) as probe:
        opts = apparmor.apparmor_security_opts("img:latest")
    assert opts == ["--security-opt", "apparmor=pawflow-mount"]
    probe.assert_called_once_with("img:latest", "pawflow-mount")


def test_falls_back_to_unconfined_when_profile_missing(monkeypatch):
    monkeypatch.delenv("PAWFLOW_APPARMOR_PROFILE", raising=False)
    with patch.object(apparmor, "_profile_usable", return_value=False):
        opts = apparmor.apparmor_security_opts("img:latest")
    assert opts == ["--security-opt", "apparmor=unconfined"]


def test_resolution_is_cached(monkeypatch):
    monkeypatch.delenv("PAWFLOW_APPARMOR_PROFILE", raising=False)
    with patch.object(apparmor, "_profile_usable", return_value=True) as probe:
        apparmor.apparmor_security_opts("img:latest")
        apparmor.apparmor_security_opts("img:latest")
    assert probe.call_count == 1


def test_env_override_skips_probe(monkeypatch):
    monkeypatch.setenv("PAWFLOW_APPARMOR_PROFILE", "my-custom-profile")
    with patch.object(apparmor, "_profile_usable") as probe:
        opts = apparmor.apparmor_security_opts("img:latest")
    assert opts == ["--security-opt", "apparmor=my-custom-profile"]
    probe.assert_not_called()


def test_probe_runs_throwaway_container(monkeypatch):
    monkeypatch.delenv("PAWFLOW_APPARMOR_PROFILE", raising=False)

    class Result:
        returncode = 0
        stderr = ""

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return Result()

    with patch.object(apparmor.subprocess, "run", side_effect=fake_run), \
            patch("core.docker_utils.docker_cmd", return_value=["docker"]):
        assert apparmor._profile_usable("img:latest") is True

    cmd = captured["cmd"]
    assert "--rm" in cmd
    assert "apparmor=pawflow-mount" in " ".join(cmd)
    assert cmd[-1] == "img:latest"


def test_probe_failure_is_handled(monkeypatch):
    monkeypatch.delenv("PAWFLOW_APPARMOR_PROFILE", raising=False)
    with patch.object(apparmor.subprocess, "run", side_effect=OSError("no docker")), \
            patch("core.docker_utils.docker_cmd", return_value=["docker"]):
        assert apparmor._profile_usable("img:latest") is False


def test_relay_profile_used_when_probe_succeeds(monkeypatch):
    monkeypatch.delenv("PAWFLOW_RELAY_APPARMOR_PROFILE", raising=False)
    with patch.object(apparmor, "_profile_usable", return_value=True) as probe:
        opts = apparmor.relay_apparmor_security_opts("img:latest")
    assert opts == ["--security-opt", "apparmor=pawflow-relay"]
    probe.assert_called_once_with("img:latest", "pawflow-relay")


def test_relay_falls_back_to_unconfined(monkeypatch):
    monkeypatch.delenv("PAWFLOW_RELAY_APPARMOR_PROFILE", raising=False)
    with patch.object(apparmor, "_profile_usable", return_value=False):
        opts = apparmor.relay_apparmor_security_opts("img:latest")
    assert opts == ["--security-opt", "apparmor=unconfined"]


def test_relay_env_override_skips_probe(monkeypatch):
    monkeypatch.setenv("PAWFLOW_RELAY_APPARMOR_PROFILE", "unconfined")
    with patch.object(apparmor, "_profile_usable") as probe:
        opts = apparmor.relay_apparmor_security_opts("img:latest")
    assert opts == ["--security-opt", "apparmor=unconfined"]
    probe.assert_not_called()


def test_pool_and_relay_profiles_cache_independently(monkeypatch):
    monkeypatch.delenv("PAWFLOW_APPARMOR_PROFILE", raising=False)
    monkeypatch.delenv("PAWFLOW_RELAY_APPARMOR_PROFILE", raising=False)

    def usable(_image, profile):
        return profile == "pawflow-relay"

    with patch.object(apparmor, "_profile_usable", side_effect=usable):
        pool = apparmor.apparmor_security_opts("img:latest")
        relay = apparmor.relay_apparmor_security_opts("img:latest")
    assert pool == ["--security-opt", "apparmor=unconfined"]
    assert relay == ["--security-opt", "apparmor=pawflow-relay"]
