import importlib.util
import json
from pathlib import Path

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

    assert "relay.base" in catalog["required_features"]
    assert base["required"] is True
    assert "python3" in base["apt"]
    assert "fuse3" in base["apt"]
    assert "libfuse3-dev" in base["apt"]
    assert "pyfuse3" in base["pip"]
    assert "trio" in base["pip"]
    post_install = "\n".join(base["post_install"])
    assert "/workspace" in post_install
    assert "/cc_sessions" in post_install
    assert "/filestore" in post_install
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
    assert "gui.vscode" in gui_features
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
    for required in ("lang.python-dev", "lang.node", "lang.rust", "desktop.runtime", "browser.chrome", "gui.gimp"):
        assert required in server_features


def test_generator_resolves_implied_features_and_writes_installer_artifacts(tmp_path):
    generator = _load_generator()
    out_dir = tmp_path / "relay"

    manifest = generator.generate(
        CATALOG_PATH,
        "client-minimal",
        ["gui.gimp", "lang.node"],
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
    assert (out_dir / "runtime" / "pawflow.py").exists()
    assert (out_dir / "runtime" / "pawflow_relay" / "__init__.py").exists()
    assert "relay.base" in manifest["features"]
    assert "gui.gimp" in manifest["features"]
    assert "desktop.runtime" in manifest["features"]
    assert "lang.node" in manifest["features"]
    assert "/dev/fuse" in manifest["runtime_docker_args"]

    dockerfile = (out_dir / "Dockerfile").read_text(encoding="utf-8")
    assert "python3 /opt/pawflow/pawflow_relay_launcher.py" not in dockerfile
    assert "https://deb.nodesource.com/setup_22.x" in dockerfile
    assert dockerfile.index("https://deb.nodesource.com/setup_22.x") < dockerfile.index("nodejs")
    assert "gimp gimp-plugin-registry" in dockerfile
    assert "COPY runtime/ /opt/pawflow/" in dockerfile

    run_script = (out_dir / "run-relay.sh").read_text(encoding="utf-8")
    assert "PAWFLOW_RELAY_TOKEN" in run_script
    assert "--server-mount /cc_sessions" in run_script
    assert "--filestore-mount /filestore" in run_script
    assert "--device /dev/fuse" in run_script


def test_installer_api_advertises_relay_image_profile_step():
    flow = json.loads((ROOT / "data/repository/flows/global/default/pawflow_installer/versions/1.0.0.json").read_text(encoding="utf-8"))
    api_content = get_install_status()

    assert flow["tasks"]["install_api"]["type"] == "installBootstrap"
    assert "relay_image_profiles" in api_content["steps"]
    assert api_content["client_relay_images"]["catalog"] == "config/relay_image_catalog.json"
    assert api_content["client_relay_images"]["server_profile"] == "server-full"
    assert api_content["client_relay_images"]["server_minimal_profile"] == "server-minimal"
    assert api_content["client_relay_images"]["advanced_features"] is True
    assert "Relay image profiles" in flow["tasks"]["install_ui"]["parameters"]["content"]
