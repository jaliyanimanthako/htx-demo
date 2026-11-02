#!/usr/bin/env bash
# Single-GPU laptop (8 GB, e.g. RTX 4060 Laptop) as the "GPU server": Qwen3-VL-2B only.
#
#   bash server/start_laptop.sh         # Qwen in tmux session 'qwen'; waits until it answers
#
# Qwen-4B (~8.9 GB of weights) and LocateAnything-3B (~7.7 GB) don't fit in 8 GB, so this
# serves the 2B model and leaves ~2.4 GB for YOLOE and the desktop. apps/exit_monitor.py then
# runs YOLOE + Qwen: QWEN_URL=http://localhost:<port>/v1 in .env, no LOCATE_URL.
# Start it before apps/exit_monitor.py — vLLM reserves its share of GPU memory at start-up.
#
# Status: bash server/status.sh · watch: tmux attach -t qwen · stop: bash server/stop_all.sh qwen
# Env overrides: QWEN_MODEL (2b), GPU_UTIL (0.70), MAX_LEN (4096), MAX_SEQS (4),
#   MM_MAX_SIDE (1280: largest image side vLLM reserves memory for)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/server/_lib.sh"
MODEL="${QWEN_MODEL:-2b}"
PFILE="$HOME/qwen-vllm/port"
LOG="$ROOT/logs/qwen.log"
mkdir -p "$ROOT/logs"

if [ -f "$PFILE" ] && http_up "http://localhost:$(cat "$PFILE")/v1/models"; then
  echo "Qwen is already running: QWEN_URL=http://localhost:$(cat "$PFILE")/v1"
  exit 0
fi
command -v tmux >/dev/null 2>&1 || { echo "tmux is needed: sudo apt install tmux"; exit 1; }

rm -f "$PFILE"
: > "$LOG"
tmux kill-session -t qwen 2>/dev/null
tmux new-session -d -s qwen
# settings go in the command line: a tmux shell doesn't inherit this script's environment
tmux send-keys -t qwen "cd '$ROOT' && GPU_UTIL=${GPU_UTIL:-0.70} MAX_LEN=${MAX_LEN:-4096} \
MAX_SEQS=${MAX_SEQS:-4} MM_MAX_SIDE=${MM_MAX_SIDE:-1280} bash server/serve_qwen.sh $MODEL 2>&1 \
| tee '$LOG'; echo __EXITED__ >> '$LOG'" Enter
echo "Qwen ($MODEL) starting in tmux session 'qwen' (the first start downloads the weights)..."

start=$(date +%s)
until [ -f "$PFILE" ] && http_up "http://localhost:$(cat "$PFILE")/v1/models"; do
  if grep -q "__EXITED__" "$LOG"; then
    echo "Qwen stopped. Last lines of $LOG:"
    tail -n 15 "$LOG" | grep -v "__EXITED__"
    exit 1
  fi
  if [ $(( $(date +%s) - start )) -gt 1800 ]; then
    echo "Qwen not ready after 30 min — see $LOG"
    exit 1
  fi
  sleep 3
done
echo "Qwen is up after $(mmss $(( $(date +%s) - start ))): QWEN_URL=http://localhost:$(cat "$PFILE")/v1"
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader | sed 's/^/GPU memory used: /'
