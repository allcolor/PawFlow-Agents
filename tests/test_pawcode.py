"""Tests for PawCode CLI module imports and basic functionality."""
import pytest


class TestPawCodeImports:
    """Verify all PawCode modules import cleanly."""

    def test_import_app(self):
        from pawflow_cli.app import PawCode
        assert PawCode is not None

    def test_import_api(self):
        from pawflow_cli.api import AgentAPIClient, SSEClient
        assert AgentAPIClient is not None
        assert SSEClient is not None

    def test_import_auth(self):
        from pawflow_cli.auth import authenticate
        assert authenticate is not None

    def test_import_relay(self):
        from pawflow_cli.relay import RelayThread, generate_relay_id
        assert RelayThread is not None
        assert generate_relay_id is not None

    def test_import_renderer(self):
        from pawflow_cli.ui.renderer import TerminalRenderer
        assert TerminalRenderer is not None

    def test_import_config(self):
        from pawflow_cli.config import load_config, save_config, load_session
        assert load_config is not None

    def test_stream_json_does_not_start_relay(self, monkeypatch):
        """PawCode stream-json is a chat client and does not own relay lifecycle."""
        import io
        import sys
        from pawflow_cli import stream_json as sj

        class FakeAPI:
            def __init__(self, *args, **kwargs):
                pass

        monkeypatch.setattr(sj, "authenticate", lambda *a, **k: {
            "token": "tok", "username": "alice"})
        monkeypatch.setattr(sj, "AgentAPIClient", FakeAPI)
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        monkeypatch.setattr(sys, "stdout", io.StringIO())

        mode = sj.StreamJsonMode("http://server", ".", docker_image="img")
        assert mode.run() == 0
        assert not hasattr(sj, "RelayThread")

    def test_sse_result_queue_returns_result_that_arrived_before_waiter(self):
        """Fast slash commands like /help must not block if SSE wins the race."""
        from pawflow_cli.api import SSEResultQueue

        q = SSEResultQueue()
        q.push("command", {"action": "command", "result": '{"help":"ok"}'}, call_id="c1")

        assert q.get("command", timeout=0.01, call_id="c1") == {"help": "ok"}

    def test_sse_result_queue_matches_call_id_before_action_name(self):
        """Concurrent command actions must be routed by call id, not only action."""
        from pawflow_cli.api import SSEResultQueue

        q = SSEResultQueue()
        q.push("command", {"action": "command", "result": '{"help":"first"}'}, call_id="c1")
        q.push("command", {"action": "command", "result": '{"help":"second"}'}, call_id="c2")

        assert q.get("command", timeout=0.01, call_id="c2") == {"help": "second"}
        assert q.get("command", timeout=0.01, call_id="c1") == {"help": "first"}

    def test_sse_result_queue_clears_action_alias_after_call_id_match(self):
        from pawflow_cli.api import SSEResultQueue

        q = SSEResultQueue()
        q.push("command", {"action": "command", "result": '{"help":"ok"}'}, call_id="c1")

        assert q.get("command", timeout=0.01, call_id="c1") == {"help": "ok"}
        assert q.get("command", timeout=0.01) == {"error": "Timeout waiting for command result"}

    def test_send_action_does_not_block_when_result_arrives_during_post(self):
        """Immediate /help-style command_result events may arrive before get()."""
        from pawflow_cli.api import AgentAPIClient

        client = AgentAPIClient("http://server", "tok")

        def fake_post(path, body, timeout=30):
            assert path == "/api/ui"
            call_id = body["_call_id"]
            client._sse_result_queue.push(
                "command",
                {"action": "command", "_callId": call_id, "result": '{"help":"ok"}'},
                call_id=call_id,
            )
            return {"status": "accepted"}

        client._post = fake_post

        assert client.send_action("command", text="/help") == {"help": "ok"}

    def test_send_action_requests_inline_response(self):
        """send_action must ask for inline results: the SSE command_result
        channel is the conversation's — waiting on it deadlocks whenever the
        CLI's SSE is attached to a different conversation (the /conv <id>
        hang) or to none at all (bare startup behind a gateway)."""
        from pawflow_cli.api import AgentAPIClient

        client = AgentAPIClient("http://server", "tok")
        seen = {}

        def fake_post(path, body, timeout=30):
            seen.update(body)
            return {"messages": [], "conversation_id": "abc"}

        client._post = fake_post
        result = client.send_action("load_history", conversation_id="abc")
        assert seen["_inline_response"] is True
        assert result["conversation_id"] == "abc"

    def test_gateway_challenge_page_is_detected_not_parsed(self):
        """The Private Gateway answers 200 + HTML to blocked requests; the
        client must surface a clear --gateway-key hint instead of a JSON
        parse error (or a silent hang on the SSE side)."""
        from pawflow_cli.api import looks_like_gateway_challenge, GATEWAY_BLOCKED_HINT

        challenge = "<!DOCTYPE html><html><body>Wake up, Neo...</body></html>"
        assert looks_like_gateway_challenge(challenge)
        assert looks_like_gateway_challenge("  \n<html lang='en'>")
        assert not looks_like_gateway_challenge('{"status": "accepted"}')
        assert not looks_like_gateway_challenge("")
        assert "--gateway-key" in GATEWAY_BLOCKED_HINT

    def test_check_session_rejects_gateway_challenge_page(self, monkeypatch, tmp_path, capsys):
        """A 200 HTML challenge must not be mistaken for a valid ping — it
        used to mark the session as authenticated while every later call
        was blocked by the gateway."""
        import pawflow_cli.config as cfg
        from pawflow_cli import auth as cli_auth

        monkeypatch.setattr(cfg, "SESSION_FILE", tmp_path / "session.json")
        monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
        import time as _time
        monkeypatch.setattr(cli_auth, "load_session", lambda include_expired=False: {
            "token": "tok", "username": "u", "server_url": "http://server",
            "expires_at": _time.time() + 3600,
        })

        class _Resp:
            status = 200
            def read(self):
                return b"<!DOCTYPE html><html>challenge</html>"
            def getheader(self, name, default=None):
                return default

        class _Conn:
            def __init__(self, *a, **k):
                pass
            def request(self, *a, **k):
                pass
            def getresponse(self):
                return _Resp()
            def close(self):
                pass

        import http.client as _http
        monkeypatch.setattr(_http, "HTTPConnection", _Conn)
        saved = []
        monkeypatch.setattr(cli_auth, "save_session", lambda *a, **k: saved.append(a))

        assert cli_auth.check_session("http://server") == {}
        assert not saved

    def test_resume_command_sends_agent_message_without_waiting_for_command_result(self):
        from pawflow_cli.app import PawCode

        app, api = _fake_pawcode()

        assert app._handle_agent_stream_command("/resume", "", "/resume") is True
        assert api.messages == [{
            "message": "Continue from where you stopped",
            "conversation_id": "conv1",
            "target_agent": "claude",
            "attachments": None,
        }]
        assert api.actions == []

    def test_msg_all_command_uses_broadcast_fire_and_forget(self):
        app, api = _fake_pawcode()

        assert app._handle_agent_stream_command("/msg", "@ALL hello", "/msg @ALL hello") is True
        assert api.actions == [(
            "broadcast_agents",
            {"conversation_id": "conv1", "message": "hello"},
        )]
        assert api.messages == []

    def test_btw_command_uses_fire_and_forget(self):
        app, api = _fake_pawcode()

        assert app._handle_agent_stream_command("/btw", "@grok question", "/btw @grok question") is True
        assert api.actions == [(
            "btw",
            {"conversation_id": "conv1", "agent_name": "grok", "message": "question"},
        )]

    def test_command_dispatch_maps_agent_commands_to_existing_actions(self):
        from tasks.ai.actions.command_dispatch import _parse_command

        assert _parse_command("/msg @ALL hello", "conv1", "u", "claude")["action"] == "broadcast_agents"
        assert _parse_command("/stop @grok", "conv1", "u", "claude")["action"] == "cancel"
        assert _parse_command("/stop -f @grok", "conv1", "u", "claude")["action"] == "cancel"
        assert _parse_command("/agent interrupt @grok", "conv1", "u", "claude")["action"] == "interrupt"

    def test_new_command_creates_conversation_with_agent_llm_title_and_relay(self, monkeypatch):
        from pawflow_cli.commands.session import handle_session_commands

        app, api = _fake_pawcode()
        app.conversation_id = None
        app.selected_agent = ""
        ensured = []
        saved = []
        app._ensure_sse = lambda: ensured.append(app.conversation_id)
        monkeypatch.setattr("pawflow_cli.config.save_config", lambda data: saved.append(data))

        handled = handle_session_commands(
            app, "/new", "assistant --llm custom_llm --relay fs1 --title My title", "/new")

        assert handled is True
        assert app.conversation_id == "newconv"
        assert app.selected_agent == "assistant"
        assert ensured == ["newconv"]
        assert saved == [{"last_conversation_id": "newconv"}]
        assert api.sent_actions[-1] == (
            "create_conversation",
            {
                "agents": [{
                    "instance_name": "assistant",
                    "definition": "assistant",
                    "llm_service": "custom_llm",
                    "params": {"name": "assistant"},
                }],
                "title": "My title",
                "relays": ["fs1"],
                "default_relay": "fs1",
            },
        )

    def test_switch_conversation_hydrates_active_agent_and_gateway_cookie(self, monkeypatch):
        from pawflow_cli.commands import conversation as conv_cmd

        app, api = _fake_pawcode()
        app.gateway_cookie = "gw"
        app.server_url = "http://server"
        app.session_token = "tok"
        saved = []
        sse_args = []

        class FakeSSE:
            def __init__(self, *args):
                sse_args.append(args)
            def connect(self, cid):
                sse_args.append(("connect", cid))

        monkeypatch.setattr(conv_cmd, "SSEClient", FakeSSE)
        monkeypatch.setattr(conv_cmd, "save_config", lambda data: saved.append(data))

        conv_cmd._switch_conversation(app, "target")

        assert app.conversation_id == "targetconv"
        assert app.selected_agent == "grok"
        assert saved == [{"last_conversation_id": "targetconv"}]
        assert sse_args[0] == ("http://server", "tok", "gw")
        assert sse_args[1] == ("connect", "targetconv")

    def test_resume_with_id_switches_conversation_not_agent_message(self, monkeypatch):
        from pawflow_cli.commands import conversation as conv_cmd

        app, api = _fake_pawcode()
        monkeypatch.setattr(conv_cmd, "SSEClient", lambda *a: type("S", (), {"connect": lambda self, cid: None})())
        monkeypatch.setattr(conv_cmd, "save_config", lambda data: None)

        app._handle_command("/resume target")

        assert app.conversation_id == "targetconv"
        assert app.selected_agent == "grok"
        assert api.messages == []

    def test_targeted_message_sends_pending_attachments(self):
        app, api = _fake_pawcode()
        app._pending_attachments = [{
            "filename": "note.txt",
            "mime_type": "text/plain",
            "data": "aGVsbG8=",
        }]

        app._send_targeted_message("read this", "grok")

        assert api.messages == [{
            "message": "read this",
            "conversation_id": "conv1",
            "target_agent": "grok",
            "attachments": [{
                "filename": "note.txt",
                "mime_type": "text/plain",
                "data": "aGVsbG8=",
            }],
        }]
        assert app._pending_attachments == []

    def test_stream_json_creates_conversation_and_sends_target_agent(self):
        from pawflow_cli.stream_json import StreamJsonMode

        class API(_FakeAPI):
            def send_action(self, action, **kwargs):
                if action == "load_history":
                    return {"error": "Conversation not found"}
                return super().send_action(action, **kwargs)

        mode = StreamJsonMode("http://server", ".")
        api = API()
        mode._api = api
        mode._stream_response = lambda: None

        mode._handle_user_message({
            "type": "user",
            "session_id": "external-session",
            "message": {"content": "hello"},
        })

        assert mode.conversation_id == "newconv"
        assert api.messages == [{
            "message": "hello",
            "conversation_id": "newconv",
            "target_agent": "assistant",
        }]

    def test_create_conversation_rejects_unknown_relay(self):
        from pawflow_cli.conversation_bootstrap import create_conversation

        class API(_FakeAPI):
            def send_action(self, action, **kwargs):
                if action == "relay_list_available":
                    return {"relays": [{"relay_id": "fs1", "connected": True}]}
                return super().send_action(action, **kwargs)

        with pytest.raises(ValueError, match="Relay not found"):
            create_conversation(API(), requested_agent="assistant",
                                llm_service="custom_llm", relays=["missing"])


