#!/bin/bash
# run.sh

source "$HOME/.bashrc"
# Or if you use zsh:
# source "$HOME/.zshrc"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
LOG_FILE="$SCRIPT_DIR/logs/sync.log"

mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "[$(date)] Starting Sync..."

# 1. Self-Healing: Check for 'uv' only, relying on the source command to find it
if ! command -v uv &>/dev/null; then
  echo "uv not found in PATH. Installing..."
  # Note: We rely on the install script adding uv to the PATH for future runs
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # For this run, we need to explicitly add it if installed for the first time
  export PATH="$HOME/.local/bin:$PATH"
fi

# 2. Execution: Let uv handle the venv and deps
# We change directory to ensure uv finds the sync_wakatime.py and .env file
cd "$SCRIPT_DIR" || exit

uv run waka_notion_sync.py
uv run sync_notion_bread_time_accounting.py
uv run aw_notion_sync.py

YYMMDD="$(date +%y%m%d)"
ANALYTICS_DIR="$HOME/digital-garden/data"
mkdir -p "$ANALYTICS_DIR"
uv run aw_analytics_export.py --output "$ANALYTICS_DIR/${YYMMDD}_aw_analytics.json"

echo "[$(date)] Sync Finished"
