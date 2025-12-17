from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from prefect.watchers.log_tail import FileTailer, RollingLogBuffer, StdoutReader


@dataclass
class ServerStatus:
    running: bool
    pid: int | None
    uptime_seconds: float | None
    last_error: str | None
    last_restart_time: float | None
    players_online: list[str]


class ServerNotConfiguredError(RuntimeError):
    pass


class NecesseProcessManager:
    def __init__(
        self,
        *,
        server_root: Path,
        log_buffer: RollingLogBuffer,
        command_output_window_seconds: float = 2.0,
        log_path: Path | None = None,
    ):
        self._server_root = server_root
        self._log_buffer = log_buffer
        self._log_path = log_path
        self._command_output_window_seconds = command_output_window_seconds

        self._proc: subprocess.Popen[str] | None = None
        self._stdout_reader: StdoutReader | None = None
        self._file_tailer: FileTailer | None = None

        self._stdin_lock = threading.Lock()
        self._state_lock = threading.Lock()

        self._start_ts: float | None = None
        self._last_restart_ts: float | None = None
        self._last_error: str | None = None
        self._players_online: set[str] = set()

        self._re_join = re.compile(r"\b(?P<name>[^\s:]{2,32})\b.*\b(joined|connected)\b", re.IGNORECASE)
        self._re_leave = re.compile(r"\b(?P<name>[^\s:]{2,32})\b.*\b(left|disconnected)\b", re.IGNORECASE)

    def _detect_command(self) -> list[str]:
        if not self._server_root.exists():
            raise ServerNotConfiguredError(f"Necesse server root does not exist: {self._server_root}")

        server_sh = self._server_root / "Server.sh"
        if server_sh.exists():
            return ["bash", str(server_sh)]

        server_jar = self._server_root / "Server.jar"
        if server_jar.exists():
            return ["java", "-jar", str(server_jar)]

        raise ServerNotConfiguredError(
            "Could not find Necesse server launcher. Expected either Server.sh or Server.jar in "
            f"{self._server_root}"
        )

    def start(self) -> None:
        if self.is_running():
            return

        cmd = self._detect_command()

        self._proc = subprocess.Popen(
            cmd,
            cwd=str(self._server_root),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._start_ts = time.time()
        self._last_restart_ts = self._start_ts

        assert self._proc.stdout is not None
        self._stdout_reader = StdoutReader(self._proc.stdout, self._log_buffer, on_line=self._ingest_line)
        self._stdout_reader.start()

        if self._log_path is not None:
            self._file_tailer = FileTailer(self._log_path, self._log_buffer)
            self._file_tailer.start()

    def stop(self, *, grace_seconds: float = 5.0) -> None:
        proc = self._proc
        if proc is None:
            return

        try:
            # Best effort: try a graceful stop command (may or may not exist).
            try:
                self.send_command_raw("stop")
            except Exception:
                pass

            proc.terminate()
            proc.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
        finally:
            if self._stdout_reader is not None:
                self._stdout_reader.stop()
            if self._file_tailer is not None:
                self._file_tailer.stop()
            self._proc = None
            self._stdout_reader = None
            self._file_tailer = None
            self._start_ts = None

    def restart(self) -> None:
        self.stop()
        time.sleep(0.2)
        self.start()

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def pid(self) -> int | None:
        if self._proc is None:
            return None
        return self._proc.pid

    def uptime_seconds(self) -> float | None:
        if not self.is_running() or self._start_ts is None:
            return None
        return max(0.0, time.time() - self._start_ts)

    def last_restart_time(self) -> float | None:
        return self._last_restart_ts

    def last_error(self) -> str | None:
        with self._state_lock:
            return self._last_error

    def players_online(self) -> list[str]:
        with self._state_lock:
            return sorted(self._players_online)

    def status(self) -> ServerStatus:
        return ServerStatus(
            running=self.is_running(),
            pid=self.pid(),
            uptime_seconds=self.uptime_seconds(),
            last_error=self.last_error(),
            last_restart_time=self.last_restart_time(),
            players_online=self.players_online(),
        )

    def send_command_raw(self, command: str) -> None:
        if not self.is_running() or self._proc is None or self._proc.stdin is None:
            raise RuntimeError("Server is not running under Prefect")

        # Serialize stdin writes.
        with self._stdin_lock:
            self._proc.stdin.write(command.rstrip("\n") + "\n")
            self._proc.stdin.flush()

    def run_command_capture(self, command: str) -> str:
        """Send a command to the server and return new log output for a short window."""
        start_ts = time.time()
        self.send_command_raw(command)
        time.sleep(self._command_output_window_seconds)
        lines = self._log_buffer.get_since(start_ts)
        # Avoid returning unbounded data.
        return "\n".join(lines[-200:])

    def _ingest_line(self, line: str) -> None:
        # Maintain a minimal session state from logs.
        lower = line.lower()
        if "error" in lower or "exception" in lower:
            with self._state_lock:
                self._last_error = line.strip()[:500]

        m_join = self._re_join.search(line)
        if m_join:
            name = m_join.group("name")
            with self._state_lock:
                self._players_online.add(name)
            return

        m_leave = self._re_leave.search(line)
        if m_leave:
            name = m_leave.group("name")
            with self._state_lock:
                self._players_online.discard(name)
