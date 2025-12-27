# Prefect (Necesse Steward)

Prefect is a server-side AI steward that can manage or attach to game servers and expose control via MCP and a local Qt GUI.

Phase 1 provides:
- Managed server process control (run Necesse under Prefect)
- Rolling log buffer + recent log retrieval
- Safe command execution (strict allowlist + sanitization)
- Local Ollama integration (HTTP to `127.0.0.1:11434`)
- MCP tool server exposing:
  - `prefect.get_status`
  - `prefect.get_recent_logs`
  - `prefect.run_command`
  - `prefect.announce`
  - `prefect.summarize_recent_logs` (helper tool for Phase 1 DoD)

Minecraft Bedrock Dedicated Server (BDS) support includes:
- Start/stop + command sending via **tmux** (default)
- Optional managed subprocess mode (stdin/stdout)
- Safe in-place updater that preserves `worlds/` and common config files

## Requirements
### Host / system dependencies
- Linux host (tested on Ubuntu)
- Python 3.11+ (3.12 is fine)
- `tmux` (required for the default Minecraft Bedrock control mode)
  - Ubuntu/Debian: `sudo apt-get update && sudo apt-get install -y tmux`
- Java runtime (only required if your Necesse install uses `Server.jar`)

### External services (optional)
- Ollama running locally (only required for AI chat responses / summaries)
  - Default: `PREFECT_OLLAMA_URL=http://127.0.0.1:11434`

### Game server files
- Necesse dedicated server files (default): `/home/kellogg/necesse-server`
- Minecraft Bedrock Dedicated Server files (default): `/home/kellogg/bedrock_server`

## Setup
```bash
cd /home/kellogg/dev/Prefect
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

Install optional extras:
```bash
# GUI
pip install -e '.[gui]'

# Dev/test
pip install -e '.[dev]'
```

## Configuration
Environment variables (defaults shown):
- `PREFECT_SERVER_ROOT=/home/kellogg/necesse-server`
- `PREFECT_OLLAMA_URL=http://127.0.0.1:11434`
- `PREFECT_MODEL=llama3.1`
- `PREFECT_LOG_PATH=` (optional; if set, Prefect tails this file)
- `PREFECT_CONTROL_MODE=managed` (or `tmux`)
- `PREFECT_TMUX_TARGET=necesse` (tmux target like `session:window.pane`)
- `PREFECT_MCP_TRANSPORT=stdio` (or `sse` if supported by your MCP host)
- `PREFECT_MCP_HOST=127.0.0.1`
- `PREFECT_MCP_PORT=8765`

### Minecraft Bedrock settings
- `PREFECT_BEDROCK_SERVER_ROOT=/home/kellogg/bedrock_server`
- `PREFECT_BEDROCK_CONTROL_MODE=tmux` (default; or `managed`)
- `PREFECT_BEDROCK_TMUX_TARGET=bedrock`
- `PREFECT_BEDROCK_START_SERVER=false`
  - If `true` and in `tmux` mode, Prefect creates the tmux session and starts BDS inside it.
  - If `true` and in `managed` mode, Prefect starts BDS as a subprocess.
- `PREFECT_BEDROCK_LOG_PATH=`
  - Optional. In `tmux` mode, if unset, Prefect will pipe tmux output into:
    - `<bedrock_server_root>/prefect_bedrock_tmux.log`

To control how Prefect posts messages into Minecraft Bedrock in-game chat:
- `PREFECT_BEDROCK_ANNOUNCE_COMMAND_TEMPLATES="say {message}"`

## Chat mention responder
If enabled, Prefect watches server logs for player chat lines that mention "Prefect" (case-insensitive) and replies using Ollama.

Env vars:
- `PREFECT_CHAT_MENTION_ENABLED=true`
- `PREFECT_CHAT_MENTION_KEYWORD=prefect`
- `PREFECT_CHAT_COOLDOWN_SECONDS=5`
- `PREFECT_CHAT_MAX_REPLY_LENGTH=240`

## Debug / Diagnostic Mode
To see exactly what Prefect is parsing from server output (useful for verifying player join/leave detection, chat parsing, etc.):

- `PREFECT_DEBUG_VERBOSE=true` (default: `true`)

When enabled, every line from the server is logged with `[DEBUG:RAW]` prefix, plus detailed diagnostics:
- `[DEBUG:CHAT_PARSED]` - Successfully parsed chat messages
- `[DEBUG:CHAT_PARSE_FAILED]` - Lines containing the keyword that couldn't be parsed
- `[DEBUG:JOIN_CHECK]` / `[DEBUG:JOIN_MATCH]` - Join detection attempts
- `[DEBUG:LEAVE_CHECK]` / `[DEBUG:LEAVE_MATCH]` - Leave detection attempts
- `[DEBUG:PLAYER_JOINED]` / `[DEBUG:PLAYER_LEFT]` - Confirmed player activity

This helps verify that Prefect correctly recognizes your Necesse server's log format.

To control how Prefect posts messages into in-game chat (varies by server command set):
- `PREFECT_ANNOUNCE_COMMAND_TEMPLATES="say {message}"`

