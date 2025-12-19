"""Tests for prefect.safety.sanitizer module."""
from __future__ import annotations

import pytest

from prefect.safety.sanitizer import (
    UnsafeInputError,
    sanitize_command,
    sanitize_announce,
    sanitize_startup_reply,
)


class TestSanitizeCommand:
    """Tests for sanitize_command function."""

    def test_valid_command(self):
        assert sanitize_command("help") == "help"
        assert sanitize_command("say hello world") == "say hello world"

    def test_strips_whitespace(self):
        assert sanitize_command("  help  ") == "help"
        assert sanitize_command("\thelp\n") == "help"

    def test_rejects_none(self):
        with pytest.raises(UnsafeInputError, match="required"):
            sanitize_command(None)  # type: ignore

    def test_rejects_empty(self):
        with pytest.raises(UnsafeInputError, match="empty"):
            sanitize_command("")
        with pytest.raises(UnsafeInputError, match="empty"):
            sanitize_command("   ")

    def test_rejects_too_long(self):
        with pytest.raises(UnsafeInputError, match="max length"):
            sanitize_command("x" * 201)
        # Custom max_length
        with pytest.raises(UnsafeInputError, match="max length"):
            sanitize_command("hello", max_length=4)

    def test_rejects_newlines(self):
        with pytest.raises(UnsafeInputError, match="Newlines"):
            sanitize_command("help\nstatus")
        with pytest.raises(UnsafeInputError, match="Newlines"):
            sanitize_command("help\rstatus")

    @pytest.mark.parametrize("char", list(";|&><$(){}[]`\\"))
    def test_rejects_shell_chars(self, char: str):
        with pytest.raises(UnsafeInputError, match="Disallowed"):
            sanitize_command(f"help {char} status")

    def test_allows_safe_special_chars(self):
        # These should NOT be blocked
        assert sanitize_command("say Hello, World!") == "say Hello, World!"
        assert sanitize_command("say 'quoted'") == "say 'quoted'"
        assert sanitize_command('say "double"') == 'say "double"'
        assert sanitize_command("say test-name_123") == "say test-name_123"


class TestSanitizeAnnounce:
    """Tests for sanitize_announce function."""

    def test_valid_message(self):
        assert sanitize_announce("Hello everyone!") == "Hello everyone!"

    def test_strips_whitespace(self):
        assert sanitize_announce("  Hello  ") == "Hello"

    def test_rejects_none(self):
        with pytest.raises(UnsafeInputError, match="required"):
            sanitize_announce(None)  # type: ignore

    def test_rejects_empty(self):
        with pytest.raises(UnsafeInputError, match="empty"):
            sanitize_announce("")

    def test_rejects_too_long(self):
        with pytest.raises(UnsafeInputError, match="max length"):
            sanitize_announce("x" * 301)

    def test_rejects_shell_chars(self):
        with pytest.raises(UnsafeInputError, match="Disallowed"):
            sanitize_announce("hello; rm -rf /")


class TestSanitizeStartupReply:
    """Tests for sanitize_startup_reply function."""

    def test_numeric_reply(self):
        assert sanitize_startup_reply("1") == "1"
        assert sanitize_startup_reply("42") == "42"
        assert sanitize_startup_reply("  5  ") == "5"

    def test_yes_no_reply(self):
        assert sanitize_startup_reply("y") == "y"
        assert sanitize_startup_reply("Y") == "y"
        assert sanitize_startup_reply("n") == "n"
        assert sanitize_startup_reply("N") == "n"
        assert sanitize_startup_reply("yes") == "y"
        assert sanitize_startup_reply("YES") == "y"
        assert sanitize_startup_reply("no") == "n"
        assert sanitize_startup_reply("NO") == "n"

    def test_rejects_none(self):
        with pytest.raises(UnsafeInputError, match="required"):
            sanitize_startup_reply(None)  # type: ignore

    def test_rejects_empty(self):
        with pytest.raises(UnsafeInputError, match="empty"):
            sanitize_startup_reply("")

    def test_rejects_too_long(self):
        with pytest.raises(UnsafeInputError, match="max length"):
            sanitize_startup_reply("123456789")  # default max is 8

    def test_rejects_invalid_content(self):
        with pytest.raises(UnsafeInputError, match="number or y/n"):
            sanitize_startup_reply("hello")
        with pytest.raises(UnsafeInputError, match="number or y/n"):
            sanitize_startup_reply("maybe")
        with pytest.raises(UnsafeInputError, match="number or y/n"):
            sanitize_startup_reply("1a")

    def test_rejects_shell_chars(self):
        with pytest.raises(UnsafeInputError, match="Disallowed"):
            sanitize_startup_reply("1;")
