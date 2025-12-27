from __future__ import annotations

import subprocess
import time
from dataclasses import asdict
from pathlib import Path
import shutil
from typing import Protocol

from prefect.server_control.process_manager import ServerStatus
from prefect.watchers.log_tail import RollingLogBuffer


class ServerController:
    def start(self) -> None:  # pragma: no cover
        raise NotImplementedError

    def status(self) -> ServerStatus:  # pragma: no cover
        raise NotImplementedError

    def run_command_capture(self, command: str) -> str:  # pragma: no cover
        raise NotImplementedError


class ManagedProcessManager(Protocol):
    def start(self) -> None:  # pragma: no cover
        ...

    def status(self) -> ServerStatus:  # pragma: no cover
        ...

    def run_command_capture(self, command: str) -> str:  # pragma: no cover
        ...


class ManagedController(ServerController):
    def __init__(self, manager: ManagedProcessManager):
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

    def __init__(
        self,
        *,
        tmux_target: str,
        log_buffer: RollingLogBuffer,
        output_window_seconds: float = 2.0,
        start_command: list[str] | None = None,
        start_cwd: str | None = None,
        pipe_output_path: str | None = None,
    ):
        self._target = tmux_target
        self._log_buffer = log_buffer
        self._window = output_window_seconds
        self._start_time = time.time()
        self._start_command = start_command
        self._start_cwd = start_cwd
        self._pipe_output_path = pipe_output_path

    def _has_session(self) -> bool:
        if shutil.which("tmux") is None:
            return False
        try:
            subprocess.run(["tmux", "has-session", "-t", self._target], check=True, capture_output=True, text=True)
            return True
        except Exception:
            return False

    def _require_tmux(self) -> None:
        if shutil.which("tmux") is None:
            raise RuntimeError(
                "tmux is not installed (required for control_mode=tmux). "
                "Install tmux (e.g. 'sudo apt-get install tmux' or 'sudo dnf install tmux') "
                "or switch to managed mode."
            )

    def start(self) -> None:
        self._require_tmux()
        # Attach-only by default. If a start command is provided, create the session
        # on demand when it does not already exist.
        if not self._has_session():
            if not self._start_command:
                return

            cmd = ["tmux", "new-session", "-d", "-s", self._target]
            if self._start_cwd:
                cmd.extend(["-c", self._start_cwd])
            cmd.extend(self._start_command)
            subprocess.run(cmd, check=True, capture_output=True, text=True)

        # If requested, pipe pane output to a file so a log tailer can ingest it.
        # -o ensures we don't double-pipe if already configured.
        if self._pipe_output_path:
            try:
                out_path = Path(self._pipe_output_path)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.touch(exist_ok=True)
                subprocess.run(
                    [
                        "tmux",
                        "pipe-pane",
                        "-o",
                        "-t",
                        self._target,
                        f"cat >> {self._pipe_output_path}",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except Exception:
                # Best-effort only; attach/commands still work without output piping.
                pass

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
        self._require_tmux()
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
