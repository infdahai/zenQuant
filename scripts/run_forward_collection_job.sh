#!/bin/bash
set -euo pipefail

REPO_ROOT="/Users/disaster/workspace/code/zenQuant"
LOG_DIR="$REPO_ROOT/logs/forward_collection"
STATUS_DIR="$REPO_ROOT/results/data_readiness"
UV_BIN="/opt/homebrew/bin/uv"
OSASCRIPT_BIN="/usr/bin/osascript"

mkdir -p "$LOG_DIR" "$STATUS_DIR"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_PATH="$LOG_DIR/collector-$STAMP.log"
LATEST_LOG_PATH="$LOG_DIR/collector-latest.log"
STATUS_PATH="$STATUS_DIR/forward_collection_latest.json"

cd "$REPO_ROOT"

{
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] start collector"
  "$UV_BIN" run zenquant-forward-collect
} >"$LOG_PATH" 2>&1 || {
  cp "$LOG_PATH" "$LATEST_LOG_PATH"
  MESSAGE="collector failed"
  if [ -f "$STATUS_PATH" ]; then
    ERROR_TEXT="$(/usr/bin/python3 - <<'PY'
import json
from pathlib import Path

path = Path("/Users/disaster/workspace/code/zenQuant/results/data_readiness/forward_collection_latest.json")
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print("collector failed")
else:
    print(payload.get("error") or "collector failed")
PY
)"
    MESSAGE="$ERROR_TEXT"
  fi
  "$OSASCRIPT_BIN" -e "display notification \"$MESSAGE\" with title \"ZenQuant Collector\""
  exit 1
}

cp "$LOG_PATH" "$LATEST_LOG_PATH"
