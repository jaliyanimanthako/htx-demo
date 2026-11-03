"""
Backend settings — shared config.toml (or HTX_CONFIG), with legacy environment
overrides for server paths/auth. Explicit model URLs in TOML precede .env:

  BACKEND_TOKEN         shared secret; if set, every API call needs it
                        (header "Authorization: Bearer <token>" or ?token=<token>)
  BACKEND_CORS_ORIGINS  comma-separated origins allowed to call the API (default "*")
  BACKEND_VIDEO_DIR     folder with the videos that sessions may play (default: project root)
  BACKEND_MAX_SESSIONS  sessions that may run at the same time (default 1 — one GPU pipeline)
  BACKEND_OUT_DIR       where reports / snapshots go (default: results)
  LOCATE_URL, QWEN_URL  model servers; if unset, the port files written by
                        server/serve_locate.sh and server/serve_qwen.sh are used (localhost)
"""

import os
from pathlib import Path

from core.config import ROOT  # also loads .env
from core.inspection_config import load_config, task_options, _coerce
from core.models import yoloe_file

CONFIG = load_config()

VIDEO_EXTS = {".mov", ".mp4", ".avi", ".mkv", ".m4v"}


def _from_port_file(env_dir: str, path_suffix: str, default_port: int) -> str:
    port_file = Path.home() / env_dir / "port"
    try:
        port = int(port_file.read_text().strip())
    except (OSError, ValueError):
        port = default_port
    return f"http://localhost:{port}{path_suffix}"


TOKEN = os.environ.get("BACKEND_TOKEN", "").strip()
CORS_ORIGINS = [o.strip() for o in os.environ.get("BACKEND_CORS_ORIGINS", "*").split(",") if o.strip()]
VIDEO_DIR = Path(os.environ.get("BACKEND_VIDEO_DIR", ROOT / CONFIG["server"]["video_dir"])).expanduser().resolve()
MAX_SESSIONS = int(os.environ.get("BACKEND_MAX_SESSIONS", CONFIG["server"]["max_sessions"]))
OUT_DIR = Path(os.environ.get("BACKEND_OUT_DIR", ROOT / CONFIG["output"]["out_dir"])).expanduser()
LOCATE_URL = ((CONFIG["models"]["locate_url"] or os.environ.get("LOCATE_URL")
               or _from_port_file("locate-anything", "", 8020))
              if CONFIG["models"]["locate_anything"] else None)
QWEN_URL = ((CONFIG["models"]["qwen_url"] or os.environ.get("QWEN_URL")
             or _from_port_file("qwen-vllm", "/v1", 8010))
            if CONFIG["models"]["qwen"] else None)
EXTERNAL_SOURCE = "external-camera"


def external_camera():
    return CONFIG["sources"]["external_camera"]


def apply_monitor_config(args, task, options=None):
    """Monitor defaults ← shared TOML ← explicit inference options.

    Output transport and credentials stay under backend control.
    """
    allowed = task_options(task)
    for key, value in CONFIG[task].items():
        if key in allowed:
            setattr(args, key, value)
    args.proc_width = CONFIG["processing"]["proc_width"]
    args.yoloe_model = yoloe_file(CONFIG["models"]["yoloe_model"])
    args.snapshots = CONFIG["output"]["snapshots"]
    for key, value in (options or {}).items():
        if key in allowed and value is not None:
            try:
                value = _coerce(f"options.{key}", value, allowed[key])
            except SystemExit as error:
                raise ValueError(str(error)) from None
            setattr(args, key, value)
    return args
