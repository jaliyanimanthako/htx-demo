"""
Live mode: the browser plays the video (or camera) locally and sends frames;
the server runs the pipeline on each frame and returns only JSON (boxes, exit
statuses, new events). No video is streamed back, so the view never lags.

Two modes (chosen in the "start" message):
    exits         exit / obstruction monitor (exit_monitor.py)
    extinguisher  fire extinguishers + expiry / service date (extinguisher_monitor.py)

Frame message (binary WebSocket message), little-endian:
    uint32 seq | float64 video_time_s | JPEG bytes

The "ready" reply says how big the frames should be (send_width, jpeg_quality):
extinguisher mode needs full-resolution frames to read the dates on a sticker.
"""

import struct
import threading
import time
import urllib.request
import uuid

import cv2
import numpy as np

from backend import settings
from exit_monitor import ExitMonitor, build_parser

MODES = {
    # send_width / jpeg_quality: what the browser should send for this mode
    "exits": {"title": "Exit obstruction", "send_width": 960, "jpeg_quality": 0.75},
    "extinguisher": {"title": "Fire extinguisher", "send_width": 1920, "jpeg_quality": 0.9},
}

HEADER = struct.Struct("<Id")          # seq, video time


def parse_frame(message: bytes):
    """→ (seq, video_time_s, BGR image) or raises ValueError."""
    if len(message) <= HEADER.size:
        raise ValueError("frame message too short")
    seq, vt = HEADER.unpack_from(message)
    img = cv2.imdecode(np.frombuffer(message, np.uint8, offset=HEADER.size), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("could not decode the JPEG")
    return seq, vt, img


def _reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


class LiveSession:
    def __init__(self, detectors, mode="exits", use_yoloe=True, use_locate=True, use_qwen=True,
                 options: dict = None):
        """detectors: callable(mode) → the shared YOLOE detector for that mode."""
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r} — one of {', '.join(MODES)}")
        self.id = uuid.uuid4().hex[:8]
        self.mode = mode
        self.created = time.time()
        options = options or {}
        self.skipped = []
        if mode == "exits":
            self._init_exits(detectors, use_yoloe, use_locate, use_qwen, options)
        else:
            self._init_extinguisher(detectors, use_qwen, options)
        self.out_dir = settings.OUT_DIR / f"{mode}_live_{time.strftime('%Y%m%d-%H%M%S')}_{self.id}"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.monitor.snap_dir = self.out_dir
        self.monitor.start_workers()
        self._lock = threading.Lock()   # one frame at a time through the pipeline
        self._sent_events = 0
        self.frames = 0
        self.times = []

    def _qwen_url(self, use_qwen: bool):
        """Qwen URL if wanted and reachable; a server that is down is skipped (and reported)."""
        if not (use_qwen and settings.QWEN_URL):
            return None
        if not _reachable(settings.QWEN_URL.rstrip("/") + "/models"):
            self.skipped.append("Qwen")
            return None
        return settings.QWEN_URL

    def _init_exits(self, detectors, use_yoloe, use_locate, use_qwen, options):
        args = build_parser().parse_args(["live"])
        args.no_yoloe = not use_yoloe
        args.locate_url = settings.LOCATE_URL if use_locate else None
        if args.locate_url and not _reachable(args.locate_url.rstrip("/") + "/health"):
            args.locate_url = None
            self.skipped.append("LocateAnything")
        args.qwen_url = self._qwen_url(use_qwen)
        args.no_display, args.stream, args.save, args.log_frames = True, None, None, False
        for key in ("hold", "la_interval", "name_every", "recheck", "confirm_timeout"):
            if options.get(key) is not None:
                setattr(args, key, float(options[key]))
        if args.no_yoloe and not args.locate_url:
            raise ValueError("enable at least YOLOE or LocateAnything")
        self.args = args
        self.models = {"yoloe": not args.no_yoloe, "locate_anything": bool(args.locate_url),
                       "qwen": bool(args.qwen_url)}
        self.monitor = ExitMonitor(args, fast_detector=detectors("exits") if use_yoloe else None)

    def _init_extinguisher(self, detectors, use_qwen, options):
        import extinguisher_monitor as X
        args = X.build_parser().parse_args(["live"])
        args.qwen_url = self._qwen_url(use_qwen)
        args.no_qwen = not args.qwen_url
        args.no_display, args.stream, args.save, args.log_frames = True, None, None, False
        for key in ("min_px", "max_reads", "parallel", "soon_days", "service_months", "life_years"):
            if options.get(key) is not None:
                setattr(args, key, int(options[key]))
        if options.get("monthfirst") is not None:
            args.monthfirst = bool(options["monthfirst"])
        self.args = args
        self.models = {"yoloe": True, "locate_anything": False, "qwen": bool(args.qwen_url)}
        self.monitor = X.ExtinguisherMonitor(args, detector=detectors("extinguisher"))

    def ready_message(self) -> dict:
        return {"type": "ready", "id": self.id, "mode": self.mode, "models": self.models,
                "skipped": self.skipped, **{k: MODES[self.mode][k] for k in ("send_width", "jpeg_quality")}}

    def process(self, message: bytes) -> dict:
        seq, vt, img = parse_frame(message)
        with self._lock:
            t0 = time.time()
            if self.mode == "exits":
                proc, fast_info, la_merged, _ = self.monitor.step(img, vt)
            else:
                proc, fast_info, _ = self.monitor.step(img, vt)
                la_merged = 0
            H, W = proc.shape[:2]
            result = self.monitor.live_result(W, H)
            events = self.monitor.events[self._sent_events:]
            self._sent_events += len(events)
            self.frames += 1
            self.times.append(time.time() - t0)
        result.update({
            "type": "result", "mode": self.mode, "seq": seq, "t": vt,
            "proc_ms": round(1000 * (time.time() - t0), 1),
            "yoloe_ms": round(fast_info[1], 1) if fast_info else None,
            "locate_update": la_merged > 0,
            "events": [{"video_time_s": round(t, 2), "text": m, "status": st} for t, m, st in events],
        })
        return result

    def reset(self):
        with self._lock:
            self.monitor.reset()

    def close(self):
        with self._lock:
            self.monitor.shutdown(self.out_dir, self.frames, 0, self.times)
