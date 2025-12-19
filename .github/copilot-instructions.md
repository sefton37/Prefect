# Prefect (Necesse Steward) AI Instructions

Prefect is a server-side AI steward for a Necesse dedicated server, exposing control via Model Context Protocol (MCP) and a local Qt GUI.

## Big Picture Architecture

- **Core Hub**: `PrefectCore` (`src/prefect/mcp/tools.py`) orchestrates all components. It manages the server process, log tailing, and command execution.
- **MCP Server**: `src/prefect/mcp/server.py` uses `FastMCP` to expose tools (`prefect.get_status`, `prefect.run_command`, etc.) to MCP clients.
- **Process Control**:
  - **Managed Mode**: `NecesseProcessManager` (`src/prefect/server_control/process_manager.py`) runs the server as a subprocess, capturing stdout/stderr.
  - **Tmux Mode**: `TmuxAttachController` (`src/prefect/server_control/controller.py`) sends keys to an existing tmux session.
- **Safety**: `CommandAllowlist` (`src/prefect/safety/allowlist.py`) and sanitizers enforce strict command policies.
- **UI**: `src/prefect/ui/qt_gui.py` provides a PySide6 interface that wraps `PrefectCore`. It features:
  - **Necesse Tab**: Server control, console, and troubleshooting (kill zombie processes).
  - **Chat Tab**: Offline-capable AI chat with channel filtering.
  - **Admin Tab**: Runtime configuration of Ollama settings and **Persona Management**.

## Player Events System

Prefect tracks player visits and generates personalized welcome messages via `src/prefect/player_events.py`:

- **PlayerTracker**: Persists player data to `~/.config/prefect/players.json`:
  - `first_seen`: Timestamp of first visit
  - `last_seen`: Timestamp of most recent activity
  - `visit_count`: Total number of visits
  - `notes`: Optional admin notes about the player
- **PlayerEventHandler**: Listens for join/leave events and triggers LLM-generated welcome messages:
  - **New players**: Get a welcome explaining what Prefect is
  - **Returning players**: Get a personalized welcome-back message
- **Rate limiting**: 60-second cooldown prevents message spam
- **Config**: `PREFECT_WELCOME_MESSAGES_ENABLED=true/false` to toggle

## Conversation History System

Prefect maintains persistent conversation history per player via `src/prefect/conversation_history.py`:

- **ConversationMessage**: Single message with timestamp, role (player/prefect), and content
- **PlayerConversation**: Full conversation history for one player:
  - `messages`: List of all messages
  - `first_interaction`: When player first spoke to Prefect
  - `total_messages`: Lifetime message count
- **ConversationHistoryStore**: Persistent storage to `~/.config/prefect/conversations.json`:
  - Automatic pruning (configurable max messages and age)
  - Case-insensitive player lookup
  - Context formatting for LLM prompts
- **Config**:
  - `PREFECT_CONVERSATION_MAX_MESSAGES=100`: Max messages stored per player
  - `PREFECT_CONVERSATION_MAX_AGE_DAYS=30`: How long to keep history

When a player mentions Prefect in chat, the LLM prompt includes:
1. Player info (visit count, first seen date)
2. Conversation history count ("You have spoken with X 42 times before")
3. Recent conversation context (last N messages)

## Persona System

Prefect supports customizable AI personas via `src/prefect/persona.py`:

- **Persona**: A named profile containing:
  - `system_prompt`: Core instructions for the AI.
  - `persona_prompt`: Personality/behavior description.
  - `parameters`: LLM generation settings (temperature, top_p, top_k, repeat_penalty).
- **PersonaManager**: Handles loading, saving, and selecting personas. Stored in `~/.config/prefect/personas.json`.
- **Parameter Descriptions**: `PARAMETER_DESCRIPTIONS` provides plain-English labels for UI sliders.

Personas are injected into all Ollama calls (chat responses, log summarization, welcome messages).

## Developer Workflows

- **Environment**: Python 3.11+ with `venv`.
  ```bash
  source .venv/bin/activate
  pip install -e '.[gui]'
  ```
- **Running**:
  - Headless MCP: `./scripts/run_prefect.sh` (uses `prefect-mcp` entrypoint).
  - GUI: `./run_prefect_gui.sh` (uses `prefect-gui` entrypoint).
