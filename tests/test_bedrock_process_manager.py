"""Tests for BedrockProcessManager (Minecraft Bedrock dedicated server)."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from prefect.server_control.process_manager import BedrockProcessManager, ServerNotConfiguredError
from prefect.watchers.log_tail import RollingLogBuffer


@pytest.fixture
def log_buffer() -> RollingLogBuffer:
    return RollingLogBuffer(max_lines=100)


def test_detect_command_missing_root(tmp_path, log_buffer: RollingLogBuffer) -> None:
    missing = tmp_path / "does-not-exist"
    mgr = BedrockProcessManager(server_root=missing, log_buffer=log_buffer)
    with pytest.raises(ServerNotConfiguredError):
        mgr._detect_command()


def test_detect_command_missing_binary(tmp_path, log_buffer: RollingLogBuffer) -> None:
    root = tmp_path / "bedrock"
    root.mkdir()
    mgr = BedrockProcessManager(server_root=root, log_buffer=log_buffer)
    with pytest.raises(ServerNotConfiguredError):
        mgr._detect_command()


def test_detect_command_non_executable_binary(tmp_path, log_buffer: RollingLogBuffer) -> None:
    root = tmp_path / "bedrock"
    root.mkdir()
    exe = root / "bedrock_server"
    exe.write_text("#!/bin/sh\necho hi\n")
    # Ensure not executable
    os.chmod(exe, 0o644)

    mgr = BedrockProcessManager(server_root=root, log_buffer=log_buffer)
    with pytest.raises(ServerNotConfiguredError):
        mgr._detect_command()


def test_detect_command_ok(tmp_path, log_buffer: RollingLogBuffer) -> None:
    root = tmp_path / "bedrock"
    root.mkdir()
    exe = root / "bedrock_server"
    exe.write_text("#!/bin/sh\necho hi\n")
    os.chmod(exe, 0o755)

    mgr = BedrockProcessManager(server_root=root, log_buffer=log_buffer)
    cmd = mgr._detect_command()
    assert cmd == [str(exe)]


def test_player_join_leave_parsing(log_buffer: RollingLogBuffer, tmp_path) -> None:
    root = tmp_path / "bedrock"
    root.mkdir()
    exe = root / "bedrock_server"
    exe.write_text("#!/bin/sh\necho hi\n")
    os.chmod(exe, 0o755)

    mgr = BedrockProcessManager(server_root=root, log_buffer=log_buffer)

    mgr._ingest_line("Player connected: Steve, xuid: 123")
    assert mgr.players_online() == ["Steve"]

    mgr._ingest_line("Player disconnected: Steve, xuid: 123")
    assert mgr.players_online() == []


def test_chat_parsing_and_mention_callback(tmp_path, log_buffer: RollingLogBuffer) -> None:
    root = tmp_path / "bedrock"
    root.mkdir()
    exe = root / "bedrock_server"
    exe.write_text("#!/bin/sh\necho hi\n")
    os.chmod(exe, 0o755)

    chat_cb = MagicMock()
    mention_cb = MagicMock()

    mgr = BedrockProcessManager(
        server_root=root,
        log_buffer=log_buffer,
        on_chat_line=chat_cb,
        on_chat_mention=mention_cb,
        chat_keyword="prefect",
    )

    mgr._ingest_line("<Steve> hello there")
    chat_cb.assert_called_once()
    mention_cb.assert_not_called()

    mgr._ingest_line("Chat: Steve: Prefect are you online?")
    assert mention_cb.call_count == 1

    mgr._ingest_line("[INFO] <sefton> Hi Prefect")
    assert mention_cb.call_count == 2
