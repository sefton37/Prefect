"""Player tracking and event handling for Prefect.

Tracks player visits, generates LLM-powered welcome messages,
and handles player join/leave events.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class PlayerRecord:
    """Record of a player's history on the server."""
    
    name: str
    first_seen: float  # Unix timestamp
    last_seen: float   # Unix timestamp
    visit_count: int = 1
    notes: str = ""  # Optional notes about the player
    
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlayerRecord":
        return cls(
            name=data.get("name", "Unknown"),
            first_seen=float(data.get("first_seen", time.time())),
            last_seen=float(data.get("last_seen", time.time())),
            visit_count=int(data.get("visit_count", 1)),
            notes=data.get("notes", ""),
        )
    
    @property
    def is_new(self) -> bool:
        """Player is new if they've only visited once."""
        return self.visit_count == 1
    
    @property
    def first_seen_date(self) -> str:
        """Human-readable first seen date."""
        return datetime.fromtimestamp(self.first_seen).strftime("%Y-%m-%d %H:%M")
    
    @property
    def last_seen_date(self) -> str:
        """Human-readable last seen date."""
        return datetime.fromtimestamp(self.last_seen).strftime("%Y-%m-%d %H:%M")


class PlayerTracker:
    """Tracks player visits and persists history to disk."""
    
    def __init__(self, storage_path: Path | str | None = None):
        if storage_path is None:
            storage_path = Path.home() / ".config" / "prefect" / "players.json"
        self._storage_path = Path(storage_path)
        self._players: dict[str, PlayerRecord] = {}
        self._load()
    
    def _load(self) -> None:
        """Load player records from disk."""
        if not self._storage_path.exists():
            logger.debug("No player storage found at %s, starting fresh", self._storage_path)
            return
        
        try:
            with open(self._storage_path) as f:
                data = json.load(f)
            
            for name, record_data in data.get("players", {}).items():
                self._players[name.lower()] = PlayerRecord.from_dict(record_data)
            
            logger.info("Loaded %d player records from %s", len(self._players), self._storage_path)
        except Exception as e:
            logger.error("Failed to load player records: %s", e)
    
    def _save(self) -> None:
        """Save player records to disk."""
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        
        data = {
            "version": 1,
            "updated_at": datetime.now().isoformat(),
            "players": {
                name: record.to_dict()
                for name, record in self._players.items()
            },
        }
        
        try:
            with open(self._storage_path, "w") as f:
                json.dump(data, f, indent=2)
            logger.debug("Saved %d player records to %s", len(self._players), self._storage_path)
        except Exception as e:
            logger.error("Failed to save player records: %s", e)
    
    def record_join(self, player_name: str) -> PlayerRecord:
        """Record a player joining. Returns the player record.
        
        If this is a new player, creates a record.
        If returning player, increments visit count and updates last_seen.
        """
        key = player_name.lower()
        now = time.time()
        
        if key in self._players:
            # Returning player
            record = self._players[key]
            record.visit_count += 1
            record.last_seen = now
            logger.info("Returning player: %s (visit #%d)", player_name, record.visit_count)
        else:
            # New player
            record = PlayerRecord(
                name=player_name,
                first_seen=now,
                last_seen=now,
                visit_count=1,
            )
            self._players[key] = record
            logger.info("New player: %s", player_name)
        
        self._save()
        return record
    
    def record_leave(self, player_name: str) -> PlayerRecord | None:
        """Record a player leaving. Updates last_seen time."""
        key = player_name.lower()
        
        if key in self._players:
            record = self._players[key]
            record.last_seen = time.time()
            self._save()
            return record
        
        return None
    
    def get_player(self, player_name: str) -> PlayerRecord | None:
        """Get a player's record, if they exist."""
        return self._players.get(player_name.lower())
    
    def is_known_player(self, player_name: str) -> bool:
        """Check if we've seen this player before."""
        return player_name.lower() in self._players
    
    def get_all_players(self) -> list[PlayerRecord]:
        """Get all tracked players, sorted by last seen."""
        return sorted(
            self._players.values(),
            key=lambda p: p.last_seen,
            reverse=True,
        )
    
    def get_player_count(self) -> int:
        """Get total number of tracked players."""
        return len(self._players)
    
    def add_note(self, player_name: str, note: str) -> bool:
        """Add a note to a player's record."""
        key = player_name.lower()
        if key in self._players:
            self._players[key].notes = note
            self._save()
            return True
        return False


