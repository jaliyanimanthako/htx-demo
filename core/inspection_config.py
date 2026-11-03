"""Validated TOML settings shared by the inspection CLI and tablet backend."""
import os
import copy
import tomllib
from datetime import date
from pathlib import Path
from core.config import ROOT
from core.models import DEFAULT_QWEN, DEFAULT_YOLOE

CONFIG_FILE = ROOT / "config.toml"
TASKS = ("exits", "extinguishers")

# Settings of the inspection itself (config.toml explains each). [exits] and
# [extinguishers] also take every option of their monitor that is not in SHARED.
DEFAULTS = {
    "server": {"host": "0.0.0.0", "port": 8100, "max_sessions": 1, "video_dir": "."},
    "sources": {"external_camera": {"name": "External camera", "url": ""}},
    "input": {"source": "", "loop": False, "camera_size": [1920, 1080], "stream_timeout": 30.0},
    "processing": {"process_fps": 0.0, "proc_width": 960},
    "models": {"yoloe_model": DEFAULT_YOLOE, "qwen": True, "qwen_url": "", "qwen_model": DEFAULT_QWEN,
               "locate_anything": True, "locate_url": ""},
    "exits": {"enabled": True, "fps": 0.0, "imgsz": 640},
    "extinguishers": {"enabled": True, "fps": 0.0, "imgsz": 640},
    "output": {"display": True, "show_original": True, "view_width": 1600, "save": "",
               "stream": 0, "snapshots": True, "log_frames": True, "out_dir": "results"},
}
# monitor options set for both tasks from [input] / [processing] / [models] / [output]
SHARED = {"source", "loop", "proc_width", "yoloe_model", "qwen_url", "qwen_model", "no_qwen",
          "locate_url", "no_display", "stream", "save", "snapshots", "log_frames", "out_dir"}
KIND = {bool: "true or false", int: "a whole number", float: "a number", str: 'text in "quotes"'}


# ── Settings ──────────────────────────────────────────────────────────────────

def task_options(task: str) -> dict:
    """Options of that task's monitor (its CLI: dest → default) that its section may set."""
    from importlib import import_module
    module = {"exits": "apps.exit_monitor", "extinguishers": "apps.extinguisher_monitor"}[task]
    defaults = vars(import_module(module).build_parser().parse_args(["x"]))
    return {k: v for k, v in defaults.items() if k not in SHARED}


def _coerce(where: str, value, default):
    """A value from the file or --set, checked against the type of its default."""
    if isinstance(default, dict):
        if not isinstance(value, dict) or value.keys() - default.keys():
            raise SystemExit(f"  Invalid settings for {where}: expected keys {list(default)}")
        return {k: _coerce(f"{where}.{k}", value.get(k, v), v) for k, v in default.items()}
    if isinstance(value, date):                      # TOML date: today = 2026-12-01
        value = value.isoformat()
    if default is None:
        return value
    if isinstance(default, list):
        ok = (isinstance(value, list) and len(value) == len(default)
              and all(type(v) is type(d) for v, d in zip(value, default)))
        kind = f"a list like {default}"
    elif isinstance(default, float):
        ok = type(value) in (int, float)
        value, kind = float(value) if ok else value, KIND[float]
    else:
        ok, kind = type(value) is type(default), KIND[type(default)]
    if not ok:
        raise SystemExit(f"  Setting {where} = {value!r}: expected {kind}")
    return value


def _parse_set(item: str) -> tuple:
    """--set SECTION.KEY=VALUE → (section, key, value); VALUE in TOML syntax or bare text."""
    key, eq, raw = item.partition("=")
    section, dot, name = key.strip().partition(".")
    if not (eq and dot):
        raise SystemExit(f"  --set {item!r}: expected SECTION.KEY=VALUE, e.g. exits.hold=2")
    try:
        value = tomllib.loads(f"v = {raw}")["v"]
    except tomllib.TOMLDecodeError:
        value = raw                                  # e.g. --set input.source=IMG_0451.MOV
    return section, name, value


def load_config(path: str = None, sets: list = ()) -> dict:
    """DEFAULTS ← the settings file (config.toml unless `path`) ← --set items."""
    path = path or os.environ.get("HTX_CONFIG")
    file = Path(path).expanduser() if path else CONFIG_FILE
    data = {}
    if path or file.is_file():
        try:
            with open(file, "rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError) as e:
            raise SystemExit(f"  Cannot read the settings file {file}: {e}") from None
    items = []
    for section, values in data.items():
        if not isinstance(values, dict):
            raise SystemExit(f"  {file}: put '{section}' under a section ({', '.join(DEFAULTS)})")
        items += [(section, k, v) for k, v in values.items()]
    items += [_parse_set(s) for s in sets]

    cfg = copy.deepcopy(DEFAULTS)
    options = {task: task_options(task) for task in TASKS}
    for section, key, value in items:
        if section not in cfg:
            raise SystemExit(f"  Unknown section [{section}] — one of: {', '.join(cfg)}")
        known = {**options.get(section, {}), **DEFAULTS[section]}
        if key not in known:
            raise SystemExit(f"  Unknown setting {section}.{key} — [{section}] takes: "
                             f"{', '.join(sorted(known))}")
        cfg[section][key] = _coerce(f"{section}.{key}", value, known[key])
    ranges = [
        ("server.port", 1 <= cfg["server"]["port"] <= 65535),
        ("server.max_sessions", cfg["server"]["max_sessions"] >= 1),
        ("input.stream_timeout", cfg["input"]["stream_timeout"] > 0),
        ("input.camera_size", all(n > 0 for n in cfg["input"]["camera_size"])),
        ("processing.proc_width", cfg["processing"]["proc_width"] >= 64),
        ("processing.process_fps", cfg["processing"]["process_fps"] >= 0),
        *[(f"{task}.fps", cfg[task]["fps"] >= 0) for task in TASKS],
        *[(f"{task}.imgsz", cfg[task]["imgsz"] > 0) for task in TASKS],
    ]
    invalid = [key for key, valid in ranges if not valid]
    if invalid:
        raise SystemExit(f"  Out of range: {', '.join(invalid)}")
    return cfg
