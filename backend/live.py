"""
Live mode: the browser plays the video (or camera) locally and sends frames;
the server runs the pipeline on each frame and returns only JSON (boxes, exit
statuses, new events). No video is streamed back, so the view never lags.

Frame message (binary WebSocket message), little-endian:
    uint32 seq | float64 video_time_s | JPEG bytes
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
    def __init__(self, fast_detector, use_yoloe=True, use_locate=True, use_qwen=True,
                 options: dict = None):
        self.id = uuid.uuid4().hex[:8]
        self.created = time.time()
        options = options or {}
        args = build_parser().parse_args(["live"])
        args.no_yoloe = not use_yoloe
        args.locate_url = settings.LOCATE_URL if use_locate else None
        args.qwen_url = settings.QWEN_URL if use_qwen else None
        # A model server that is down is skipped (and reported) instead of failing the session.
        self.skipped = []
        if args.locate_url and not _reachable(args.locate_url.rstrip("/") + "/health"):
            args.locate_url = None
            self.skipped.append("LocateAnything")
        if args.qwen_url and not _reachable(args.qwen_url.rstrip("/") + "/models"):
            args.qwen_url = None
            self.skipped.append("Qwen")
        args.no_display, args.stream, args.save, args.log_frames = True, None, None, False
        for key in ("hold", "la_interval", "name_every", "recheck", "confirm_timeout"):
            if options.get(key) is not None:
                setattr(args, key, float(options[key]))
        if args.no_yoloe and not args.locate_url:
            raise ValueError("enable at least YOLOE or LocateAnything")
        self.args = args
        self.monitor = ExitMonitor(args, fast_detector=fast_detector if use_yoloe else None)
        self.out_dir = settings.OUT_DIR / f"exit_monitor_live_{time.strftime('%Y%m%d-%H%M%S')}_{self.id}"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.monitor.snap_dir = self.out_dir
        self.monitor.start_workers()
        self._lock = threading.Lock()   # one frame at a time through the pipeline
        self._sent_events = 0
        self.frames = 0
        self.times = []

    def process(self, message: bytes) -> dict:
        seq, vt, img = parse_frame(message)
        with self._lock:
            t0 = time.time()
            proc, fast_info, la_merged, _ = self.monitor.step(img, vt)
            H, W = proc.shape[:2]
            result = self.monitor.live_result(W, H)
            events = self.monitor.events[self._sent_events:]
            self._sent_events += len(events)
            self.frames += 1
            self.times.append(time.time() - t0)
        result.update({
            "type": "result", "seq": seq, "t": vt,
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
