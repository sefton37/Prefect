from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
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


class PrefectCore:
    def __init__(self, settings: PrefectSettings | None = None):
        self.settings = settings or get_settings()

        self.command_names = load_command_names(self.settings.commands_file)

        self.log_buffer = RollingLogBuffer(max_lines=self.settings.log_buffer_lines)

        self.process_manager = NecesseProcessManager(
            server_root=self.settings.server_root,
            log_buffer=self.log_buffer,
            command_output_window_seconds=self.settings.command_output_window_seconds,
            log_path=self.settings.log_path,
            on_chat_mention=self._on_chat_mention if self.settings.chat_mention_enabled else None,
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

        self.allowlist = CommandAllowlist.default(extra_prefixes=tuple(extra_prefixes))
        self.ollama = OllamaClient(OllamaConfig(base_url=self.settings.ollama_url, model=self.settings.model))

        self._started = False
        self._start_time = time.time()

        self._chat_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._chat_thread: threading.Thread | None = None
        self._chat_stop = threading.Event()
        self._last_player_reply_ts: dict[str, float] = {}

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

        system_prompt = (
            "You are Prefect, a helpful steward AI for a Necesse dedicated server. "
            "Reply briefly and politely. Do not give hacking/cheating advice. "
            "If asked to do admin actions, explain you can only do safe server admin commands."
        )
        user_prompt = f"Player {player} said: {msg}\nReply as Prefect in 1-2 sentences.".strip()

        # Call async Ollama from this thread.
        self.log_buffer.append(f"[Prefect] generating_reply player={player}")
        reply = asyncio.run(self.ollama.generate(system_prompt, user_prompt))
        safe = _coerce_safe_chat_text(reply, max_len=int(self.settings.chat_max_reply_length))

        # Try to send to server chat.
        self.log_buffer.append(f"[Prefect] sending_reply_to_chat player={player}")
        out = self.announce(f"{player}: {safe}")
        if not out.get("ok"):
            # Last resort: strip harder and try again.
            safe2 = _coerce_safe_chat_text(safe, max_len=int(self.settings.chat_max_reply_length)).replace("(", "").replace(")", "")
            self.announce(f"{player}: {safe2}")

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

        # Best-effort mapping to server "say".
        if self.allowlist.is_allowed(f"say {msg}"):
            resp = self.run_command(f"say {msg}")
            return {"ok": resp.get("ok", False), "sent": resp.get("ok", False), "error": resp.get("error")}

        # If say isn't allowlisted, acknowledge locally.
        self.log_buffer.append(f"[Prefect] announce (not sent to server): {msg}")
        return {"ok": True, "sent": False}

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