If `say` doesn’t show up in-game on your server, set multiple templates (comma-separated) and Prefect will try them in order:
- `PREFECT_ANNOUNCE_COMMAND_TEMPLATES="say {message},announce {message},broadcast {message}"`

## Run
```bash
./scripts/run_prefect.sh
```

That starts the **headless MCP server** (stdio transport by default), so it will appear to “hang” waiting for an MCP client.

## Run (GUI)
Install GUI deps (one-time):
```bash
source .venv/bin/activate
pip install -e '.[gui]'
```

Launch the GUI:
```bash
./run_prefect_gui.sh
```

The GUI provides three main tabs:
1. **Necesse**: Start/Stop the server, view console output, send server commands, and troubleshoot zombie processes.
2. **Chat**: Interact with the AI agent. Works even if the game server is offline. Includes toggles to filter Necesse game chat, System messages, and Admin/AI chat.
3. **Admin**: Configure Ollama connection settings and manage AI Personas.

### Persona Management
The Admin tab includes a full Persona editor where you can:
- **System Prompt**: Define core instructions for the AI (what it is, what it should do).
- **Personality Description**: Customize how the AI behaves and responds.
- **Parameter Tuning**: Adjust generation parameters with plain-English sliders:
  - *Creativity (Temperature)*: Lower = focused, Higher = creative
  - *Response Diversity (Top P)*: Controls vocabulary breadth
  - *Vocabulary Limit (Top K)*: Restricts token consideration
  - *Repetition Penalty*: Discourages repeated phrases
- **Save/Load Personas**: Create named profiles and switch between them.

Personas are stored in `~/.config/prefect/personas.json` and persist between sessions.

If you are switching to **managed mode**, stop any already-running Necesse dedicated server first (to avoid port conflicts), then start Prefect.

If you want Prefect to manage the Necesse server process, ensure your server directory contains either:
- `StartServer-nogui.sh` (preferred; headless), or
- `StartServer.sh` (may launch a GUI on some setups), or
- `Server.sh`, or
- `Server.jar` (fallback, runs via `java -jar`)

## MCP tools
- `prefect.get_status()` -> dict
- `prefect.get_recent_logs(n: int)` -> list[str]
- `prefect.run_command(command: str)` -> dict `{ "ok": bool, "output": str }`
- `prefect.announce(message: str)` -> dict `{ "ok": bool, "sent": bool }`
- `prefect.summarize_recent_logs(n: int = 50)` -> dict `{ "ok": bool, "summary": str }`
- `prefect.bootstrap_allowlist()` -> dict (see Command Discovery below)

### Server command tools
Prefect also auto-registers one MCP tool per server command listed in `commands.json`:
- `prefect.cmd.<command>(args: str = "")`

Example (if `commands.json` contains `"ban"`):
- call `prefect.cmd.ban(args="PlayerName")`

To add more commands, edit `commands.json` and restart Prefect.

## Command Discovery

Prefect can automatically discover all available server commands via paginated help output.

### How it works
1. Runs `help` command and parses output for command entries
2. Iterates through pages (`help 2`, `help 3`, etc.) until:
   - Output is empty
   - Same page repeats (pagination loop detected)
   - No new commands found for 2 consecutive pages
   - Max pages reached (default: 50)
3. Classifies each command by safety:
   - **Safe (allowed)**: `help`, `status`, `players`, `list`, `info`, etc.
   - **Messaging (restricted)**: `say`, `announce`, `broadcast` (length-limited)
   - **Dangerous (denied)**: `kick`, `ban`, `save`, `give`, `tp`, `op`, etc.
4. Generates allowlist files under `<server_root>/prefect/`

### Running Discovery
```python
# Via MCP tool
result = prefect.bootstrap_allowlist()
# Returns: { ok, commands_discovered, allowed_count, denied_count, ... }
```

### Output Files
- `prefect/snapshots/commands-YYYYMMDD-HHMMSS.json` - Full discovery snapshot
- `prefect/allowlist/allowlist-active.json` - Generated allowlist
- `prefect/allowlist/allowlist-active.md` - Human-readable report

### Configuration
- `PREFECT_DISCOVERY_MAX_HELP_PAGES=50` - Hard cap on pages to try
- `PREFECT_DISCOVERY_PAGE_STABLE_LIMIT=2` - Stop after N pages with no new commands
- `PREFECT_DISCOVERY_HELP_CMD=help` - Base help command
- `PREFECT_DISCOVERY_HELP_PAGE_TEMPLATE=help {page}` - Template for paginated help

Safety note: Prefect is built for legitimate server administration. It does not include or document cheats/exploit tooling.

## Safety model
- Default deny for commands.
- Strict input sanitization disallows shell metacharacters and chaining.
- Hard limits:
  - commands: 200 chars
  - announce: 300 chars

## Dev workflow
Initialize git and push to GitHub:
```bash
git init
git add -A
git commit -m "Initial Prefect scaffold"
# then create a GitHub repo and add origin
git remote add origin <your_repo_url>
git push -u origin main
```
