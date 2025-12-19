"""Persistent conversation history for player interactions.

Stores and retrieves conversation history per player, persisted to disk.
Supports session-based and historical context for LLM prompts.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ConversationMessage:
    """A single message in a conversation."""
    
    timestamp: float  # Unix timestamp
    role: str  # "player" or "prefect"
    content: str
    
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConversationMessage":
        return cls(
            timestamp=float(data.get("timestamp", time.time())),
            role=data.get("role", "player"),
            content=data.get("content", ""),
        )
    
    @property
    def time_str(self) -> str:
        """Human-readable timestamp."""
        return datetime.fromtimestamp(self.timestamp).strftime("%Y-%m-%d %H:%M:%S")


@dataclass 
class PlayerConversation:
    """Conversation history for a single player."""
    
    player_name: str
    messages: list[ConversationMessage] = field(default_factory=list)
    first_interaction: float = field(default_factory=time.time)
    last_interaction: float = field(default_factory=time.time)
    total_messages: int = 0
    
    def add_message(self, role: str, content: str) -> None:
        """Add a message to the conversation."""
        now = time.time()
        self.messages.append(ConversationMessage(
            timestamp=now,
            role=role,
            content=content,
        ))
        self.last_interaction = now
        self.total_messages += 1
    
    def get_recent_messages(self, count: int = 10, max_age_seconds: float | None = None) -> list[ConversationMessage]:
        """Get the most recent messages, optionally filtered by age."""
        if max_age_seconds is not None:
            cutoff = time.time() - max_age_seconds
            filtered = [m for m in self.messages if m.timestamp >= cutoff]
        else:
            filtered = self.messages
        
        return filtered[-count:] if count > 0 else filtered
    
    def format_context(self, count: int = 10, max_age_seconds: float | None = None) -> str:
        """Format recent messages as context for LLM prompt."""
        messages = self.get_recent_messages(count, max_age_seconds)
        if not messages:
            return ""
        
        lines = []
        for msg in messages:
            role_name = "Player" if msg.role == "player" else "Prefect"
            lines.append(f"{role_name}: {msg.content}")
        
        return "\n".join(lines)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "player_name": self.player_name,
            "messages": [m.to_dict() for m in self.messages],
            "first_interaction": self.first_interaction,
            "last_interaction": self.last_interaction,
            "total_messages": self.total_messages,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlayerConversation":
        conv = cls(
            player_name=data.get("player_name", "Unknown"),
            first_interaction=float(data.get("first_interaction", time.time())),
            last_interaction=float(data.get("last_interaction", time.time())),
            total_messages=int(data.get("total_messages", 0)),
        )
        conv.messages = [
            ConversationMessage.from_dict(m) 
            for m in data.get("messages", [])
        ]
        return conv


class ConversationHistoryStore:
    """Persistent storage for player conversation histories.
    
    Stores conversations per player in a JSON file.
    Automatically prunes old messages to prevent unbounded growth.
    """
    
    def __init__(
        self, 
        storage_path: Path | str | None = None,
        max_messages_per_player: int = 100,
        max_message_age_days: int = 30,
    ):
        """Initialize the conversation store.
        
        Args:
            storage_path: Path to the JSON file for persistence.
            max_messages_per_player: Maximum messages to keep per player.
            max_message_age_days: Maximum age of messages to keep.
        """
        if storage_path is None:
            storage_path = Path.home() / ".config" / "prefect" / "conversations.json"
        self._storage_path = Path(storage_path)
        self._max_messages = max_messages_per_player
        self._max_age_seconds = max_message_age_days * 86400
        
        self._conversations: dict[str, PlayerConversation] = {}
        self._load()
    
    def _load(self) -> None:
        """Load conversations from disk."""
        if not self._storage_path.exists():
            logger.debug("No conversation history found at %s, starting fresh", self._storage_path)
            return
        
        try:
            with open(self._storage_path) as f:
                data = json.load(f)
            
            for name, conv_data in data.get("conversations", {}).items():
                self._conversations[name.lower()] = PlayerConversation.from_dict(conv_data)
            
            logger.info(
                "Loaded conversation history for %d players from %s", 
                len(self._conversations), 
                self._storage_path
            )
        except Exception as e:
            logger.error("Failed to load conversation history: %s", e)
    
    def _save(self) -> None:
        """Save conversations to disk."""
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        
        data = {
            "version": 1,
            "updated_at": datetime.now().isoformat(),
            "conversations": {
                name: conv.to_dict()
                for name, conv in self._conversations.items()
            },
        }
        
        try:
            with open(self._storage_path, "w") as f:
                json.dump(data, f, indent=2)
            logger.debug(
                "Saved conversation history for %d players to %s", 
                len(self._conversations), 
                self._storage_path
            )
        except Exception as e:
            logger.error("Failed to save conversation history: %s", e)
    
    def _prune_conversation(self, conv: PlayerConversation) -> None:
        """Remove old messages and limit total count."""
        now = time.time()
        cutoff = now - self._max_age_seconds
        
        # Filter out messages older than max age
        conv.messages = [m for m in conv.messages if m.timestamp >= cutoff]
        
        # Keep only the most recent max_messages
        if len(conv.messages) > self._max_messages:
            conv.messages = conv.messages[-self._max_messages:]
    
    def get_conversation(self, player_name: str) -> PlayerConversation:
        """Get or create a conversation for a player."""
        key = player_name.lower()
        
        if key not in self._conversations:
            self._conversations[key] = PlayerConversation(player_name=player_name)
        
        return self._conversations[key]
    
    def add_player_message(self, player_name: str, content: str) -> PlayerConversation:
        """Record a message from a player."""
        conv = self.get_conversation(player_name)
        conv.add_message("player", content)
        self._prune_conversation(conv)
        self._save()
        return conv
    
    def add_prefect_response(self, player_name: str, content: str) -> PlayerConversation:
        """Record a response from Prefect."""
        conv = self.get_conversation(player_name)
        conv.add_message("prefect", content)
        self._prune_conversation(conv)
        self._save()
        return conv
    
    def get_context_for_player(
        self, 
        player_name: str, 
        recent_count: int = 10,
        session_ttl_seconds: float | None = None,
    ) -> str:
        """Get formatted conversation context for LLM prompt.
        
        Args:
            player_name: The player to get context for.
            recent_count: Number of recent messages to include.
            session_ttl_seconds: If set, only include messages within this TTL
                                for "current session" context.
        
        Returns:
            Formatted conversation history string.
        """
        conv = self.get_conversation(player_name)
        return conv.format_context(count=recent_count, max_age_seconds=session_ttl_seconds)
    
    def get_player_summary(self, player_name: str) -> dict[str, Any]:
        """Get a summary of interactions with a player."""
        conv = self.get_conversation(player_name)
        
        return {
            "player_name": conv.player_name,
            "total_messages": conv.total_messages,
            "first_interaction": datetime.fromtimestamp(conv.first_interaction).isoformat(),
            "last_interaction": datetime.fromtimestamp(conv.last_interaction).isoformat(),
            "stored_messages": len(conv.messages),
        }
    
    def get_all_players(self) -> list[str]:
        """Get list of all players with conversation history."""
        return [conv.player_name for conv in self._conversations.values()]
    
    def clear_player_history(self, player_name: str) -> bool:
        """Clear conversation history for a player."""
        key = player_name.lower()
        if key in self._conversations:
            del self._conversations[key]
            self._save()
            return True
        return False
