"""Tests for prefect.discovery.bootstrap module."""
from __future__ import annotations

from pathlib import Path

import pytest

from prefect.discovery.bootstrap import (
    AllowlistBootstrapper,
    AllowlistEntry,
    DeniedEntry,
    GeneratedAllowlist,
)
from prefect.discovery.registry import (
    CommandEntry,
    CommandRegistry,
    DiscoveryMetadata,
    DiscoverySnapshot,
)


def make_snapshot(commands: list[tuple[str, str]]) -> DiscoverySnapshot:
    """Helper to create a snapshot with given commands.
    
    Args:
        commands: List of (name, description) tuples.
    """
    snapshot = DiscoverySnapshot(
        server_root="/test",
        captured_at="2025-12-18T12:00:00",
        help=DiscoveryMetadata(pages_captured=1),
        commands=CommandRegistry(),
    )
    
    for name, desc in commands:
        entry = CommandEntry(
            key=name.lstrip("/!").lower(),
            name=name,
            syntax=name,
            description=desc,
            source="help",
            page=1,
            raw_line=f"{name} - {desc}",
        )
        snapshot.commands.add(entry)
    
    return snapshot


class TestAllowlistEntry:
    """Tests for AllowlistEntry dataclass."""

    def test_to_dict(self):
        entry = AllowlistEntry(
            command="help",
            max_len=100,
            arg_policy="any",
            reason="safe_name_pattern",
        )
        
        d = entry.to_dict()
        
        assert d["command"] == "help"
        assert d["max_len"] == 100
        assert d["arg_policy"] == "any"
        assert d["reason"] == "safe_name_pattern"

    def test_from_dict(self):
        d = {
            "command": "say",
            "max_len": 400,
            "arg_policy": "message",
            "reason": "messaging_command",
        }
        
        entry = AllowlistEntry.from_dict(d)
        
        assert entry.command == "say"
        assert entry.max_len == 400
        assert entry.arg_policy == "message"


class TestGeneratedAllowlist:
    """Tests for GeneratedAllowlist."""

    def test_default_sanitization(self):
        allowlist = GeneratedAllowlist(generated_at="2025-12-18T12:00:00")
        
        assert ";" in allowlist.sanitization["reject_chars"]
        assert "|" in allowlist.sanitization["reject_chars"]
        assert "&&" in allowlist.sanitization["reject_patterns"]

    def test_get_allowed_commands(self):
        allowlist = GeneratedAllowlist(
            generated_at="",
            allowed=[
                AllowlistEntry(command="help", max_len=100),
                AllowlistEntry(command="status", max_len=100),
            ],
        )
        
        allowed = allowlist.get_allowed_commands()
        
        assert allowed == {"help", "status"}

    def test_is_allowed_returns_entry(self):
        allowlist = GeneratedAllowlist(
            generated_at="",
            allowed=[
                AllowlistEntry(command="help", max_len=100),
            ],
        )
        
        entry = allowlist.is_allowed("help")
        assert entry is not None
        assert entry.command == "help"

    def test_is_allowed_normalizes_key(self):
        allowlist = GeneratedAllowlist(
            generated_at="",
            allowed=[
                AllowlistEntry(command="help", max_len=100),
            ],
        )
        
        assert allowlist.is_allowed("HELP") is not None
        assert allowlist.is_allowed("/help") is not None

    def test_is_allowed_returns_none_for_denied(self):
        allowlist = GeneratedAllowlist(
            generated_at="",
            allowed=[AllowlistEntry(command="help", max_len=100)],
            denied=[DeniedEntry(command="kick", reason="dangerous")],
        )
        
        assert allowlist.is_allowed("kick") is None

    def test_save_and_load(self, temp_dir: Path):
        allowlist = GeneratedAllowlist(
            generated_at="2025-12-18T12:00:00",
            mode="default_deny",
            allowed=[AllowlistEntry(command="help", max_len=100)],
            denied=[DeniedEntry(command="kick", reason="moderation")],
        )
        
        path = temp_dir / "allowlist.json"
        allowlist.save(path)
        
        loaded = GeneratedAllowlist.load(path)
        
        assert loaded.generated_at == "2025-12-18T12:00:00"
        assert loaded.mode == "default_deny"
        assert len(loaded.allowed) == 1
        assert len(loaded.denied) == 1
        assert loaded.allowed[0].command == "help"

    def test_to_markdown(self):
        allowlist = GeneratedAllowlist(
            generated_at="2025-12-18T12:00:00",
            allowed=[AllowlistEntry(command="help", max_len=100, reason="safe")],
            denied=[DeniedEntry(command="kick", reason="dangerous")],
        )
        
        md = allowlist.to_markdown()
        
        assert "# Prefect Allowlist Report" in md
        assert "help" in md
        assert "kick" in md
        assert "Allowed Commands" in md
        assert "Denied Commands" in md


