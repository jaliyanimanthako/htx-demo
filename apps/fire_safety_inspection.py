"""
Fire safety inspection — exits and fire extinguishers on the same frames
========================================================================

  EVERY FRAME     exit monitor (exit_monitor.py): YOLOE doors / signs / obstacles →
                  tracker → clearance rule → exit memory; Qwen on events
                  extinguisher monitor (extinguisher_monitor.py): YOLOE extinguishers →
                  tracker → best crop; Qwen reads the expiry / service dates
  VIEW            one annotated frame with both overlays, one panel per task
  REPORT          results/fire_safety_<video>/report.json (both tasks, one event log);
                  each task's own report + snapshots in exits/ and extinguishers/

Each task keeps its own YOLOE model, tracker and Qwen jobs; they share the Qwen server,
and nothing in the frame loop waits for it. Settings come from config.toml (frame
rates, resolution, models, rules, output); the command line overrides them.

Usage:
  python run.py inspect IMG_0451.MOV                       # settings from config.toml
  python run.py inspect rtsp://<windows-host>:8554/live/x2 --save results/inspection.mp4
  python run.py inspect IMG_0451.MOV --process-fps 10 --proc-width 640
  python run.py inspect IMG_0451.MOV --set exits.fps=5 --set extinguishers.today=2026-12-01
  python run.py inspect IMG_0451.MOV --only extinguishers --no-display --stream
  q / Esc quits, space pauses.
"""

import argparse
import json
import os
import time
import tomllib
from collections import Counter, deque
from datetime import date
from pathlib import Path

import cv2
import numpy as np

from apps import exit_monitor, extinguisher_monitor
from core import video_io
from core.config import ROOT  # also loads .env
from core.expiry import STATUS_TEXT as EXT_STATUS_TEXT
from core.qwen_assist import first_served_model
from core.stream_view import MJPEGStreamer
from core.video_io import LiveSource

CONFIG_FILE = ROOT / "config.toml"
TASKS = {"exits": exit_monitor, "extinguishers": extinguisher_monitor}

