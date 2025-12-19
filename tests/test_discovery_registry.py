"""Tests for prefect.discovery.registry module."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from prefect.discovery.parser import ParsedCommand
from prefect.discovery.registry import (
    CommandEntry,
    CommandRegistry,
    DiscoveryMetadata,
    DiscoverySnapshot,
    generate_snapshot_filename,
)


class TestCommandEntry:
    """Tests for CommandEntry dataclass."""

    def test_to_dict(self):
        entry = CommandEntry(
            key="help",
            name="help",
            syntax="help [page]",
            description="Lists commands",
            source="help",
            page=1,
            raw_line="help [page] - Lists commands",
        )
        
        d = entry.to_dict()
        
        assert d["key"] == "help"
        assert d["name"] == "help"
        assert d["syntax"] == "help [page]"
        assert d["description"] == "Lists commands"
        assert d["source"] == "help"
        assert d["page"] == 1
        assert d["raw_line"] == "help [page] - Lists commands"

    def test_from_dict(self):
        d = {
            "key": "kick",
            "name": "kick",
            "syntax": "kick <player>",
            "description": "Kicks a player",
            "source": "help",
            "page": 2,
            "raw_line": "kick <player> - Kicks a player",
        }
        
        entry = CommandEntry.from_dict(d)
        
        assert entry.key == "kick"
        assert entry.name == "kick"
        assert entry.page == 2

    def test_from_dict_minimal(self):
        d = {"key": "test", "name": "test"}
        
        entry = CommandEntry.from_dict(d)
        
        assert entry.key == "test"
        assert entry.syntax == "test"  # defaults to name
        assert entry.description == ""
        assert entry.source == "help"
        assert entry.page == 1

    def test_from_parsed(self):
        parsed = ParsedCommand(
            name="/kick",
            syntax="/kick <player>",
            description="Kicks a player",
            raw_line="/kick <player> - Kicks a player",
        )
        
        entry = CommandEntry.from_parsed(parsed, page=3, source="help")
        
        assert entry.key == "kick"  # normalized
        assert entry.name == "/kick"  # original
        assert entry.page == 3


class TestCommandRegistry:
    """Tests for CommandRegistry."""

    def test_add_new_command(self):
        registry = CommandRegistry()
        entry = CommandEntry(key="help", name="help", syntax="help", description="", source="help", page=1, raw_line="")
        
        result = registry.add(entry)
        
        assert result is True
        assert len(registry) == 1

    def test_add_duplicate_returns_false(self):
        registry = CommandRegistry()
        entry1 = CommandEntry(key="help", name="help", syntax="help", description="", source="help", page=1, raw_line="")
        entry2 = CommandEntry(key="help", name="Help", syntax="Help", description="", source="help", page=2, raw_line="")
        
        registry.add(entry1)
        result = registry.add(entry2)
        
        assert result is False
        assert len(registry) == 1

    def test_get_by_key(self):
        registry = CommandRegistry()
        entry = CommandEntry(key="kick", name="kick", syntax="kick", description="", source="help", page=1, raw_line="")
        registry.add(entry)
        
        assert registry.get("kick") is entry
        assert registry.get("KICK") is entry  # normalized lookup
        assert registry.get("/kick") is entry  # slash stripped

    def test_get_missing_returns_none(self):
        registry = CommandRegistry()
        assert registry.get("nonexistent") is None

    def test_iter(self):
        registry = CommandRegistry()
        registry.add(CommandEntry(key="a", name="a", syntax="", description="", source="", page=1, raw_line=""))
        registry.add(CommandEntry(key="b", name="b", syntax="", description="", source="", page=1, raw_line=""))
        
        keys = [e.key for e in registry]
        assert set(keys) == {"a", "b"}

    def test_to_list(self):
        registry = CommandRegistry()
        registry.add(CommandEntry(key="help", name="help", syntax="help", description="", source="help", page=1, raw_line=""))
        registry.add(CommandEntry(key="kick", name="kick", syntax="kick", description="", source="help", page=1, raw_line=""))
        
        lst = registry.to_list()
        
        assert len(lst) == 2
        assert all(isinstance(item, dict) for item in lst)
        # Should be sorted by key
        assert lst[0]["key"] == "help"
        assert lst[1]["key"] == "kick"

    def test_from_list(self):
        lst = [
            {"key": "help", "name": "help"},
            {"key": "status", "name": "status"},
        ]
        
        registry = CommandRegistry.from_list(lst)
        
        assert len(registry) == 2
        assert registry.get("help") is not None
        assert registry.get("status") is not None


class TestDiscoveryMetadata:
    """Tests for DiscoveryMetadata."""

    def test_to_dict(self):
        meta = DiscoveryMetadata(
            pages_attempted=10,
            pages_captured=5,
            termination_reason="stable_no_new_commands",
            page_hashes={1: "abc123", 2: "def456"},
        )
        
        d = meta.to_dict()
        
        assert d["pages_attempted"] == 10
        assert d["pages_captured"] == 5
        assert d["termination_reason"] == "stable_no_new_commands"
        assert d["page_hashes"]["1"] == "abc123"  # keys become strings

    def test_from_dict(self):
        d = {
            "pages_attempted": 7,
            "pages_captured": 4,
            "termination_reason": "empty_page",
            "page_hashes": {"1": "hash1", "3": "hash3"},
        }
        
        meta = DiscoveryMetadata.from_dict(d)
        
        assert meta.pages_attempted == 7
        assert meta.page_hashes[1] == "hash1"  # keys converted to int


class TestDiscoverySnapshot:
    """Tests for DiscoverySnapshot."""

    def test_create(self, temp_dir: Path):
        snapshot = DiscoverySnapshot.create(temp_dir)
        
        assert snapshot.server_root == str(temp_dir)
        assert snapshot.captured_at  # not empty
        assert len(snapshot.commands) == 0

    def test_to_dict(self, temp_dir: Path):
        snapshot = DiscoverySnapshot.create(temp_dir)
        snapshot.help.pages_attempted = 3
        snapshot.commands.add(CommandEntry(
            key="help", name="help", syntax="help", description="", source="help", page=1, raw_line=""
        ))
        
        d = snapshot.to_dict()
        
        assert d["server_root"] == str(temp_dir)
        assert d["captured_at"]
        assert d["help"]["pages_attempted"] == 3
        assert len(d["commands"]) == 1

    def test_save_and_load(self, temp_dir: Path):
        snapshot = DiscoverySnapshot.create(temp_dir)
        snapshot.help.pages_attempted = 5
        snapshot.help.termination_reason = "test"
        snapshot.commands.add(CommandEntry(
            key="test", name="test", syntax="test", description="A test", source="help", page=1, raw_line="test - A test"
        ))
        
        path = temp_dir / "test_snapshot.json"
        snapshot.save(path)
        
        loaded = DiscoverySnapshot.load(path)
        
        assert loaded.server_root == str(temp_dir)
        assert loaded.help.pages_attempted == 5
        assert loaded.help.termination_reason == "test"
        assert len(loaded.commands) == 1
        assert loaded.commands.get("test") is not None


class TestGenerateSnapshotFilename:
    """Tests for snapshot filename generation."""

    def test_format(self):
        filename = generate_snapshot_filename()
        
        assert filename.startswith("commands-")
        assert filename.endswith(".json")
        # Should be like commands-20251218-123456.json
        assert len(filename) == len("commands-YYYYMMDD-HHMMSS.json")