class TestAllowlistBootstrapper:
    """Tests for AllowlistBootstrapper."""

    def test_allows_help(self):
        snapshot = make_snapshot([("help", "Lists commands")])
        bootstrapper = AllowlistBootstrapper()
        
        allowlist = bootstrapper.bootstrap(snapshot)
        
        assert allowlist.is_allowed("help") is not None
        assert len(allowlist.denied) == 0

    def test_allows_status_commands(self):
        snapshot = make_snapshot([
            ("status", "Shows status"),
            ("playerlist", "Lists players"),
            ("serverinfo", "Server info"),
        ])
        bootstrapper = AllowlistBootstrapper()
        
        allowlist = bootstrapper.bootstrap(snapshot)
        
        assert allowlist.is_allowed("status") is not None
        assert allowlist.is_allowed("playerlist") is not None
        assert allowlist.is_allowed("serverinfo") is not None

    def test_allows_messaging_with_restrictions(self):
        snapshot = make_snapshot([("say", "Send message")])
        bootstrapper = AllowlistBootstrapper()
        
        allowlist = bootstrapper.bootstrap(snapshot)
        
        entry = allowlist.is_allowed("say")
        assert entry is not None
        assert entry.arg_policy == "message"

    def test_denies_kick_ban(self):
        snapshot = make_snapshot([
            ("kick", "Kicks a player"),
            ("ban", "Bans a player"),
        ])
        bootstrapper = AllowlistBootstrapper()
        
        allowlist = bootstrapper.bootstrap(snapshot)
        
        assert allowlist.is_allowed("kick") is None
        assert allowlist.is_allowed("ban") is None
        assert len(allowlist.denied) == 2

    def test_denies_admin_commands(self):
        snapshot = make_snapshot([
            ("op", "Make operator"),
            ("deop", "Remove operator"),
            ("admin", "Admin commands"),
        ])
        bootstrapper = AllowlistBootstrapper()
        
        allowlist = bootstrapper.bootstrap(snapshot)
        
        for cmd in ["op", "deop", "admin"]:
            assert allowlist.is_allowed(cmd) is None

    def test_denies_save_load_commands(self):
        snapshot = make_snapshot([
            ("save", "Save world"),
            ("load", "Load world"),
            ("backup", "Backup world"),
        ])
        bootstrapper = AllowlistBootstrapper()
        
        allowlist = bootstrapper.bootstrap(snapshot)
        
        for cmd in ["save", "load", "backup"]:
            assert allowlist.is_allowed(cmd) is None

    def test_denies_cheats(self):
        snapshot = make_snapshot([
            ("give", "Give items"),
            ("spawn", "Spawn entity"),
            ("god", "God mode"),
            ("tp", "Teleport"),
        ])
        bootstrapper = AllowlistBootstrapper()
        
        allowlist = bootstrapper.bootstrap(snapshot)
        
        for cmd in ["give", "spawn", "god", "tp"]:
            assert allowlist.is_allowed(cmd) is None

    def test_denies_unknown_by_default(self):
        snapshot = make_snapshot([
            ("weirdcommand", "Does something unknown"),
        ])
        bootstrapper = AllowlistBootstrapper()
        
        allowlist = bootstrapper.bootstrap(snapshot)
        
        assert allowlist.is_allowed("weirdcommand") is None
        assert len(allowlist.denied) == 1
        assert allowlist.denied[0].reason == "unknown_unclassified"

    def test_allows_description_hints(self):
        snapshot = make_snapshot([
            ("showstats", "Shows current settings"),
            ("displaylist", "Display player list"),
        ])
        bootstrapper = AllowlistBootstrapper()
        
        allowlist = bootstrapper.bootstrap(snapshot)
        
        # These should be allowed due to "show"/"display" in description
        # The name pattern matching also matters - "showstats" might match safe pattern
        assert allowlist.is_allowed("displaylist") is not None

    def test_bootstrap_and_save(self, temp_dir: Path):
        snapshot = make_snapshot([
            ("help", "Lists commands"),
            ("kick", "Kicks player"),
        ])
        bootstrapper = AllowlistBootstrapper()
        
        allowlist, json_path, md_path = bootstrapper.bootstrap_and_save(
            snapshot, temp_dir, snapshot_path="/test/snapshot.json"
        )
        
        assert json_path.exists()
        assert md_path is not None and md_path.exists()
        assert allowlist.source_snapshot == "/test/snapshot.json"
