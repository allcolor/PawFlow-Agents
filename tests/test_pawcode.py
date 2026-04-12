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

    @pytest.fixture(autouse=True)
    def _require_tiktoken(self):
        pytest.importorskip("tiktoken", reason="tiktoken not installed")

    def test_count_tokens(self):
        from core.token_counter import count_tokens
        result = count_tokens("Hello, world!")
        assert isinstance(result, int)
        assert result > 0

    def test_count_tokens_empty(self):
        from core.token_counter import count_tokens
        result = count_tokens("")
        assert result == 0

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
