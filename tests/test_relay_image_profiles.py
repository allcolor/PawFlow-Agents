import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from core.install_bootstrap import get_install_status

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "config" / "relay_image_catalog.json"
GENERATOR_PATH = ROOT / "scripts" / "generate-relay-image.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_relay_image", GENERATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _catalog():
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def test_relay_catalog_has_required_base_runtime():
    catalog = _catalog()
    base = catalog["features"]["relay.base"]

    assert re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", catalog["relay_image_version"])
    assert "relay.base" in catalog["required_features"]
    assert base["required"] is True
    assert "python3" in base["apt"]
    assert "python3-dev" in base["apt"]
    assert "ripgrep" in base["apt"]
    assert "time" in base["apt"]
    assert "vim-tiny" in base["apt"]
    assert "nano" in base["apt"]
    assert "procps" in base["apt"]
    assert "iproute2" in base["apt"]
    assert "netcat-openbsd" in base["apt"]
    assert "fuse3" in base["apt"]
    assert {"tini", "slirp4netns", "util-linux"} <= set(base["apt"])
    assert "libfuse3-dev" in base["apt"]
    assert "build-essential" in base["apt"]
    assert "pkg-config" in base["apt"]
    assert "pyfuse3" in base["pip"]
    assert "trio" in base["pip"]
    assert "defusedxml" in base["pip"]
    assert "ripgrep" not in catalog["features"]["dev.shell-tools"]["apt"]
    post_install = "\n".join(base["post_install"])
    assert "/workspace" in post_install
    assert "/cc_sessions" in post_install
    assert "/filestore" in post_install
    assert 'chown -R pawflow:pawflow "$d"' in post_install
    assert "chmod g+rwx" in post_install
    assert "sudo -E -u pawflow" in post_install
    assert base["runtime"]["requires_fuse"] is True
    assert "/dev/fuse" in base["runtime"]["docker_args"]


def test_gui_apps_are_individually_selectable_and_imply_desktop_runtime():
    catalog = _catalog()
    gui_features = {
        feature_id: feature
        for feature_id, feature in catalog["features"].items()
        if feature.get("category") == "gui_apps"
    }

    assert "gui.gimp" in gui_features
    assert "gui.inkscape" in gui_features
    assert "gui.vscode" not in gui_features
    assert "gui.libreoffice-calc" in gui_features
    assert "gui.audacity" in gui_features
    assert len(gui_features) >= 10
    for feature in gui_features.values():
        assert "desktop.runtime" in feature.get("implies", [])


def test_server_profile_is_full_and_execution_profile_is_minimal():
    catalog = _catalog()
    server_features = set(catalog["profiles"][catalog["server_profile"]]["features"])
    server_minimal_features = set(catalog["profiles"][catalog["server_minimal_profile"]]["features"])
    client_features = set(catalog["profiles"][catalog["default_client_profile"]]["features"])

    assert catalog["server_profile"] == "server-full"
    assert catalog["server_minimal_profile"] == "server-minimal"
    assert catalog["default_client_profile"] == "client-minimal"
    assert server_minimal_features == {"relay.base"}
    assert client_features == {"relay.base"}
    for required in ("lang.python-dev", "lang.node", "lang.rust", "lang.java", "desktop.runtime", "browser.chromium", "gui.gimp"):
        assert required in server_features

    for omitted in ("lang.dotnet", "lang.zig", "gui.libreoffice-calc", "gui.vlc", "gui.audacity"):
        assert omitted not in server_features
    assert "lang.java-kotlin" not in server_features
    assert "lang.java" in _catalog()["features"]["lang.java-kotlin"].get("implies", [])
    assert "browser.chrome" not in server_features
    assert "gui.vscode" not in server_features


