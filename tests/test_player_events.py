"""Tests for prefect.player_events module."""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from prefect.player_events import (
    PlayerRecord,
    PlayerTracker,
    PlayerEventHandler,
    build_welcome_prompt,
    WELCOME_NEW_PLAYER_PROMPT,
    WELCOME_RETURNING_PLAYER_PROMPT,
)


class TestPlayerRecord:
    """Tests for PlayerRecord dataclass."""

    def test_create_record(self):
        now = time.time()
        record = PlayerRecord(
            name="TestPlayer",
            first_seen=now,
            last_seen=now,
            visit_count=1,
        )
        assert record.name == "TestPlayer"
        assert record.first_seen == now
        assert record.visit_count == 1

    def test_is_new_first_visit(self):
        record = PlayerRecord(
            name="NewPlayer",
            first_seen=time.time(),
            last_seen=time.time(),
            visit_count=1,
        )
        assert record.is_new is True

    def test_is_new_returning_player(self):
        record = PlayerRecord(
            name="OldPlayer",
            first_seen=time.time() - 86400,
            last_seen=time.time(),
            visit_count=5,
        )
        assert record.is_new is False

    def test_to_dict(self):
        now = time.time()
        record = PlayerRecord(
            name="TestPlayer",
            first_seen=now,
            last_seen=now,
            visit_count=3,
            notes="Good player",
        )
        data = record.to_dict()
        assert data["name"] == "TestPlayer"
        assert data["visit_count"] == 3
        assert data["notes"] == "Good player"

    def test_from_dict(self):
        data = {
            "name": "TestPlayer",
            "first_seen": 1700000000.0,
            "last_seen": 1700001000.0,
            "visit_count": 5,
            "notes": "Test note",
        }
        record = PlayerRecord.from_dict(data)
        assert record.name == "TestPlayer"
        assert record.visit_count == 5
        assert record.notes == "Test note"

    def test_date_formatting(self):
        # Use a known timestamp
        record = PlayerRecord(
            name="TestPlayer",
            first_seen=1702900000.0,  # 2023-12-18 around noon UTC
            last_seen=1702900000.0,
            visit_count=1,
        )
        # Just verify it doesn't crash and returns a string
        assert isinstance(record.first_seen_date, str)
        assert isinstance(record.last_seen_date, str)


