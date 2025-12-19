"""Integration tests for prefect.mcp.tools.PrefectCore."""
from __future__ import annotations

import time
from pathlib import Path
from unittest import mock

import pytest

from prefect.config import PrefectSettings
from prefect.mcp.tools import PrefectCore, _coerce_safe_chat_text, _looks_like_unknown_command


class TestCoerceSafeChatText:
    """Tests for _coerce_safe_chat_text helper."""

    def test_basic_text(self):
        assert _coerce_safe_chat_text("Hello world", max_len=100) == "Hello world"

    def test_strips_newlines(self):
        assert _coerce_safe_chat_text("Hello\nworld", max_len=100) == "Hello world"
        assert _coerce_safe_chat_text("Hello\r\nworld", max_len=100) == "Hello world"

    def test_removes_disallowed_chars(self):
        assert _coerce_safe_chat_text("test;cmd", max_len=100) == "testcmd"
        assert _coerce_safe_chat_text("test|pipe", max_len=100) == "testpipe"
        assert _coerce_safe_chat_text("test$(cmd)", max_len=100) == "testcmd"

    def test_truncates_long_text(self):
        result = _coerce_safe_chat_text("a" * 100, max_len=10)
        assert len(result) == 10
        assert result.endswith("…")

    def test_normalizes_whitespace(self):
        assert _coerce_safe_chat_text("hello    world", max_len=100) == "hello world"
        assert _coerce_safe_chat_text("  hello  ", max_len=100) == "hello"

    def test_handles_empty(self):
        assert _coerce_safe_chat_text("", max_len=100) == ""
        assert _coerce_safe_chat_text(None, max_len=100) == ""  # type: ignore


class TestLooksLikeUnknownCommand:
    """Tests for _looks_like_unknown_command helper."""

    def test_detects_unknown_command(self):
        assert _looks_like_unknown_command("Unknown command: foo")
        assert _looks_like_unknown_command("Command not recognized")
        assert _looks_like_unknown_command("unrecognized: bar")
        assert _looks_like_unknown_command("Invalid command")
        assert _looks_like_unknown_command("No such command: test")

    def test_case_insensitive(self):
        assert _looks_like_unknown_command("UNKNOWN COMMAND")
        assert _looks_like_unknown_command("Unknown Command")

    def test_returns_false_for_valid_output(self):
        assert not _looks_like_unknown_command("Players online: 5")
        assert not _looks_like_unknown_command("Server status: running")
        assert not _looks_like_unknown_command("")


class TestPrefectCoreInit:
    """Tests for PrefectCore initialization."""

    def test_creates_with_default_settings(self, temp_dir: Path):
        settings = PrefectSettings(
            server_root=temp_dir,
            start_server=False,
            control_mode="managed",
        )
        core = PrefectCore(settings)
        assert core.settings == settings
        assert core.log_buffer is not None
        assert core.persona_manager is not None

    def test_loads_command_names(self, temp_dir: Path, commands_json: Path):
        settings = PrefectSettings(
            server_root=temp_dir,
            commands_file=commands_json,
            start_server=False,
        )
        core = PrefectCore(settings)
        assert "help" in core.command_names
        assert "kick" in core.command_names


class TestPrefectCoreRunCommand:
    """Tests for PrefectCore.run_command method."""

    def test_rejects_unsafe_command(self, temp_dir: Path):
        settings = PrefectSettings(
            server_root=temp_dir,
            start_server=False,
        )
        core = PrefectCore(settings)
        
        result = core.run_command("help; rm -rf /")
        assert result["ok"] is False
        assert "Disallowed" in result["error"]

    def test_rejects_disallowed_command(self, temp_dir: Path):
        settings = PrefectSettings(
            server_root=temp_dir,
            start_server=False,
        )
        core = PrefectCore(settings)
        
        result = core.run_command("shutdown")
        assert result["ok"] is False
        assert "not permitted" in result["error"]

    def test_allows_allowed_command_format(self, temp_dir: Path):
        """Test that allowed commands pass validation (execution may fail without server)."""
        settings = PrefectSettings(
            server_root=temp_dir,
            start_server=False,
        )
        core = PrefectCore(settings)
        
        # The command passes validation but fails execution (no server)
        result = core.run_command("help")
        # Will fail because server not running, but shouldn't fail on allowlist
        assert "not permitted" not in result.get("error", "")


