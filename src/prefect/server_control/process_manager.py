from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import logging

from prefect.watchers.log_tail import FileTailer, RollingLogBuffer, StdoutReader

logger = logging.getLogger(__name__)


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
        on_chat_line=None,
        on_chat_mention=None,
        on_activity=None,
        chat_keyword: str = "prefect",
        debug_verbose: bool = False,
    ):
        self._server_root = server_root
        self._log_buffer = log_buffer
        self._log_path = log_path
        self._on_chat_line = on_chat_line
        self._on_chat_mention = on_chat_mention
        self._on_activity = on_activity
        self._chat_keyword = (chat_keyword or "prefect").lower()
        self._command_output_window_seconds = command_output_window_seconds
        self._debug_verbose = debug_verbose

        self._proc: subprocess.Popen[str] | None = None
        self._stdout_reader: StdoutReader | None = None
        self._file_tailer: FileTailer | None = None

        self._stdin_lock = threading.Lock()
        self._state_lock = threading.Lock()

        self._start_ts: float | None = None
        self._last_restart_ts: float | None = None
        self._last_error: str | None = None
        self._players_online: set[str] = set()
        
        # Deduplication: track recent events to prevent duplicates
        # Format: {(kind, name_lower): timestamp}
        self._recent_activity_events: dict[tuple[str, str], float] = {}
        self._activity_dedup_seconds = 5.0  # Ignore duplicate events within 5 seconds

        self._re_join = re.compile(r"\b(?P<name>[^\s:]{2,32})\b.*\b(joined|connected)\b", re.IGNORECASE)
        self._re_leave = re.compile(r"\b(?P<name>[^\s:]{2,32})\b.*\b(left|disconnected)\b", re.IGNORECASE)

        # Chat heuristics (Necesse log formats may vary)
        # Examples we try to handle:
        #   [12:34:56] Name: message
        #   [12:34:56] (Name): message   <-- Necesse actual format
        #   <Name> message
        #   Chat: Name: message
        #   Name: message
        #   [2025-12-18 12:34:56] Name says: message
        self._re_chat1 = re.compile(r"\[[^\]]+\]\s*(?P<name>[^:]{1,32}):\s*(?P<msg>.+)$")
        # Necesse actual format: [timestamp] (PlayerName): message
        self._re_chat_paren = re.compile(r"\[[^\]]+\]\s*\((?P<name>[^)]{1,32})\):\s*(?P<msg>.+)$")
        self._re_chat2 = re.compile(r"<(?P<name>[^>]{1,32})>\s*(?P<msg>.+)$")
        self._re_chat3 = re.compile(r"(?:^|\bchat\b\s*[:\-]\s*)(?P<name>[^:]{1,32}):\s*(?P<msg>.+)$", re.IGNORECASE)
        # Necesse "says" format: PlayerName says: message (capture name before "says")
        self._re_chat4 = re.compile(r"(?P<name>\w{2,32})\s+says:\s*(?P<msg>.+)$", re.IGNORECASE)
        # Fallback: simple "Name: message" anywhere in line
        self._re_chat5 = re.compile(r"^\s*(?P<name>\w{2,32}):\s+(?P<msg>.+)$")

    def _detect_command(self) -> list[str]:
        if not self._server_root.exists():
            raise ServerNotConfiguredError(f"Necesse server root does not exist: {self._server_root}")

        start_server_nogui_sh = self._server_root / "StartServer-nogui.sh"
        if start_server_nogui_sh.exists():
            return ["bash", str(start_server_nogui_sh)]

        start_server_sh = self._server_root / "StartServer.sh"
        if start_server_sh.exists():
            return ["bash", str(start_server_sh)]

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

        logger.info("Necesse server process started pid=%s cmd=%s", self._proc.pid, cmd)

        assert self._proc.stdout is not None
        self._stdout_reader = StdoutReader(self._proc.stdout, self._log_buffer, on_line=self._ingest_line)
        self._stdout_reader.start()

        if self._log_path is not None:
            self._file_tailer = FileTailer(self._log_path, self._log_buffer, on_line=self._ingest_line)
            self._file_tailer.start()

    def start_log_tailing(self) -> None:
        """Start tailing the log file without starting the server process.

        This is used in tmux mode where the server is already running but we
        still want to parse logs for chat mentions and player events.
        """
        if self._file_tailer is not None:
            return  # Already tailing

        if self._log_path is None:
            logger.warning("Cannot start log tailing: no log path configured")
            return

        self._file_tailer = FileTailer(self._log_path, self._log_buffer, on_line=self._ingest_line)
        self._file_tailer.start()
        logger.info("Started log file tailing for %s", self._log_path)

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
        line_stripped = line.rstrip("\n")
        
        # === VERBOSE DEBUG: Log every single line received ===
        if self._debug_verbose:
            self._log_buffer.append(f"[DEBUG:RAW] {line_stripped[:500]}")
            logger.debug("[RAW LINE] %s", line_stripped[:500])
        
        # Check for error/exception keywords
        if "error" in lower or "exception" in lower:
            with self._state_lock:
                self._last_error = line_stripped[:500]
            if self._debug_verbose:
                self._log_buffer.append(f"[DEBUG:ERROR_DETECTED] {line_stripped[:300]}")

        # === CHAT PARSING ===
        parsed = self._parse_chat(line_stripped)
        if parsed is not None:
            name, msg = parsed
            if self._debug_verbose:
                self._log_buffer.append(f"[DEBUG:CHAT_PARSED] player='{name}' msg='{msg[:200]}'")
                logger.debug("[CHAT PARSED] player='%s' msg='%s'", name, msg[:200])
            
            if self._on_chat_line is not None:
                try:
                    self._on_chat_line(name.strip()[:32], msg.strip()[:400])
                except Exception as e:
                    if self._debug_verbose:
                        self._log_buffer.append(f"[DEBUG:CHAT_CALLBACK_ERROR] {e}")

            # Detect chat mentions of Prefect (only on parsed chat).
            if self._on_chat_mention is not None and self._chat_keyword in (msg or "").lower():
                if name.strip().lower() not in {"prefect", "server"}:
                    if self._debug_verbose:
                        self._log_buffer.append(f"[DEBUG:CHAT_MENTION_TRIGGERED] player='{name}' keyword='{self._chat_keyword}'")
                    try:
                        self._on_chat_mention(name.strip()[:32], msg.strip()[:400])
                    except Exception as e:
                        if self._debug_verbose:
                            self._log_buffer.append(f"[DEBUG:MENTION_CALLBACK_ERROR] {e}")
        else:
            # Breadcrumb for debugging: keyword present but we couldn't parse.
            if self._chat_keyword in lower:
                self._log_buffer.append(f"[DEBUG:CHAT_PARSE_FAILED] keyword '{self._chat_keyword}' in line but no regex matched: {line_stripped[:240]}")
                if self._debug_verbose:
                    # Also try each regex individually and report
                    self._log_buffer.append(f"[DEBUG:REGEX_ATTEMPT] re_chat_paren (parentheses): match={bool(self._re_chat_paren.search(line_stripped))}")
                    self._log_buffer.append(f"[DEBUG:REGEX_ATTEMPT] re_chat1 (timestamp): match={bool(self._re_chat1.search(line_stripped))}")
                    self._log_buffer.append(f"[DEBUG:REGEX_ATTEMPT] re_chat2 (angle brackets): match={bool(self._re_chat2.search(line_stripped))}")
                    self._log_buffer.append(f"[DEBUG:REGEX_ATTEMPT] re_chat3 (chat prefix): match={bool(self._re_chat3.search(line_stripped))}")
                    self._log_buffer.append(f"[DEBUG:REGEX_ATTEMPT] re_chat4 (says): match={bool(self._re_chat4.search(line_stripped))}")
                    self._log_buffer.append(f"[DEBUG:REGEX_ATTEMPT] re_chat5 (simple): match={bool(self._re_chat5.search(line_stripped))}")

        # === JOIN/LEAVE DETECTION ===
        m_join = self._re_join.search(line)
        if self._debug_verbose:
            # Always log join regex attempts for lines that might be join/leave related
            join_keywords = ["join", "connect", "enter", "login", "log in", "logged"]
            if any(kw in lower for kw in join_keywords):
                self._log_buffer.append(f"[DEBUG:JOIN_CHECK] line='{line_stripped[:200]}' regex_matched={bool(m_join)}")
                if m_join:
                    self._log_buffer.append(f"[DEBUG:JOIN_MATCH] groups={m_join.groupdict()}")
        
        if m_join:
            name = m_join.group("name")
            name_clean = name.strip()[:32]
            
            # Deduplication check for join events
            now = time.time()
            event_key = ("join", name_clean.lower())
            last_event_time = self._recent_activity_events.get(event_key, 0)
            if now - last_event_time < self._activity_dedup_seconds:
                if self._debug_verbose:
                    self._log_buffer.append(f"[DEBUG:JOIN_DEDUP] Ignoring duplicate join for '{name_clean}' (last seen {now - last_event_time:.1f}s ago)")
                return
            self._recent_activity_events[event_key] = now
            
            if self._debug_verbose:
                self._log_buffer.append(f"[DEBUG:PLAYER_JOINED] name='{name}' raw_line='{line_stripped[:200]}'")
                logger.info("[PLAYER JOINED] name='%s'", name)
            with self._state_lock:
                self._players_online.add(name)
            if self._on_activity is not None:
                try:
                    self._on_activity("join", name_clean)
                except Exception as e:
                    if self._debug_verbose:
                        self._log_buffer.append(f"[DEBUG:JOIN_CALLBACK_ERROR] {e}")
            return

        m_leave = self._re_leave.search(line)
        if self._debug_verbose:
            # Always log leave regex attempts for lines that might be join/leave related
            leave_keywords = ["left", "disconnect", "quit", "logout", "log out", "logged out"]
            if any(kw in lower for kw in leave_keywords):
                self._log_buffer.append(f"[DEBUG:LEAVE_CHECK] line='{line_stripped[:200]}' regex_matched={bool(m_leave)}")
                if m_leave:
                    self._log_buffer.append(f"[DEBUG:LEAVE_MATCH] groups={m_leave.groupdict()}")
        
        if m_leave:
            name = m_leave.group("name")
            name_clean = name.strip()[:32]
            
            # Deduplication check for leave events
            now = time.time()
            event_key = ("leave", name_clean.lower())
            last_event_time = self._recent_activity_events.get(event_key, 0)
            if now - last_event_time < self._activity_dedup_seconds:
                if self._debug_verbose:
                    self._log_buffer.append(f"[DEBUG:LEAVE_DEDUP] Ignoring duplicate leave for '{name_clean}' (last seen {now - last_event_time:.1f}s ago)")
                return
            self._recent_activity_events[event_key] = now
            
            if self._debug_verbose:
                self._log_buffer.append(f"[DEBUG:PLAYER_LEFT] name='{name}' raw_line='{line_stripped[:200]}'")
                logger.info("[PLAYER LEFT] name='%s'", name)
            with self._state_lock:
                self._players_online.discard(name)
            if self._on_activity is not None:
                try:
                    self._on_activity("leave", name_clean)
                except Exception as e:
                    if self._debug_verbose:
                        self._log_buffer.append(f"[DEBUG:LEAVE_CALLBACK_ERROR] {e}")

    def _parse_chat(self, line: str) -> tuple[str, str] | None:
        # Strip ANSI codes before parsing
        clean = self._strip_ansi(line)
        
        # Skip lines that are clearly server log entries, not player chat
        # Necesse format: [YYYY-MM-DD HH:MM:SS] Server message
        # Player chat would be: [timestamp] PlayerName: message or <PlayerName> message
        
        # Check if this looks like a server status line (not player chat)
        # These patterns indicate server output, not player messages
        # Note: We use startswith for "> " to avoid matching angle brackets in chat
        if clean.startswith("> "):
            return None
            
        server_indicators = (
            "Selected save:",
            "Custom server options",
            "Started server",
            "Started lan socket",
            "Found ",
            "Local address:",
            "Type help",
            "Loading",
            "Saving",
            "Error",
            "Exception",
            "Warning",
        )
        for indicator in server_indicators:
            if indicator in clean:
                return None
        
        # Use search to allow prefixes (timestamps, log levels, etc.).
        # Order matters: more specific patterns first.
        # _re_chat_paren is the actual Necesse format: [timestamp] (PlayerName): message
        for rx in (self._re_chat_paren, self._re_chat1, self._re_chat2, self._re_chat4, self._re_chat3, self._re_chat5):
            m = rx.search(clean)
            if m:
                name = m.group("name").strip()
                msg = m.group("msg").strip()
                
                # Reject if name looks like a timestamp or server prefix
                if any(c in name for c in "[]<>") or name.startswith("20"):
                    continue
                # Reject very short names
                if len(name) < 2:
                    continue
                    
                return name, msg
        return None
    
    def _strip_ansi(self, text: str) -> str:
        """Remove ANSI escape sequences from text."""
        import re
        return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][AB012]", "", text)
