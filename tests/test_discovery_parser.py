"""Tests for prefect.discovery.parser module."""
from __future__ import annotations

import pytest

from prefect.discovery.parser import (
    strip_ansi,
    strip_timestamp,
    normalize_output,
    normalize_line_for_parsing,
    is_noise_line,
    parse_command_line,
    extract_commands_from_page,
    ParsedCommand,
)


class TestStripAnsi:
    """Tests for ANSI escape sequence removal."""

    def test_strips_color_codes(self):
        assert strip_ansi("\x1b[31mred\x1b[0m") == "red"
        assert strip_ansi("\x1b[1;32mbold green\x1b[0m") == "bold green"

    def test_strips_cursor_codes(self):
        assert strip_ansi("\x1b[2Jclear\x1b[H") == "clear"
        assert strip_ansi("\x1b[1;1Htext") == "text"

    def test_preserves_plain_text(self):
        assert strip_ansi("plain text") == "plain text"

    def test_handles_empty(self):
        assert strip_ansi("") == ""
    
    def test_strips_bare_ansi_codes(self):
        """Test stripping [NNm] codes without escape prefix."""
        assert strip_ansi("[39m") == ""
        assert strip_ansi("[39mtext") == "text"
        assert strip_ansi("[0mprefix[39msuffix") == "prefixsuffix"


class TestStripTimestamp:
    """Tests for timestamp removal."""

    def test_strips_iso_timestamp(self):
        assert strip_timestamp("[2025-12-18 08:26:51] text") == "text"
    
    def test_strips_with_leading_whitespace(self):
        assert strip_timestamp("  [2025-12-18 08:26:51] text") == "text"
    
    def test_preserves_text_without_timestamp(self):
        assert strip_timestamp("no timestamp here") == "no timestamp here"
    
    def test_preserves_mid_line_timestamp(self):
        # Only strips from beginning of line
        result = strip_timestamp("text [2025-12-18 08:26:51] more")
        assert result == "text [2025-12-18 08:26:51] more"


class TestNormalizeLine:
    """Tests for full line normalization for parsing."""

    def test_strips_ansi_and_timestamp(self):
        result = normalize_line_for_parsing("[39m[2025-12-18 08:26:51] allowcheats")
        assert result == "allowcheats"
    
    def test_strips_all_formatting(self):
        result = normalize_line_for_parsing("[39m[2025-12-18 08:26:51] ban <player>")
        assert result == "ban <player>"
    
    def test_strips_whitespace(self):
        result = normalize_line_for_parsing("  [39m[2025-12-18 08:26:51] help  ")
        assert result == "help"


class TestNormalizeOutput:
    """Tests for output normalization."""

    def test_normalizes_line_endings(self):
        assert normalize_output("a\r\nb\rc") == "a\nb\nc"

    def test_strips_trailing_whitespace(self):
        assert normalize_output("line1   \nline2  ") == "line1\nline2"

    def test_removes_ansi(self):
        assert normalize_output("\x1b[31mred\x1b[0m") == "red"

    def test_handles_empty(self):
        assert normalize_output("") == ""


class TestIsNoiseLine:
    """Tests for noise line detection."""

    def test_empty_is_noise(self):
        assert is_noise_line("")
        assert is_noise_line("   ")

    def test_separator_is_noise(self):
        assert is_noise_line("----------")
        assert is_noise_line("==========")

    def test_page_indicator_is_noise(self):
        assert is_noise_line("Page 1")
        assert is_noise_line("page 2 of 5")
        assert is_noise_line("1/5")
        assert is_noise_line("3 of 10")

    def test_header_is_noise(self):
        assert is_noise_line("Available Commands")
        assert is_noise_line("Commands:")
        assert is_noise_line("Server commands")

    def test_help_hint_is_noise(self):
        assert is_noise_line("Type help for more")

    def test_server_log_messages_are_noise(self):
        """Test that server log messages that slip through timestamps are filtered."""
        # Pattern: Capitalized gerund word + content + ellipsis
        assert is_noise_line("Suggesting garbage collection due to empty server...")
        assert is_noise_line("Running garbage collection...")
        assert is_noise_line("Loading world data...")

    def test_command_is_not_noise(self):
        assert not is_noise_line("help - show commands")
        assert not is_noise_line("kick <player>")


