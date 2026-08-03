"""ComfyUI services execute only configured API workflows."""

import copy
import os

import pytest

from core import ServiceError, ServiceFactory
from services._comfyui_client import ComfyUIClient
from services.comfyui_image_service import ComfyUIImageService
from services.comfyui_video_service import ComfyUIVideoService


def _workflow(output_key="images"):
    return {
        "1": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "template prompt", "strength": 1.0},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "PawFlow"},
        },
    }, {
        "prompt": {"node": "1", "input": "text"},
        "guidance_scale": {
            "node": "1", "input": "strength", "multiply": 2,
            "coerce": "int",
        },
    }, {"node": "9", "key": output_key, "index": 0}


def _config(*operations, media_kind="image"):
    workflow, bindings, output = _workflow(
        "images" if media_kind == "image" else "gifs")
    return {
        "base_url": "https://comfy.example.test",
        "workflows": {
            operation: {
                "workflow": copy.deepcopy(workflow),
                "bindings": copy.deepcopy(bindings),
                "output": copy.deepcopy(output),
            }
            for operation in (operations or ("generate",))
        },
    }


def test_client_rejects_ui_workflow_format():
    config = _config("generate")
    config["workflows"]["generate"]["workflow"] = {
        "nodes": [], "links": []}

    with pytest.raises(ValueError, match="export API format"):
        ComfyUIClient(config, media_kind="image")


def test_client_validates_binding_and_output_nodes():
    bad_binding = _config("generate")
    bad_binding["workflows"]["generate"]["bindings"]["prompt"]["node"] = "404"
    with pytest.raises(ValueError, match="valid node and input"):
        ComfyUIClient(bad_binding, media_kind="image")

    bad_output = _config("generate")
    bad_output["workflows"]["generate"]["output"]["node"] = "404"
    with pytest.raises(ValueError, match="not in the workflow"):
        ComfyUIClient(bad_output, media_kind="image")


def test_client_rejects_invalid_kind_and_non_positive_limits():
    with pytest.raises(ValueError, match="media_kind"):
        ComfyUIClient(_config("generate"), media_kind="audio")

    config = _config("generate")
    config["timeout"] = 0
    with pytest.raises(ValueError, match="timeouts must be positive"):
        ComfyUIClient(config, media_kind="image")


def test_client_selects_relay_host_or_container_explicitly(monkeypatch):
    captured = []

    def resolve(url, **kwargs):
        captured.append((url, kwargs))
        return "http://resolved.test"

    monkeypatch.setattr(
        "services._comfyui_client.resolve_relay_aware_url", resolve)
    host_client = ComfyUIClient(_config("generate"), media_kind="image")
    container_config = _config("generate")
    container_config["relay_local"] = False
    container_client = ComfyUIClient(container_config, media_kind="image")

    assert host_client._base_url() == "http://resolved.test"
    assert container_client._base_url() == "http://resolved.test"
    assert captured[0][1]["relay_local"] is True
    assert captured[1][1]["relay_local"] is False


def test_run_applies_bindings_to_a_copy_and_selects_configured_output(monkeypatch):
    client = ComfyUIClient(_config("generate"), media_kind="image")
    original = copy.deepcopy(client.workflows["generate"]["workflow"])
    submitted = {}

    def request_json(method, path, body=None):
        if method == "POST":
            submitted.update(body)
            return {"prompt_id": "server-prompt"}
        assert path == "/history/server-prompt"
        return {
            "server-prompt": {
                "status": {"status_str": "success"},
                "outputs": {
                    "9": {"images": [{
                        "filename": "result.png",
                        "subfolder": "generated",
                        "type": "output",
                    }]},
                },
            },
        }

    monkeypatch.setattr(client, "request_json", request_json)
    monkeypatch.setattr(
        client, "_download_artifact",
        lambda artifact: ("/tmp/result.png", "image/png"))

    result = client.run("generate", {
        "prompt": "a red fox", "guidance_scale": 3.2})

    assert submitted["prompt"]["1"]["inputs"] == {
        "text": "a red fox", "strength": 6}
    assert client.workflows["generate"]["workflow"] == original
    assert result == {
        "path": "/tmp/result.png",
        "content_type": "image/png",
        "prompt_id": "server-prompt",
        "artifact": {
            "filename": "result.png",
            "subfolder": "generated",
            "type": "output",
        },
    }


def test_wait_history_surfaces_comfyui_execution_failure(monkeypatch):
    client = ComfyUIClient(_config("generate"), media_kind="image")
    monkeypatch.setattr(client, "request_json", lambda *_args, **_kwargs: {
        "prompt-1": {
            "status": {
                "status_str": "error",
                "messages": [["execution_error", {"node_id": "9"}]],
            },
        },
    })

    with pytest.raises(ServiceError, match="workflow failed.*execution_error"):
        client._wait_history("prompt-1")


class _ChunkResponse:
    def __init__(self, chunks, content_type="video/mp4", content_length=""):
        self._chunks = list(chunks)
        self.headers = {"Content-Type": content_type}
        if content_length:
            self.headers["Content-Length"] = content_length
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size):
        assert 0 < size <= 64 * 1024
        self.read_sizes.append(size)
        return self._chunks.pop(0) if self._chunks else b""


