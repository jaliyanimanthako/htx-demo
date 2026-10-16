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

# set_aside DIR → move a broken env out of the way (kept, not deleted)
set_aside() {
  local dir="$1" dest
  dest="$dir.broken-$(date +%Y%m%d-%H%M%S)"
  echo "Moving the broken environment aside: $dir → $dest"
  mv "$dir" "$dest"
}
