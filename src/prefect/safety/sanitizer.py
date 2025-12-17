from __future__ import annotations


class UnsafeInputError(ValueError):
    pass


# Explicitly disallow common shell chaining / expansion / redirection characters.
_DISALLOWED_CHARS = set(";|&><$(){}[]`\\")


def _check_common(value: str) -> None:
    if "\n" in value or "\r" in value:
        raise UnsafeInputError("Newlines are not permitted")
    if any(ch in _DISALLOWED_CHARS for ch in value):
        raise UnsafeInputError("Disallowed characters detected")


def sanitize_command(command: str, *, max_length: int = 200) -> str:
    if command is None:
        raise UnsafeInputError("Command is required")

    cmd = command.strip()
    if not cmd:
        raise UnsafeInputError("Command is empty")

    if len(cmd) > max_length:
        raise UnsafeInputError(f"Command exceeds max length ({max_length})")

    _check_common(cmd)
    return cmd


def sanitize_announce(message: str, *, max_length: int = 300) -> str:
    if message is None:
        raise UnsafeInputError("Message is required")

    msg = message.strip()
    if not msg:
        raise UnsafeInputError("Message is empty")

    if len(msg) > max_length:
        raise UnsafeInputError(f"Message exceeds max length ({max_length})")

    _check_common(msg)
    return msg


def sanitize_startup_reply(reply: str, *, max_length: int = 8) -> str:
    """Very strict sanitizer for interactive startup prompts.

    Only allows short numeric answers (e.g. "1") or y/n (case-insensitive).
    """

    if reply is None:
        raise UnsafeInputError("Reply is required")

    value = reply.strip()
    if not value:
        raise UnsafeInputError("Reply is empty")

    if len(value) > max_length:
        raise UnsafeInputError(f"Reply exceeds max length ({max_length})")

    _check_common(value)

    lower = value.lower()
    if lower in {"y", "n", "yes", "no"}:
        return lower[0]

    if value.isdigit():
        return value

    raise UnsafeInputError("Reply must be a number or y/n")