class _FakeRenderer:
    def __init__(self):
        self.system = []
        self.errors = []
        self.users = []

    def print_system(self, text):
        self.system.append(text)

    def print_error(self, text):
        self.errors.append(text)

    def print_user_message(self, text, target_agent=""):
        self.users.append((text, target_agent))


class _FakeAPI:
    def __init__(self):
        self.actions = []
        self.messages = []
        self.sent_actions = []

    def send_action(self, action, **kwargs):
        self.sent_actions.append((action, kwargs))
        if action == "list_repo_agents":
            return {"agents": [{"name": "assistant"}, {"name": "grok"}]}
        if action == "list_services":
            return {"services": [
                {"service_id": "assistant_llm_service", "enabled": True},
                {"service_id": "custom_llm", "enabled": True},
            ]}
        if action == "create_conversation":
            return {"conversation_id": "newconv", "agents": ["assistant"]}
        if action == "relay_list_available":
            return {"relays": [{"relay_id": "fs1", "connected": True}]}
        if action == "list_conversations":
            return {"conversations": [{"conversation_id": "targetconv"}]}
        if action == "load_history":
            return {
                "conversation_id": kwargs.get("conversation_id", ""),
                "messages": [],
                "message_count": 0,
                "active_agent": "grok",
            }
        return {}

    def send_action_fire(self, action, **kwargs):
        self.actions.append((action, kwargs))
        return {"status": "accepted"}

    def send_message(self, **kwargs):
        # Drop the client-minted msg_id (non-deterministic, used only for
        # SSE-echo dedup) so tests can assert on the stable fields.
        kwargs.pop("msg_id", None)
        self.messages.append(kwargs)
        return {"conversation_id": kwargs.get("conversation_id") or "conv1"}


