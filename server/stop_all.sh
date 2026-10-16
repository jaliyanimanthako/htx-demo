#!/usr/bin/env bash
# Stop everything started by start_all.sh:   bash server/stop_all.sh [qwen|locate|backend ...]
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMES=("$@")
[ ${#NAMES[@]} -eq 0 ] && NAMES=(backend locate qwen)

kill_tree() {   # TERM a process and all its descendants (children first)
  local pid="$1" child
  for child in $(pgrep -P "$pid" 2>/dev/null); do kill_tree "$child"; done
  kill -TERM "$pid" 2>/dev/null
}

for name in "${NAMES[@]}"; do
  stopped=0
  if command -v tmux >/dev/null 2>&1 && tmux has-session -t "$name" 2>/dev/null; then
    tmux send-keys -t "$name" C-c          # let the server shut down cleanly first
    sleep 3
    tmux kill-session -t "$name" 2>/dev/null
    stopped=1
  fi
  pidfile="$ROOT/logs/$name.pid"
  if [ -f "$pidfile" ]; then
    pid="$(cat "$pidfile")"
    kill_tree "$pid"
    kill -TERM -- "-$pid" 2>/dev/null        # whole process group, if it has its own
    rm -f "$pidfile"
    stopped=1
  fi
  if [ "$stopped" = 1 ]; then echo "  stopped $name"; else echo "  $name was not running"; fi
done