def test_download_artifact_streams_bounded_chunks_to_disk(monkeypatch):
    client = ComfyUIClient(_config("generate", media_kind="video"),
                           media_kind="video")
    response = _ChunkResponse([b"abc", b"def", b"ghi"])
    opened = {}
    monkeypatch.setattr(client, "_base_url", lambda: "https://comfy.example.test")

    def urlopen(request, **kwargs):
        opened["url"] = request.full_url
        opened.update(kwargs)
        return response

    monkeypatch.setattr("services._comfyui_client.urllib.request.urlopen", urlopen)

    path, content_type = client._download_artifact({
        "filename": "movie.mp4", "subfolder": "clips", "type": "output"})
    try:
        with open(path, "rb") as handle:
            assert handle.read() == b"abcdefghi"
        assert content_type == "video/mp4"
        assert "/view?" in opened["url"]
        assert "filename=movie.mp4" in opened["url"]
        assert response.read_sizes
    finally:
        os.unlink(path)


def test_download_artifact_removes_partial_file_when_limit_is_exceeded(
        tmp_path, monkeypatch):
    config = _config("generate", media_kind="video")
    config["max_output_bytes"] = 4
    client = ComfyUIClient(config, media_kind="video")
    response = _ChunkResponse([b"abc", b"de"])
    target = tmp_path / "partial.mp4"
    monkeypatch.setattr(client, "_base_url", lambda: "https://comfy.example.test")

    monkeypatch.setattr(
        "services._comfyui_client.urllib.request.urlopen",
        lambda *_args, **_kwargs: response)
    monkeypatch.setattr(
        "services._comfyui_client.tempfile.mkstemp",
        lambda **_kwargs: (
            os.open(target, os.O_RDWR | os.O_CREAT | os.O_TRUNC), str(target)))

    with pytest.raises(ServiceError, match="max_output_bytes"):
        client._download_artifact({"filename": "movie.mp4"})
    assert not target.exists()


def test_input_source_rejects_private_url_before_open(monkeypatch):
    client = ComfyUIClient(_config("edit_image"), media_kind="image")
    opened = []
    monkeypatch.setattr(
        "services._comfyui_client.urllib.request.build_opener",
        lambda *_args: opened.append(True))

    with pytest.raises(ServiceError, match="private/local network"):
        client._load_source("http://127.0.0.1/private.png", index=0)

    assert opened == []


def test_input_redirect_is_revalidated():
    from services._comfyui_client import _ValidatedInputRedirectHandler

    checked = []

    def validate(url):
        checked.append(url)
        raise ServiceError("redirect blocked")

    handler = _ValidatedInputRedirectHandler(validate)
    with pytest.raises(ServiceError, match="redirect blocked"):
        handler.redirect_request(
            object(), None, 302, "Found", {},
            "http://169.254.169.254/latest/meta-data")

    assert checked == ["http://169.254.169.254/latest/meta-data"]


def test_media_defaults_match_service_schemas():
    image = ComfyUIImageService(_config("generate"))
    video = ComfyUIVideoService(_config("generate", media_kind="video"))

    assert image.client.timeout == image.get_parameter_schema()["timeout"]["default"]
    assert image.client.poll_interval == image.get_parameter_schema()["poll_interval"]["default"]
    assert video.client.timeout == video.get_parameter_schema()["timeout"]["default"]
    assert video.client.poll_interval == video.get_parameter_schema()["poll_interval"]["default"]
    assert video.client.max_input_bytes == video.get_parameter_schema()["max_input_bytes"]["default"]
    assert video.client.max_output_bytes == video.get_parameter_schema()["max_output_bytes"]["default"]


def test_services_expose_only_configured_operations():
    image = ComfyUIImageService(_config("generate", "edit_image"))
    video = ComfyUIVideoService(_config(
        "generate", "image_to_video", media_kind="video"))

    assert image.get_operations() == {"edit_image": {}, "generate": {}}
    assert image.get_model_info()["supports_edit"] is True
    assert video.get_operations() == {"generate": {}, "image_to_video": {}}


def test_image_service_returns_file_backed_result(monkeypatch):
    service = ComfyUIImageService(_config("generate"))
    monkeypatch.setattr(service, "ensure_connected", lambda: None)
    monkeypatch.setattr(service.client, "run", lambda operation, values: {
        "path": "/tmp/generated.png",
        "content_type": "image/png",
        "prompt_id": "prompt-2",
    })

    result = service.generate(prompt="a lighthouse", width=768, height=1024)

    assert result == {
        "image_path": "/tmp/generated.png",
        "content_type": "image/png",
        "_delete_media_path": True,
        "provider_prompt_id": "prompt-2",
    }


def test_comfyui_services_are_registered_and_categorized():
    from tasks import _register_all_services
    from tasks.ai.actions.service_flow import _service_category

    _register_all_services()

    assert ServiceFactory.get("comfyUIImageGeneration") is ComfyUIImageService
    assert ServiceFactory.get("comfyUIVideoGeneration") is ComfyUIVideoService
    assert _service_category(
        "comfyUIImageGeneration", ComfyUIImageService) == "image"
    assert _service_category(
        "comfyUIVideoGeneration", ComfyUIVideoService) == "video"
