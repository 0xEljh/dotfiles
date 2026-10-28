#!/bin/bash

if [ -f "$HOME/.bashrc" ]; then
  source "$HOME/.bashrc"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
LOG_FILE="$SCRIPT_DIR/logs/sync.log"

mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "[$(date)] Starting Sync..."

if ! command -v uv &>/dev/null; then
  echo "uv not found in PATH. Installing..."
  # Note: We rely on the install script adding uv to the PATH for future runs
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # For this run, we need to explicitly add it if installed for the first time
  export PATH="$HOME/.local/bin:$PATH"
fi

cd "$SCRIPT_DIR" || exit

# Propagate failures so dotfiles-sync.service enters failed state and the
# OnFailure= Telegram alert fires; all scripts still run even if one fails.
FAILED=""

uv run aw_notion_sync.py || FAILED="$FAILED aw_notion_sync"
uv run waka_notion_sync.py || FAILED="$FAILED waka_notion_sync"
uv run sync_notion_bread_time_accounting.py || FAILED="$FAILED bread_time_accounting"

YYMMDD="$(date +%y%m%d)"
ANALYTICS_DIR="$HOME/digital-garden/data"
mkdir -p "$ANALYTICS_DIR"
uv run aw_analytics_export.py --output "$ANALYTICS_DIR/${YYMMDD}_aw_analytics.json" || FAILED="$FAILED aw_analytics_export"

if [ -n "$FAILED" ]; then
  echo "[$(date)] Sync Finished with failures:$FAILED"
  exit 1
fi
echo "[$(date)] Sync Finished"
