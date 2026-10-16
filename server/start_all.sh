#!/usr/bin/env bash
# One command to run everything on the GPU server:
#
#   bash server/start_all.sh                 # Qwen-4B + LocateAnything + backend
#   QWEN_MODEL=8b-fp8 bash server/start_all.sh
#
# - First run creates the three Python environments automatically (~20–30 min,
#   downloads PyTorch and the model weights); later runs start in ~1–2 min.
# - Each service runs in its own tmux session (qwen, locate, backend) — or as a
#   background process if tmux is not installed — and logs to logs/<name>.log.
# - Creates a backend token in .env if there is none.
# - Waits until everything answers, then prints the address to open on your Mac.
# Already-running services are left alone. Stop with: bash server/stop_all.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/server/_lib.sh"
QWEN_MODEL="${QWEN_MODEL:-4b}"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"
command -v tmux >/dev/null 2>&1 && USE_TMUX=1 || USE_TMUX=0

# name | command | port file | health path | timeout (s)
SERVICES=(
  "qwen|bash server/serve_qwen.sh $QWEN_MODEL|$HOME/qwen-vllm/port|/v1/models|2400"
  "locate|bash server/serve_locate.sh|$HOME/locate-anything/port|/health|2400"
  "backend|bash server/run_backend.sh|$HOME/exit-monitor/port|/api/health|1800"
)

field() { echo "$1" | cut -d'|' -f"$2"; }

healthy() {   # healthy <port file> <health path>
  [ -f "$1" ] && http_up "http://localhost:$(cat "$1")$2"
}

running() {   # is the service's process still alive?
  local name="$1" log="$LOG_DIR/$1.log"
  if [ "$USE_TMUX" = 1 ]; then
    tmux has-session -t "$name" 2>/dev/null && ! grep -q "__EXITED__" "$log" 2>/dev/null
  else
    [ -f "$LOG_DIR/$name.pid" ] && kill -0 "$(cat "$LOG_DIR/$name.pid")" 2>/dev/null
  fi
}

launch() {
  local name cmd pfile hpath
  name="$(field "$1" 1)"; cmd="$(field "$1" 2)"; pfile="$(field "$1" 3)"; hpath="$(field "$1" 4)"
  if healthy "$pfile" "$hpath"; then
    ui_ok "$name" "already running ${C_DIM}· port $(cat "$pfile")${C_OFF}"
    return
  fi
  rm -f "$pfile"
  : > "$LOG_DIR/$name.log"
  local line="cd '$ROOT' && $cmd 2>&1 | tee '$LOG_DIR/$name.log'; echo __EXITED__ >> '$LOG_DIR/$name.log'"
  if [ "$USE_TMUX" = 1 ]; then
    tmux kill-session -t "$name" 2>/dev/null
    tmux new-session -d -s "$name"
    tmux send-keys -t "$name" "$line" Enter          # runs in a normal login shell
    ui_info "$name launched in tmux session '$name'"
  else
    nohup setsid bash -ic "$line" >/dev/null 2>&1 &
    echo $! > "$LOG_DIR/$name.pid"
    ui_info "$name launched in the background (pid $(cat "$LOG_DIR/$name.pid"))"
  fi
}

show_errors() {   # first error-looking lines + the tail of a log
  local log="$1"
  printf '%s    error lines in %s:%s\n' "$C_DIM" "$log" "$C_OFF"
  grep -n -i -m 8 -E "error|exception|traceback|failed|denied|not found" "$log" \
    | grep -v "__EXITED__" | cut -c1-160 | sed 's/^/      /'
  printf '%s    last lines:%s\n' "$C_DIM" "$C_OFF"
  tail -n 12 "$log" | grep -v "__EXITED__" | cut -c1-160 | sed 's/^/      /'
}

wait_for() {
  local name pfile hpath timeout start now tick=0 msg="" last_plain=0
  name="$(field "$1" 1)"; pfile="$(field "$1" 3)"; hpath="$(field "$1" 4)"; timeout="$(field "$1" 5)"
  start=$(date +%s)
  while true; do
    now=$(( $(date +%s) - start ))
    if [ $(( tick % 20 )) -eq 0 ]; then            # check every ~2 s
      if healthy "$pfile" "$hpath"; then
        ui_ok "$name" "up ${C_DIM}· port $(cat "$pfile") · $(mmss "$now")${C_OFF}"
        return 0
      fi
      if ! running "$name"; then
        ui_fail "$name" "stopped after $(mmss "$now")"
        show_errors "$LOG_DIR/$name.log"
        return 1
      fi
      if [ "$now" -ge "$timeout" ]; then
        ui_fail "$name" "not ready after $(mmss "$timeout") — see $LOG_DIR/$name.log"
        return 1
      fi
      msg="$(last_log_line "$LOG_DIR/$name.log")"
    fi
    if [ "$UI_TTY" = 1 ]; then
      ui_spin "$tick" "$name" "$(mmss "$now")" "${msg:-starting…}"
    elif [ $(( now - last_plain )) -ge 30 ]; then  # log files: a plain line every 30 s
      last_plain=$now
      echo "    … $name still starting ($(mmss "$now")): ${msg:0:110}"
    fi
    sleep 0.1
    tick=$(( tick + 1 ))
  done
}

ui_head "Starting services  ${C_DIM}(logs: $LOG_DIR)${C_OFF}"
launch "${SERVICES[0]}"
launch "${SERVICES[1]}"

ui_head "Waiting for the model servers  ${C_DIM}(first run also builds environments + downloads weights)${C_OFF}"
ok=1
wait_for "${SERVICES[0]}" || ok=0
wait_for "${SERVICES[1]}" || ok=0
[ "$ok" = 1 ] || ui_warn "continuing — the backend still starts, but sessions need the model servers"

ui_head "Backend"
launch "${SERVICES[2]}"
wait_for "${SERVICES[2]}" || { echo; ui_warn "Backend did not start — fix the error above and rerun."; exit 1; }

TOKEN="$(grep -E '^BACKEND_TOKEN=' "$ROOT/.env" 2>/dev/null | tail -n 1 | cut -d= -f2-)"
PORT="$(cat "$HOME/exit-monitor/port")"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
URL="http://${IP:-<server-ip>}:$PORT/?token=${TOKEN:-<token>}"
BAR="$(printf '─%.0s' $(seq 1 64))"
echo
printf '%s%s%s\n' "$C_GREEN" "$BAR" "$C_OFF"
if [ "$ok" = 1 ]; then
  printf '  %s%s All services are up.%s Open on your Mac:\n' "$C_GREEN$C_BOLD" "$UI_OK" "$C_OFF"
else
  printf '  %sBackend is up, but a model server failed (see above).%s Open on your Mac:\n' "$C_YELLOW" "$C_OFF"
fi
printf '\n    %s%s%s\n\n' "$C_BOLD$C_CYAN" "$URL" "$C_OFF"
printf '  %snot reachable? ssh -L %s:localhost:%s <user>@<server>  →  http://localhost:%s/?token=…%s\n' \
  "$C_DIM" "$PORT" "$PORT" "$PORT" "$C_OFF"
printf '  %sstatus: bash server/status.sh   ·   stop: bash server/stop_all.sh%s\n' "$C_DIM" "$C_OFF"
[ "$USE_TMUX" = 1 ] && printf '  %swatch a service: tmux attach -t qwen | locate | backend  (Ctrl+B then D)%s\n' "$C_DIM" "$C_OFF"
printf '%s%s%s\n' "$C_GREEN" "$BAR" "$C_OFF"
