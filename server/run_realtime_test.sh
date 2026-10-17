#!/usr/bin/env bash
# Real-time test entirely on the GPU server: vLLM + our client on the same box.
#
#   1) In another terminal / tmux:   bash server/serve_qwen.sh 4b
#   2) From the htx_demo folder:     bash server/run_realtime_test.sh IMG_0450.MOV
#
# Runs:
#   a) bench_latency.py — per-call latency / throughput at 448 & 640 px, 1–8 parallel calls
#   b) live_infer.py    — the video as a live stream (headless), annotated video saved
# and prints a verdict on how "live" the results are.
set -euo pipefail

VIDEO="${1:?usage: bash server/run_realtime_test.sh <video> }"
VIDEO="$(realpath "$VIDEO")"
ENV_DIR="${ENV_DIR:-$HOME/qwen-vllm}"

# Run from the project root (the folder that contains server/), wherever we were called from
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
missing=()
for f in bench_latency.py live_infer.py video_infer.py core/prompts.py core/stream_view.py engines/qwen_engine.py engines/__init__.py; do
  [ -f "$f" ] || missing+=("$f")
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "Missing in $ROOT: ${missing[*]}"
  echo "The project is incomplete — clone / update the repository here:"
  echo "  git clone <repo-url> $ROOT     (or: cd $ROOT && git pull)"
  exit 1
fi
INTERVAL="${INTERVAL:-0.2}"     # seconds between API calls in the live run
WORKERS="${WORKERS:-4}"         # max calls in flight in the live run
OUT="results/server"

# shellcheck disable=SC1091
source "$ENV_DIR/.venv/bin/activate"
PORT="${PORT:-$(cat "$ENV_DIR/port" 2>/dev/null || echo 8000)}"
URL="http://localhost:$PORT/v1"

MODEL="$(python - "$URL" <<'PY'
import json, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1] + "/models", timeout=5) as r:
        print(json.load(r)["data"][0]["id"])
except Exception as e:
    sys.exit(f"vLLM not reachable at {sys.argv[1]} ({e}).\n"
             "Start it first (in tmux / another terminal): bash server/serve_qwen.sh 4b")
PY
)"
TAG="$(echo "$MODEL" | tr '/' '_')"
mkdir -p "$OUT"

echo "== GPU =="
nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader
echo "== Model: $MODEL @ $URL =="
echo

echo "== a) Latency benchmark =="
python bench_latency.py "$VIDEO" --provider local --base-url "$URL" --model "$MODEL" \
  --n 10 --sizes 448 640 --concurrency 1 2 4 8 --out-dir "$OUT"
echo

echo "== b) Live run (interval ${INTERVAL}s, ${WORKERS} in flight, headless) =="
python live_infer.py "$VIDEO" --provider local --base-url "$URL" --model "$MODEL" \
  --interval "$INTERVAL" --workers "$WORKERS" --change-thresh 0 \
  --no-display --save "$OUT/live_${TAG}.mp4" --out-dir "$OUT"
echo

echo "== Verdict =="
python - "$OUT/$(basename "${VIDEO%.*}")_live_detections.json" <<'PY'
import json, statistics, sys
d = json.load(open(sys.argv[1]))
fr = [f for f in d["frames"] if "error" not in f]
errs = len(d["frames"]) - len(fr)
if not fr:
    sys.exit("No successful results — check the vLLM terminal for errors.")
lat = [f["latency_s"] for f in fr]
ts = sorted(f["video_time_s"] for f in fr)
gaps = [b - a for a, b in zip(ts, ts[1:])] or [0]
span = (ts[-1] - ts[0]) or 1
print(f"  results            : {len(fr)} ({errs} errors)")
print(f"  latency (age)      : mean {statistics.mean(lat):.2f}s  p90 {sorted(lat)[int(0.9*(len(lat)-1))]:.2f}s  max {max(lat):.2f}s")
print(f"  update rate        : {len(fr)/span:.1f} results per second of video "
      f"(gap mean {statistics.mean(gaps):.2f}s)")
m = statistics.mean(lat)
verdict = ("REAL-TIME: status lags < 0.5 s" if m < 0.5 else
           "NEAR REAL-TIME: status lags 0.5–1 s (fine with motion tracking)" if m < 1.0 else
           "NOT REAL-TIME: status lags > 1 s — try the 2b model or --sizes 448")
print(f"  verdict            : {verdict}")
PY
echo
echo "Annotated video: $OUT/live_${TAG}.mp4   (copy it to your computer to watch)"
