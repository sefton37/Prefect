"""Tests for prefect.discovery.discoverer module."""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from prefect.discovery.discoverer import CommandDiscoverer, ConsoleResult


class MockCommandRunner:
    """Mock command runner for testing discovery."""
    
    def __init__(self):
        self.responses: dict[str, str] = {}
        self.call_count = 0
        self.calls: list[str] = []
    
    def set_response(self, cmd: str, output: str):
        self.responses[cmd] = output
    
    def __call__(self, cmd: str) -> ConsoleResult:
        self.calls.append(cmd)
        self.call_count += 1
        output = self.responses.get(cmd, "")
        return ConsoleResult(
            stdout=output,
            exit_ok=True,
            timestamp="2025-12-18T12:00:00-06:00",
        )


class TestCommandDiscoverer:
    """Tests for CommandDiscoverer."""

    def test_discovers_from_single_page(self, temp_dir: Path):
        runner = MockCommandRunner()
        runner.set_response("help", """Available Commands:
help [page] - Lists commands
status - Shows server status
players - List online players
""")
        runner.set_response("help 2", "")  # Empty = terminate
        
        discoverer = CommandDiscoverer(
            run_command=runner,
            server_root=temp_dir,
            max_help_pages=10,
            page_stable_limit=2,
            inter_page_delay=0,
        )
        
        snapshot = discoverer.discover()
        
        assert len(snapshot.commands) >= 3
        assert snapshot.commands.get("help") is not None
        assert snapshot.commands.get("status") is not None
        assert snapshot.commands.get("players") is not None

    def test_discovers_from_multiple_pages(self, temp_dir: Path):
        runner = MockCommandRunner()
        runner.set_response("help", """Page 1
help - Lists commands
status - Status""")
        runner.set_response("help 2", """Page 2
kick <player> - Kicks
ban <player> - Bans""")
        runner.set_response("help 3", "")  # Empty = terminate
        
        discoverer = CommandDiscoverer(
            run_command=runner,
            server_root=temp_dir,
            max_help_pages=10,
            page_stable_limit=2,
            inter_page_delay=0,
        )
        
        snapshot = discoverer.discover()
        
        assert snapshot.help.pages_captured >= 2
        assert snapshot.commands.get("help") is not None
        assert snapshot.commands.get("kick") is not None
        assert snapshot.commands.get("ban") is not None

    def test_detects_pagination_loop(self, temp_dir: Path):
        runner = MockCommandRunner()
        # Same content on pages 1 and 2 (loop)
        page_content = """help - Lists commands
status - Status"""
        runner.set_response("help", page_content)
        runner.set_response("help 2", page_content)  # Same hash
        
        discoverer = CommandDiscoverer(
            run_command=runner,
            server_root=temp_dir,
            max_help_pages=10,
            page_stable_limit=2,
            inter_page_delay=0,
        )
        
        snapshot = discoverer.discover()
        
        assert snapshot.help.termination_reason == "pagination_loop_detected"
        assert snapshot.help.pages_captured == 1

    def test_terminates_on_stable_no_new_commands(self, temp_dir: Path):
        runner = MockCommandRunner()
        runner.set_response("help", "help - Lists commands")
        runner.set_response("help 2", "status - Status (same page different content)")
        runner.set_response("help 3", "other - Other (no new commands)")
        runner.set_response("help 4", "another - Another (no new commands)")
        
        # With page_stable_limit=2, after 2 pages with no new unique commands, stop
        discoverer = CommandDiscoverer(
            run_command=runner,
            server_root=temp_dir,
            max_help_pages=10,
            page_stable_limit=2,
            inter_page_delay=0,
        )
        
        snapshot = discoverer.discover()
        
        # Should have discovered commands and terminated
        assert len(snapshot.commands) >= 1

    def test_respects_max_pages(self, temp_dir: Path):
        runner = MockCommandRunner()
        # Generate unique content for each page
        for i in range(1, 60):
            if i == 1:
                runner.set_response("help", f"cmd{i} - Command {i}")
            else:
                runner.set_response(f"help {i}", f"cmd{i} - Command {i}")
        
        discoverer = CommandDiscoverer(
            run_command=runner,
            server_root=temp_dir,
            max_help_pages=5,  # Hard cap
            page_stable_limit=10,
            inter_page_delay=0,
        )
        
        snapshot = discoverer.discover()
        
        assert snapshot.help.pages_attempted <= 5
        assert snapshot.help.termination_reason == "max_pages_reached"

    def test_handles_command_failure(self, temp_dir: Path):
        def failing_runner(cmd: str) -> ConsoleResult:
            return ConsoleResult(stdout="Error", exit_ok=False, timestamp="")
        
        discoverer = CommandDiscoverer(
            run_command=failing_runner,
            server_root=temp_dir,
            max_help_pages=10,
            inter_page_delay=0,
        )
        
        snapshot = discoverer.discover()
        
        assert snapshot.help.termination_reason == "help_command_failed"
        assert len(snapshot.commands) == 0

    def test_discover_and_save(self, temp_dir: Path):
        runner = MockCommandRunner()
        runner.set_response("help", "test - A test command")
        runner.set_response("help 2", "")
        
        discoverer = CommandDiscoverer(
            run_command=runner,
            server_root=temp_dir,
            inter_page_delay=0,
        )
        
        output_dir = temp_dir / "snapshots"
        snapshot, filepath = discoverer.discover_and_save(output_dir)
        
        assert filepath.exists()
        assert filepath.suffix == ".json"
        assert len(snapshot.commands) >= 1


class TestConsoleResult:
    """Tests for ConsoleResult dataclass."""

    def test_fields(self):
        result = ConsoleResult(
            stdout="output",
            exit_ok=True,
            timestamp="2025-12-18T12:00:00",
        )
        
        assert result.stdout == "output"
        assert result.exit_ok is True
        assert result.timestamp == "2025-12-18T12:00:00"
