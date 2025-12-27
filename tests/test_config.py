"""Tests for prefect.config module."""
from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from prefect.config import PrefectSettings, get_settings


class TestPrefectSettings:
    """Tests for PrefectSettings class."""

    def test_default_values(self):
        settings = PrefectSettings()
        assert settings.ollama_url == "http://127.0.0.1:11434"
        assert settings.model == "llama3.1"
        assert settings.control_mode == "managed"
        assert settings.bedrock_control_mode == "tmux"
        assert settings.mcp_transport == "stdio"
        assert settings.chat_mention_enabled is True

    def test_env_override(self):
        with mock.patch.dict(os.environ, {
            "PREFECT_OLLAMA_URL": "http://custom:1234",
            "PREFECT_MODEL": "custom-model",
        }):
            settings = PrefectSettings()
            assert settings.ollama_url == "http://custom:1234"
            assert settings.model == "custom-model"

    def test_server_root_is_path(self):
        settings = PrefectSettings()
        assert isinstance(settings.server_root, Path)

    def test_log_path_default_none(self):
        settings = PrefectSettings()
        assert settings.log_path is None

    def test_commands_file_default(self):
        settings = PrefectSettings()
        assert settings.commands_file == Path("commands.json")

    def test_safety_limits(self):
        settings = PrefectSettings()
        assert settings.max_command_length == 200
        assert settings.max_announce_length == 300
        assert settings.max_startup_reply_length == 8

    def test_chat_settings(self):
        settings = PrefectSettings()
        assert settings.chat_cooldown_seconds == 5.0
        assert settings.chat_max_reply_length == 240
        assert settings.chat_context_turns == 6
        assert settings.chat_context_ttl_seconds == 180.0


class TestGetSettings:
    """Tests for get_settings function."""

    def test_returns_settings_instance(self):
        settings = get_settings()
        assert isinstance(settings, PrefectSettings)

    def test_creates_new_instance_each_call(self):
        """Document current behavior: each call creates new instance."""
        s1 = get_settings()
        s2 = get_settings()
        # Currently creates new instances
        assert s1 is not s2
