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


def get_settings() -> PrefectSettings:
    return PrefectSettings()
