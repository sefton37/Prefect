"""Tests for prefect.safety.allowlist module."""
from __future__ import annotations

import pytest

from prefect.safety.allowlist import CommandAllowlist, CommandNotPermittedError


class TestCommandAllowlist:
    """Tests for CommandAllowlist class."""

    def test_default_allowlist_allows_help(self):
        allowlist = CommandAllowlist.default()
        assert allowlist.is_allowed("help")
        assert allowlist.is_allowed("?")
        assert allowlist.is_allowed("status")
        assert allowlist.is_allowed("players")
        assert allowlist.is_allowed("list")

    def test_default_allowlist_allows_say_with_space(self):
        allowlist = CommandAllowlist.default()
        assert allowlist.is_allowed("say hello")
        assert allowlist.is_allowed("say hello world")

    def test_default_allowlist_blocks_dangerous(self):
        allowlist = CommandAllowlist.default()
        assert not allowlist.is_allowed("kick")
        assert not allowlist.is_allowed("ban")
        assert not allowlist.is_allowed("shutdown")
        assert not allowlist.is_allowed("restart")

    def test_default_allowlist_allows_interactive_prompts(self):
        allowlist = CommandAllowlist.default()
        # Single digits for menu selection
        assert allowlist.is_allowed("1")
        assert allowlist.is_allowed("2")
        assert allowlist.is_allowed("9")
        assert allowlist.is_allowed("0")
        # y/n for confirmations
        assert allowlist.is_allowed("y")
        assert allowlist.is_allowed("n")
        assert allowlist.is_allowed("yes")
        assert allowlist.is_allowed("no")

    def test_extra_prefixes(self):
        allowlist = CommandAllowlist.default(extra_prefixes=("kick", "kick "))
        assert allowlist.is_allowed("kick")
        assert allowlist.is_allowed("kick player1")
        # But ban is still blocked
        assert not allowlist.is_allowed("ban")

    def test_is_allowed_strips_leading_whitespace(self):
        allowlist = CommandAllowlist.default()
        assert allowlist.is_allowed("  help")
        assert allowlist.is_allowed("\thelp")

    def test_is_allowed_empty_string(self):
        allowlist = CommandAllowlist.default()
        assert not allowlist.is_allowed("")
        assert not allowlist.is_allowed("   ")

    def test_require_allowed_passes(self):
        allowlist = CommandAllowlist.default()
        # Should not raise
        allowlist.require_allowed("help")
        allowlist.require_allowed("say hello")

    def test_require_allowed_raises(self):
        allowlist = CommandAllowlist.default()
        with pytest.raises(CommandNotPermittedError, match="not permitted"):
            allowlist.require_allowed("kick player1")

    def test_prefix_matching_exact(self):
        # When prefix equals command exactly
        allowlist = CommandAllowlist(prefixes=("help",))
        assert allowlist.is_allowed("help")
        # "helper" starts with "help" so it should match
        assert allowlist.is_allowed("helper")

    def test_prefix_matching_with_space(self):
        # "say " prefix should require the space
        allowlist = CommandAllowlist(prefixes=("say ",))
        assert allowlist.is_allowed("say hello")
        # "say" alone doesn't have the trailing space
        assert not allowlist.is_allowed("say")
        # "saying" doesn't start with "say "
        assert not allowlist.is_allowed("saying")

    def test_deduplication(self):
        allowlist = CommandAllowlist.default(extra_prefixes=("help", "help"))
        # Should not error, duplicates are removed
        assert allowlist.is_allowed("help")


class TestDigitPrefixBehavior:
    """Tests to verify digit prefix behavior and potential issues."""

    def test_digit_allows_multi_digit(self):
        """Verify that '1' prefix allows '10', '11', etc."""
        allowlist = CommandAllowlist.default()
        # This is current behavior - may be a bug
        assert allowlist.is_allowed("10")
        assert allowlist.is_allowed("123")

    def test_digit_prefix_potential_issue(self):
        """Document that digit prefixes could allow unintended commands.
        
        This test documents current behavior that may be undesirable:
        A command starting with '1' would be allowed even if not numeric.
        """
        allowlist = CommandAllowlist.default()
        # This passes because '1abc' starts with '1'
        # Depending on server, this could be a security concern
        assert allowlist.is_allowed("1abc")