def test_generator_resolves_implied_features_and_writes_installer_artifacts(tmp_path):
    generator = _load_generator()
    out_dir = tmp_path / "relay"

    manifest = generator.generate(
        CATALOG_PATH,
        "client-minimal",
        ["gui.gimp", "lang.node", "browser.chromium"],
        out_dir,
        "pawflow-relay:test",
    )

    assert (out_dir / "Dockerfile").exists()
    assert (out_dir / "manifest.json").exists()
    assert (out_dir / "build.sh").exists()
    assert (out_dir / "run-relay.sh").exists()
    assert (out_dir / "runtime" / "pawflow_relay_launcher.py").exists()
    assert (out_dir / "runtime" / "fs_actions.py").exists()
    assert (out_dir / "runtime" / "screen_actions.py").exists()
    assert (out_dir / "runtime" / "screen_actions_cua.py").exists()
    assert (out_dir / "runtime" / "pawflow.py").exists()
    assert (out_dir / "runtime" / "pawflow_relay" / "__init__.py").exists()
    assert "relay.base" in manifest["features"]
    assert manifest["relay_image_version"] == _catalog()["relay_image_version"]
    assert "gui.gimp" in manifest["features"]
    assert "desktop.runtime" in manifest["features"]
    assert "lang.node" in manifest["features"]
    assert "browser.chromium" in manifest["features"]
    assert "/dev/fuse" in manifest["runtime_docker_args"]

    dockerfile = (out_dir / "Dockerfile").read_text(encoding="utf-8")
    assert "python3 /opt/pawflow/pawflow_relay_launcher.py" not in dockerfile
    assert dockerfile.index("pkg-config") < dockerfile.index("pip3 install")
    assert dockerfile.index("libfuse3-dev") < dockerfile.index("pip3 install")
    assert "ripgrep" in dockerfile
    assert "tini" in dockerfile
    assert "defusedxml" in dockerfile
    assert "https://deb.nodesource.com/setup_22.x" in dockerfile
    assert dockerfile.index("https://deb.nodesource.com/setup_22.x") < dockerfile.index("nodejs")
    assert "gimp gimp-plugin-registry" in dockerfile
    assert "software-properties-common" in dockerfile
    assert "ppa:xtradeb/apps" in dockerfile
    assert "apt-get install -y --no-install-recommends" in dockerfile
    assert "chromium" in dockerfile
    assert "/usr/bin/chromium --no-sandbox" in dockerfile
    assert 'profile_dir="$HOME/.chromium-profile"' in dockerfile
    assert "--disk-cache-dir=\"$cache_dir\"" in dockerfile
    assert "rm -f \"$profile_dir/SingletonLock\"" in dockerfile
    assert "\"$profile_dir/SingletonSocket\"" in dockerfile
    assert "\"$profile_dir/SingletonCookie\"" in dockerfile
    assert "update-alternatives --set x-www-browser /usr/local/bin/chromium" in dockerfile
    assert "WebBrowser=chromium" in dockerfile
    assert "X-XFCE-CommandsWithParameter=/usr/local/bin/chromium" in dockerfile
    assert "x-scheme-handler/https=chromium.desktop" in dockerfile
    assert "/ms-playwright" not in dockerfile
    assert "google-chrome" not in dockerfile
    assert "packages.microsoft.com/repos/code" not in dockerfile
    assert "COPY runtime/ /opt/pawflow/" not in dockerfile
    assert "USER root" in dockerfile
    assert "sudo -E -u pawflow" in dockerfile
    assert 'chown -R pawflow:pawflow "$d"' in dockerfile
    assert "chmod g+rwx" in dockerfile
    assert "XDG_CACHE_HOME=\"/tmp/pawflow-cache\"" in dockerfile
    assert "HF_HOME=\"/tmp/pawflow-cache/huggingface\"" in dockerfile

    run_script = (out_dir / "run-relay.sh").read_text(encoding="utf-8")
    assert "PAWFLOW_RELAY_TOKEN" in run_script
    assert '"$SCRIPT_DIR/runtime:/opt/pawflow:ro"' in run_script
    assert "--server-mount /cc_sessions" in run_script
    assert "--filestore-mount /filestore" in run_script
    assert "--device /dev/fuse" in run_script

    build_script = (out_dir / "build.sh").read_text(encoding="utf-8")
    assert "SCRIPT_DIR=" in build_script
    assert "PAWFLOW_DOCKER_PLATFORM" in build_script
    assert 'docker build "${BUILD_ARGS[@]}" -t "$IMAGE" "$SCRIPT_DIR"' in build_script