class TestPrefectCoreStatus:
    """Tests for PrefectCore.get_status method."""

    def test_returns_status_dict(self, temp_dir: Path):
        settings = PrefectSettings(
            server_root=temp_dir,
            start_server=False,
        )
        core = PrefectCore(settings)
        
        status = core.get_status()
        
        assert "running" in status
        assert "prefect_uptime_seconds" in status
        assert "log_buffer_lines" in status
        assert status["running"] is False  # Server not started


class TestPrefectCoreChatEvents:
    """Tests for PrefectCore chat event methods."""

    def test_on_chat_line_records_event(self, temp_dir: Path):
        settings = PrefectSettings(
            server_root=temp_dir,
            start_server=False,
        )
        core = PrefectCore(settings)
        
        core._on_chat_line("Player1", "Hello world")
        
        events = core.get_chat_events()
        assert len(events) == 1
        ts, player, msg = events[0]
        assert player == "Player1"
        assert msg == "Hello world"

    def test_on_activity_records_event(self, temp_dir: Path):
        settings = PrefectSettings(
            server_root=temp_dir,
            start_server=False,
        )
        core = PrefectCore(settings)
        
        core._on_activity("join", "Player1")
        core._on_activity("leave", "Player2")
        
        events = core.get_activity_events()
        assert len(events) == 2
        assert "joined" in events[0][1]
        assert "left" in events[1][1]

    def test_get_events_since_ts(self, temp_dir: Path):
        settings = PrefectSettings(
            server_root=temp_dir,
            start_server=False,
        )
        core = PrefectCore(settings)
        
        core._on_chat_line("Early", "First message")
        time.sleep(0.01)
        cutoff = time.time()
        time.sleep(0.01)
        core._on_chat_line("Late", "Second message")
        
        events = core.get_chat_events(since_ts=cutoff)
        assert len(events) == 1
        assert events[0][1] == "Late"


class TestPrefectCoreOllama:
    """Tests for PrefectCore Ollama integration."""

    def test_set_ollama(self, temp_dir: Path):
        settings = PrefectSettings(
            server_root=temp_dir,
            start_server=False,
        )
        core = PrefectCore(settings)
        
        core.set_ollama(base_url="http://new:1234", model="new-model")
        
        assert core.settings.ollama_url == "http://new:1234"
        assert core.settings.model == "new-model"

    def test_set_ollama_partial(self, temp_dir: Path):
        settings = PrefectSettings(
            server_root=temp_dir,
            start_server=False,
            ollama_url="http://original:1234",
            model="original-model",
        )
        core = PrefectCore(settings)
        
        core.set_ollama(model="new-model")
        
        assert core.settings.ollama_url == "http://original:1234"  # unchanged
        assert core.settings.model == "new-model"

    def test_list_ollama_models_handles_error(self, temp_dir: Path):
        settings = PrefectSettings(
            server_root=temp_dir,
            start_server=False,
            ollama_url="http://nonexistent:9999",
        )
        core = PrefectCore(settings)
        
        result = core.list_ollama_models()
        
        assert result["ok"] is False
        assert "error" in result


class TestPrefectCoreAnnounce:
    """Tests for PrefectCore.announce method."""

    def test_rejects_unsafe_message(self, temp_dir: Path):
        settings = PrefectSettings(
            server_root=temp_dir,
            start_server=False,
        )
        core = PrefectCore(settings)
        
        result = core.announce("test; rm -rf /")
        
        assert result["ok"] is False
        assert result["sent"] is False
        assert "Disallowed" in result["error"]

    def test_rejects_empty_message(self, temp_dir: Path):
        settings = PrefectSettings(
            server_root=temp_dir,
            start_server=False,
        )
        core = PrefectCore(settings)
        
        result = core.announce("")
        
        assert result["ok"] is False
        assert "empty" in result["error"]
