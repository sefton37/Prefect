from __future__ import annotations

import subprocess
import time
from dataclasses import asdict

from prefect.server_control.process_manager import NecesseProcessManager, ServerStatus
from prefect.watchers.log_tail import RollingLogBuffer


class ServerController:
    def start(self) -> None:  # pragma: no cover
        raise NotImplementedError

    def status(self) -> ServerStatus:  # pragma: no cover
        raise NotImplementedError

    def run_command_capture(self, command: str) -> str:  # pragma: no cover
        raise NotImplementedError


class ManagedController(ServerController):
    def __init__(self, manager: NecesseProcessManager):
        self._manager = manager

    def start(self) -> None:
        self._manager.start()

    def status(self) -> ServerStatus:
        return self._manager.status()

    def run_command_capture(self, command: str) -> str:
        return self._manager.run_command_capture(command)


class TmuxAttachController(ServerController):
    """Send commands to an already-running server inside tmux.

    Output capture is best-effort and relies on log ingestion (e.g. PREFECT_LOG_PATH).
    """

    def __init__(self, *, tmux_target: str, log_buffer: RollingLogBuffer, output_window_seconds: float = 2.0):
        self._target = tmux_target
        self._log_buffer = log_buffer
        self._window = output_window_seconds
        self._start_time = time.time()

    def _has_session(self) -> bool:
        try:
            subprocess.run(["tmux", "has-session", "-t", self._target], check=True, capture_output=True, text=True)
            return True
        except Exception:
            return False

    def start(self) -> None:
        # Attach mode doesn't start anything.
        return

    def status(self) -> ServerStatus:
        running = self._has_session()
        return ServerStatus(
            running=running,
            pid=None,
            uptime_seconds=(time.time() - self._start_time) if running else None,
            last_error=None,
            last_restart_time=None,
            players_online=[],
        )

    def run_command_capture(self, command: str) -> str:
        if not self._has_session():
            raise RuntimeError(f"tmux target not found: {self._target}")

        start_ts = time.time()
        # Send as a single argument to preserve spaces.
        subprocess.run(["tmux", "send-keys", "-t", self._target, command, "Enter"], check=True)
        time.sleep(self._window)
        lines = self._log_buffer.get_since(start_ts)
        return "\n".join(lines[-200:])


def status_to_dict(status: ServerStatus) -> dict:
    return asdict(status)
