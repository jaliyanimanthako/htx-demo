#!/usr/bin/env bash
# Show which services are running:   bash server/status.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/server/_lib.sh"

show() {   # show <name> <port file> <health path>
  local port="-" state="DOWN"
  if [ -f "$2" ]; then
    port="$(cat "$2")"
    http_up "http://localhost:$port$3" && state="up"
  fi
  local where=""
  if command -v tmux >/dev/null 2>&1 && tmux has-session -t "$1" 2>/dev/null; then
    where="tmux session '$1'"
  elif [ -f "$ROOT/logs/$1.pid" ] && kill -0 "$(cat "$ROOT/logs/$1.pid")" 2>/dev/null; then
    where="pid $(cat "$ROOT/logs/$1.pid")"
  fi
  local col="$C_RED"; [ "$state" = "up" ] && col="$C_GREEN"
  printf "  %s%s%s %s%-8s%s %s%-5s%s port %-6s %s%s%s\n" "$col" "$UI_DOT" "$C_OFF" "$C_BOLD" "$1" "$C_OFF" \
    "$col" "$state" "$C_OFF" "$port" "$C_DIM" "$where" "$C_OFF"
}

ui_head "Services"
show qwen    "$HOME/qwen-vllm/port"       /v1/models
show locate  "$HOME/locate-anything/port" /health
show backend "$HOME/exit-monitor/port"    /api/health
echo
nvidia-smi --query-gpu=index,memory.used,memory.total,utilization.gpu --format=csv,noheader \
  | sed 's/^/  GPU /'
echo "Logs: $ROOT/logs/<name>.log"
