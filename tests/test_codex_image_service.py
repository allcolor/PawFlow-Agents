from pathlib import Path
from types import SimpleNamespace

from services.codex_image_service import CodexImageService


class FakeClient:
    def __init__(self):
        self.provider = "codex-app-server"
        self.setup_workdirs = []
        self.recovered = []
        self._agent_service = ""

    def _codex_setup_credentials(self, workdir):
        self.setup_workdirs.append(workdir)

    def _codex_env(self, workdir=""):
        return {
            "CODEX_HOME": "/wrong/host/path",
            "CODEX_API_KEY": "test-key",
            "OPENAI_API_KEY": "test-key",
            "UNRELATED": "ignored",
        }

    def _codex_recover_tokens(self, workdir):
        self.recovered.append(workdir)


class FakeLLMService:
    provider = "codex-app-server"
    default_model = "gpt-5.4"

    def __init__(self, client):
        self._client = client


class FakeProc:
    returncode = 0

    def __init__(self, pool):
        self.pool = pool

    def communicate(self, stdin_text, timeout=None):
        self.pool.stdin_text = stdin_text
        output = self.pool.last_host_dir / ".codex" / "generated_images" / "out.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"png-bytes")
        return "ok", ""


class FakePool:
    def __init__(self, base):
        self.base = base
        self.acquire_calls = 0
        self.release_calls = []
        self.exec_calls = []
        self.stdin_text = ""
        self.last_host_dir = None

    def acquire(self):
        self.acquire_calls += 1
        return "pf-codex-pool-test"

    def release(self, container):
        self.release_calls.append(container)

    def exec_codex(self, container, session_dir, codex_args, extra_env=None, **popen_kwargs):
        parts = session_dir.strip("/").split("/")
        assert parts[:1] == ["cc_sessions"]
        self.last_host_dir = self.base.joinpath(*parts[1:])
        self.exec_calls.append({
            "container": container,
            "session_dir": session_dir,
            "codex_args": codex_args,
            "extra_env": extra_env,
            "popen_kwargs": popen_kwargs,
        })
        return FakeProc(self)


class FakeRegistry:
    def __init__(self, client):
        self.client = client

    def resolve_definition(self, service_id, user_id="", conv_id=""):
        assert user_id == "alice"
        assert service_id == "codex_llm"
        return SimpleNamespace(
            service_id="codex_llm",
            service_type="llmConnection",
            config={"provider": "codex-app-server"},
        )

    def resolve(self, service_id, user_id="", conv_id=""):
        assert user_id == "alice"
        assert service_id == "codex_llm"
        return FakeLLMService(self.client)


def _service(tmp_path, monkeypatch, **config):
    import core.paths as paths
    from core.service_registry import ServiceRegistry
    from core.codex_pool import CodexPool

    client = FakeClient()
    pool = FakePool(tmp_path)
    monkeypatch.setattr(paths, "CODEX_SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(ServiceRegistry, "get_instance", staticmethod(lambda: FakeRegistry(client)))
    monkeypatch.setattr(CodexPool, "instance", staticmethod(lambda: pool))

    svc = CodexImageService({"llm_service": "codex_llm", "cleanup": False, **config})
    svc.set_runtime_context(user_id="alice", conversation_id="conv1")
    return svc, client, pool


def test_codex_image_schema_requires_codex_llm_service_combo():
    schema = CodexImageService({}).get_parameter_schema()

    assert schema["llm_service"]["type"] == "service_ref"
    assert schema["llm_service"]["service_type"] == "llmConnection"
    assert schema["llm_service"]["provider"] == "codex-app-server"
    assert "codex_binary" not in schema
    assert "relay_service" not in schema
    assert "local" not in schema


def test_codex_image_service_ref_ui_filters_fixed_provider():
    src = Path("tasks/io/chat_ui/resources.js").read_text(encoding="utf-8")

    assert "pdef.provider || ''" in src
    assert "data-provider=\"' + fp + '\"" in src
    assert "!wantedProvider || s.provider === wantedProvider" in src


def test_codex_image_runtime_context_accepts_agent_name():
    svc = CodexImageService({"llm_service": "codex_llm"})

    svc.set_runtime_context(
        user_id="alice", conversation_id="conv1", agent_name="agentA")

    assert svc._runtime_user_id == "alice"
    assert svc._runtime_conversation_id == "conv1"
    assert svc._runtime_agent_name == "agentA"


def test_codex_image_generate_runs_through_codex_pool_and_llm_service(tmp_path, monkeypatch):
    svc, client, pool = _service(tmp_path, monkeypatch, timeout=123)

    result = svc.generate(prompt="Create a dark dashboard banner", width=1280, height=512)

    assert result == {"image_bytes": b"png-bytes", "content_type": "image/png"}
    assert client._agent_service == "codex_llm"
    assert client.setup_workdirs
    assert client.recovered == client.setup_workdirs
    assert pool.acquire_calls == 1
    assert pool.release_calls == ["pf-codex-pool-test"]
    call = pool.exec_calls[0]
    assert call["session_dir"].startswith("/cc_sessions/alice/_image_generation/")
    assert call["extra_env"] == {
        "CODEX_API_KEY": "test-key",
        "OPENAI_API_KEY": "test-key",
    }
    assert "exec" in call["codex_args"]
    assert "--model" in call["codex_args"]
    assert "gpt-5.4" in call["codex_args"]
    assert "--disable" in call["codex_args"]
    assert "image_generation" not in call["codex_args"]
    assert call["codex_args"][-1] == "-"
    assert "$imagegen" in pool.stdin_text
    assert "1280x512" in pool.stdin_text


def test_codex_image_service_reads_filestore_references_locally(monkeypatch):
    svc = CodexImageService({"llm_service": "codex_llm"})
    svc.set_runtime_context(user_id="alice", conversation_id="conv1")

    class Store:
        def get_required(self, file_id, user_id, conversation_id):
            assert file_id == "fid123"
            assert user_id == "alice"
            assert conversation_id == "conv1"
            return ("logo.webp", b"webp-bytes", "image/webp")

    from core.file_store import FileStore
    monkeypatch.setattr(FileStore, "instance", staticmethod(lambda: Store()))

    name, data = svc._load_image_reference("fs://filestore/fid123/logo.webp", 0)

    assert name == "reference_0.webp"
    assert data == b"webp-bytes"


def test_codex_image_edit_writes_references_and_passes_image_flags(tmp_path, monkeypatch):
    svc, _client, pool = _service(tmp_path, monkeypatch)

    def fake_ref(url, index):
        return f"ref_{index}.png", f"ref-{index}".encode()

    monkeypatch.setattr(svc, "_load_image_reference", fake_ref)

    result = svc.edit_image(
        prompt="Turn these into a unified app icon",
        image_urls=["https://example.test/a.png", "https://example.test/b.png"],
    )

    assert result["image_bytes"] == b"png-bytes"
    call = pool.exec_calls[0]
    assert call["codex_args"].count("-i") == 2
    assert "ref_0.png" in call["codex_args"]
    assert "ref_1.png" in call["codex_args"]
    assert (pool.last_host_dir / "ref_0.png").read_bytes() == b"ref-0"
    assert (pool.last_host_dir / "ref_1.png").read_bytes() == b"ref-1"


def test_codex_image_service_registered_by_tasks():
    from core import ServiceFactory
    from tasks import _register_all_services

    _register_all_services()

    assert ServiceFactory.get("codexImageGeneration") is CodexImageService
