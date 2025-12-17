#!/usr/bin/env bash
set -euo pipefail

# Convenience wrapper so you can run: ./run_prefect_gui.sh
exec "$(dirname "$0")/scripts/run_prefect_gui.sh"
