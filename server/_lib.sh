# shellcheck shell=bash
# Helpers shared by the server scripts (sourced, not run).

# http_up URL → exit 0 if the URL answers within 5 s
http_up() {
  python3 - "$1" <<'PY' >/dev/null 2>&1
import sys, urllib.request
urllib.request.urlopen(sys.argv[1], timeout=5)
PY
}

# pip_into PYTHON PKG... → install packages into that interpreter's environment,
# whichever installer it has (pip, uv, or pip bootstrapped with ensurepip)
pip_into() {
  local py="$1"; shift
  if "$py" -m pip --version >/dev/null 2>&1; then
    "$py" -m pip install "$@"
  elif command -v uv >/dev/null 2>&1; then
    uv pip install --python "$py" "$@"
  else
    "$py" -m ensurepip --upgrade >/dev/null && "$py" -m pip install "$@"
  fi
}

# set_aside DIR → move an old / broken env out of the way (kept, not deleted)
set_aside() {
  local dir="$1" dest
  dest="$dir.broken-$(date +%Y%m%d-%H%M%S)"
  echo "Moving the old environment aside (not deleted): $dir → $dest"
  mv "$dir" "$dest"
}

# ensure_uv → prints the path of a working `uv` (the fast Python package/env manager).
# Uses one on PATH, else installs it into ~/.local/uv-bootstrap (no sudo, no conda).
# `uv venv --python 3.11` then downloads its own Python if the system lacks that version.
ensure_uv() {
  if command -v uv >/dev/null 2>&1; then command -v uv; return; fi
  local boot="$HOME/.local/uv-bootstrap"
  if [ ! -x "$boot/bin/uv" ]; then
    echo "Installing uv into $boot (one time)…" >&2
    [ -e "$boot" ] && mv "$boot" "$boot.old-$(date +%Y%m%d-%H%M%S)"
    python3 -m venv "$boot" >&2 && "$boot/bin/python" -m pip install -q --upgrade pip uv >&2 \
      || { echo "Could not install uv (needs python3 with the venv module)." >&2; return 1; }
  fi
  echo "$boot/bin/uv"
}

# new_venv DIR PYVER → create a fresh virtualenv at DIR with Python PYVER using uv
# (an existing DIR is moved aside first, never deleted)
new_venv() {
  local dir="$1" ver="$2" uv
  uv="$(ensure_uv)" || return 1
  [ -e "$dir" ] && set_aside "$dir"
  "$uv" venv --python "$ver" "$dir"
}

# ── terminal UI: colours + spinner only on a real terminal (plain text in log files) ──
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  UI_TTY=1
  C_GREEN=$'\033[32m'; C_RED=$'\033[31m'; C_YELLOW=$'\033[33m'; C_CYAN=$'\033[36m'
  C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'; C_CLR=$'\r\033[K'
else
  UI_TTY=0
  C_GREEN=""; C_RED=""; C_YELLOW=""; C_CYAN=""; C_BOLD=""; C_DIM=""; C_OFF=""; C_CLR=""
fi
if [ "$(locale charmap 2>/dev/null)" = "UTF-8" ]; then
  UI_FRAMES=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏); UI_OK="✓"; UI_FAIL="✗"; UI_DOT="●"; UI_ARROW="▸"
else
  UI_FRAMES=('|' '/' '-' '\'); UI_OK="OK"; UI_FAIL="X"; UI_DOT="*"; UI_ARROW=">"
fi

ui_head() { printf '\n%s%s%s\n' "$C_BOLD" "$1" "$C_OFF"; }
ui_info() { printf '%s  %s %s%s\n' "$C_DIM" "$UI_ARROW" "$1" "$C_OFF"; }
ui_ok()   { printf '%s  %s%s%s %s%-8s%s %s\n' "$C_CLR" "$C_GREEN" "$UI_OK" "$C_OFF" "$C_BOLD" "$1" "$C_OFF" "$2"; }
ui_fail() { printf '%s  %s%s%s %s%-8s%s %s%s%s\n' "$C_CLR" "$C_RED" "$UI_FAIL" "$C_OFF" "$C_BOLD" "$1" "$C_OFF" "$C_RED" "$2" "$C_OFF"; }
ui_warn() { printf '  %s%s%s\n' "$C_YELLOW" "$1" "$C_OFF"; }

# ui_spin FRAME_INDEX NAME ELAPSED MESSAGE → one self-overwriting spinner line
ui_spin() {
  local cols width msg
  cols="$(tput cols 2>/dev/null || echo 100)"
  width=$(( cols - 30 )); [ "$width" -lt 20 ] && width=20
  msg="${4:0:$width}"
  printf '%s  %s%s%s %s%-8s%s %s%6s%s  %s%s%s' "$C_CLR" "$C_CYAN" "${UI_FRAMES[$(( $1 % ${#UI_FRAMES[@]} ))]}" \
    "$C_OFF" "$C_BOLD" "$2" "$C_OFF" "$C_DIM" "$3" "$C_OFF" "$C_DIM" "$msg" "$C_OFF"
}

# mmss SECONDS → "1m05s" / "42s"
mmss() { local s="$1"; [ "$s" -ge 60 ] && printf '%dm%02ds' $(( s / 60 )) $(( s % 60 )) || printf '%ds' "$s"; }

# last_log_line FILE → last meaningful line (handles progress bars that use \r, strips colours)
last_log_line() {
  tail -c 4000 "$1" 2>/dev/null | tr '\r' '\n' | sed 's/\x1b\[[0-9;]*[A-Za-z]//g' \
    | grep -v -e '^[[:space:]]*$' -e '__EXITED__' | tail -n 1 | sed 's/^[[:space:]]*//'
}