# Default prompts for welcome message generation
WELCOME_NEW_PLAYER_PROMPT = """A new variable has entered the system: "{player_name}" - first recorded contact.

Generate a brief welcome message (1-2 sentences) in Prefect's voice:
- Acknowledge their arrival with dry, understated observation
- Note that you are Prefect, an administrative presence who monitors this settlement
- Do not be warm or effusive. Be measured, slightly weary, mildly curious.
- No asterisks, roleplay actions, or formatting. Just plain text.

Example tones:
- "Another settler. I am Prefect. I observe, I record, I occasionally advise. Whether you listen is your concern."
- "New arrival logged. I am Prefect, the administrative intelligence here. Try not to die immediately."
"""

WELCOME_RETURNING_PLAYER_PROMPT = """Returning variable detected: "{player_name}"
Visit count: {visit_count}
First recorded: {first_seen}

Generate a brief welcome-back message (1 sentence) in Prefect's voice:
- Acknowledge their return with dry observation
- You may reference their persistence, survival, or return as a statistical note
- Do not be warm or effusive. Be measured, perhaps mildly approving.
- No asterisks, roleplay actions, or formatting. Just plain text.

Example tones:
- "You return. The system notes your persistence."
- "Back again. Your survival rate continues to exceed projections."
- "Logged. The settlement's continuity improves with familiar variables."
"""


def build_welcome_prompt(record: PlayerRecord, is_first_visit: bool) -> str:
    """Build the appropriate welcome prompt for a player."""
    if is_first_visit:
        return WELCOME_NEW_PLAYER_PROMPT.format(player_name=record.name)
    else:
        return WELCOME_RETURNING_PLAYER_PROMPT.format(
            player_name=record.name,
            visit_count=record.visit_count,
            first_seen=record.first_seen_date,
        )


class PlayerEventHandler:
    """Handles player join/leave events with LLM-powered responses."""
    
    def __init__(
        self,
        player_tracker: PlayerTracker,
        send_message: Callable[[str], None],
        generate_message: Callable[[str, str], str] | None = None,
        system_prompt: str = "",
        enabled: bool = True,
    ):
        """Initialize the event handler.
        
        Args:
            player_tracker: PlayerTracker instance for persistence
            send_message: Function to send a message to the server (e.g., via 'say' command)
            generate_message: Async function (system_prompt, user_prompt) -> str for LLM generation.
                             If None, welcome messages are disabled.
            system_prompt: Base system prompt for the persona
            enabled: Whether welcome messages are enabled
        """
        self._tracker = player_tracker
        self._send_message = send_message
        self._generate_message = generate_message
        self._system_prompt = system_prompt
        self._enabled = enabled
        
        # Rate limiting: don't spam welcome messages
        self._last_welcome: dict[str, float] = {}
        self._welcome_cooldown_seconds = 60.0  # Don't re-welcome within 60s
    
    @property
    def enabled(self) -> bool:
        return self._enabled
    
    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value
    
    def on_player_join(self, player_name: str) -> str | None:
        """Handle a player joining. Returns the welcome message if sent, else None."""
        # Record the join and get player info
        record = self._tracker.record_join(player_name)
        is_first_visit = record.visit_count == 1
        
        if not self._enabled:
            logger.debug("Welcome messages disabled, skipping for %s", player_name)
            return None
        
        if self._generate_message is None:
            logger.debug("No message generator configured, skipping welcome for %s", player_name)
            return None
        
        # Rate limit check
        now = time.time()
        last = self._last_welcome.get(player_name.lower(), 0)
        if now - last < self._welcome_cooldown_seconds:
            logger.debug("Cooldown active for %s, skipping welcome", player_name)
            return None
        
        try:
            # Build prompt and generate message
            user_prompt = build_welcome_prompt(record, is_first_visit)
            
            logger.info(
                "Generating welcome message for %s (first_visit=%s, visits=%d)",
                player_name, is_first_visit, record.visit_count
            )
            
            message = self._generate_message(self._system_prompt, user_prompt)
            
            if message:
                # Clean up the message (remove quotes, extra whitespace)
                message = message.strip().strip('"\'')
                
                # Send to server
                self._send_message(message)
                self._last_welcome[player_name.lower()] = now
                
                logger.info("Sent welcome message to %s: %s", player_name, message[:100])
                return message
            
        except Exception as e:
            logger.error("Failed to generate welcome message for %s: %s", player_name, e)
        
        return None
    
    def on_player_leave(self, player_name: str) -> None:
        """Handle a player leaving."""
        self._tracker.record_leave(player_name)
        logger.debug("Recorded departure for %s", player_name)