- **Configuration**:
  - Controlled via environment variables (see `README.md` and `src/prefect/config.py`).
  - Key vars: `PREFECT_SERVER_ROOT`, `PREFECT_CONTROL_MODE`, `PREFECT_OLLAMA_URL`.
  - Debug mode: `PREFECT_DEBUG_VERBOSE=true` logs every server line with detailed parsing info.
- **Adding Commands**:
  - Edit `commands.json` to register new server commands.
  - `PrefectCore` dynamically creates MCP tools for these commands at startup.

## Conventions & Patterns

- **Type Hinting**: Use `from __future__ import annotations` and standard library types (`list[str]`, `dict`, `tuple`).
- **Settings**: Use `pydantic-settings` in `src/prefect/config.py`. Access via `get_settings()`.
- **Logging**: Use standard `logging.getLogger(__name__)`.
- **Persistence**: The GUI uses `QSettings` to persist user preferences (Ollama URL, Model) across sessions.
- **Async/Sync**: Most core logic is synchronous (threaded). `summarize_recent_logs` is `async`.
- **Agent Threading**: The AI chat agent runs in a separate thread (`start_agent` in `PrefectCore`) to allow offline interaction without blocking the GUI.
- **Safety First**: Always validate inputs using `prefect.safety` modules before execution.
- **Imports**: Group imports: standard lib, third-party, local `prefect` modules.

## Key Files

- `src/prefect/mcp/server.py`: MCP server entrypoint and tool definitions.
- `src/prefect/mcp/tools.py`: `PrefectCore` logic (the "brain").
- `src/prefect/mcp/command_tools.py`: Dynamic MCP tool generation from discovered commands:
  - `parse_command_syntax()`: Extract parameters from syntax strings
  - `CommandToolDefinition`: Tool schema with required/optional params
  - `CommandToolRegistry`: Registry for lookup by name, category, or risk level
  - `load_tools_from_snapshot()`: Load tools from discovery JSON
- `src/prefect/persona.py`: Persona management (prompts, parameters, profiles).
- `src/prefect/player_events.py`: Player tracking and welcome messages:
  - `PlayerTracker`: Persists player visit history
  - `PlayerEventHandler`: Triggers LLM welcome messages on join
- `src/prefect/conversation_history.py`: Persistent per-player conversation storage:
  - `ConversationHistoryStore`: JSON-backed conversation database
  - `PlayerConversation`: Per-player message history with context formatting
  - Auto-pruning by count and age
- `src/prefect/server_control/process_manager.py`: Subprocess handling and log parsing.
- `src/prefect/safety/allowlist.py`: Command permission logic.
- `src/prefect/discovery/`: Command discovery module:
  - `parser.py`: ANSI stripping, normalization, heuristic command extraction
  - `registry.py`: CommandEntry, CommandRegistry, DiscoverySnapshot data models
  - `discoverer.py`: Paginated help iteration with loop detection
  - `bootstrap.py`: Safety classification and allowlist generation
- `commands.json`: Configuration for allowed server commands.

## Command Tools System

The command tools system allows LLMs to interact with Necesse server commands:

1. **Discovery**: Run `prefect.bootstrap_allowlist()` to scan all server commands via paginated help
2. **Snapshots**: Commands stored in `~/.config/prefect/snapshots/commands-YYYYMMDD-HHMMSS.json`
3. **Tool Generation**: `CommandToolRegistry` creates MCP-compatible tools from snapshots
4. **Intent Mapping**: LLMs can select tools based on user intent (e.g., "who is online?" → `players`)
5. **Risk Levels**: Commands categorized as `safe`, `moderate`, or `dangerous`
6. **Categories**: `info`, `moderation`, `player`, `items`, `environment`, `team`, `admin`, `world`, `general`

### Parameter Syntax

Commands use angle brackets for required params and square brackets for optional:
- `<player>` - Required parameter
- `[<player>]` - Optional parameter
- `<start/clear>` - Required with choices
- `[<page/command>]` - Optional with choices

## Integration Details

- **Necesse Server**: Requires `StartServer-nogui.sh` (or similar) in `PREFECT_SERVER_ROOT`.
- **Ollama**: Local integration via HTTP for chat replies and log summarization.
- **MCP**: Uses `mcp` library. Tools are namespaced with `prefect.`.
