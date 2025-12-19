"""Tests for NecesseProcessManager event handling and deduplication."""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from prefect.server_control.process_manager import NecesseProcessManager
from prefect.watchers.log_tail import RollingLogBuffer


@pytest.fixture
def mock_log_buffer():
    """Create a mock log buffer."""
    return RollingLogBuffer(max_lines=100)


@pytest.fixture
def manager(tmp_path, mock_log_buffer):
    """Create a process manager with mocked callbacks."""
    # Create a fake server root with a script
    server_root = tmp_path / "server"
    server_root.mkdir()
    (server_root / "StartServer-nogui.sh").write_text("#!/bin/bash\necho 'server'")
    
    activity_callback = MagicMock()
    chat_callback = MagicMock()
    chat_mention_callback = MagicMock()
    
    mgr = NecesseProcessManager(
        server_root=server_root,
        log_buffer=mock_log_buffer,
        on_activity=activity_callback,
        on_chat_line=chat_callback,
        on_chat_mention=chat_mention_callback,
        chat_keyword="prefect",
        debug_verbose=True,
    )
    mgr._activity_callback = activity_callback
    mgr._chat_mention_callback = chat_mention_callback
    return mgr


class TestEventDeduplication:
    """Tests for join/leave event deduplication."""
    
    def test_duplicate_join_events_filtered(self, manager):
        """Duplicate join events within 5 seconds should be filtered."""
        # First join should trigger callback
        manager._ingest_line("TestPlayer joined the server")
        assert manager._activity_callback.call_count == 1
        manager._activity_callback.assert_called_with("join", "TestPlayer")
        
        # Second join within 5 seconds should be filtered
        manager._ingest_line("TestPlayer joined the server")
        assert manager._activity_callback.call_count == 1  # Still 1
        
    def test_join_after_dedup_window_triggers(self, manager):
        """Join event after dedup window should trigger callback."""
        manager._ingest_line("TestPlayer joined the server")
        assert manager._activity_callback.call_count == 1
        
        # Manually expire the dedup window
        event_key = ("join", "testplayer")
        manager._recent_activity_events[event_key] = time.time() - 10  # 10 seconds ago
        
        # Now another join should trigger
        manager._ingest_line("TestPlayer joined the server")
        assert manager._activity_callback.call_count == 2
        
    def test_different_players_not_deduplicated(self, manager):
        """Different players should not be affected by each other's dedup."""
        manager._ingest_line("Player1 joined the server")
        manager._ingest_line("Player2 joined the server")
        
        assert manager._activity_callback.call_count == 2
        
    def test_duplicate_leave_events_filtered(self, manager):
        """Duplicate leave events within 5 seconds should be filtered."""
        # First leave should trigger callback
        manager._ingest_line("TestPlayer left the server")
        assert manager._activity_callback.call_count == 1
        manager._activity_callback.assert_called_with("leave", "TestPlayer")
        
        # Second leave within 5 seconds should be filtered
        manager._ingest_line("TestPlayer left the server")
        assert manager._activity_callback.call_count == 1  # Still 1


class TestChatParsing:
    """Tests for chat message parsing patterns."""
    
    def test_parse_chat_timestamp_format(self, manager):
        """Parse [timestamp] Player: message format."""
        result = manager._parse_chat("[12:34:56] TestPlayer: Hi Prefect")
        assert result is not None
        name, msg = result
        assert name == "TestPlayer"
        assert msg == "Hi Prefect"
    
    def test_parse_chat_parentheses_format(self, manager):
        """Parse Necesse actual format: [timestamp] (Player): message."""
        result = manager._parse_chat("[09:31] (sefton): hi Prefect")
        assert result is not None
        name, msg = result
        assert name == "sefton"
        assert msg == "hi Prefect"
        
    def test_parse_chat_parentheses_format_with_date(self, manager):
        """Parse Necesse format with full timestamp."""
        result = manager._parse_chat("[2025-12-18 09:31:45] (TestPlayer): Prefect who are you?")
        assert result is not None
        name, msg = result
        assert name == "TestPlayer"
        assert msg == "Prefect who are you?"
        
    def test_parse_chat_angle_bracket_format(self, manager):
        """Parse <Player> message format."""
        result = manager._parse_chat("<TestPlayer> Hello world")
        assert result is not None
        name, msg = result
        assert name == "TestPlayer"
        assert msg == "Hello world"
        
    def test_parse_chat_says_format(self, manager):
        """Parse Player says: message format."""
        result = manager._parse_chat("TestPlayer says: Hi there Prefect")
        assert result is not None
        name, msg = result
        assert name == "TestPlayer"
        assert msg == "Hi there Prefect"
        
    def test_parse_chat_simple_format(self, manager):
        """Parse simple Player: message format."""
        result = manager._parse_chat("TestPlayer: Hello Prefect")
        assert result is not None
        name, msg = result
        assert name == "TestPlayer"
        assert msg == "Hello Prefect"
        
    def test_parse_chat_rejects_server_messages(self, manager):
        """Server status messages should not be parsed as chat."""
        # Lines with server indicators should return None
        assert manager._parse_chat("> help") is None
        assert manager._parse_chat("Started server on port 25565") is None
        assert manager._parse_chat("Loading world...") is None
        assert manager._parse_chat("Saving progress...") is None
        
    def test_parse_chat_rejects_timestamps_as_names(self, manager):
        """Timestamps should not be captured as player names."""
        # Should not parse timestamp as player name
        result = manager._parse_chat("[2025-12-18 10:30:00] Server started")
        # Either None or filtered by validation
        if result is not None:
            name, _ = result
            assert not name.startswith("20")