# Settings of the inspection itself (config.toml explains each). [exits] and
# [extinguishers] also take every option of their monitor that is not in SHARED.
DEFAULTS = {
    "input": {"source": "", "loop": False, "camera_size": [1920, 1080], "stream_timeout": 30.0},
    "processing": {"process_fps": 0.0, "proc_width": 960},
    "models": {"yoloe_model": "yoloe-11s-seg.pt", "qwen": True, "qwen_url": "", "qwen_model": "",
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

def _task_options(task: str) -> dict:
    """Options of that task's monitor (its CLI: dest → default) that its section may set."""
    defaults = vars(TASKS[task].build_parser().parse_args(["x"]))
    return {k: v for k, v in defaults.items() if k not in SHARED}


def _coerce(where: str, value, default):
    """A value from the file or --set, checked against the type of its default."""
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
    file = Path(path) if path else CONFIG_FILE
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

    cfg = {section: dict(values) for section, values in DEFAULTS.items()}
    options = {task: _task_options(task) for task in TASKS}
    for section, key, value in items:
        if section not in cfg:
            raise SystemExit(f"  Unknown section [{section}] — one of: {', '.join(cfg)}")
        known = {**options.get(section, {}), **DEFAULTS[section]}
        if key not in known:
            raise SystemExit(f"  Unknown setting {section}.{key} — [{section}] takes: "
                             f"{', '.join(sorted(known))}")
        cfg[section][key] = _coerce(f"{section}.{key}", value, known[key])
    return cfg


def check_config(cfg: dict) -> None:
    """Values that would fail later, once the command line has been applied."""
    i, p, o = cfg["input"], cfg["processing"], cfg["output"]
    src = Path(i["source"])
    if not i["source"]:
        raise SystemExit("  No source: python run.py inspect <video | 0 | rtsp://…>, "
                         "or set [input] source in config.toml")
    if src.is_dir() or src.suffix.lower() in extinguisher_monitor.IMAGE_SUFFIXES:
        raise SystemExit("  The inspection needs a video, camera or stream. For photos: "
                         "python run.py extinguishers <photo | folder>")
    if not any(cfg[task]["enabled"] for task in TASKS):
        raise SystemExit("  Both tasks are off ([exits] / [extinguishers] enabled = false)")
    bad = [f"{s}.{k}" for s, k, ok in [
        ("processing", "process_fps", p["process_fps"] >= 0),
        ("processing", "proc_width", p["proc_width"] >= 64),
        ("exits", "fps", cfg["exits"]["fps"] >= 0),
        ("extinguishers", "fps", cfg["extinguishers"]["fps"] >= 0),
        ("output", "view_width", o["view_width"] >= 320),
        ("output", "stream", 0 <= o["stream"] <= 65535)] if not ok]
    if bad:
        raise SystemExit(f"  Out of range: {', '.join(bad)} (see config.toml)")


def _qwen_server(m: dict) -> tuple:
    """→ (url, model) of the Qwen server, or (None, None) without Qwen."""
    url = (m["qwen_url"] or os.environ.get("QWEN_URL")) if m["qwen"] else None
    if not url:
        return None, None
    try:
        return url, m["qwen_model"] or first_served_model(url)
    except OSError as e:
        raise SystemExit(f"\n  Cannot reach Qwen at {url} ({getattr(e, 'reason', e)}).\n"
                         "  Start it (bash server/start_laptop.sh), or set [models] qwen = false.") from None


# ── Inspection ────────────────────────────────────────────────────────────────

class FireSafetyInspection:
    """Both monitors on one source: step() runs each task that is due on the frame,
    draw() / view() combine their overlays and panels, run() is the CLI loop."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        m = cfg["models"]
        qwen_url, qwen_model = _qwen_server(m)
        locate_url = (m["locate_url"] or os.environ.get("LOCATE_URL")) if m["locate_anything"] else None
        print(f"  Qwen: {f'{qwen_url} ({qwen_model})' if qwen_url else 'OFF'} | "
              f"LocateAnything: {locate_url or 'OFF'} | YOLOE: {m['yoloe_model']}")
        # Two YOLOE models, not one with both prompt lists: YOLOE gives each box its best-
        # scoring prompt, so extinguisher decoys ("red bag", "red trash can") would take
        # obstacle boxes away from the exit rule.
        self.exits = self.ext = None
        if cfg["exits"]["enabled"]:
            a = self._args("exits", qwen_url, qwen_model)
            a.locate_url = locate_url
            fast = None if a.no_yoloe else exit_monitor.FastDetector(a.yoloe_model, cfg["exits"]["imgsz"])
            self.exits = exit_monitor.ExitMonitor(a, fast_detector=fast)
        if cfg["extinguishers"]["enabled"]:
            a = self._args("extinguishers", qwen_url, qwen_model)
            a.no_qwen = not qwen_url
            det = extinguisher_monitor.ExtinguisherDetector(a.yoloe_model, a.conf,
                                                            cfg["extinguishers"]["imgsz"])
            self.ext = extinguisher_monitor.ExtinguisherMonitor(a, detector=det)
        self._due = {task: 0.0 for task in TASKS}       # next time a task with an fps cap runs
        self._seen = {task: 0 for task in TASKS}        # events already copied from each monitor
        self.events: list = []                          # (video time, task, text, status), in order

    def _args(self, task: str, qwen_url, qwen_model):
        """That monitor's CLI defaults ← its section ← the shared settings."""
        c, o = self.cfg, self.cfg["output"]
        a = TASKS[task].build_parser().parse_args([c["input"]["source"]])
        options = _task_options(task)
        for key, value in c[task].items():
            if key in options:
                setattr(a, key, value)
        a.loop, a.proc_width = c["input"]["loop"], c["processing"]["proc_width"]
        a.yoloe_model, a.qwen_url, a.qwen_model = c["models"]["yoloe_model"], qwen_url, qwen_model
        a.no_display, a.stream, a.save = True, None, None    # the inspection owns these
        a.snapshots, a.log_frames, a.out_dir = o["snapshots"], o["log_frames"], o["out_dir"]
        return a

    def _monitors(self) -> list:
        return [(task, mon) for task, mon in (("exits", self.exits), ("extinguishers", self.ext)) if mon]

    def _is_due(self, task: str, now: float) -> bool:
        """The task's fps cap: run it on this frame?"""
        fps = self.cfg[task]["fps"]
        if fps <= 0:
            return True
        due, period = self._due[task], 1.0 / fps
        if now < due:
            return False
        self._due[task] = now + period if now - due > period else due + period
        return True

    # ── one frame ────────────────────────────────────────────────────────────
    def step(self, frame, vt: float):
        """Full-resolution BGR frame → (processed frame, {task: what it returned}, now)."""
        now = time.time()
        proc, ran = None, {}
        if self.exits and self._is_due("exits", now):
            proc, fast_info, la_merged, _ = self.exits.step(frame, vt)
            ran["exits"] = (fast_info, la_merged)
        if self.ext and self._is_due("extinguishers", now):
            proc, info, _ = self.ext.step(frame, vt)
            ran["extinguishers"] = info
        if proc is None:                     # both skipped (fps caps): same size as theirs
            H0, W0 = frame.shape[:2]
            scale = min(1.0, self.cfg["processing"]["proc_width"] / W0)
            W, H = int(W0 * scale) // 2 * 2, int(H0 * scale) // 2 * 2
            proc = frame if (W, H) == (W0, H0) else cv2.resize(frame, (W, H), interpolation=cv2.INTER_AREA)
        self._collect_events()
        return proc, ran, now

    def _collect_events(self) -> None:
        """Copy the monitors' new events into one log, in the order they happened."""
        for task, mon in self._monitors():
            self.events += [(t, task, msg, st) for t, msg, st in mon.events[self._seen[task]:]]
            self._seen[task] = len(mon.events)

    def frame_log(self, ran: dict) -> str:
        """One log line for both tasks (also clears the monitors' notes)."""
        parts = []
        if self.exits:
            parts.append("EXITS " + (self.exits.frame_log(*ran["exits"]) if "exits" in ran else "(skipped)"))
        if self.ext:
            parts.append("EXT " + (self.ext.frame_log(ran["extinguishers"])
                                   if "extinguishers" in ran else "(skipped)"))
        return " || ".join(parts)

    def draw(self, proc, now: float) -> np.ndarray:
        out = proc
        if self.exits:
            out = self.exits.draw(out, now)
        if self.ext:
            out = self.ext.draw(out)
        return out

    def view(self, proc, annotated, now: float, vt: float, fps: float) -> np.ndarray:
        """Original | annotated (or annotated only) at view_width, then one panel per task."""
        o = self.cfg["output"]
        top = cv2.hconcat([proc, annotated]) if o["show_original"] else annotated
        w = min(o["view_width"], top.shape[1]) // 2 * 2
        top = cv2.resize(top, (w, int(top.shape[0] * w / top.shape[1]) // 2 * 2),
                         interpolation=cv2.INTER_AREA)
        panels = []
        if self.exits:
            panels.append(self.exits.panel(w, now, vt, fps))
        if self.ext:
            panels.append(self.ext.panel(w, vt, fps))
        return cv2.vconcat([top, *panels])

    def state(self, W: int, H: int, now: float, vt: float, fps: float) -> dict:
        """JSON for the browser view (--stream → GET /state)."""
        st = {"video_time_s": round(vt, 2), "fps": round(fps, 1)}
        if self.exits:
            st["exits"] = self.exits.state(now, vt, fps)["exits"]
        if self.ext:
            st["extinguishers"] = self.ext.live_result(W, H)["inventory"]
        st["events"] = [{"video_time_s": round(t, 2), "task": task, "text": msg, "status": s}
                        for t, task, msg, s in self.events[-20:]]
        return st

    # ── video / camera ───────────────────────────────────────────────────────
    def run(self):
        c, o = self.cfg, self.cfg["output"]
        source = c["input"]["source"]
        video_io.CAMERA_SIZE = tuple(c["input"]["camera_size"])      # webcam sources only
        src = LiveSource(source, loop=c["input"]["loop"], timeout=c["input"]["stream_timeout"])
        stem = Path(source).stem if Path(source).is_file() else "live"
        out_dir = Path(o["out_dir"]) / f"fire_safety_{stem}"
        for task, mon in self._monitors():
            mon.snap_dir = out_dir / task
            mon.snap_dir.mkdir(parents=True, exist_ok=True)
        rate = c["processing"]["process_fps"]
        caps = ", ".join(f"{task} " + (f"≤ {c[task]['fps']:g} fps" if c[task]["fps"] > 0 else "every frame")
                         for task, _ in self._monitors())
        print(f"  Source: {source} | {src.width}x{src.height} @ {src.fps:.1f} fps | processing "
              f"{f'≤ {rate:g} fps' if rate > 0 else 'all it can'} at {c['processing']['proc_width']} px "
              f"wide ({caps}) | output → {out_dir}\n")

        if self.exits:
            self.exits.start_workers()
        streamer = MJPEGStreamer(o["stream"]).start() if o["stream"] else None
        period, slot = (1.0 / rate if rate > 0 else 0.0), 0.0
        writer, win, times, stamps = None, "Fire safety inspection (q: quit, space: pause)", [], deque(maxlen=30)
        try:
            while True:
                idx, frame, skipped = src.read()
                if frame is None:
                    break
                t0 = time.time()
                vt = src.video_time()
                proc, ran, now = self.step(frame, vt)
                annotated = self.draw(proc, now)
                times.append(time.time() - t0)
                stamps.append(t0)
                fps = (len(stamps) - 1) / max(1e-3, stamps[-1] - stamps[0])   # frames processed / s
                line = self.frame_log(ran)
                if o["log_frames"]:
                    print(f"  f{idx:5d} t={vt:6.2f}s {1000 * times[-1]:4.0f}ms | {line}", flush=True)
                combined = self.view(proc, annotated, now, vt, fps)

                if o["save"] and writer is None:
                    Path(o["save"]).parent.mkdir(parents=True, exist_ok=True)
                    writer = cv2.VideoWriter(o["save"], cv2.VideoWriter_fourcc(*"mp4v"), src.fps,
                                             (combined.shape[1], combined.shape[0]))
                if writer:
                    for _ in range(skipped + 1):            # skipped frames: keep real-time length
                        writer.write(combined)
                if streamer:
                    streamer.publish(combined)
                    streamer.publish(proc, "original")
                    streamer.publish(annotated, "annotated")
                    streamer.set_state(self.state(proc.shape[1], proc.shape[0], now, vt, fps))
                # process_fps: wait for the next slot (a fixed cadence; restarts after falling
                # behind); the source skips the frames in between
                slot = t0 + period if t0 - slot > period else slot + period
                wait = slot - time.time() if period else 0.0
                if o["display"]:
                    cv2.imshow(win, combined)
                    key = cv2.waitKey(max(1, int(1000 * wait))) & 0xFF
                    if key in (ord("q"), 27):
                        break
                    if key == ord(" "):
                        tp = time.time()
                        cv2.waitKey(0)
                        src.pause_clock(time.time() - tp)
                elif wait > 0:
                    time.sleep(wait)
        finally:
            src.release()
            if writer:
                writer.release()
            if streamer:
                streamer.stop()
            if o["display"]:
                cv2.destroyAllWindows()
            for task, mon in self._monitors():
                mon.shutdown(out_dir / task, src.idx + 1, src.dropped, times)
            self._collect_events()                   # reads that finished during shutdown
            self._report(out_dir, src, times)

    def _report(self, out_dir: Path, src, times: list) -> None:
        """One report for the inspection: both tasks' results and one event log."""
        sub = {task: json.loads((out_dir / task / "report.json").read_text())
               for task, _ in self._monitors()}
        exits = sub.get("exits", {}).get("exits", [])
        exts = sub.get("extinguishers", {}).get("extinguishers", [])
        rep = {"source": self.cfg["input"]["source"], "frames": src.idx + 1, "processed": len(times),
               "dropped": src.dropped,
               "loop_ms": round(1000 * float(np.mean(times)), 1) if times else None,
               "today": sub["extinguishers"]["today"] if self.ext else None,
               "summary": {"exits": dict(Counter(e["final_status"] for e in exits)) if self.exits else None,
                           "extinguishers": dict(Counter(e["status"] for e in exts)) if self.ext else None},
               "exits": exits, "extinguishers": exts,
               "events": [{"video_time_s": round(t, 2), "task": task, "event": msg}
                          for t, task, msg, _ in self.events],
               "task_reports": {task: str(out_dir / task / "report.json") for task in sub},
               "settings": self.cfg}
        path = out_dir / "report.json"
        path.write_text(json.dumps(rep, indent=2))

        def counts(items, key, names):
            n = Counter(i[key] for i in items)
            return f" ({', '.join(f'{n[s]} {t}' for s, t in names.items() if n[s])})" if n else ""
        parts = []
        if self.exits:
            parts.append(f"{len(exits)} exits{counts(exits, 'final_status', exit_monitor.STATUS_TEXT)}")
        if self.ext:
            parts.append(f"{len(exts)} extinguishers{counts(exts, 'status', EXT_STATUS_TEXT)}")
        print(f"\n  Fire safety inspection: {' | '.join(parts)} | {len(times)} frames processed")
        print(f"  Report: {path}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Fire safety inspection: exits and fire extinguishers on the same frames. "
                    "Settings: config.toml; these options override it.")
    ap.add_argument("source", nargs="?", default=None,
                    help="Video file, webcam index or stream URL (default: [input] source)")
    ap.add_argument("--config", default=None, help="Settings file (default: config.toml)")
    ap.add_argument("--set", action="append", default=[], metavar="SECTION.KEY=VALUE",
                    help="Override one setting, e.g. --set exits.hold=2 (repeatable)")
    ap.add_argument("--only", choices=list(TASKS), help="Run one task only")
    ap.add_argument("--process-fps", type=float, default=None,
                    help="Frames per second taken from the source (0 = all it can)")
    ap.add_argument("--proc-width", type=int, default=None, help="Processing width in px")
    ap.add_argument("--save", default=None, help="Record the view to this .mp4")
    ap.add_argument("--stream", type=int, nargs="?", const=8090, default=None, metavar="PORT",
                    help="Browser view on this port (default 8090)")
    ap.add_argument("--loop", action="store_true", help="Replay a video file until q")
    ap.add_argument("--no-display", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="No per-frame log line (events only)")
    return ap


def main():
    a = build_parser().parse_args()
    cfg = load_config(a.config, a.set)
    i, p, o = cfg["input"], cfg["processing"], cfg["output"]
    if a.source:
        i["source"] = a.source
    if a.loop:
        i["loop"] = True
    if a.only:
        for task in TASKS:
            cfg[task]["enabled"] = task == a.only
    if a.process_fps is not None:
        p["process_fps"] = a.process_fps
    if a.proc_width is not None:
        p["proc_width"] = a.proc_width
    if a.save:
        o["save"] = a.save
    if a.stream:
        o["stream"] = a.stream
    if a.no_display:
        o["display"] = False
    if a.quiet:
        o["log_frames"] = False
    check_config(cfg)
    FireSafetyInspection(cfg).run()


if __name__ == "__main__":
    main()
