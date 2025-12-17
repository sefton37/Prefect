#!/usr/bin/env bash
set -euo pipefail

cd /home/kellogg/dev/Prefect

if [[ ! -d .venv ]]; then
  echo "Missing .venv. Create it first: python3 -m venv .venv" >&2
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

export PREFECT_SERVER_ROOT="${PREFECT_SERVER_ROOT:-/home/kellogg/necesse-server}"
export PREFECT_OLLAMA_URL="${PREFECT_OLLAMA_URL:-http://127.0.0.1:11434}"
export PREFECT_MODEL="${PREFECT_MODEL:-llama3.1}"

# Server control mode:
# - managed: Prefect starts/owns the Necesse process
# - tmux: Prefect attaches and sends commands to an existing tmux target
export PREFECT_CONTROL_MODE="${PREFECT_CONTROL_MODE:-managed}"
export PREFECT_TMUX_TARGET="${PREFECT_TMUX_TARGET:-necesse}"

# Optional: file log tail mode
# export PREFECT_LOG_PATH="/home/kellogg/necesse-server/logs/latest.log"

# MCP transport. Most MCP hosts use stdio.
export PREFECT_MCP_TRANSPORT="${PREFECT_MCP_TRANSPORT:-stdio}"
export PREFECT_MCP_HOST="${PREFECT_MCP_HOST:-127.0.0.1}"
export PREFECT_MCP_PORT="${PREFECT_MCP_PORT:-8765}"

# Start the MCP server.
python -m prefect.mcp.server
