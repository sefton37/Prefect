from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PrefectSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PREFECT_", extra="ignore")

    server_root: Path = Field(default=Path("/home/kellogg/necesse-server"))
    log_path: Path | None = Field(default=None)
    commands_file: Path | None = Field(default=Path("commands.json"))

    ollama_url: str = Field(default="http://127.0.0.1:11434")
    model: str = Field(default="llama3.1")

    # MCP transport options. Many MCP hosts use stdio; some support HTTP/SSE.
    mcp_transport: str = Field(default="stdio")  # "stdio" | "sse"
    mcp_host: str = Field(default="127.0.0.1")
    mcp_port: int = Field(default=8765)

    # If true, Prefect starts and owns the Necesse server process.
    # If false, Prefect runs in log-only mode (commands will fail unless you later add an attach mode).
    start_server: bool = Field(default=True)

    # Log buffer
    log_buffer_lines: int = Field(default=2000)

    # Optional connectivity probe (set port to enable)
    game_host: str = Field(default="127.0.0.1")
    game_port: int | None = Field(default=None)

    # Chat mention responder (minimal trigger)
    chat_mention_enabled: bool = Field(default=True)
    chat_mention_keyword: str = Field(default="prefect")
    chat_cooldown_seconds: float = Field(default=5.0)
    chat_max_reply_length: int = Field(default=240)
    chat_context_turns: int = Field(default=6)
    chat_context_ttl_seconds: float = Field(default=180.0)
    
    # Persistent conversation history settings
    conversation_max_messages: int = Field(default=100)  # Max messages to store per player
    conversation_max_age_days: int = Field(default=30)   # How long to keep conversation history

    # How Prefect posts messages into in-game chat.
    # Comma-separated templates, tried in order. Use {message}.
    # Example: "say {message},announce {message}"
    announce_command_templates: str = Field(default="say {message}")

    # Command capture window
    command_output_window_seconds: float = Field(default=2.0)

    # Server control mode
    # - "managed": Prefect starts/owns the Necesse server process (stdin/stdout)
    # - "tmux": Prefect attaches to an existing tmux session and sends keys
    control_mode: str = Field(default="managed")
    tmux_target: str = Field(default="necesse")

    # Safety limits
    max_command_length: int = Field(default=200)
    max_announce_length: int = Field(default=300)
    max_startup_reply_length: int = Field(default=8)

    # Debug mode - enables verbose logging of all server output
    debug_verbose: bool = Field(default=True)

    # Welcome messages for player joins
    welcome_messages_enabled: bool = Field(default=True)
    welcome_cooldown_seconds: float = Field(default=60.0)

    # Command discovery settings
    discovery_max_help_pages: int = Field(default=50)
    discovery_page_stable_limit: int = Field(default=2)
    discovery_help_cmd: str = Field(default="help")
    discovery_help_page_template: str = Field(default="help {page}")
    discovery_inter_page_delay: float = Field(default=0.5)

    # Discovery output paths (relative to server_root)
    discovery_output_dir: str = Field(default="prefect")
    discovery_snapshots_subdir: str = Field(default="snapshots")
    discovery_allowlist_subdir: str = Field(default="allowlist")


def get_settings() -> PrefectSettings:
    return PrefectSettings()
