import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from core import pfp_package


ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    "pawflow.pixazo-provider": {
        "path": ROOT / "packages" / "pawflow.pixazo-provider.pfpdir",
        "classes": {
            "pixazoImageGeneration": ("services.pixazo_image_service", "PixazoImageService"),
            "pixazoVideoGeneration": ("services.pixazo_video_service", "PixazoVideoService"),
            "pixazoAudioGeneration": ("services.pixazo_audio_service", "PixazoAudioService"),
            "pixazo3DGeneration": ("services.pixazo_capability_services", "Pixazo3DService"),
            "pixazoUpscale": ("services.pixazo_capability_services", "PixazoUpscaleService"),
            "pixazoTryOn": ("services.pixazo_capability_services", "PixazoTryOnService"),
            "pixazoLipsync": ("services.pixazo_capability_services", "PixazoLipsyncService"),
            "pixazoTrainer": ("services.pixazo_capability_services", "PixazoTrainerService"),
        },
    },
    "pawflow.wavespeed-provider": {
        "path": ROOT / "packages" / "pawflow.wavespeed-provider.pfpdir",
        "classes": {
            "wavespeedImageGeneration": ("services.wavespeed_image_service", "WaveSpeedImageService"),
            "wavespeedVideoGeneration": ("services.wavespeed_video_service", "WaveSpeedVideoService"),
            "wavespeedAudioGeneration": ("services.wavespeed_audio_service", "WaveSpeedAudioService"),
            "wavespeedVoiceClone": ("services.wavespeed_voice_clone_service", "WaveSpeedVoiceCloneService"),
            "wavespeed3DGeneration": ("services.wavespeed_capability_services", "WaveSpeed3DService"),
            "wavespeedUpscale": ("services.wavespeed_capability_services", "WaveSpeedUpscaleService"),
            "wavespeedTryOn": ("services.wavespeed_capability_services", "WaveSpeedTryOnService"),
            "wavespeedLipsync": ("services.wavespeed_capability_services", "WaveSpeedLipsyncService"),
            "wavespeedTrainer": ("services.wavespeed_capability_services", "WaveSpeedTrainerService"),
        },
    },
    "pawflow.kling-provider": {
        "path": ROOT / "packages" / "pawflow.kling-provider.pfpdir",
        "classes": {
            "klingVideoGeneration": ("services.kling_video_service", "KlingVideoService"),
        },
    },
}

DUMP_CONTRACT = """
import importlib
import json
import sys

module = importlib.import_module(sys.argv[1])
service_class = getattr(module, sys.argv[2])
service = service_class({})
operations = json.loads(sys.argv[3])
print(json.dumps({
    "schema": service.get_parameter_schema(),
    "operations": {name: callable(getattr(service, name, None)) for name in operations},
}, ensure_ascii=False, sort_keys=True))
"""


@pytest.mark.parametrize("package_id", PACKAGES)
def test_bundled_provider_manifest_matches_vendored_runtime(package_id):
    spec = PACKAGES[package_id]
    package_path = spec["path"]
    manifest = json.loads((package_path / "pfp.json").read_text(encoding="utf-8"))
    objects = manifest["objects"]
    plan = pfp_package.inspect_pfp(str(package_path))

    assert manifest["package"] == package_id
    assert {item["service_type"] for item in objects} == set(spec["classes"])
    assert all(item["installable"] for item in plan["objects"])
    assert all(item["status"] != "blocked" for item in plan["objects"])

    runtime_path = package_path / "content" / "runtime"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(runtime_path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for item in objects:
        module_name, class_name = spec["classes"][item["service_type"]]
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                DUMP_CONTRACT,
                module_name,
                class_name,
                json.dumps(list(item["operations"])),
            ],
            cwd=runtime_path,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        contract = json.loads(completed.stdout)
        assert item["parameters"] == contract["schema"]
        assert contract["operations"] == {
            name: True for name in item["operations"]
        }


@pytest.mark.parametrize("package_id", PACKAGES)
def test_bundled_provider_source_excludes_generated_cache(package_id):
    package_path = PACKAGES[package_id]["path"]
    relative_paths = {
        path.relative_to(package_path).as_posix()
        for path in package_path.rglob("*")
    }

    assert not any("graphify-out" in path for path in relative_paths)


@pytest.mark.parametrize("package_id", PACKAGES)
def test_bundled_provider_artifact_excludes_generated_cache(package_id):
    manifest = json.loads(
        (PACKAGES[package_id]["path"] / "pfp.json").read_text(encoding="utf-8"))
    artifact = (
        ROOT / "data" / "repository" / "packages" / "bundled"
        / f"{package_id}-{manifest['version']}.pfp"
    )

    with zipfile.ZipFile(artifact) as archive:
        names = archive.namelist()

    assert not any("__pycache__" in name for name in names)
    assert not any("graphify-out" in name for name in names)
