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

export PREFECT_CONTROL_MODE="${PREFECT_CONTROL_MODE:-managed}"
export PREFECT_START_SERVER="${PREFECT_START_SERVER:-true}"

# Optional: enable game port probe
# export PREFECT_GAME_PORT=14159

prefect-gui
