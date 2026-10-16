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
    echo "  $name: already running (port $(cat "$pfile"))"
    return
  fi
  rm -f "$pfile"
  : > "$LOG_DIR/$name.log"
  local line="cd '$ROOT' && $cmd 2>&1 | tee '$LOG_DIR/$name.log'; echo __EXITED__ >> '$LOG_DIR/$name.log'"
  if [ "$USE_TMUX" = 1 ]; then
    tmux kill-session -t "$name" 2>/dev/null
    tmux new-session -d -s "$name"
    tmux send-keys -t "$name" "$line" Enter          # runs in a normal login shell
    echo "  $name: started in tmux session '$name'"
  else
    nohup setsid bash -ic "$line" >/dev/null 2>&1 &
    echo $! > "$LOG_DIR/$name.pid"
    echo "  $name: started in the background (pid $(cat "$LOG_DIR/$name.pid"))"
  fi
}

wait_for() {
  local name pfile hpath timeout start last=0
  name="$(field "$1" 1)"; pfile="$(field "$1" 3)"; hpath="$(field "$1" 4)"; timeout="$(field "$1" 5)"
  start=$(date +%s)
  while true; do
    if healthy "$pfile" "$hpath"; then
      echo "  ✓ $name is up (port $(cat "$pfile"), $(( $(date +%s) - start ))s)"
      return 0
    fi
    if ! running "$name"; then
      echo "  ✗ $name stopped. Last lines of $LOG_DIR/$name.log:"
      tail -n 25 "$LOG_DIR/$name.log" | sed 's/^/      /'
      return 1
    fi
    local now=$(( $(date +%s) - start ))
    if [ "$now" -ge "$timeout" ]; then
      echo "  ✗ $name not ready after ${timeout}s — see $LOG_DIR/$name.log"
      return 1
    fi
    if [ $(( now - last )) -ge 30 ]; then
      last=$now
      echo "    … $name still starting (${now}s): $(tail -n 1 "$LOG_DIR/$name.log" 2>/dev/null | cut -c1-110)"
    fi
    sleep 5
  done
}

echo "== Starting (logs in $LOG_DIR) =="
launch "${SERVICES[0]}"
launch "${SERVICES[1]}"
echo
echo "== Waiting for the model servers (first run downloads environments + weights) =="
ok=1
wait_for "${SERVICES[0]}" || ok=0
wait_for "${SERVICES[1]}" || ok=0
[ "$ok" = 1 ] || echo "  (continuing — the backend still starts; sessions need the model servers)"
echo
launch "${SERVICES[2]}"
wait_for "${SERVICES[2]}" || { echo; echo "Backend did not start — fix the error above and rerun."; exit 1; }

TOKEN="$(grep -E '^BACKEND_TOKEN=' "$ROOT/.env" 2>/dev/null | tail -n 1 | cut -d= -f2-)"
PORT="$(cat "$HOME/exit-monitor/port")"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "================================================================"
echo " Ready. On your Mac open:"
echo "   http://${IP:-<server-ip>}:$PORT/?token=${TOKEN:-<token>}"
echo " (not reachable? ssh -L $PORT:localhost:$PORT <user>@<server>, then http://localhost:$PORT/?token=…)"
echo
echo " Status: bash server/status.sh    Stop: bash server/stop_all.sh"
[ "$USE_TMUX" = 1 ] && echo " Look at a service: tmux attach -t qwen | locate | backend  (Ctrl+B then D to leave)"
echo "================================================================"
