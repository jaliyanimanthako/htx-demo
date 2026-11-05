#!/usr/bin/env bash
# The venue demo on this laptop, WSL side: Qwen, the backend and the two web pages, each in
# its own tmux session, then a status check and every address Mimo and the tablet need.
# The Windows side (port forwards, firewall, MediaMTX) is run_demo/run_windows.cmd.
#
#   bash run_demo/run_wsl.sh [model]   # (re)start everything; model as for server/start_laptop.sh
#   bash run_demo/run_wsl.sh check     # status + addresses only, e.g. once Mimo streams
#   bash run_demo/run_wsl.sh stop      # stop everything and free the GPU
#
# - Qwen keeps running if it already serves the model. The backend always restarts, so it
#   reads config.toml again. The web pages start only if they aren't up.
# - Points [sources.external_camera] url at MediaMTX on Windows (the WSL gateway, which can
#   change after a reboot), unless it names another relay.
# - No internet: Qwen starts offline (HF_HUB_OFFLINE=1), from the downloaded weights.
# - YOLOE runs inside the backend. LocateAnything doesn't fit this 8 GB GPU: not started.
# Watch a service: tmux attach -t backend (Ctrl+B, then D leaves it running). Logs: logs/<name>.log
# Env: GPU_UTIL, MAX_LEN, ... pass through to start_laptop.sh; CONDA_ENV (htx); HTX_CONFIG.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/server/_lib.sh"
CONFIG="${HTX_CONFIG:-$ROOT/config.toml}"
CONDA_ENV="${CONDA_ENV:-htx}"
LOG_DIR="$ROOT/logs"
PORT=8100                                                   # the only backend port Windows forwards
WIN="$(ip route show default | awk '{print $3; exit}')"     # Windows, as seen from WSL
BACKEND_PATTERN='run\.py backend|python -m backend'
mkdir -p "$LOG_DIR"

in_tmux() {   # in_tmux NAME COMMAND → run COMMAND in tmux session NAME, output to logs/NAME.log
  local log="$LOG_DIR/$1.log"
  : > "$log"
  tmux kill-session -t "$1" 2>/dev/null
  tmux new-session -d -s "$1"
  # a tmux shell doesn't inherit this script's environment: everything goes in the command line
  tmux send-keys -t "$1" "cd '$ROOT' && $2 2>&1 | tee '$log'; echo __EXITED__ >> '$log'" Enter
}

wait_up() {   # wait_up NAME URL SECONDS → until URL answers; on failure, the end of the log
  local start log="$LOG_DIR/$1.log"
  start=$(date +%s)
  until http_up "$2"; do
    if grep -q __EXITED__ "$log" 2>/dev/null || [ $(( $(date +%s) - start )) -ge "$3" ]; then
      ui_fail "$1" "not up after $(mmss $(( $(date +%s) - start ))). End of $log:"
      tail -n 12 "$log" | grep -v __EXITED__ | cut -c1-160 | sed 's/^/      /'
      return 1
    fi
    sleep 1
  done
  ui_ok "$1" "up after $(mmss $(( $(date +%s) - start )))"
}

port_taken() { ss -ltn "sport = :$1" 2>/dev/null | grep -q LISTEN; }

toml_get() {   # toml_get SECTION.KEY → a value from the config file (empty if unset)
  python3 - "$CONFIG" "$1" <<'PY' 2>/dev/null
import sys, tomllib
value = tomllib.load(open(sys.argv[1], "rb"))
for key in sys.argv[2].split("."):
    value = value.get(key, {})
print("" if value == {} else value)
PY
}

set_camera_url() {   # [sources.external_camera] url → MediaMTX on Windows; prints the result
  python3 - "$CONFIG" "rtsp://$WIN:8554/live/x2" <<'PY'
import re, sys
path, new = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
section = re.search(r"(?ms)^\[sources\.external_camera\][^\n]*\n(.*?)(?=^\[|\Z)", text)
url = section and re.search(r'(?m)^url\s*=\s*"([^"]*)"', section.group(1))
if not url:
    sys.exit(f"no [sources.external_camera] url in {path}")
old = url.group(1)
if old == new:
    print(new)
elif old and not re.fullmatch(r"rtsp://[^/]+:8554/live/x2", old):
    print(f"{old} (another relay: left as it is)")
else:
    at = section.start(1) + url.start(1)
    open(path, "w", encoding="utf-8").write(text[:at] + new + text[at + len(old):])
    print(f"{new} (was {old or 'empty'})")
PY
}