class TestChatMentionDetection:
    """Tests for chat mention (keyword) detection."""
    
    def test_mention_triggers_callback(self, manager):
        """Chat containing keyword should trigger mention callback."""
        manager._ingest_line("TestPlayer: Hi Prefect how are you?")
        
        # Should trigger chat mention callback
        assert manager._chat_mention_callback.call_count == 1
        manager._chat_mention_callback.assert_called_with("TestPlayer", "Hi Prefect how are you?")
        
    def test_mention_case_insensitive(self, manager):
        """Keyword detection should be case insensitive."""
        manager._ingest_line("TestPlayer: hello PREFECT")
        
        assert manager._chat_mention_callback.call_count == 1
        
    def test_no_mention_without_keyword(self, manager):
        """Chat without keyword should not trigger mention callback."""
        manager._ingest_line("TestPlayer: Hello world")
        
        assert manager._chat_mention_callback.call_count == 0
        
    def test_prefect_messages_ignored(self, manager):
        """Messages from 'prefect' should not trigger mention."""
        manager._ingest_line("Prefect: I am an AI assistant")
        
        # Should NOT trigger mention callback (Prefect talking to itself)
        assert manager._chat_mention_callback.call_count == 0


class TestLogTailing:
    """Tests for log file tailing functionality."""

    def test_start_log_tailing_without_log_path(self, tmp_path, mock_log_buffer):
        """start_log_tailing should do nothing when no log path is configured."""
        server_root = tmp_path / "server"
        server_root.mkdir()
        (server_root / "StartServer-nogui.sh").write_text("#!/bin/bash\necho 'server'")
        
        mgr = NecesseProcessManager(
            server_root=server_root,
            log_buffer=mock_log_buffer,
            log_path=None,  # No log path
        )
        
        # Should not raise, just log a warning
        mgr.start_log_tailing()
        assert mgr._file_tailer is None

    def test_start_log_tailing_with_log_path(self, tmp_path, mock_log_buffer):
        """start_log_tailing should start file tailer when log path exists."""
        server_root = tmp_path / "server"
        server_root.mkdir()
        (server_root / "StartServer-nogui.sh").write_text("#!/bin/bash\necho 'server'")
        
        # Create a log file
        log_file = tmp_path / "server.log"
        log_file.write_text("Initial log content\n")
        
        mgr = NecesseProcessManager(
            server_root=server_root,
            log_buffer=mock_log_buffer,
            log_path=log_file,
        )
        
        try:
            mgr.start_log_tailing()
            assert mgr._file_tailer is not None
            # FileTailer uses _stop Event - not set means it's running
            assert not mgr._file_tailer._stop.is_set()
        finally:
            if mgr._file_tailer:
                mgr._file_tailer.stop()

    def test_start_log_tailing_idempotent(self, tmp_path, mock_log_buffer):
        """start_log_tailing should be idempotent (safe to call twice)."""
        server_root = tmp_path / "server"
        server_root.mkdir()
        (server_root / "StartServer-nogui.sh").write_text("#!/bin/bash\necho 'server'")
        
        log_file = tmp_path / "server.log"
        log_file.write_text("Initial log content\n")
        
        mgr = NecesseProcessManager(
            server_root=server_root,
            log_buffer=mock_log_buffer,
            log_path=log_file,
        )
        
        try:
            mgr.start_log_tailing()
            first_tailer = mgr._file_tailer
            
            # Calling again should not create a new tailer
            mgr.start_log_tailing()
            assert mgr._file_tailer is first_tailer
        finally:
            if mgr._file_tailer:
                mgr._file_tailer.stop()