def test_server_minimal_build_script_targets_runtime_default_image():
    script = ROOT / "scripts" / "build-server-minimal-relay.sh"
    src = script.read_text(encoding="utf-8")

    assert script.exists()
    assert script.stat().st_mode & 0o111
    assert "set -euo pipefail" in src
    assert "--profile server-minimal" in src
    assert "pawflow-relay-minimal:latest" in src
    assert "docker/relay-generated/server-minimal" in src
    assert "PAWFLOW_SERVER_MINIMAL_RELAY_IMAGE" in src


def _relay_init_script(profile):
    if profile == "dev":
        dockerfile = (ROOT / "docker/relay-dev/Dockerfile").read_text(encoding="utf-8")
    else:
        generator = _load_generator()
        catalog = _catalog()
        features = generator._resolve_features(catalog, profile, [])
        dockerfile = generator._render_dockerfile(catalog, features, "relay:test")
    line = next(line.strip() for line in dockerfile.splitlines()
                if "> /usr/local/bin/init.sh" in line)
    tokens = shlex.split(line.removeprefix("RUN ").removeprefix("&& ").rstrip("\\").strip())
    assert tokens[0] == "printf"
    # Use printf itself to decode the exact script embedded in the image.
    return subprocess.run(
        ["bash", "-c", 'printf "$@"', "printf", *tokens[1:tokens.index(">")]],
        check=True, capture_output=True, text=True, timeout=5,
    ).stdout


@pytest.mark.skipif(os.name != "posix" or shutil.which("bash") is None,
                    reason="relay init requires POSIX and bash")
@pytest.mark.parametrize("profile", ["dev", "server-full", "server-minimal", "client-minimal"])
def test_relay_init_preserves_chromium_profiles(tmp_path, profile):
    script = _relay_init_script(profile)
    relay_home = tmp_path / "home"
    profiles = [relay_home / name for name in (
        ".config/chromium", ".chromium-profile", "browser profiles/custom")]
    sentinels = {}
    for directory in profiles:
        (directory / "Default").mkdir(parents=True)
        for name in ("Default/Cookies", "Default/Bookmarks", "Local State"):
            path = directory / name
            sentinels[path] = f"persistent profile: {directory.name}/{name}".encode()
            path.write_bytes(sentinels[path])
    cache = relay_home / ".cache/huggingface"
    cache.mkdir(parents=True)
    (cache / "old-cache").write_text("disposable", encoding="utf-8")

    # Confine all filesystem operations to fixtures and stub privileged setup.
    script = script.replace("/home/pawflow", shlex.quote(str(relay_home)))
    for mount in ("workspace", "cc_sessions", "filestore", "skills"):
        script = script.replace("/" + mount, shlex.quote(str(tmp_path / mount)))
    script = script.replace('exec sudo -E -u pawflow "$@"', 'exec "$@"')
    setup = (
        "chronyd() { :; }\nchown() { :; }\nusermod() { :; }\n"
        "groupmod() { :; }\ngroupadd() { :; }\n"
        "id() { printf '1001\\n'; }\n"
        "getent() { printf 'pawflow:x:1001:\\n'; }\n"
    )
    for _ in range(2):
        subprocess.run(
            ["bash", "-c", setup + script, "init.sh", "/usr/bin/true"],
            env={"PATH": "/usr/bin:/bin", "HOME": str(relay_home),
                 "PAWFLOW_CHROMIUM_USER_DATA_DIR": str(profiles[-1])},
            cwd=tmp_path, check=True, capture_output=True, text=True, timeout=5,
        )
        assert not cache.exists(), "the startup cleanup must actually run"
        for path, expected in sentinels.items():
            assert path.is_file(), f"startup removed browser data: {path}"
            assert path.read_bytes() == expected


