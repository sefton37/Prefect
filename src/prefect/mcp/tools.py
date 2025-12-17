from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict

from prefect.config import PrefectSettings, get_settings
from prefect.ollama_client import OllamaClient, OllamaConfig
from prefect.safety.allowlist import CommandAllowlist, CommandNotPermittedError
from prefect.safety.sanitizer import UnsafeInputError, sanitize_announce, sanitize_command
from prefect.server_control.command_runner import CommandRunner
from prefect.server_control.controller import ManagedController, TmuxAttachController
from prefect.server_control.process_manager import NecesseProcessManager
from prefect.watchers.log_tail import RollingLogBuffer

logger = logging.getLogger(__name__)


class PrefectCore:
    def __init__(self, settings: PrefectSettings | None = None):
        self.settings = settings or get_settings()

        self.log_buffer = RollingLogBuffer(max_lines=self.settings.log_buffer_lines)

        self.process_manager = NecesseProcessManager(
            server_root=self.settings.server_root,
            log_buffer=self.log_buffer,
            command_output_window_seconds=self.settings.command_output_window_seconds,
            log_path=self.settings.log_path,
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

        self.allowlist = CommandAllowlist.default()
        self.ollama = OllamaClient(OllamaConfig(base_url=self.settings.ollama_url, model=self.settings.model))

        self._started = False
        self._start_time = time.time()

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

    def get_status(self) -> dict:
        st = self.controller.status()
        return {
            **asdict(st),
            "prefect_uptime_seconds": max(0.0, time.time() - self._start_time),
            "log_buffer_lines": len(self.log_buffer.get_recent(self.settings.log_buffer_lines)),
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
