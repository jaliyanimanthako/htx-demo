"""
Sessions: one ExitMonitor running in a background thread per session.

The monitor thread pushes into the session (latest JPEG per stream, latest state,
event list); the API reads from it. Nothing here blocks the detection loop.
"""

import re
import threading
import time
import uuid

import cv2

from backend import settings
from exit_monitor import ExitMonitor, FastDetector, build_parser

STREAMS = ("original", "annotated", "combined")
_URL = re.compile(r"^(rtsp|rtsps|rtmp|http|https)://", re.I)


class SessionError(ValueError):
    pass


def resolve_source(source: str) -> str:
    """A stream URL, or a video file name inside BACKEND_VIDEO_DIR (no paths outside it)."""
    source = source.strip()
    if _URL.match(source):
        return source
    path = (settings.VIDEO_DIR / source).resolve()
    if settings.VIDEO_DIR not in path.parents and path != settings.VIDEO_DIR:
        raise SessionError("source must be a video in the video folder or a stream URL")
    if not path.is_file() or path.suffix.lower() not in settings.VIDEO_EXTS:
        raise SessionError(f"video not found: {source}")
    return str(path)


def list_videos() -> list:
    return sorted(p.name for p in settings.VIDEO_DIR.iterdir()
                  if p.is_file() and p.suffix.lower() in settings.VIDEO_EXTS)


class Session:
    def __init__(self, source: str, loop: bool, use_yoloe: bool, use_locate: bool,
                 use_qwen: bool, options: dict, fast_detector):
        self.id = uuid.uuid4().hex[:8]
        self.source_name = source
        self.created = time.time()
        self.status = "starting"            # starting → running → finished | stopped | error
        self.error = None
        self._frames = {}                   # stream name → (jpeg bytes, seq)
        self._state = {}
        self._events = []
        self._lock = threading.Lock()
        self._stop_requested = False

        args = build_parser().parse_args([resolve_source(source)])
        args.loop = loop
        args.no_yoloe = not use_yoloe
        args.locate_url = settings.LOCATE_URL if use_locate else None
        args.qwen_url = settings.QWEN_URL if use_qwen else None
        args.no_display, args.stream, args.save, args.log_frames = True, None, None, False
        args.out_dir = str(settings.OUT_DIR)
        for key in ("hold", "la_interval", "name_every", "recheck", "confirm_timeout"):
            if options.get(key) is not None:
                setattr(args, key, float(options[key]))
        if not (args.locate_url or not args.no_yoloe):
            raise SessionError("enable at least YOLOE or LocateAnything")
        self.args = args
        self._fast = fast_detector if use_yoloe else None
        self.monitor = None
        self.thread = threading.Thread(target=self._run, name=f"session-{self.id}", daemon=True)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> "Session":
        self.thread.start()
        return self

    def _run(self):
        try:
            self.monitor = ExitMonitor(self.args, on_frame=self._on_frame,
                                       on_event=self._on_event, fast_detector=self._fast)
            if self._stop_requested:
                self.status = "stopped"
                return
            self.status = "running"
            self.monitor.run()
            self.status = "stopped" if self._stop_requested else "finished"
        except Exception as e:                       # models down, bad stream, …
            self.status, self.error = "error", f"{type(e).__name__}: {e}"
            print(f"  [session {self.id}] error: {self.error}")

    def stop(self):
        self._stop_requested = True
        if self.monitor:
            self.monitor.request_stop()

    @property
    def done(self) -> bool:
        return self.status in ("finished", "stopped", "error")

    # ── data from the monitor thread ─────────────────────────────────────────
    def _on_frame(self, original, annotated, combined, state):
        encoded = {}
        for name, img in (("original", original), ("annotated", annotated), ("combined", combined)):
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                encoded[name] = buf.tobytes()
        with self._lock:
            for name, jpeg in encoded.items():
                self._frames[name] = (jpeg, self._frames.get(name, (None, 0))[1] + 1)
            self._state = state

    def _on_event(self, event: dict):
        with self._lock:
            self._events.append({**event, "seq": len(self._events)})

    # ── reads for the API ────────────────────────────────────────────────────
    def frame(self, name: str):
        with self._lock:
            return self._frames.get(name, (None, 0))

    def events_since(self, index: int) -> list:
        with self._lock:
            return self._events[index:]

    def info(self) -> dict:
        with self._lock:
            state, n_events = dict(self._state), len(self._events)
        report = self.monitor.report_path if self.monitor else None
        return {"id": self.id, "source": self.source_name, "status": self.status,
                "error": self.error, "created": self.created, "loop": self.args.loop,
                "models": {"yoloe": not self.args.no_yoloe,
                           "locate_anything": bool(self.args.locate_url),
                           "qwen": bool(self.args.qwen_url)},
                "events": n_events, "state": state,
                "report_ready": bool(report)}      # GET …/report; no server paths to clients


class SessionManager:
    def __init__(self):
        self.sessions: dict = {}
        self.live: dict = {}                # live (browser-fed) sessions, see backend/live.py
        self._lock = threading.Lock()
        self._fast = None                   # YOLOE loaded once, shared by sessions
        self._ext = None                    # YOLOE with the extinguisher classes

    def _fast_detector(self):
        if self._fast is None:
            self._fast = FastDetector(build_parser().parse_args(["x"]).yoloe_model)
        return self._fast

    def _detector(self, mode: str):
        if mode == "exits":
            return self._fast_detector()
        if self._ext is None:
            import extinguisher_monitor as X
            a = X.build_parser().parse_args(["x"])
            self._ext = X.ExtinguisherDetector(a.yoloe_model, a.conf)
        return self._ext

    def create(self, source: str, loop: bool = True, use_yoloe: bool = True,
               use_locate: bool = True, use_qwen: bool = True, options: dict = None) -> Session:
        with self._lock:
            running = [s for s in self.sessions.values() if not s.done] + list(self.live.values())
            if len(running) >= settings.MAX_SESSIONS:
                raise SessionError(f"already running: {', '.join(s.id for s in running)} "
                                   f"(max {settings.MAX_SESSIONS}) — stop it first")
            fast = self._fast_detector() if use_yoloe else None
            session = Session(source, loop, use_yoloe, use_locate, use_qwen, options or {}, fast)
            self.sessions[session.id] = session
        return session.start()

    def create_live(self, mode="exits", use_yoloe=True, use_locate=True, use_qwen=True,
                    options=None):
        from backend.live import LiveSession
        with self._lock:
            running = [s for s in self.sessions.values() if not s.done] + list(self.live.values())
            if len(running) >= settings.MAX_SESSIONS:
                raise SessionError(f"already running: {', '.join(s.id for s in running)} "
                                   f"(max {settings.MAX_SESSIONS}) — stop it first")
        live = LiveSession(self._detector, mode, use_yoloe, use_locate, use_qwen, options)
        with self._lock:
            self.live[live.id] = live
        return live

    def close_live(self, live) -> None:
        with self._lock:
            self.live.pop(live.id, None)
        live.close()

    def get(self, sid: str) -> Session:
        return self.sessions.get(sid)

    def stop_all(self):
        for s in self.sessions.values():
            s.stop()