def _run_relay_init(tmp_path, profile, filesystem, writable):
    script = _relay_init_script(profile)
    paths = {}
    for name in ("home/pawflow", "workspace", "cc_sessions", "filestore", "skills"):
        path = tmp_path / "mounted directories" / name
        paths[name] = str(path)
        script = script.replace("/" + name, shlex.quote(str(path)))
    script = script.replace('exec sudo -E -u pawflow "$@"', 'exec "$@"')
    setup = r"""
chronyd() { :; }
usermod() { :; }
groupmod() { :; }
groupadd() { :; }
id() { printf '1001\n'; }
chown() { printf 'chown'; printf ' <%s>' "$@"; printf '\n'; }
getent() {
    printf 'getent <%s>\n' "$*" >&2
    printf 'mountgroup:x:2468:\n'
}
stat() {
    if [ "$1" = -f ]; then
        if [ "$PF_TEST_FS" = error ]; then return 1; fi
        printf '%s\n' "$PF_TEST_FS"
    elif [ "$1" = -c ] && [ "$2" = '%g' ]; then
        printf '2468\n'
    else
        command stat "$@"
    fi
}
sudo() {
    if [ "$1" = -u ] && [ "$2" = pawflow ] &&
       [ "$3" = test ] && [ "$4" = -w ]; then
        [ "$PF_TEST_WRITABLE" = 1 ]
    else
        return 2
    fi
}
"""
    result = subprocess.run(
        ["bash", "-c", setup + script, "init.sh", "/usr/bin/true"],
        env={"PATH": "/usr/bin:/bin", "HOME": paths["home/pawflow"],
             "PF_TEST_FS": filesystem, "PF_TEST_WRITABLE": str(int(writable))},
        cwd=tmp_path, check=True, capture_output=True, text=True, timeout=5,
    )
    return result, paths


@pytest.mark.skipif(os.name != "posix" or shutil.which("bash") is None,
                    reason="relay init requires POSIX and bash")
@pytest.mark.parametrize("profile", ["dev", "server-full", "server-minimal", "client-minimal"])
@pytest.mark.parametrize(("filesystem", "writable", "skip_workspace"), [
    ("v9fs", True, True),
    ("9p", True, True),
    ("drvfs", True, True),
    ("v9fs", False, False),
    ("ext2/ext3", True, False),
    ("error", True, False),
])
def test_relay_init_bounds_windows_workspace_ownership(
        tmp_path, profile, filesystem, writable, skip_workspace):
    result, paths = _run_relay_init(tmp_path, profile, filesystem, writable)
    recursive = [line for line in result.stdout.splitlines()
                 if line.startswith("chown <-R>")]
    workspace_calls = [line for line in recursive if f'<{paths["workspace"]}>' in line]
    assert bool(workspace_calls) is not skip_workspace, result.stdout
    # Skipping the workspace must not skip the other mountpoints or home repair.
    for name in ("cc_sessions", "filestore", "skills"):
        assert any(f"<{paths[name]}>" in line for line in recursive), result.stdout
    if profile == "dev":
        assert any(f'<{paths["home/pawflow"]}>' in line for line in recursive)


@pytest.mark.skipif(os.name != "posix" or shutil.which("bash") is None,
                    reason="relay init requires POSIX and bash")
@pytest.mark.parametrize("profile", ["dev", "server-full", "server-minimal", "client-minimal"])
def test_relay_init_looks_up_the_actual_mount_group(tmp_path, profile):
    result, _ = _run_relay_init(tmp_path, profile, "ext2/ext3", True)
    # This also catches printf consuming stat's %g while building the image.
    assert result.stderr.count("getent <group 2468>") == 4, result.stderr


def test_installer_api_advertises_relay_image_profile_step():
    flow = json.loads((ROOT / "data/repository/flows/global/default/pawflow_installer/versions/1.0.0.json").read_text(encoding="utf-8"))
    api_content = get_install_status()

    assert flow["tasks"]["install_api"]["type"] == "installBootstrap"
    assert "relay_image_profiles" in api_content["steps"]
    assert api_content["client_relay_images"]["catalog"] == "config/relay_image_catalog.json"
    assert api_content["client_relay_images"]["server_profile"] == "server-full"
    assert api_content["client_relay_images"]["server_minimal_profile"] == "server-minimal"
    assert api_content["client_relay_images"]["advanced_features"] is True
    ui_asset = ROOT / "data/repository/flows/global/default/pawflow_installer/versions/assets/install.html"
    assert "Relay image profiles" in ui_asset.read_text(encoding="utf-8")