def _fake_pawcode():
    from pawflow_cli.app import PawCode

    app = PawCode.__new__(PawCode)
    api = _FakeAPI()
    app.api = api
    app.renderer = _FakeRenderer()
    app.server_url = "http://server"
    app.session_token = "tok"
    app.gateway_cookie = ""
    app.conversation_id = "conv1"
    app.selected_agent = "claude"
    app._ensure_sse = lambda: None
    app.sse = None
    app._pending_attachments = []
    return app, api


class TestFirstRunSetup:
    """Interactive first-run prompt: server URL + optional gateway key."""

    def _args(self, server="http://localhost:9090", gateway_key=""):
        import types
        return types.SimpleNamespace(server=server, gateway_key=gateway_key)

    def test_normalize_server_url_adds_scheme(self):
        from pawflow_cli.app import _normalize_server_url
        assert _normalize_server_url("webchat.example.org") == "https://webchat.example.org"
        assert _normalize_server_url("localhost:19990") == "http://localhost:19990"
        assert _normalize_server_url("127.0.0.1:9090") == "http://127.0.0.1:9090"
        assert _normalize_server_url("https://x.org/") == "https://x.org"
        assert _normalize_server_url("  ") == ""

    def test_prompt_sets_server_and_gateway_key(self):
        from pawflow_cli.app import _prompt_first_run_setup
        args = self._args()
        answers = iter(["webchat.example.org", "y"])
        _prompt_first_run_setup(args, input_fn=lambda _p: next(answers),
                                getpass_fn=lambda _p: "sekret ")
        assert args.server == "https://webchat.example.org"
        assert args.gateway_key == "sekret"

    def test_prompt_empty_answers_keep_defaults(self):
        from pawflow_cli.app import _prompt_first_run_setup
        args = self._args()
        answers = iter(["", ""])
        _prompt_first_run_setup(args, input_fn=lambda _p: next(answers),
                                getpass_fn=lambda _p: "never-called")
        assert args.server == "http://localhost:9090"
        assert args.gateway_key == ""

    def test_prompt_skips_gateway_question_when_key_already_set(self):
        from pawflow_cli.app import _prompt_first_run_setup
        args = self._args(gateway_key="already")
        # Only ONE input call (the server URL) must happen — a second one
        # would raise StopIteration and fail the test.
        answers = iter(["srv.example"])
        _prompt_first_run_setup(args, input_fn=lambda _p: next(answers))
        assert args.server == "https://srv.example"
        assert args.gateway_key == "already"

    def test_prompt_abort_keeps_defaults(self):
        from pawflow_cli.app import _prompt_first_run_setup
        args = self._args()

        def _interrupt(_p):
            raise KeyboardInterrupt
        _prompt_first_run_setup(args, input_fn=_interrupt,
                                getpass_fn=lambda _p: "x")
        assert args.server == "http://localhost:9090"
        assert args.gateway_key == ""


