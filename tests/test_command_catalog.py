"""Tests for prefect.command_catalog module."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from prefect.command_catalog import (
    CommandCatalogError,
    load_command_names,
)


class TestLoadCommandNames:
    """Tests for load_command_names function."""

    def test_returns_default_when_path_none(self):
        result = load_command_names(None)
        assert "help" in result
        assert "?" in result
        assert "status" in result

    def test_returns_default_when_file_missing(self, temp_dir: Path):
        path = temp_dir / "nonexistent.json"
        result = load_command_names(path)
        assert "help" in result

    def test_loads_valid_file(self, commands_json: Path):
        result = load_command_names(commands_json)
        assert "help" in result
        assert "status" in result
        assert "kick" in result
        assert "ban" in result

    def test_deduplicates_commands(self, temp_dir: Path):
        path = temp_dir / "commands.json"
        data = {"commands": ["help", "help", "status", "status"]}
        path.write_text(json.dumps(data), encoding="utf-8")
        
        result = load_command_names(path)
        assert result.count("help") == 1
        assert result.count("status") == 1

    def test_strips_whitespace(self, temp_dir: Path):
        path = temp_dir / "commands.json"
        data = {"commands": ["  help  ", "status\t"]}
        path.write_text(json.dumps(data), encoding="utf-8")
        
        result = load_command_names(path)
        assert "help" in result
        assert "status" in result

    def test_skips_empty_names(self, temp_dir: Path):
        path = temp_dir / "commands.json"
        data = {"commands": ["help", "", "  ", "status"]}
        path.write_text(json.dumps(data), encoding="utf-8")
        
        result = load_command_names(path)
        assert "" not in result
        assert len(result) == 2

    def test_skips_non_string_items(self, temp_dir: Path):
        path = temp_dir / "commands.json"
        data = {"commands": ["help", 123, None, ["nested"], "status"]}
        path.write_text(json.dumps(data), encoding="utf-8")
        
        result = load_command_names(path)
        assert "help" in result
        assert "status" in result
        assert len(result) == 2

    def test_rejects_invalid_name_chars(self, temp_dir: Path):
        path = temp_dir / "commands.json"
        data = {"commands": ["help", "invalid;name"]}
        path.write_text(json.dumps(data), encoding="utf-8")
        
        with pytest.raises(CommandCatalogError, match="Invalid command name"):
            load_command_names(path)

    def test_rejects_too_long_name(self, temp_dir: Path):
        path = temp_dir / "commands.json"
        data = {"commands": ["a" * 33]}  # max is 32
        path.write_text(json.dumps(data), encoding="utf-8")
        
        with pytest.raises(CommandCatalogError, match="Invalid command name"):
            load_command_names(path)

    def test_allows_valid_special_chars(self, temp_dir: Path):
        path = temp_dir / "commands.json"
        data = {"commands": ["test-cmd", "test_cmd", "test.cmd", "Test123"]}
        path.write_text(json.dumps(data), encoding="utf-8")
        
        result = load_command_names(path)
        assert "test-cmd" in result
        assert "test_cmd" in result
        assert "test.cmd" in result
        assert "Test123" in result

    def test_raises_on_invalid_json(self, temp_dir: Path):
        path = temp_dir / "commands.json"
        path.write_text("not valid json {{{", encoding="utf-8")
        
        with pytest.raises(CommandCatalogError, match="Failed to parse"):
            load_command_names(path)

    def test_raises_on_missing_commands_key(self, temp_dir: Path):
        path = temp_dir / "commands.json"
        data = {"other_key": ["help"]}
        path.write_text(json.dumps(data), encoding="utf-8")
        
        with pytest.raises(CommandCatalogError, match="Invalid commands file format"):
            load_command_names(path)

    def test_raises_on_commands_not_list(self, temp_dir: Path):
        path = temp_dir / "commands.json"
        data = {"commands": "help"}  # string instead of list
        path.write_text(json.dumps(data), encoding="utf-8")
        
        with pytest.raises(CommandCatalogError, match="Invalid commands file format"):
            load_command_names(path)

    def test_returns_default_on_empty_list(self, temp_dir: Path):
        path = temp_dir / "commands.json"
        data = {"commands": []}
        path.write_text(json.dumps(data), encoding="utf-8")
        
        result = load_command_names(path)
        # When parsed list is empty, returns default
        assert "help" in result