class TestPlayerTracker:
    """Tests for PlayerTracker persistence."""

    def test_record_new_player(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tracker = PlayerTracker(storage_path=Path(f.name))
            
            record = tracker.record_join("NewPlayer")
            
            assert record.name == "NewPlayer"
            assert record.visit_count == 1
            assert record.is_new is True

    def test_record_returning_player(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tracker = PlayerTracker(storage_path=Path(f.name))
            
            # First visit
            tracker.record_join("ReturningPlayer")
            
            # Second visit
            record = tracker.record_join("ReturningPlayer")
            
            assert record.name == "ReturningPlayer"
            assert record.visit_count == 2
            assert record.is_new is False

    def test_case_insensitive_lookup(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tracker = PlayerTracker(storage_path=Path(f.name))
            
            tracker.record_join("TestPlayer")
            
            assert tracker.is_known_player("testplayer")
            assert tracker.is_known_player("TESTPLAYER")
            assert tracker.is_known_player("TestPlayer")

    def test_persistence(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = Path(f.name)
            
            # Create tracker and add player
            tracker1 = PlayerTracker(storage_path=path)
            tracker1.record_join("PersistentPlayer")
            tracker1.record_join("PersistentPlayer")  # 2 visits
            
            # Create new tracker from same file
            tracker2 = PlayerTracker(storage_path=path)
            
            record = tracker2.get_player("PersistentPlayer")
            assert record is not None
            assert record.visit_count == 2

    def test_record_leave_updates_last_seen(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tracker = PlayerTracker(storage_path=Path(f.name))
            
            record1 = tracker.record_join("LeavingPlayer")
            first_last_seen = record1.last_seen
            
            time.sleep(0.01)  # Small delay
            
            record2 = tracker.record_leave("LeavingPlayer")
            
            assert record2 is not None
            assert record2.last_seen > first_last_seen

    def test_get_all_players(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tracker = PlayerTracker(storage_path=Path(f.name))
            
            tracker.record_join("Player1")
            tracker.record_join("Player2")
            tracker.record_join("Player3")
            
            players = tracker.get_all_players()
            
            assert len(players) == 3
            # Should be sorted by last_seen descending
            names = [p.name for p in players]
            assert "Player3" in names
            assert "Player2" in names
            assert "Player1" in names

    def test_add_note(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tracker = PlayerTracker(storage_path=Path(f.name))
            
            tracker.record_join("NotedPlayer")
            result = tracker.add_note("NotedPlayer", "This is a test note")
            
            assert result is True
            record = tracker.get_player("NotedPlayer")
            assert record.notes == "This is a test note"

    def test_add_note_unknown_player(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tracker = PlayerTracker(storage_path=Path(f.name))
            
            result = tracker.add_note("UnknownPlayer", "Note")
            
            assert result is False


class TestBuildWelcomePrompt:
    """Tests for welcome prompt generation."""

    def test_new_player_prompt(self):
        record = PlayerRecord(
            name="NewGuy",
            first_seen=time.time(),
            last_seen=time.time(),
            visit_count=1,
        )
        
        prompt = build_welcome_prompt(record, is_first_visit=True)
        
        assert "NewGuy" in prompt
        # New character voice uses "new variable" and "first recorded contact"
        assert "new variable" in prompt.lower() or "first recorded" in prompt.lower()

    def test_returning_player_prompt(self):
        record = PlayerRecord(
            name="OldFriend",
            first_seen=time.time() - 86400 * 7,  # A week ago
            last_seen=time.time(),
            visit_count=10,
        )
        
        prompt = build_welcome_prompt(record, is_first_visit=False)
        
        assert "OldFriend" in prompt
        assert "10" in prompt  # visit count
        # New character voice uses "returning variable"
        assert "returning" in prompt.lower() or "visit" in prompt.lower()


class TestPlayerEventHandler:
    """Tests for PlayerEventHandler."""

    def test_join_triggers_welcome(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tracker = PlayerTracker(storage_path=Path(f.name))
            
            sent_messages = []
            def mock_send(msg: str):
                sent_messages.append(msg)
            
            def mock_generate(system: str, user: str) -> str:
                return "Welcome to the server!"
            
            handler = PlayerEventHandler(
                player_tracker=tracker,
                send_message=mock_send,
                generate_message=mock_generate,
                enabled=True,
            )
            
            result = handler.on_player_join("TestPlayer")
            
            assert result == "Welcome to the server!"
            assert len(sent_messages) == 1
            assert "Welcome" in sent_messages[0]

    def test_join_disabled(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tracker = PlayerTracker(storage_path=Path(f.name))
            
            sent_messages = []
            handler = PlayerEventHandler(
                player_tracker=tracker,
                send_message=lambda m: sent_messages.append(m),
                generate_message=lambda s, u: "Welcome!",
                enabled=False,
            )
            
            result = handler.on_player_join("TestPlayer")
            
            assert result is None
            assert len(sent_messages) == 0

    def test_join_no_generator(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tracker = PlayerTracker(storage_path=Path(f.name))
            
            handler = PlayerEventHandler(
                player_tracker=tracker,
                send_message=lambda m: None,
                generate_message=None,  # No generator
                enabled=True,
            )
            
            result = handler.on_player_join("TestPlayer")
            
            assert result is None

    def test_cooldown_prevents_spam(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tracker = PlayerTracker(storage_path=Path(f.name))
            
            sent_messages = []
            handler = PlayerEventHandler(
                player_tracker=tracker,
                send_message=lambda m: sent_messages.append(m),
                generate_message=lambda s, u: "Welcome!",
                enabled=True,
            )
            handler._welcome_cooldown_seconds = 60.0
            
            # First join
            result1 = handler.on_player_join("SpamPlayer")
            assert result1 is not None
            
            # Immediate second join (within cooldown)
            result2 = handler.on_player_join("SpamPlayer")
            assert result2 is None  # Blocked by cooldown
            
            assert len(sent_messages) == 1

    def test_leave_records_departure(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tracker = PlayerTracker(storage_path=Path(f.name))
            
            handler = PlayerEventHandler(
                player_tracker=tracker,
                send_message=lambda m: None,
                generate_message=lambda s, u: "Welcome!",
                enabled=True,
            )
            
            # Player joins
            handler.on_player_join("LeavingPlayer")
            time.sleep(0.01)
            
            # Player leaves
            handler.on_player_leave("LeavingPlayer")
            
            record = tracker.get_player("LeavingPlayer")
            assert record is not None

    def test_new_vs_returning_prompts_differ(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tracker = PlayerTracker(storage_path=Path(f.name))
            
            received_prompts = []
            def mock_generate(system: str, user: str) -> str:
                received_prompts.append(user)
                return "Welcome!"
            
            handler = PlayerEventHandler(
                player_tracker=tracker,
                send_message=lambda m: None,
                generate_message=mock_generate,
                enabled=True,
            )
            handler._welcome_cooldown_seconds = 0  # Disable cooldown for test
            
            # First visit
            handler.on_player_join("TestPlayer")
            first_prompt = received_prompts[-1]
            
            # Second visit (need to bypass cooldown)
            handler._last_welcome.clear()
            handler.on_player_join("TestPlayer")
            second_prompt = received_prompts[-1]
            
            # Prompts should be different
            assert first_prompt != second_prompt
            assert "NEW" in first_prompt or "first" in first_prompt.lower()
            assert "returning" in second_prompt.lower() or "visited" in second_prompt.lower()

    def test_enable_disable(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tracker = PlayerTracker(storage_path=Path(f.name))
            
            handler = PlayerEventHandler(
                player_tracker=tracker,
                send_message=lambda m: None,
                generate_message=lambda s, u: "Welcome!",
                enabled=True,
            )
            
            assert handler.enabled is True
            
            handler.enabled = False
            assert handler.enabled is False
            
            # Should not generate welcome when disabled
            result = handler.on_player_join("NewPlayer")
            assert result is None