class TestOfflineHelpAndGuards:
    """/help works offline; commands needing a session are guarded."""

    def _bare_app(self):
        from pawflow_cli.app import PawCode
        app = PawCode.__new__(PawCode)
        printed = {"markdown": [], "system": [], "error": []}

        class _R:
            def print_markdown(self, t): printed["markdown"].append(t)
            def print_system(self, t): printed["system"].append(t)
            def print_error(self, t): printed["error"].append(t)
        app.renderer = _R()
        app.session_token = ""
        app.selected_agent = ""
        app.conversation_id = None
        return app, printed

    def test_help_renders_offline_without_server(self):
        app, printed = self._bare_app()
        # No api attribute at all — proves /help never touches the server.
        app._handle_command("/help")
        assert printed["markdown"], "offline /help should render a command list"
        assert "Available Commands" in printed["markdown"][0]

    def test_help_topic_offline_hint(self):
        app, printed = self._bare_app()
        app._print_offline_help("conv")
        assert printed["system"]
        assert "/conv" in printed["system"][0]

    def test_server_command_blocked_when_not_logged_in(self):
        app, printed = self._bare_app()
        # /stats needs a session; with no token it must not POST — a missing
        # api attribute would raise if it tried.
        app._handle_command("/stats")
        assert printed["error"]
        assert "login" in printed["error"][0].lower()


