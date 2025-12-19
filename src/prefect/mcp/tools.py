from __future__ import annotations

import asyncio
import logging
import queue
import subprocess
import threading
import time
from collections import deque
from dataclasses import asdict
from pathlib import Path

from prefect.config import PrefectSettings, get_settings
from prefect.command_catalog import load_command_names
from prefect.conversation_history import ConversationHistoryStore
from prefect.discovery.discoverer import CommandDiscoverer, ConsoleResult
from prefect.discovery.bootstrap import AllowlistBootstrapper, GeneratedAllowlist
from prefect.discovery.registry import DiscoverySnapshot
from prefect.ollama_client import OllamaClient, OllamaConfig
from prefect.persona import PersonaManager, Persona
from prefect.player_events import PlayerTracker, PlayerEventHandler, build_welcome_prompt
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
            debug_verbose=self.settings.debug_verbose,
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

        # Persona management
        self.persona_manager = PersonaManager()

        # Player tracking and welcome messages
        self.player_tracker = PlayerTracker()
        self.player_event_handler = PlayerEventHandler(
            player_tracker=self.player_tracker,
            send_message=self._send_welcome_message,
            generate_message=self._generate_welcome_message,
            enabled=self.settings.welcome_messages_enabled,
        )

        # Persistent conversation history for chat interactions
        self.conversation_store = ConversationHistoryStore(
            max_messages_per_player=self.settings.conversation_max_messages,
            max_message_age_days=self.settings.conversation_max_age_days,
        )

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
        
        # Handle player join/leave for welcome messages
        if kind == "join":
            # Run in a thread to avoid blocking the log parser
            threading.Thread(
                target=self._handle_player_join,
                args=(who,),
                name=f"prefect-welcome-{who}",
                daemon=True,
            ).start()
        elif kind == "leave":
            self.player_event_handler.on_player_leave(who)
    
    def _handle_player_join(self, player_name: str) -> None:
        """Handle a player join event (runs in background thread)."""
        try:
            message = self.player_event_handler.on_player_join(player_name)
            if message:
                self.log_buffer.append(f"[Prefect] Welcomed {player_name}: {message[:100]}")
        except Exception as e:
            logger.error("Failed to handle player join for %s: %s", player_name, e)
    
    def _send_welcome_message(self, message: str) -> None:
        """Send a welcome message to the server chat."""
        try:
            safe = _coerce_safe_chat_text(message, max_len=int(self.settings.chat_max_reply_length))
            result = self.announce(safe)
            if not result.get("ok"):
                logger.warning("Failed to send welcome message: %s", result.get("error"))
        except Exception as e:
            logger.error("Failed to send welcome message: %s", e)
    
    def _generate_welcome_message(self, system_prompt: str, user_prompt: str) -> str:
        """Generate a welcome message using Ollama."""
        persona = self.persona_manager.get_active_persona()
        full_system = persona.get_full_system_prompt()
        params = persona.parameters
        
        try:
            reply = asyncio.run(self.ollama.generate(
                full_system,
                user_prompt,
                temperature=params.temperature,
                top_p=params.top_p,
                top_k=params.top_k,
                repeat_penalty=params.repeat_penalty,
            ))
            return reply
        except Exception as e:
            logger.error("Ollama generate failed for welcome message: %s", e)
            return ""

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

    def start_agent(self) -> None:
        """Start background agent services (chat watcher, etc.) independent of game server."""
        if self.settings.chat_mention_enabled:
            self._start_chat_thread()

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

        # Start agent services
        self.start_agent()

        if (self.settings.control_mode or "managed").lower() == "tmux":
            # In tmux mode, we don't start the server process but we still want
            # to tail logs for chat mentions and player events
            self.process_manager.start_log_tailing()
            return

        if self.settings.start_server:
            try:
                self.process_manager.start()
            except Exception as exc:
                # Keep running so log-only mode can still work (e.g., if log_path is set).
                logger.error("Failed to start Necesse server process: %s", exc)


    def start_server(self) -> dict:
        """Start the Necesse server process (managed mode only)."""

        self.start()

        mode = (self.settings.control_mode or "managed").lower()
        if mode == "tmux":
            return {"ok": False, "error": "control_mode=tmux; cannot start server process"}

        try:
            self.process_manager.start()
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def stop_server(self) -> dict:
        """Stop the Necesse server process (managed mode only)."""

        mode = (self.settings.control_mode or "managed").lower()
        if mode == "tmux":
            return {"ok": False, "error": "control_mode=tmux; cannot stop server process"}

        try:
            self.process_manager.stop()
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def kill_zombie_processes(self) -> dict:
        """Force kill any lingering Necesse server processes."""
        # We target common patterns for the Necesse server.
        # 1. "Server.jar" (the actual game)
        # 2. "StartServer" (the launcher script)
        
        killed = []
        try:
            # Try killing java processes running Server.jar
            subprocess.run(["pkill", "-f", "Server.jar"], check=False)
            killed.append("Server.jar")
            
            # Try killing the launcher script
            subprocess.run(["pkill", "-f", "StartServer"], check=False)
            killed.append("StartServer")
            
            return {"ok": True, "message": f"Sent kill signals to: {', '.join(killed)}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # === Command Discovery ===

    def _run_command_for_discovery(self, cmd: str) -> ConsoleResult:
        """Execute a command and return result for discovery (no allowlist check)."""
        try:
            output = self.command_runner.run(cmd)
            return ConsoleResult(
                stdout=output.output or "",
                exit_ok=output.ok,
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            )
        except Exception as e:
            return ConsoleResult(
                stdout=str(e),
                exit_ok=False,
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            )

    def bootstrap_allowlist(self) -> dict:
        """Discover server commands and generate a default-deny allowlist.
        
        This method:
        1. Runs paginated help to discover all available commands
        2. Parses and classifies each command
        3. Generates allowlist with safe commands enabled
        4. Saves snapshot and allowlist files
        
        Returns:
            Dictionary with discovery results and file paths.
        """
        if not self.controller.status().running:
            return {
                "ok": False,
                "error": "Server is not running. Start the server before running discovery.",
            }

        # Build output paths
        base_dir = Path(self.settings.server_root) / self.settings.discovery_output_dir
        snapshots_dir = base_dir / self.settings.discovery_snapshots_subdir
        allowlist_dir = base_dir / self.settings.discovery_allowlist_subdir

        try:
            # Create discoverer
            discoverer = CommandDiscoverer(
                run_command=self._run_command_for_discovery,
                server_root=Path(self.settings.server_root),
                max_help_pages=self.settings.discovery_max_help_pages,
                page_stable_limit=self.settings.discovery_page_stable_limit,
                help_cmd=self.settings.discovery_help_cmd,
                help_page_template=self.settings.discovery_help_page_template,
                inter_page_delay=self.settings.discovery_inter_page_delay,
            )

            # Run discovery
            self.log_buffer.append("[Prefect] Starting command discovery...")
            snapshot, snapshot_path = discoverer.discover_and_save(snapshots_dir)
            
            self.log_buffer.append(
                f"[Prefect] Discovery complete: {len(snapshot.commands)} commands found, "
                f"reason={snapshot.help.termination_reason}"
            )

            # Bootstrap allowlist
            bootstrapper = AllowlistBootstrapper(
                safe_command_max_len=self.settings.max_command_length,
                messaging_max_len=self.settings.max_announce_length,
            )
            
            allowlist, allowlist_json_path, allowlist_md_path = bootstrapper.bootstrap_and_save(
                snapshot,
                allowlist_dir,
                snapshot_path=str(snapshot_path),
            )

            self.log_buffer.append(
                f"[Prefect] Allowlist generated: {len(allowlist.allowed)} allowed, "
                f"{len(allowlist.denied)} denied"
            )

            # Store reference for potential future use
            self._discovered_allowlist = allowlist

            return {
                "ok": True,
                "commands_discovered": len(snapshot.commands),
                "allowed_count": len(allowlist.allowed),
                "denied_count": len(allowlist.denied),
                "termination_reason": snapshot.help.termination_reason,
                "pages_captured": snapshot.help.pages_captured,
                "snapshot_path": str(snapshot_path),
                "allowlist_path": str(allowlist_json_path),
                "report_path": str(allowlist_md_path) if allowlist_md_path else None,
            }

        except Exception as exc:
            logger.exception("Discovery failed")
            self.log_buffer.append(f"[Prefect] Discovery failed: {exc}")
            return {
                "ok": False,
                "error": str(exc),
            }

    def get_discovered_allowlist(self) -> GeneratedAllowlist | None:
        """Get the most recently discovered allowlist, if any."""
        return getattr(self, "_discovered_allowlist", None)

    def load_allowlist_from_file(self, path: Path | str) -> dict:
        """Load an allowlist from a JSON file.
        
        Args:
            path: Path to allowlist JSON file.
            
        Returns:
            Dictionary with load result.
        """
        try:
            path = Path(path)
            if not path.exists():
                return {"ok": False, "error": f"File not found: {path}"}
            
            allowlist = GeneratedAllowlist.load(path)
            self._discovered_allowlist = allowlist
            
            return {
                "ok": True,
                "allowed_count": len(allowlist.allowed),
                "denied_count": len(allowlist.denied),
                "generated_at": allowlist.generated_at,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def set_ollama(self, *, base_url: str | None = None, model: str | None = None) -> None:
        if base_url is not None and base_url.strip():
            self.settings.ollama_url = base_url.strip()
        if model is not None and model.strip():
            self.settings.model = model.strip()
        self.ollama = OllamaClient(OllamaConfig(base_url=self.settings.ollama_url, model=self.settings.model))

    def list_ollama_models(self) -> dict:
        try:
            models = asyncio.run(self.ollama.list_models())
            return {"ok": True, "models": models}
        except Exception as exc:
            return {"ok": False, "models": [], "error": str(exc)}

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

        # Record player message in persistent conversation store
        self.conversation_store.add_player_message(player, msg)
        
        # Get player info for context
        player_record = self.player_tracker.get_player(player)
        player_context = ""
        if player_record:
            player_context = (
                f"Player info: {player} has visited {player_record.visit_count} times, "
                f"first seen {player_record.first_seen_date}.\n"
            )
        
        # Build conversation context from persistent history
        # Use session TTL for "recent" context, but also include older history
        session_ttl = float(self.settings.chat_context_ttl_seconds)
        recent_context = self.conversation_store.get_context_for_player(
            player,
            recent_count=int(self.settings.chat_context_turns),
            session_ttl_seconds=session_ttl,
        )
        
        # Also get some older history for longer-term context (if available)
        full_history = self.conversation_store.get_context_for_player(
            player,
            recent_count=int(self.settings.chat_context_turns) * 2,
            session_ttl_seconds=None,  # No TTL filter
        )
        
        # If there's more history than recent session, mention it
        history_note = ""
        conv_summary = self.conversation_store.get_player_summary(player)
        if conv_summary["total_messages"] > int(self.settings.chat_context_turns):
            history_note = f"(You have spoken with {player} {conv_summary['total_messages']} times before.)\n"

        # Get active persona for prompts and parameters
        persona = self.persona_manager.get_active_persona()
        system_prompt = persona.get_full_system_prompt()
        
        # Build prompt with player context and conversation history
        user_prompt = (
            f"{player_context}"
            f"{history_note}"
            f"Recent conversation:\n{recent_context}\n\n"
            f"Reply as Prefect to {player} in 1-2 sentences."
        ).strip()

        # Call async Ollama from this thread with persona parameters.
        params = persona.parameters
        self.log_buffer.append(f"[Prefect] generating_reply player={player} persona={persona.name} history={conv_summary['total_messages']}")
        try:
            reply = asyncio.run(self.ollama.generate(
                system_prompt, 
                user_prompt,
                temperature=params.temperature,
                top_p=params.top_p,
                top_k=params.top_k,
                repeat_penalty=params.repeat_penalty,
            ))
        except Exception as e:
            logger.warning("Ollama generate failed: %s", e)
            return

        safe = _coerce_safe_chat_text(reply, max_len=int(self.settings.chat_max_reply_length))
        
        # Record Prefect's response in persistent conversation store
        self.conversation_store.add_prefect_response(player, safe)

        # Try to send to server chat.
        self.log_buffer.append(f"[Prefect] sending_reply_to_chat player={player}")
        out = self.announce(f"@{player} {safe}")
        self.log_buffer.append(f"[Prefect] chat_send_result ok={out.get('ok')} sent={out.get('sent')} err={out.get('error')}")
        if not out.get("ok"):
            # If announce failed, check if server is offline.
            # If so, we should still show the reply in the GUI log (local chat).
            if not self.controller.status().running:
                self._on_chat_line("Prefect", f"@{player} {safe}")
                return

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
            # Use a short timeout to avoid blocking the UI thread for too long
            port_open = tcp_port_open(self.settings.game_host, self.settings.game_port, timeout_seconds=0.2)
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

    def get_logs_since(self, ts: float) -> list[tuple[float, str]]:
        return self.log_buffer.get_since_with_ts(ts)


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

                # Check if this announcement should trigger the AI (as if "Admin" said it)
                if self.settings.chat_mention_enabled:
                    keyword = (self.settings.chat_mention_keyword or "prefect").lower()
                    # Relaxed check: trigger if keyword is anywhere in the message
                    if keyword in msg.lower():
                        logger.debug("Triggering AI for announced message: %s", msg)
                        self._on_chat_mention("Admin", msg)

                return {"ok": True, "sent": True}
            last_err = resp.get("error") or last_err

        self.log_buffer.append(f"[Prefect] announce_failed (not sent): err={last_err} out={str(last_out)[:120]}")
        return {"ok": False, "sent": False, "error": last_err or "Unable to post message to server chat"}

    def send_chat_message(self, message: str, origin: str = "Admin") -> dict:
        """Send a chat message. If server is running, announce it. If not, treat as local chat."""
        
        # 1. Try to announce to server if it's running
        announce_result = {"ok": False}
        if self.controller.status().running:
            announce_result = self.announce(message)
        
        # 2. If announce succeeded, it already handled events.
        if announce_result.get("ok"):
            return announce_result

        # 3. If announce failed or server is down, handle locally for Admin/Console
        # This allows chatting with the LLM even if the game server is offline.
        try:
            msg = sanitize_announce(message, max_length=self.settings.max_announce_length)
        except UnsafeInputError as exc:
            return {"ok": False, "sent": False, "error": str(exc)}

        # Record the "local" chat line
        self._on_chat_line(origin, msg)

        # Check for AI trigger
        if self.settings.chat_mention_enabled:
            keyword = (self.settings.chat_mention_keyword or "prefect").lower()
            # Relaxed check: trigger if keyword is anywhere in the message
            if keyword in msg.lower():
                logger.debug("Triggering AI for local message: %s", msg)
                self._on_chat_mention(origin, msg)

        return {"ok": True, "sent": True, "local_only": True}

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
        persona = self.persona_manager.get_active_persona()
        system_prompt = (
            f"{persona.system_prompt}\n\n"
            "Summarize logs concisely. If errors appear, call them out. Do not invent facts."
        )
        user_prompt = "Summarize these server log lines:\n\n" + "\n".join(lines)
        params = persona.parameters

        try:
            text = await self.ollama.generate(
                system_prompt, 
                user_prompt,
                temperature=params.temperature,
                top_p=params.top_p,
                top_k=params.top_k,
                repeat_penalty=params.repeat_penalty,
            )
            return {"ok": True, "summary": text.strip()}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "summary": ""}


def ensure_started(core: PrefectCore) -> PrefectCore:
    core.start()
    return core
