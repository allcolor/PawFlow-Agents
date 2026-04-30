import re

from core.file_store import FileStore
from core.handlers.bash import BashHandler


class FakeRelay:
    TYPE = "relay"
    _service_id = "fs_test"

    def exec(self, path, command, **kwargs):
        return {"stdout": "done\n", "stderr": "", "returncode": 0}


def _handler_with_filestore(tmp_path):
    FileStore._instance = FileStore(base_dir=str(tmp_path / "filestore"))
    handler = BashHandler()
    handler.set_user_id("user-1")
    handler.set_conversation_id("conv-1")
    handler.set_agent_name("assistant")
    return handler


def test_background_bash_relay_returns_filestore_output_url(tmp_path):
    handler = _handler_with_filestore(tmp_path)
    handler.set_fs_service(FakeRelay())

    result = handler.execute({
        "relay": "fs_test",
        "command": "pytest -q",
        "run_in_background": True,
    })

    assert "Background command started" in result
    assert "C:\\" not in result
    assert "AppData" not in result
    assert "Output file: fs://filestore/" in result
    assert 'Use read(path="fs://filestore/' in result
    assert 'relay="fs_test"' not in result

    bg_id = re.search(r"id: (bg_[a-f0-9]+)", result).group(1)
    task = BashHandler._bg_tasks[bg_id]
    task["thread"].join(timeout=2)

    url = re.search(r"Output file: (fs://filestore/[^\n]+)", result).group(1)
    file_id = url.split("/", 4)[3]
    stored = FileStore.instance().get(file_id, user_id="user-1")
    assert stored is not None
    filename, content, content_type = stored
    assert filename == f"bash_bg_{bg_id}.out"
    assert content == b"done\n"
    assert content_type == "text/plain"


def test_background_bash_uses_filestore_even_with_requested_workdir(tmp_path):
    handler = _handler_with_filestore(tmp_path)
    handler.set_fs_service(FakeRelay())

    result = handler.execute({
        "relay": "fs_test",
        "path": "/workspace/subdir",
        "command": "pytest -q",
        "run_in_background": True,
    })

    assert "Output file: fs://filestore/" in result
    assert "/workspace/subdir/.bash_bg_" not in result

    bg_id = re.search(r"id: (bg_[a-f0-9]+)", result).group(1)
    BashHandler._bg_tasks[bg_id]["thread"].join(timeout=2)

    url = re.search(r"Output file: (fs://filestore/[^\n]+)", result).group(1)
    file_id = url.split("/", 4)[3]
    stored = FileStore.instance().get(file_id, user_id="user-1")
    assert stored is not None
    assert stored[1] == b"done\n"