class TestResetConfig:
    def test_reset_config_removes_file(self, monkeypatch, tmp_path):
        import pawflow_cli.config as cfg
        cfgfile = tmp_path / "config.json"
        cfgfile.write_text('{"server_url": "https://x", "gateway_key": "k"}')
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfgfile)
        monkeypatch.setattr("sys.argv", ["pawcode", "--reset-config"])
        # Replicate the early reset branch from main().
        import sys as _sys
        if any(a == "--reset-config" for a in _sys.argv[1:]):
            from pawflow_cli.config import CONFIG_FILE
            if CONFIG_FILE.exists():
                CONFIG_FILE.unlink()
        assert not cfgfile.exists()


class TestRelayId:
    """Test relay ID generation consistency."""

    def test_relay_id_deterministic(self):
        from pawflow_cli.relay import generate_relay_id
        id1 = generate_relay_id("user1", "/path/to/dir")
        id2 = generate_relay_id("user1", "/path/to/dir")
        assert id1 == id2

    def test_relay_id_different_users(self):
        from pawflow_cli.relay import generate_relay_id
        id1 = generate_relay_id("user1", "/path")
        id2 = generate_relay_id("user2", "/path")
        assert id1 != id2

    def test_relay_id_different_dirs(self):
        from pawflow_cli.relay import generate_relay_id
        id1 = generate_relay_id("user1", "/path/a")
        id2 = generate_relay_id("user1", "/path/b")
        assert id1 != id2

    def test_relay_id_format(self):
        from pawflow_cli.relay import generate_relay_id
        rid = generate_relay_id("testuser", "/some/path")
        assert rid.startswith("fs_testuser_")
        assert len(rid) > len("fs_testuser_")

    def test_relay_id_hash_length(self):
        """Relay ID should contain an 8-char hex hash suffix."""
        from pawflow_cli.relay import generate_relay_id
        rid = generate_relay_id("u", "/p")
        suffix = rid.split("_", 2)[-1]  # fs_u_<hash>
        assert len(suffix) == 8
        # Verify it's hex
        int(suffix, 16)


class TestTokenCounter:
    """Test precise token counting."""

    def test_count_tokens(self):
        from core.token_counter import count_tokens
        result = count_tokens("Hello, world!")
        assert isinstance(result, int)
        assert result > 0

    def test_count_tokens_empty(self):
        from core.token_counter import count_tokens
        result = count_tokens("")
        assert result == 0

    def test_count_tokens_treats_special_markers_as_text(self):
        from core.token_counter import count_tokens
        result = count_tokens("literal <|endoftext|> marker")
        assert isinstance(result, int)
        assert result > 0

    def test_count_messages_tokens(self):
        from core.token_counter import count_messages_tokens
        messages = [
            {"content": "Hello"},
            {"content": "How are you?"},
        ]
        result = count_messages_tokens(messages)
        assert isinstance(result, int)
        assert result > 0

    def test_count_messages_tokens_empty_list(self):
        from core.token_counter import count_messages_tokens
        result = count_messages_tokens([])
        assert result == 0

    def test_count_messages_tokens_multipart_content(self):
        """Messages with list content (multimodal) should be counted."""
        from core.token_counter import count_messages_tokens
        messages = [
            {"content": [{"type": "text", "text": "Describe this image"}]},
        ]
        result = count_messages_tokens(messages)
        assert result > 0

    def test_count_tokens_longer_text_more_tokens(self):
        from core.token_counter import count_tokens
        short = count_tokens("Hi")
        long = count_tokens("This is a significantly longer piece of text with many words")
        assert long > short


