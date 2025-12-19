"""Allowlist bootstrap from discovered commands.

Generates a default-deny allowlist using strict safety policies.
If Prefect is uncertain, it denies.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prefect.discovery.registry import CommandRegistry, DiscoverySnapshot

logger = logging.getLogger(__name__)


# === Safety Classification Patterns ===

# Category A: Always allow (read-only / informational)
# Commands that match these patterns are considered safe
_SAFE_NAME_PATTERNS = [
    re.compile(r"^help$", re.IGNORECASE),
    re.compile(r"^version$", re.IGNORECASE),
    re.compile(r"^\?$"),
    re.compile(r".*list$", re.IGNORECASE),  # playerlist, whitelist (read), etc.
    re.compile(r".*status$", re.IGNORECASE),
    re.compile(r".*info$", re.IGNORECASE),
    re.compile(r"^players?$", re.IGNORECASE),
    re.compile(r"^online$", re.IGNORECASE),
    re.compile(r"^uptime$", re.IGNORECASE),
    re.compile(r"^time$", re.IGNORECASE),  # Get world time
    re.compile(r"^seed$", re.IGNORECASE),  # Read-only seed display
    re.compile(r"^tps$", re.IGNORECASE),  # Server performance
    re.compile(r"^ping$", re.IGNORECASE),
    re.compile(r"^motd$", re.IGNORECASE),  # Read MOTD
]

# Description patterns that suggest read-only behavior
_SAFE_DESC_PATTERNS = [
    re.compile(r"\b(show|list|display|view|print|get)\b", re.IGNORECASE),
    re.compile(r"\b(information|status|info)\b", re.IGNORECASE),
]

# Category B: Allow with restrictions (messaging)
_MESSAGING_PATTERNS = [
    re.compile(r"^say$", re.IGNORECASE),
    re.compile(r"^announce$", re.IGNORECASE),
    re.compile(r"^broadcast$", re.IGNORECASE),
    re.compile(r"^msg$", re.IGNORECASE),
    re.compile(r"^tell$", re.IGNORECASE),
    re.compile(r"^whisper$", re.IGNORECASE),
]

# Category C: Deny by default (dangerous commands)
# These patterns explicitly mark commands as dangerous even if they seem safe
_DANGEROUS_PATTERNS = [
    re.compile(r"\b(kick|ban|unban|pardon)\b", re.IGNORECASE),
    re.compile(r"\b(op|deop|admin)\b", re.IGNORECASE),
    re.compile(r"\b(save|load|backup|restore)\b", re.IGNORECASE),
    re.compile(r"\b(stop|shutdown|restart|reload)\b", re.IGNORECASE),
    re.compile(r"\b(give|spawn|summon|create)\b", re.IGNORECASE),
    re.compile(r"\b(tp|teleport|warp)\b", re.IGNORECASE),
    re.compile(r"\b(kill|damage|heal)\b", re.IGNORECASE),
    re.compile(r"\b(set|config|configure|edit)\b", re.IGNORECASE),
    re.compile(r"\b(add|remove|delete|clear)\b", re.IGNORECASE),
    re.compile(r"\b(cheat|god|fly|noclip)\b", re.IGNORECASE),
    re.compile(r"\b(world|gen|generate)\b", re.IGNORECASE),
    re.compile(r"\b(whitelist|blacklist)\b.*\b(add|remove)\b", re.IGNORECASE),
    re.compile(r"\bexec(ute)?\b", re.IGNORECASE),
]


@dataclass
class AllowlistEntry:
    """A single allowlist entry with constraints."""
    command: str  # Normalized command key
    max_len: int = 200  # Maximum total command length
    arg_policy: str = "any"  # "any", "message", "none"
    reason: str = ""  # Why allowed (for documentation)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "max_len": self.max_len,
            "arg_policy": self.arg_policy,
            "reason": self.reason,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AllowlistEntry:
        return cls(
            command=data["command"],
            max_len=data.get("max_len", 200),
            arg_policy=data.get("arg_policy", "any"),
            reason=data.get("reason", ""),
        )


@dataclass
class DeniedEntry:
    """A denied command with reason."""
    command: str
    reason: str
    
    def to_dict(self) -> dict[str, Any]:
        return {"command": self.command, "reason": self.reason}


@dataclass
class GeneratedAllowlist:
    """A complete generated allowlist."""
    generated_at: str
    mode: str = "default_deny"
    source_snapshot: str = ""
    allowed: list[AllowlistEntry] = field(default_factory=list)
    denied: list[DeniedEntry] = field(default_factory=list)
    sanitization: dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if not self.sanitization:
            self.sanitization = {
                "reject_chars": [";", "|", "&", ">", "<", "$", "(", ")", "`", "\\n", "\\r", "\\", "{", "}", "[", "]"],
                "reject_patterns": ["&&", "||", "../", "~/", "`", "$("],
            }
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "mode": self.mode,
            "source_snapshot": self.source_snapshot,
            "allowed": [e.to_dict() for e in self.allowed],
            "denied": [e.to_dict() for e in self.denied],
            "sanitization": self.sanitization,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GeneratedAllowlist:
        return cls(
            generated_at=data.get("generated_at", ""),
            mode=data.get("mode", "default_deny"),
            source_snapshot=data.get("source_snapshot", ""),
            allowed=[AllowlistEntry.from_dict(e) for e in data.get("allowed", [])],
            denied=[DeniedEntry(d["command"], d["reason"]) for d in data.get("denied", [])],
            sanitization=data.get("sanitization", {}),
        )
    
    def save(self, path: Path) -> None:
        """Save allowlist to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
    
    @classmethod
    def load(cls, path: Path) -> GeneratedAllowlist:
        """Load allowlist from JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)
    
    def get_allowed_commands(self) -> set[str]:
        """Get set of allowed command keys."""
        return {e.command for e in self.allowed}
    
    def is_allowed(self, command_key: str) -> AllowlistEntry | None:
        """Check if command is allowed, return entry if so."""
        key = command_key.lstrip("/!").lower()
        for entry in self.allowed:
            if entry.command == key:
                return entry
        return None
    
    def to_markdown(self) -> str:
        """Generate markdown report of allowlist."""
        lines = [
            "# Prefect Allowlist Report",
            "",
            f"**Generated:** {self.generated_at}",
            f"**Mode:** {self.mode}",
            f"**Source Snapshot:** {self.source_snapshot or 'N/A'}",
            "",
            "## Allowed Commands",
            "",
            "| Command | Max Length | Arg Policy | Reason |",
            "|---------|------------|------------|--------|",
        ]
        
        for entry in sorted(self.allowed, key=lambda e: e.command):
            lines.append(f"| `{entry.command}` | {entry.max_len} | {entry.arg_policy} | {entry.reason} |")
        
        lines.extend([
            "",
            "## Denied Commands",
            "",
            "| Command | Reason |",
            "|---------|--------|",
        ])
        
        for entry in sorted(self.denied, key=lambda e: e.command):
            lines.append(f"| `{entry.command}` | {entry.reason} |")
        
        lines.extend([
            "",
            "## Sanitization Rules",
            "",
            f"**Rejected Characters:** `{', '.join(self.sanitization.get('reject_chars', []))}`",
            "",
            f"**Rejected Patterns:** `{', '.join(self.sanitization.get('reject_patterns', []))}`",
        ])
        
        return "\n".join(lines)


class AllowlistBootstrapper:
    """Generates allowlists from discovered commands using safety policies."""
    
    def __init__(
        self,
        *,
        safe_command_max_len: int = 200,
        messaging_max_len: int = 400,
    ):
        self._safe_max_len = safe_command_max_len
        self._messaging_max_len = messaging_max_len
    
    def _classify_command(self, key: str, name: str, description: str) -> tuple[str, str]:
        """Classify a command into category and reason.
        
        Returns:
            Tuple of (category, reason) where category is one of:
            - "safe": Read-only/informational
            - "messaging": Say/announce with restrictions
            - "dangerous": Deny by default
        """
        # First check if explicitly dangerous
        for pattern in _DANGEROUS_PATTERNS:
            if pattern.search(name) or pattern.search(description):
                return "dangerous", "matches_dangerous_pattern"
        
        # Check if messaging command
        for pattern in _MESSAGING_PATTERNS:
            if pattern.match(name):
                return "messaging", "messaging_command"
        
        # Check if safe by name
        for pattern in _SAFE_NAME_PATTERNS:
            if pattern.match(name):
                return "safe", "safe_name_pattern"
        
        # Check if safe by description
        for pattern in _SAFE_DESC_PATTERNS:
            if pattern.search(description):
                return "safe", "safe_description"
        
        # Default to dangerous (default-deny)
        return "dangerous", "unknown_unclassified"
    
    def bootstrap(
        self,
        snapshot: DiscoverySnapshot,
        snapshot_path: str = "",
    ) -> GeneratedAllowlist:
        """Generate allowlist from discovery snapshot.
        
        Args:
            snapshot: Discovery snapshot with commands.
            snapshot_path: Path to snapshot file (for reference).
            
        Returns:
            GeneratedAllowlist ready to save or use.
        """
        now = datetime.now(timezone.utc).astimezone()
        allowlist = GeneratedAllowlist(
            generated_at=now.isoformat(),
            source_snapshot=snapshot_path,
        )
        
        for entry in snapshot.commands:
            category, reason = self._classify_command(
                entry.key,
                entry.name,
                entry.description,
            )
            
            if category == "safe":
                allowlist.allowed.append(AllowlistEntry(
                    command=entry.key,
                    max_len=self._safe_max_len,
                    arg_policy="any",
                    reason=reason,
                ))
                logger.info("Allowlist: ALLOW '%s' (%s)", entry.key, reason)
                
            elif category == "messaging":
                allowlist.allowed.append(AllowlistEntry(
                    command=entry.key,
                    max_len=self._messaging_max_len,
                    arg_policy="message",
                    reason=reason,
                ))
                logger.info("Allowlist: ALLOW '%s' with message restrictions (%s)", entry.key, reason)
                
            else:  # dangerous
                allowlist.denied.append(DeniedEntry(
                    command=entry.key,
                    reason=reason,
                ))
                logger.info("Allowlist: DENY '%s' (%s)", entry.key, reason)
        
        logger.info(
            "Allowlist bootstrap complete: %d allowed, %d denied",
            len(allowlist.allowed),
            len(allowlist.denied),
        )
        
        return allowlist
    
    def bootstrap_and_save(
        self,
        snapshot: DiscoverySnapshot,
        output_dir: Path,
        snapshot_path: str = "",
    ) -> tuple[GeneratedAllowlist, Path, Path | None]:
        """Generate allowlist and save to files.
        
        Args:
            snapshot: Discovery snapshot.
            output_dir: Directory for output files.
            snapshot_path: Path to snapshot file (for reference).
            
        Returns:
            Tuple of (allowlist, json_path, markdown_path).
        """
        allowlist = self.bootstrap(snapshot, snapshot_path)
        
        output_dir.mkdir(parents=True, exist_ok=True)
        
        json_path = output_dir / "allowlist-active.json"
        allowlist.save(json_path)
        logger.info("Allowlist saved to %s", json_path)
        
        md_path = output_dir / "allowlist-active.md"
        try:
            md_path.write_text(allowlist.to_markdown(), encoding="utf-8")
            logger.info("Allowlist report saved to %s", md_path)
        except Exception as e:
            logger.warning("Failed to write markdown report: %s", e)
            md_path = None
        
        return allowlist, json_path, md_path
