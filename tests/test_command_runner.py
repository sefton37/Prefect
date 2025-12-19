"""Tests for prefect.server_control.command_runner module."""
from __future__ import annotations

import pytest

from prefect.server_control.command_runner import CommandRunner, CommandResult
from prefect.server_control.controller import ServerController
from prefect.server_control.process_manager import ServerStatus


class MockController(ServerController):
    """Mock controller for testing."""
    
    def __init__(self, *, capture_result: str = "", should_raise: Exception | None = None):
        self._capture_result = capture_result
        self._should_raise = should_raise
        self.commands_received: list[str] = []
    
    def start(self) -> None:
        pass
    
    def status(self) -> ServerStatus:
        return ServerStatus(
            running=True,
            pid=1234,
            uptime_seconds=100.0,
            last_error=None,
            last_restart_time=None,
            players_online=[],
        )
    
    def run_command_capture(self, command: str) -> str:
        self.commands_received.append(command)
        if self._should_raise:
            raise self._should_raise
        return self._capture_result


class TestCommandRunner:
    """Tests for CommandRunner class."""

    def test_run_returns_ok_result(self):
        controller = MockController(capture_result="Command executed")
        runner = CommandRunner(controller)
        
        result = runner.run("help")
        
        assert result.ok is True
        assert result.output == "Command executed"
        assert result.error is None
        assert controller.commands_received == ["help"]

    def test_run_returns_error_on_exception(self):
        controller = MockController(should_raise=RuntimeError("Server not running"))
        runner = CommandRunner(controller)
        
        result = runner.run("help")
        
        assert result.ok is False
        assert result.output == ""
        assert result.error == "Server not running"

    def test_run_passes_command_unchanged(self):
        controller = MockController()
        runner = CommandRunner(controller)
        
        runner.run("say hello world")
        
        assert controller.commands_received == ["say hello world"]


class TestCommandResult:
    """Tests for CommandResult dataclass."""

    def test_successful_result(self):
        result = CommandResult(ok=True, output="test output")
        assert result.ok is True
        assert result.output == "test output"
        assert result.error is None

    def test_failed_result(self):
        result = CommandResult(ok=False, output="", error="Something failed")
        assert result.ok is False
        assert result.error == "Something failed"

    def test_result_is_frozen(self):
        result = CommandResult(ok=True, output="test")
        # Dataclass is frozen
        with pytest.raises(AttributeError):
            result.ok = False  # type: ignore
