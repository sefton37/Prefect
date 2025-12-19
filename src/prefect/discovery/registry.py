"""Command registry data models and snapshot serialization.

Stores discovered commands with full provenance for auditing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prefect.discovery.parser import ParsedCommand


@dataclass
class CommandEntry:
    """A discovered command with full metadata."""
    key: str  # Normalized de-dupe key
    name: str  # Original name as discovered
    syntax: str  # Full syntax string
    description: str  # Description if found
    source: str  # "help" or other discovery source
    page: int  # Page number where discovered
    raw_line: str  # Original unparsed line
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "syntax": self.syntax,
            "description": self.description,
            "source": self.source,
            "page": self.page,
            "raw_line": self.raw_line,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommandEntry:
        return cls(
            key=data["key"],
            name=data["name"],
            syntax=data.get("syntax", data["name"]),
            description=data.get("description", ""),
            source=data.get("source", "help"),
            page=data.get("page", 1),
            raw_line=data.get("raw_line", ""),
        )
    
    @classmethod
    def from_parsed(cls, parsed: ParsedCommand, page: int, source: str = "help") -> CommandEntry:
        return cls(
            key=parsed.key,
            name=parsed.name,
            syntax=parsed.syntax,
            description=parsed.description,
            source=source,
            page=page,
            raw_line=parsed.raw_line,
        )


@dataclass
class DiscoveryMetadata:
    """Metadata about the discovery session."""
    pages_attempted: int = 0
    pages_captured: int = 0
    termination_reason: str = ""
    page_hashes: dict[int, str] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "pages_attempted": self.pages_attempted,
            "pages_captured": self.pages_captured,
            "termination_reason": self.termination_reason,
            "page_hashes": {str(k): v for k, v in self.page_hashes.items()},
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiscoveryMetadata:
        page_hashes = {}
        raw_hashes = data.get("page_hashes", {})
        for k, v in raw_hashes.items():
            page_hashes[int(k)] = v
        return cls(
            pages_attempted=data.get("pages_attempted", 0),
            pages_captured=data.get("pages_captured", 0),
            termination_reason=data.get("termination_reason", ""),
            page_hashes=page_hashes,
        )


@dataclass
class CommandRegistry:
    """Registry of discovered commands."""
    commands: dict[str, CommandEntry] = field(default_factory=dict)  # key -> entry
    
    def add(self, entry: CommandEntry) -> bool:
        """Add a command entry. Returns True if new, False if duplicate."""
        if entry.key in self.commands:
            return False
        self.commands[entry.key] = entry
        return True
    
    def get(self, key: str) -> CommandEntry | None:
        """Get command by normalized key."""
        return self.commands.get(key.lstrip("/!").lower())
    
    def __len__(self) -> int:
        return len(self.commands)
    
    def __iter__(self):
        return iter(self.commands.values())
    
    def keys(self) -> list[str]:
        return list(self.commands.keys())
    
    def to_list(self) -> list[dict[str, Any]]:
        """Convert to list of dicts for JSON serialization."""
        return [entry.to_dict() for entry in sorted(self.commands.values(), key=lambda e: e.key)]
    
    @classmethod
    def from_list(cls, data: list[dict[str, Any]]) -> CommandRegistry:
        registry = cls()
        for item in data:
            entry = CommandEntry.from_dict(item)
            registry.commands[entry.key] = entry
        return registry


@dataclass
class DiscoverySnapshot:
    """A complete snapshot of a discovery session."""
    server_root: str
    captured_at: str  # ISO 8601 timestamp
    help: DiscoveryMetadata
    commands: CommandRegistry
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "server_root": self.server_root,
            "captured_at": self.captured_at,
            "help": self.help.to_dict(),
            "commands": self.commands.to_list(),
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiscoverySnapshot:
        return cls(
            server_root=data.get("server_root", ""),
            captured_at=data.get("captured_at", ""),
            help=DiscoveryMetadata.from_dict(data.get("help", {})),
            commands=CommandRegistry.from_list(data.get("commands", [])),
        )
    
    def save(self, path: Path) -> None:
        """Save snapshot to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
    
    @classmethod
    def load(cls, path: Path) -> DiscoverySnapshot:
        """Load snapshot from JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)
    
    @classmethod
    def create(cls, server_root: Path | str) -> DiscoverySnapshot:
        """Create a new empty snapshot with current timestamp."""
        now = datetime.now(timezone.utc).astimezone()
        return cls(
            server_root=str(server_root),
            captured_at=now.isoformat(),
            help=DiscoveryMetadata(),
            commands=CommandRegistry(),
        )


def generate_snapshot_filename() -> str:
    """Generate a timestamped snapshot filename."""
    now = datetime.now()
    return f"commands-{now.strftime('%Y%m%d-%H%M%S')}.json"
