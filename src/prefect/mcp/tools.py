from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from collections import deque
from dataclasses import asdict

from prefect.config import PrefectSettings, get_settings
from prefect.command_catalog import load_command_names
from prefect.ollama_client import OllamaClient, OllamaConfig
from prefect.safety.allowlist import CommandAllowlist, CommandNotPermittedError
from prefect.safety.sanitizer import (
    UnsafeInputError,
    sanitize_announce,
    sanitize_command,
    sanitize_startup_reply,
)
from prefect.server_control.command_runner import CommandRunner
from prefect.server_control.controller import ManagedController, TmuxAttachController
from prefect.server_control.process_manager import NecesseProcessManager
from prefect.server_control.probes import tcp_port_open
from prefect.watchers.log_tail import RollingLogBuffer

logger = logging.getLogger(__name__)


_DISALLOWED_FOR_ANNOUNCE = set(";|&><$`\\(){}[]")


def _coerce_safe_chat_text(text: str, *, max_len: int) -> str:
    value = (text or "").replace("\n", " ").replace("\r", " ")
    value = "".join(ch for ch in value if ch not in _DISALLOWED_FOR_ANNOUNCE)
    value = " ".join(value.split())
    if len(value) > max_len:
        value = value[: max_len - 1].rstrip() + "…"
    return value


def _looks_like_unknown_command(output: str) -> bool:
    text = (output or "").lower()
    needles = (
        "unknown command",
        "not recognized",
        "unrecognized",
        "invalid command",
        "no such command",
    )
    return any(n in text for n in needles)