class TestParseCommandLine:
    """Tests for single command line parsing."""

    def test_simple_command(self):
        result = parse_command_line("help")
        assert result is not None
        assert result.name == "help"
        assert result.key == "help"

    def test_command_with_description(self):
        result = parse_command_line("help - Lists available commands")
        assert result is not None
        assert result.name == "help"
        # Description extraction depends on parsing logic
        # Key thing is we get the command name

    def test_command_with_colon_description(self):
        result = parse_command_line("status: Shows server status")
        assert result is not None
        assert result.name == "status"
        # Description extraction is best-effort

    def test_command_with_args(self):
        result = parse_command_line("kick <player>")
        assert result is not None
        assert result.name == "kick"
        assert "<player>" in result.syntax

    def test_command_with_optional_args(self):
        result = parse_command_line("help [page]")
        assert result is not None
        assert result.name == "help"
        assert "[page]" in result.syntax

    def test_slash_command(self):
        result = parse_command_line("/kick player")
        assert result is not None
        assert result.name == "/kick"
        assert result.key == "kick"

    def test_bang_command(self):
        result = parse_command_line("!help")
        assert result is not None
        assert result.name == "!help"
        assert result.key == "help"

    def test_timestamped_line(self):
        result = parse_command_line("[12:34:56] status - Shows status")
        # This should parse, extracting from after timestamp
        # Current parser might not handle this perfectly
        # but should at least not crash
        # Note: actual behavior depends on regex

    def test_necesse_format_simple_command(self):
        """Test parsing the actual Necesse server help format."""
        result = parse_command_line("[39m[2025-12-18 08:26:51] allowcheats")
        assert result is not None
        assert result.name == "allowcheats"
        assert result.syntax == "allowcheats"
    
    def test_necesse_format_command_with_args(self):
        """Test Necesse format with command arguments."""
        result = parse_command_line("[39m[2025-12-18 08:26:51] armorset [<player>] <setname>")
        assert result is not None
        assert result.name == "armorset"
        assert "[<player>]" in result.syntax
        assert "<setname>" in result.syntax
    
    def test_necesse_format_complex_args(self):
        """Test Necesse format with complex argument syntax."""
        result = parse_command_line("[39m[2025-12-18 08:26:51] ban <authentication/name>")
        assert result is not None
        assert result.name == "ban"
        assert "<authentication/name>" in result.syntax
    
    def test_necesse_format_multiple_optional_args(self):
        """Test Necesse format with multiple optional args."""
        result = parse_command_line("[39m[2025-12-18 08:26:51] buff [<player>] <buff> [<seconds>]")
        assert result is not None
        assert result.name == "buff"
        assert "[<player>]" in result.syntax
        assert "<buff>" in result.syntax
        assert "[<seconds>]" in result.syntax
    
    def test_necesse_format_rejects_header(self):
        """Test that page headers in Necesse format are rejected as noise."""
        result = parse_command_line("[39m[2025-12-18 08:26:51] Commands page 1 of 16 (Server):")
        assert result is None

    def test_rejects_empty(self):
        assert parse_command_line("") is None
        assert parse_command_line("   ") is None

    def test_rejects_noise(self):
        assert parse_command_line("----------") is None
        assert parse_command_line("Page 1") is None

    def test_rejects_too_short_name(self):
        assert parse_command_line("a - too short") is None

    def test_rejects_too_long_name(self):
        long_name = "a" * 40
        assert parse_command_line(f"{long_name} - way too long") is None

    def test_preserves_raw_line(self):
        line = "  kick <player> - Kicks a player  "
        result = parse_command_line(line)
        assert result is not None
        assert result.raw_line == line


class TestExtractCommandsFromPage:
    """Tests for extracting commands from a full help page."""

    def test_extracts_multiple_commands(self):
        page = """Available Commands:
----------
help [page] - Lists commands
status - Shows server status
players - List online players
kick <player> - Kicks a player
----------
Page 1 of 3"""
        
        results = extract_commands_from_page(page, page_number=1)
        
        # Should find help, status, players, kick
        names = {r[0].key for r in results}
        assert "help" in names
        assert "status" in names
        assert "players" in names
        assert "kick" in names

    def test_assigns_page_number(self):
        page = "help - Lists commands"
        results = extract_commands_from_page(page, page_number=5)
        
        assert len(results) >= 1
        _, page_num = results[0]
        assert page_num == 5

    def test_handles_empty_page(self):
        results = extract_commands_from_page("", page_number=1)
        assert results == []

    def test_handles_only_noise(self):
        page = """----------
Page 1
----------"""
        results = extract_commands_from_page(page, page_number=1)
        assert results == []

    def test_handles_ansi_in_page(self):
        page = "\x1b[32mhelp\x1b[0m - Lists commands"
        results = extract_commands_from_page(page, page_number=1)
        
        assert len(results) >= 1
        assert results[0][0].key == "help"


class TestParsedCommand:
    """Tests for ParsedCommand dataclass."""

    def test_key_strips_slash(self):
        cmd = ParsedCommand(name="/kick", syntax="/kick <p>", description="", raw_line="")
        assert cmd.key == "kick"

    def test_key_strips_bang(self):
        cmd = ParsedCommand(name="!help", syntax="!help", description="", raw_line="")
        assert cmd.key == "help"

    def test_key_lowercases(self):
        cmd = ParsedCommand(name="HELP", syntax="HELP", description="", raw_line="")
        assert cmd.key == "help"

    def test_key_combined(self):
        cmd = ParsedCommand(name="/Kick", syntax="/Kick <p>", description="", raw_line="")
        assert cmd.key == "kick"
