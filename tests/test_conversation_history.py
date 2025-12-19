"""Tests for persistent conversation history."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from prefect.conversation_history import (
    ConversationMessage,
    PlayerConversation,
    ConversationHistoryStore,
)


class TestConversationMessage:
    """Tests for ConversationMessage dataclass."""
    
    def test_create_message(self):
        msg = ConversationMessage(
            timestamp=1000.0,
            role="player",
            content="Hello Prefect",
        )
        assert msg.role == "player"
        assert msg.content == "Hello Prefect"
        assert msg.timestamp == 1000.0
    
    def test_to_dict(self):
        msg = ConversationMessage(
            timestamp=1000.0,
            role="prefect",
            content="Greetings.",
        )
        d = msg.to_dict()
        assert d["timestamp"] == 1000.0
        assert d["role"] == "prefect"
        assert d["content"] == "Greetings."
    
    def test_from_dict(self):
        data = {
            "timestamp": 2000.0,
            "role": "player",
            "content": "How are you?",
        }
        msg = ConversationMessage.from_dict(data)
        assert msg.timestamp == 2000.0
        assert msg.role == "player"
        assert msg.content == "How are you?"


class TestPlayerConversation:
    """Tests for PlayerConversation dataclass."""
    
    def test_create_conversation(self):
        conv = PlayerConversation(player_name="TestPlayer")
        assert conv.player_name == "TestPlayer"
        assert len(conv.messages) == 0
        assert conv.total_messages == 0
    
    def test_add_message(self):
        conv = PlayerConversation(player_name="TestPlayer")
        conv.add_message("player", "Hello")
        
        assert len(conv.messages) == 1
        assert conv.total_messages == 1
        assert conv.messages[0].role == "player"
        assert conv.messages[0].content == "Hello"
    
    def test_get_recent_messages(self):
        conv = PlayerConversation(player_name="TestPlayer")
        conv.add_message("player", "First")
        conv.add_message("prefect", "Response")
        conv.add_message("player", "Second")
        
        recent = conv.get_recent_messages(count=2)
        assert len(recent) == 2
        assert recent[0].content == "Response"
        assert recent[1].content == "Second"
    
    def test_get_recent_messages_with_age_filter(self):
        conv = PlayerConversation(player_name="TestPlayer")
        
        # Add old message (manually set timestamp)
        old_msg = ConversationMessage(
            timestamp=time.time() - 1000,  # 1000 seconds ago
            role="player",
            content="Old message",
        )
        conv.messages.append(old_msg)
        conv.total_messages += 1
        
        # Add recent message
        conv.add_message("player", "New message")
        
        # Filter to only messages within 500 seconds
        recent = conv.get_recent_messages(count=10, max_age_seconds=500)
        assert len(recent) == 1
        assert recent[0].content == "New message"
    
    def test_format_context(self):
        conv = PlayerConversation(player_name="TestPlayer")
        conv.add_message("player", "Hello")
        conv.add_message("prefect", "Greetings.")
        conv.add_message("player", "How are you?")
        
        context = conv.format_context(count=10)
        assert "Player: Hello" in context
        assert "Prefect: Greetings." in context
        assert "Player: How are you?" in context
    
    def test_to_dict_from_dict_roundtrip(self):
        conv = PlayerConversation(player_name="TestPlayer")
        conv.add_message("player", "Hello")
        conv.add_message("prefect", "Response")
        
        data = conv.to_dict()
        restored = PlayerConversation.from_dict(data)
        
        assert restored.player_name == conv.player_name
        assert restored.total_messages == conv.total_messages
        assert len(restored.messages) == len(conv.messages)
        assert restored.messages[0].content == conv.messages[0].content


class TestConversationHistoryStore:
    """Tests for ConversationHistoryStore."""
    
    @pytest.fixture
    def store(self, tmp_path):
        """Create a store with a temp storage path."""
        return ConversationHistoryStore(
            storage_path=tmp_path / "conversations.json",
            max_messages_per_player=10,
            max_message_age_days=30,
        )
    
    def test_add_player_message(self, store):
        store.add_player_message("TestPlayer", "Hello Prefect")
        
        conv = store.get_conversation("TestPlayer")
        assert conv.total_messages == 1
        assert conv.messages[0].role == "player"
        assert conv.messages[0].content == "Hello Prefect"
    
    def test_add_prefect_response(self, store):
        store.add_player_message("TestPlayer", "Hello")
        store.add_prefect_response("TestPlayer", "Greetings.")
        
        conv = store.get_conversation("TestPlayer")
        assert conv.total_messages == 2
        assert conv.messages[1].role == "prefect"
        assert conv.messages[1].content == "Greetings."
    
    def test_case_insensitive_player_names(self, store):
        store.add_player_message("TestPlayer", "Hello")
        store.add_player_message("testplayer", "Again")
        
        conv = store.get_conversation("TESTPLAYER")
        assert conv.total_messages == 2
    
    def test_persistence(self, tmp_path):
        storage_path = tmp_path / "conversations.json"
        
        # Create store and add data
        store1 = ConversationHistoryStore(storage_path=storage_path)
        store1.add_player_message("TestPlayer", "Hello")
        store1.add_prefect_response("TestPlayer", "Response")
        
        # Create new store instance (simulating restart)
        store2 = ConversationHistoryStore(storage_path=storage_path)
        
        conv = store2.get_conversation("TestPlayer")
        assert conv.total_messages == 2
        assert conv.messages[0].content == "Hello"
        assert conv.messages[1].content == "Response"
    
    def test_get_context_for_player(self, store):
        store.add_player_message("TestPlayer", "Hello")
        store.add_prefect_response("TestPlayer", "Greetings.")
        store.add_player_message("TestPlayer", "Who are you?")
        
        context = store.get_context_for_player("TestPlayer", recent_count=10)
        assert "Player: Hello" in context
        assert "Prefect: Greetings." in context
        assert "Player: Who are you?" in context
    
    def test_get_player_summary(self, store):
        store.add_player_message("TestPlayer", "Hello")
        store.add_prefect_response("TestPlayer", "Greetings.")
        
        summary = store.get_player_summary("TestPlayer")
        assert summary["player_name"] == "TestPlayer"
        assert summary["total_messages"] == 2
        assert summary["stored_messages"] == 2
    
    def test_prune_old_messages(self, tmp_path):
        store = ConversationHistoryStore(
            storage_path=tmp_path / "conversations.json",
            max_messages_per_player=3,
            max_message_age_days=30,
        )
        
        # Add more than max messages
        for i in range(5):
            store.add_player_message("TestPlayer", f"Message {i}")
        
        conv = store.get_conversation("TestPlayer")
        # Should only have the last 3 messages
        assert len(conv.messages) == 3
        assert conv.messages[0].content == "Message 2"
        assert conv.messages[2].content == "Message 4"
    
    def test_get_all_players(self, store):
        store.add_player_message("Player1", "Hello")
        store.add_player_message("Player2", "Hi")
        
        players = store.get_all_players()
        assert len(players) == 2
        # Names should preserve original casing
        assert "Player1" in players or "player1" in [p.lower() for p in players]
    
    def test_clear_player_history(self, store):
        store.add_player_message("TestPlayer", "Hello")
        store.add_prefect_response("TestPlayer", "Response")
        
        result = store.clear_player_history("TestPlayer")
        assert result is True
        
        # New conversation should be empty
        conv = store.get_conversation("TestPlayer")
        assert conv.total_messages == 0
    
    def test_clear_nonexistent_player(self, store):
        result = store.clear_player_history("NonexistentPlayer")
        assert result is False
    
    def test_separate_player_histories(self, store):
        store.add_player_message("Player1", "Hello from Player1")
        store.add_player_message("Player2", "Hello from Player2")
        
        conv1 = store.get_conversation("Player1")
        conv2 = store.get_conversation("Player2")
        
        assert conv1.messages[0].content == "Hello from Player1"
        assert conv2.messages[0].content == "Hello from Player2"