class PrefectCore:
    def __init__(self, settings: PrefectSettings | None = None):
        self.settings = settings or get_settings()

        self.command_names = load_command_names(self.settings.commands_file)

        self.log_buffer = RollingLogBuffer(max_lines=self.settings.log_buffer_lines)

        self._events_lock = threading.Lock()
        self._chat_events: deque[tuple[float, str, str]] = deque(maxlen=500)
        self._activity_events: deque[tuple[float, str]] = deque(maxlen=500)

        self.process_manager = NecesseProcessManager(
            server_root=self.settings.server_root,
            log_buffer=self.log_buffer,
            command_output_window_seconds=self.settings.command_output_window_seconds,
            log_path=self.settings.log_path,
            on_chat_line=self._on_chat_line,
            on_chat_mention=self._on_chat_mention if self.settings.chat_mention_enabled else None,
            on_activity=self._on_activity,
            chat_keyword=self.settings.chat_mention_keyword,
        )

        mode = (self.settings.control_mode or "managed").lower()
        if mode == "tmux":
            self.controller = TmuxAttachController(
                tmux_target=self.settings.tmux_target,
                log_buffer=self.log_buffer,
                output_window_seconds=self.settings.command_output_window_seconds,
            )
        else:
            self.controller = ManagedController(self.process_manager)

        self.command_runner = CommandRunner(self.controller)

        # Convert command names into safe allowlist prefixes.
        # For single-word commands like "help" allow exact match; for arg commands, allow "cmd " prefix.
        extra_prefixes: list[str] = []
        for name in self.command_names:
            extra_prefixes.append(name)
            extra_prefixes.append(name + " ")

        # Also allow announce candidates based on templates.
        templates = [t.strip() for t in (self.settings.announce_command_templates or "").split(",") if t.strip()]
        for tmpl in templates:
            cmd_token = tmpl.strip().split(" ", 1)[0]
            if cmd_token:
                extra_prefixes.append(cmd_token)
                extra_prefixes.append(cmd_token + " ")

        self.allowlist = CommandAllowlist.default(extra_prefixes=tuple(extra_prefixes))
        self.ollama = OllamaClient(OllamaConfig(base_url=self.settings.ollama_url, model=self.settings.model))

        self._started = False
        self._start_time = time.time()

        self._chat_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._chat_thread: threading.Thread | None = None
        self._chat_stop = threading.Event()
        self._last_player_reply_ts: dict[str, float] = {}
        self._chat_history: dict[str, deque[tuple[float, str, str]]] = {}

    def _on_chat_line(self, player: str, message: str) -> None:
        ts = time.time()
        with self._events_lock:
            self._chat_events.append((ts, player, message))

    def _on_activity(self, kind: str, who: str) -> None:
        ts = time.time()
        text = f"{who} {('joined' if kind == 'join' else 'left')}"
        with self._events_lock:
            self._activity_events.append((ts, text))

    def get_chat_events(self, *, since_ts: float | None = None) -> list[tuple[float, str, str]]:
        with self._events_lock:
            events = list(self._chat_events)
        if since_ts is None:
            return events
        return [e for e in events if e[0] >= since_ts]

    def get_activity_events(self, *, since_ts: float | None = None) -> list[tuple[float, str]]:
        with self._events_lock:
            events = list(self._activity_events)
        if since_ts is None:
            return events
        return [e for e in events if e[0] >= since_ts]

    def start(self) -> None:
        if self._started:
            return
        self._started = True

        logger.info(
            "PrefectCore starting control_mode=%s start_server=%s log_path=%s",
            self.settings.control_mode,
            self.settings.start_server,
            self.settings.log_path,
        )

        if (self.settings.control_mode or "managed").lower() == "tmux":
            return

        if self.settings.start_server:
            try:
                self.process_manager.start()
            except Exception as exc:
                # Keep running so log-only mode can still work (e.g., if log_path is set).
                logger.error("Failed to start Necesse server process: %s", exc)

        if self.settings.chat_mention_enabled:
            self._start_chat_thread()

    def _start_chat_thread(self) -> None:
        if self._chat_thread and self._chat_thread.is_alive():
            return
        self._chat_stop.clear()
        self._chat_thread = threading.Thread(target=self._chat_worker, name="prefect-chat-worker", daemon=True)
        self._chat_thread.start()

    def _on_chat_mention(self, player: str, message: str) -> None:
        # Called from stdout reader thread.
        try:
            self.log_buffer.append(f"[Prefect] chat_event_queued player={player} msg={message[:200]}")
            self._chat_queue.put_nowait((player, message))
        except Exception:
            pass

    def _chat_worker(self) -> None:
        while not self._chat_stop.is_set():
            try:
                player, message = self._chat_queue.get(timeout=0.25)
            except queue.Empty:
                continue

            now = time.time()
            last = self._last_player_reply_ts.get(player.lower(), 0.0)
            if now - last < float(self.settings.chat_cooldown_seconds):
                continue

            self._last_player_reply_ts[player.lower()] = now

            try:
                self._respond_to_player(player, message)
            except Exception as exc:
                logger.warning("Chat responder failed: %s", exc)

    def _respond_to_player(self, player: str, message: str) -> None:
        keyword = (self.settings.chat_mention_keyword or "prefect").lower()
        msg = message
        # If message starts with "prefect" style mention, strip it.
        lower = msg.lower().strip()
        for prefix in (keyword + ":", "@" + keyword, keyword):
            if lower.startswith(prefix):
                msg = msg[len(prefix) :].lstrip(" :,-")
                break

        # Maintain a short per-player conversation context.
        now = time.time()
        key = player.lower()
        history = self._chat_history.get(key)
        if history is None:
            history = deque(maxlen=max(2, int(self.settings.chat_context_turns)))
            self._chat_history[key] = history

        ttl = float(self.settings.chat_context_ttl_seconds)
        while history and (now - history[0][0]) > ttl:
            history.popleft()

        history.append((now, "player", msg))

        context_lines: list[str] = []
        for _, role, text in list(history)[-int(self.settings.chat_context_turns) :]:
            who = "Player" if role == "player" else "Prefect"
            context_lines.append(f"{who}: {text}")
        context_block = "\n".join(context_lines)

        system_prompt = (
            "You are Prefect, a helpful steward AI for a Necesse dedicated server. "
            "Reply briefly and politely. Do not give hacking/cheating advice. "
            "If asked to do admin actions, explain you can only do safe server admin commands."
        )
        user_prompt = (
            f"Conversation so far:\n{context_block}\n\n"
            f"Reply as Prefect to Player {player} in 1-2 sentences."
        ).strip()

        # Call async Ollama from this thread.
        self.log_buffer.append(f"[Prefect] generating_reply player={player}")
        reply = asyncio.run(self.ollama.generate(system_prompt, user_prompt))
        safe = _coerce_safe_chat_text(reply, max_len=int(self.settings.chat_max_reply_length))

        history.append((time.time(), "prefect", safe))

        # Try to send to server chat.
        self.log_buffer.append(f"[Prefect] sending_reply_to_chat player={player}")
        out = self.announce(f"@{player} {safe}")
        self.log_buffer.append(f"[Prefect] chat_send_result ok={out.get('ok')} sent={out.get('sent')} err={out.get('error')}")
        if not out.get("ok"):
            # Last resort: strip harder and try again.
            safe2 = _coerce_safe_chat_text(safe, max_len=int(self.settings.chat_max_reply_length)).replace("(", "").replace(")", "")
            out2 = self.announce(f"@{player} {safe2}")
            self.log_buffer.append(
                f"[Prefect] chat_send_retry ok={out2.get('ok')} sent={out2.get('sent')} err={out2.get('error')}"
            )

    def get_status(self) -> dict:
        st = self.controller.status()
        port_open = None
        if self.settings.game_port is not None:
            port_open = tcp_port_open(self.settings.game_host, self.settings.game_port)
        return {
            **asdict(st),
            "prefect_uptime_seconds": max(0.0, time.time() - self._start_time),
            "log_buffer_lines": len(self.log_buffer.get_recent(self.settings.log_buffer_lines)),
            "game_host": self.settings.game_host,
            "game_port": self.settings.game_port,
            "game_port_open": port_open,
        }

    def get_recent_logs(self, n: int) -> list[str]:
        # Clamp n to avoid huge responses.
        n = max(0, min(int(n), 5000))
        return self.log_buffer.get_recent(n)

    def run_command(self, command: str) -> dict:
        try:
            cmd = sanitize_command(command, max_length=self.settings.max_command_length)
            self.allowlist.require_allowed(cmd)

            result = self.command_runner.run(cmd)
            if result.ok:
                return {"ok": True, "output": result.output}
            return {"ok": False, "error": result.error or "Command failed", "output": result.output}

        except (UnsafeInputError, CommandNotPermittedError) as exc:
            return {"ok": False, "error": str(exc), "output": ""}

    def announce(self, message: str) -> dict:
        try:
            msg = sanitize_announce(message, max_length=self.settings.max_announce_length)
        except UnsafeInputError as exc:
            return {"ok": False, "sent": False, "error": str(exc)}

        templates = [t.strip() for t in (self.settings.announce_command_templates or "").split(",") if t.strip()]
        if not templates:
            templates = ["say {message}"]

        last_err: str | None = None
        last_out: str | None = None

        for tmpl in templates:
            cmd = tmpl.replace("{message}", msg)
            resp = self.run_command(cmd)
            last_out = resp.get("output", "")
            if resp.get("ok") and not _looks_like_unknown_command(last_out):
                # Emit a local chat event so UI can show server messages even if the server doesn't echo them.
                self._on_chat_line("Server", msg)
                return {"ok": True, "sent": True}
            last_err = resp.get("error") or last_err

        self.log_buffer.append(f"[Prefect] announce_failed (not sent): err={last_err} out={str(last_out)[:120]}")
        return {"ok": False, "sent": False, "error": last_err or "Unable to post message to server chat"}

    def startup_reply(self, reply: str) -> dict:
        """Send a very-limited reply for interactive server startup prompts."""

        try:
            cleaned = sanitize_startup_reply(reply, max_length=self.settings.max_startup_reply_length)
            # Bypass allowlist (answers are intentionally constrained).
            output = self.command_runner.run(cleaned)
            return {"ok": output.ok, "output": output.output, "error": output.error}
        except UnsafeInputError as exc:
            return {"ok": False, "error": str(exc), "output": ""}

    def wait_until_ready(self, *, timeout_seconds: float = 60.0) -> dict:
        """Best-effort readiness check based on logs.

        Since exact Necesse messages vary, we look for common markers.
        """

        start = time.time()
        patterns = (
            "server started",
            "listening",
            "done",
            "loaded",
            "world",
        )
        while time.time() - start < timeout_seconds:
            recent = "\n".join(self.get_recent_logs(200)).lower()
            if any(p in recent for p in patterns):
                return {"ok": True, "ready": True}
            time.sleep(0.5)
        return {"ok": True, "ready": False, "error": "Timed out waiting for ready markers in logs"}

    async def summarize_recent_logs(self, n: int = 50) -> dict:
        lines = self.get_recent_logs(n)
        system_prompt = (
            "You are Prefect, a careful server steward for a Necesse dedicated server. "
            "Summarize logs concisely. If errors appear, call them out. Do not invent facts."
        )
        user_prompt = "Summarize these server log lines:\n\n" + "\n".join(lines)

        try:
            text = await self.ollama.generate(system_prompt, user_prompt)
            return {"ok": True, "summary": text.strip()}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "summary": ""}


def ensure_started(core: PrefectCore) -> PrefectCore:
    core.start()
    return core
