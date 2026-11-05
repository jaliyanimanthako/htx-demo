"""
Sessions: one exit or extinguisher monitor in a background thread per session.

The monitor thread pushes into the session (latest JPEG per stream, latest state,
event list); the API reads from it. Nothing here blocks the detection loop.
"""

import re
import threading
import time
import uuid

import cv2

from backend import recording, settings
from backend.qwen_status import QwenUnavailable
from apps.exit_monitor import ExitMonitor, FastDetector, build_parser

STREAMS = ("original", "annotated", "combined")
_URL = re.compile(r"^(rtsp|rtsps|rtmp|http|https)://", re.I)


class SessionError(ValueError):
    pass


def resolve_source(source: str) -> str:
    """A stream URL, or a video file name inside BACKEND_VIDEO_DIR (no paths outside it)."""
    source = source.strip()
    if source == settings.EXTERNAL_SOURCE:
        source = settings.external_camera()["url"].strip()
        if not _URL.match(source):
            raise SessionError("External camera is not configured. Set sources.external_camera.url in config.toml")
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
                 use_qwen: bool, options: dict, fast_detector, mode="exits", demo_client_id=None, require_qwen=False):
        self.mode = mode
        self.demo_client_id = demo_client_id
        self.qwen_requested = use_qwen
        self.require_qwen = require_qwen
        self.id = uuid.uuid4().hex[:8]
        self.source_name = source
        self.created = time.time()
        self.status = "starting"            # starting → running → finished | stopped | error
        self.error = None
        self.error_code = None
        self._frames = {}                   # stream name → (jpeg bytes, seq)
        self._state = {}
        self._events = []
        self._lock = threading.Lock()
        self._stop_requested = False

        if mode == "extinguisher":
            from apps import extinguisher_monitor as X
            parser = X.build_parser()
        else:
            parser = build_parser()
        args = parser.parse_args([resolve_source(source)])
        settings.apply_monitor_config(args, "extinguishers" if mode == "extinguisher" else "exits", options)
        args.ext_options = options
        args.loop = loop and source != settings.EXTERNAL_SOURCE
        args.no_yoloe = False if mode in ("extinguisher", "combined") else not use_yoloe
        args.locate_url = settings.LOCATE_URL if use_locate and mode in ("exits", "combined") else None
        args.qwen_url = settings.QWEN_URL if use_qwen else None
        args.qwen_requested = use_qwen
        args.require_qwen = require_qwen
        if mode == "extinguisher":
            args.no_qwen = not use_qwen
        args.no_display, args.stream, args.save, args.log_frames = True, None, None, False
        args.out_dir = str(settings.OUT_DIR / f"combined_{self.id}" if mode == "combined" else settings.OUT_DIR)
        for key in (() if mode == "extinguisher" else ("hold", "la_interval", "name_every", "recheck", "confirm_timeout")):
            if options.get(key) is not None:
                setattr(args, key, float(options[key]))
        if not (args.locate_url or not args.no_yoloe):
            raise SessionError("enable at least YOLOE or LocateAnything")
        self.args = args
        self._fast = fast_detector if use_yoloe or mode in ("extinguisher", "combined") else None
        self.monitor = None
        self.recorder = None              # camera / stream sources only (backend/recording.py)
        self.thread = threading.Thread(target=self._run, name=f"session-{self.id}", daemon=True)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> "Session":
        self.thread.start()
        return self

    def _run(self):
        try:
            if self._stop_requested:
                self.status = "stopped"
                return
            if recording.enabled() and _URL.match(self.args.source):
                self.recorder = recording.Recorder(
                    f"{self.mode}_stream_{time.strftime('%Y%m%d-%H%M%S')}_{self.id}")
            if self.mode == "combined":
                from backend.combined import CombinedMonitor
                self.monitor = CombinedMonitor(self.args, self._fast, self._on_frame, self._on_event)
            elif self.mode == "extinguisher":
                from apps import extinguisher_monitor as X
                self.monitor = X.ExtinguisherMonitor(self.args, detector=self._fast,
                    on_frame=self._on_frame, on_event=self._on_event)
            else:
                self.monitor = ExitMonitor(self.args, on_frame=self._on_frame,
                                           on_event=self._on_event, fast_detector=self._fast)
            if self._stop_requested:
                if self.mode == "extinguisher":
                    self.monitor.pool.shutdown(wait=False, cancel_futures=True)
                self.status = "stopped"
                return
            self.status = "running"
            self.monitor.run()
            self.status = "stopped" if self._stop_requested else "finished"
        except (Exception, SystemExit) as e:
            if self._stop_requested:
                self.status = "stopped"
                return
            self.error_code = "qwen_unavailable" if isinstance(e, QwenUnavailable) else "demo_failed"
            self.status, self.error = "error", f"{type(e).__name__}: {e}"
            print(f"  [session {self.id}] error: {self.error}")
        finally:
            if self.recorder:
                self.recorder.close()

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
            self._state = {**state, "mode": self.mode}
        if self.recorder:
            self.recorder.add(original, annotated, video_time=state.get("video_time_s"))

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
        return {"id": self.id, "demo_client_id": self.demo_client_id, "mode": self.mode, "source": self.source_name, "status": self.status,
                "error": self.error, "error_code": self.error_code, "created": self.created, "loop": self.args.loop,
                "models": {"yoloe": not self.args.no_yoloe,
                           "locate_anything": bool(self.args.locate_url),
                           "qwen": (bool(getattr(self.monitor, "qwen_active", False)) if self.mode == "combined" else bool(self.args.qwen_url)),
                           "qwen_requested": self.qwen_requested},
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
            self._fast = FastDetector(settings.apply_monitor_config(build_parser().parse_args(["x"]), "exits").yoloe_model,
                                      settings.CONFIG["exits"]["imgsz"])
        return self._fast

    def _detector(self, mode: str):
        if mode == "exits":
            return self._fast_detector()
        if self._ext is None:
            from apps import extinguisher_monitor as X
            a = settings.apply_monitor_config(X.build_parser().parse_args(["x"]), "extinguishers")
            self._ext = X.ExtinguisherDetector(a.yoloe_model, a.conf, settings.CONFIG["extinguishers"]["imgsz"])
        return self._ext

    def create(self, source: str, loop: bool = True, use_yoloe: bool = True,
               use_locate: bool = True, use_qwen: bool = True, options: dict = None,
               mode: str = "exits", demo_client_id: str = None, require_qwen: bool = False) -> Session:
        if mode not in ("exits", "extinguisher", "combined"):
            raise SessionError("mode must be exits, extinguisher or combined")
        resolve_source(source)
        with self._lock:
            running = [s for s in self.sessions.values() if not s.done] + list(self.live.values())
            # Retry of an accepted request whose response was lost: reuse only
            # this tablet's matching demo, while holding the creation lock.
            if demo_client_id:
                for existing in self.sessions.values():
                    if (not existing.done and existing.demo_client_id == demo_client_id
                            and existing.source_name == source and existing.mode == mode
                            and existing.qwen_requested == use_qwen
                            and existing.require_qwen == require_qwen):
                        return existing
            if len(running) >= settings.MAX_SESSIONS:
                raise SessionError(f"already running: {', '.join(s.id for s in running)} "
                                   f"(max {settings.MAX_SESSIONS}) — stop it first")
            fast = ((self._detector("exits"), self._detector("extinguisher")) if mode == "combined"
                    else self._detector(mode) if use_yoloe or mode == "extinguisher" else None)
            session = Session(source, loop, use_yoloe, use_locate, use_qwen, options or {}, fast, mode, demo_client_id, require_qwen)
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
        # Keep the slot occupied until both pipelines have released their work.
        try:
            live.close()
        finally:
            with self._lock:
                self.live.pop(live.id, None)

    def get(self, sid: str) -> Session:
        return self.sessions.get(sid)

    def stop_all(self):
        for s in self.sessions.values():
            s.stop()