rtsp_state() {   # rtsp_state URL → MediaMTX's answer: 200 streaming, 404 nothing yet; empty = unreachable
  python3 - "$1" <<'PY' 2>/dev/null
import socket, sys
from urllib.parse import urlsplit
url = urlsplit(sys.argv[1])
with socket.create_connection((url.hostname, url.port or 554), timeout=3) as s:
    s.settimeout(3)
    s.sendall(f"DESCRIBE {sys.argv[1]} RTSP/1.0\r\nCSeq: 1\r\n\r\n".encode())
    print(s.recv(200).split()[1].decode())
PY
}

web_page() {   # web_page NAME PORT COMMAND → start a static web page unless its port answers
  if http_up "http://localhost:$2/"; then
    ui_ok "$1" "already up"
  else
    in_tmux "$1" "$3"
    wait_up "$1" "http://localhost:$2/" 20
  fi
}

start() {
  command -v tmux >/dev/null 2>&1 || { echo "tmux is needed: sudo apt install tmux"; exit 1; }
  local conda_sh msg
  conda_sh="$(dirname "$(dirname "${CONDA_EXE:-$(command -v conda)}")")/etc/profile.d/conda.sh"
  [ -f "$conda_sh" ] || { echo "conda not found: the backend runs in the conda env '$CONDA_ENV'."; exit 1; }
  if [ $# -gt 0 ]; then qwen_lookup "$1" >/dev/null || exit 1; fi   # a typo stops nothing

  ui_head "Qwen"
  if tmux has-session -t backend 2>/dev/null || pgrep -f "$BACKEND_PATTERN" >/dev/null; then
    bash "$ROOT/server/stop_all.sh" backend >/dev/null
    pkill -f "$BACKEND_PATTERN"                              # also one started by hand
    ui_info "stopped the old backend (Qwen starts before YOLOE takes GPU memory)"
  fi
  if [ -z "${HF_HUB_OFFLINE:-}" ] && ! http_up https://huggingface.co; then
    export HF_HUB_OFFLINE=1
    ui_warn "No internet: Qwen starts offline, from the downloaded weights."
  fi
  if [ -n "${HF_HUB_OFFLINE:-}" ] && tmux ls >/dev/null 2>&1; then
    tmux set-environment -g HF_HUB_OFFLINE "$HF_HUB_OFFLINE"   # a running tmux server doesn't see it
  fi
  bash "$ROOT/server/start_laptop.sh" "$@" \
    || ui_warn "Qwen didn't start: detection goes on without it (no exit confirmation, dates not verified)."

  ui_head "Backend"
  if msg="$(set_camera_url 2>&1)"; then ui_ok camera "$msg"; else ui_fail camera "$msg"; fi
  for _ in $(seq 15); do port_taken "$PORT" || break; sleep 1; done
  if port_taken "$PORT"; then
    ui_fail backend "port $PORT is taken by another program (ss -ltnp 'sport = :$PORT'), and Windows forwards only $PORT"
    return 1
  fi
  in_tmux backend "source '$conda_sh' && conda activate $CONDA_ENV && python run.py backend --config '$CONFIG' --port $PORT"
  ui_info "starting in tmux session 'backend'"
  wait_up backend "http://localhost:$PORT/api/health" 180

  ui_head "Web pages"
  web_page webpage 5174 "python3 web-tablet/serve.py"
  web_page webapp 5173 "bash frontend/serve.sh"
}

check() {
  local qport served wsl_ip fwd missing="" p cam vdir n ip token
  ui_head "Status"
  qport="$(cat "$HOME/qwen-vllm/port" 2>/dev/null)"
  served="$([ -n "$qport" ] && served_model "http://localhost:$qport/v1")"
  if [ -n "$served" ]; then ui_ok qwen "$served"
  else ui_fail qwen "not running: detection goes on without it (bash server/start_laptop.sh)"; fi
  if http_up "http://localhost:$PORT/api/health"; then ui_ok backend "port $PORT, tmux attach -t backend"
  else ui_fail backend "not answering on port $PORT: see logs/backend.log"; fi
  if http_up http://localhost:5174/; then ui_ok webpage "tablet web page, port 5174"
  else ui_fail webpage "not running (only the fallback needs it)"; fi
  if http_up http://localhost:5173/; then ui_ok webapp "web app, port 5173 (this laptop only)"
  else ui_fail webapp "not running"; fi

  wsl_ip="$(hostname -I | awk '{print $1}')"
  if fwd="$(netsh.exe interface portproxy show v4tov4 2>/dev/null | tr -d '\r')"; then
    for p in "$PORT" 5174; do
      awk -v p="$p" -v ip="$wsl_ip" '$2 == p && $3 == ip {found = 1} END {exit !found}' <<< "$fwd" \
        || missing="$missing $p"
    done
    if [ -z "$missing" ]; then ui_ok forward "Windows forwards $PORT + 5174 to WSL ($wsl_ip)"
    else ui_fail forward "port$missing not forwarded to WSL ($wsl_ip): run run_windows.cmd again"; fi
  else
    ui_warn "Can't check the Windows port forwards (no Windows interop)."
  fi

  cam="$(toml_get sources.external_camera.url)"
  if [ -z "$cam" ]; then
    ui_fail camera "no [sources.external_camera] url in $CONFIG"
  else
    case "$(rtsp_state "$cam")" in
      200) ui_ok camera "streaming: $cam" ;;
      404) ui_fail camera "MediaMTX is up, but nothing streams yet: start the livestream in Mimo" ;;
      "")  ui_fail camera "MediaMTX not reachable at $cam: run run_windows.cmd (it starts MediaMTX)" ;;
      *)   ui_warn "Camera: MediaMTX gives an unexpected answer for $cam" ;;
    esac
  fi

  vdir="$(toml_get server.video_dir)"
  vdir="${vdir:-.}"; vdir="${vdir/#\~/$HOME}"; [[ "$vdir" = /* ]] || vdir="$(realpath -m "$ROOT/$vdir")"
  n="$(find "$vdir" -maxdepth 1 -type f \( -iname '*.mov' -o -iname '*.mp4' -o -iname '*.avi' \
        -o -iname '*.mkv' -o -iname '*.m4v' \) 2>/dev/null | wc -l)"
  if [ "$n" -gt 0 ]; then ui_ok videos "$n for Pre-recorded video (the fallback)"
  else ui_warn "No videos in $vdir: the tablet's Pre-recorded video fallback lists nothing."; fi

  ip="$(timeout 15 powershell.exe -NoProfile -NonInteractive -Command "(Get-NetIPAddress \
-InterfaceAlias 'Wi-Fi' -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object IPAddress \
-NotLike '169.254.*' | Select-Object -First 1).IPAddress" 2>/dev/null | tr -d '\r')"
  token="$(grep -E '^BACKEND_TOKEN=' "$ROOT/.env" 2>/dev/null | tail -n 1 | cut -d= -f2-)"
  ui_head "For Mimo and the tablet"
  if [ -n "$ip" ]; then
    echo "  DJI Mimo RTMP address : rtmp://$ip:1935/live/x2"
    echo "  Tablet app backend URL: http://$ip:$PORT"
    echo "  Tablet browser check  : http://$ip:$PORT/api/health"
    echo "  Tablet web page       : http://$ip:5174"
  else
    ui_warn "No Wi-Fi IP found: join the venue Wi-Fi (run_windows.cmd prints the addresses too)."
  fi
  echo "  Token                 : ${token:-none (the backend runs without a token)}"
  echo "  On this laptop        : http://localhost:5174 (tablet web page), http://localhost:5173 (web app)"
  echo
  echo "Check again: bash run_demo/run_wsl.sh check    Stop: bash run_demo/run_wsl.sh stop"
}

case "${1:-}" in
  -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//' ;;
  check)     check ;;
  stop)      ui_head "Stopping"; bash "$ROOT/server/stop_all.sh" webapp webpage backend qwen ;;
  *)         start "$@"; check ;;
esac
