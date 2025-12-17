# Prefect (Necesse Steward)

Prefect is a server-side AI steward for a Necesse dedicated server.

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

## Requirements
- Ubuntu host
- Python 3.11+
- Ollama already running locally
- Necesse server files in `/home/kellogg/necesse-server`

## Setup
```bash
cd /home/kellogg/dev/Prefect
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
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

## Run
```bash
./scripts/run_prefect.sh
```

If you are switching to **managed mode**, stop any already-running Necesse dedicated server first (to avoid port conflicts), then start Prefect.

If you want Prefect to manage the Necesse server process, ensure your server directory contains either:
- `StartServer.sh` (your current setup), or
- `Server.sh`, or
- `Server.jar` (fallback, runs via `java -jar`)

## MCP tools
- `prefect.get_status()` -> dict
- `prefect.get_recent_logs(n: int)` -> list[str]
- `prefect.run_command(command: str)` -> dict `{ "ok": bool, "output": str }`
- `prefect.announce(message: str)` -> dict `{ "ok": bool, "sent": bool }`
- `prefect.summarize_recent_logs(n: int = 50)` -> dict `{ "ok": bool, "summary": str }`

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
