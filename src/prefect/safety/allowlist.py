from __future__ import annotations

from dataclasses import dataclass


class CommandNotPermittedError(PermissionError):
    pass


@dataclass(frozen=True)
class CommandAllowlist:
    """Default-deny allowlist for server console commands.

    Allowlist is a list of prefixes; a command is allowed if it starts with one
    of these prefixes (after leading whitespace is stripped).
    """

    prefixes: tuple[str, ...]

    @staticmethod
    def default(*, extra_prefixes: tuple[str, ...] = ()) -> "CommandAllowlist":
        # Keep Phase-1 conservative. Expand later as you learn Necesse console commands.
        base = (
            "help",
            "?",
            "say ",
            "players",
            "list",
            "status",
        )
        merged = tuple(dict.fromkeys(base + tuple(extra_prefixes)))
        return CommandAllowlist(prefixes=merged)

    def is_allowed(self, command: str) -> bool:
        normalized = command.lstrip()
        if not normalized:
            return False
        return any(normalized == p or normalized.startswith(p) for p in self.prefixes)

    def require_allowed(self, command: str) -> None:
        if not self.is_allowed(command):
            raise CommandNotPermittedError("Command not permitted by allowlist")