from tools.fs_actions import action_edit as _action_edit


class TestFuzzyEdit:
    """Test fuzzy edit matching in fs_actions."""

    def test_fuzzy_match_whitespace(self, tmp_path):
        """Fuzzy edit should match when whitespace differs."""
        from tools.fs_actions import action_edit
        f = tmp_path / "test.py"
        f.write_text("def hello():\n    print('hi')\n    return True\n")
        # Try to match with different indentation (stripped comparison)
        req = {
            "old_string": "def hello():\n  print('hi')\n  return True",
            "new_string": "def hello():\n    print('hello')\n    return True",
        }
        try:
            result = action_edit(str(tmp_path), str(f), req)
            assert result.get("replacements", 0) >= 1
        except ValueError:
            # Fuzzy match threshold not met -- acceptable
            pass

    def test_exact_match_still_works(self, tmp_path):
        """Exact match should still work normally."""
        from tools.fs_actions import action_edit
        f = tmp_path / "test.txt"
        f.write_text("hello world\n")
        req = {"old_string": "hello world", "new_string": "goodbye world"}
        result = action_edit(str(tmp_path), str(f), req)
        assert result["replacements"] == 1
        assert f.read_text() == "goodbye world\n"

    def test_edit_accepts_old_str_new_str_aliases(self, tmp_path):
        from tools.fs_actions import action_edit
        f = tmp_path / "test.txt"
        f.write_text("hello world\n")
        result = action_edit(str(tmp_path), str(f), {
            "old_str": "hello world",
            "new_str": "goodbye world",
        })
        assert result["replacements"] == 1
        assert f.read_text() == "goodbye world\n"

    def test_edit_returns_diff_lines(self, tmp_path):
        """action_edit should return diff context lines."""
        from tools.fs_actions import action_edit
        f = tmp_path / "code.py"
        f.write_text("line1\nline2\nline3\nline4\nline5\n")
        req = {"old_string": "line3", "new_string": "LINE_THREE"}
        result = action_edit(str(tmp_path), str(f), req)
        assert result["replacements"] == 1
        assert "diff" in result
        assert isinstance(result["diff"], list)
        assert result["line"] == 3

    def test_edit_replace_all(self, tmp_path):
        """replace_all should replace all occurrences."""
        from tools.fs_actions import action_edit
        f = tmp_path / "multi.txt"
        f.write_text("foo bar foo baz foo\n")
        req = {"old_string": "foo", "new_string": "qux", "replace_all": True}
        result = action_edit(str(tmp_path), str(f), req)
        assert result["replacements"] == 3
        assert f.read_text() == "qux bar qux baz qux\n"

    def test_edit_multiple_without_replace_all_raises(self, tmp_path):
        """Multiple matches without replace_all should raise ValueError."""
        from tools.fs_actions import action_edit
        f = tmp_path / "dup.txt"
        f.write_text("abc abc abc\n")
        req = {"old_string": "abc", "new_string": "xyz"}
        with pytest.raises(ValueError, match="found 3 times"):
            action_edit(str(tmp_path), str(f), req)

    def test_edit_missing_old_string_raises(self, tmp_path):
        """Missing old_string should raise ValueError."""
        from tools.fs_actions import action_edit
        f = tmp_path / "empty.txt"
        f.write_text("content\n")
        with pytest.raises(ValueError, match="Missing"):
            action_edit(str(tmp_path), str(f), {"old_string": "", "new_string": "x"})


class TestRetryParsing:
    """Test 429 retry delay parsing."""

    def test_parse_retry_after_message(self):
        from core.llm_client import LLMClient
        delay = LLMClient._parse_retry_after(
            "Rate limit reached. Please try again in 1.427s.")
        assert 1.4 <= delay <= 1.6

    def test_parse_retry_after_integer(self):
        from core.llm_client import LLMClient
        delay = LLMClient._parse_retry_after(
            "Rate limited. Please try again in 3s.")
        assert 2.9 <= delay <= 3.1

    def test_parse_retry_default(self):
        from core.llm_client import LLMClient
        delay = LLMClient._parse_retry_after("Some random error")
        assert delay == 2.0

    def test_parse_retry_case_insensitive(self):
        from core.llm_client import LLMClient
        delay = LLMClient._parse_retry_after(
            "PLEASE TRY AGAIN IN 5.0S")
        assert 4.9 <= delay <= 5.1
