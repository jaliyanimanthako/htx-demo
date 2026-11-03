"""
Session recordings: each camera / stream session is saved as two videos in
<out_dir>/recordings/ ([server] record, record_fps in config.toml):

    <name>_original.mkv   the frames the pipeline analysed (replay: python run.py inspect <file>)
    <name>_annotated.mkv  the same frames as the viewer saw them, with boxes and statuses

Frames are encoded on a background thread, so recording never slows the pipeline.
The videos play in real time: the previous frame repeats until the next one
arrives, and frames that arrive faster than record_fps are left out.
MKV, not MP4: a backend that is killed still leaves a playable file.
"""

import queue
import threading
import time

import cv2
import numpy as np

from backend import settings
from backend.annotations import draw_annotations

KINDS = ("original", "annotated")
_open = set()                       # recorders not closed yet; close_all() at backend shutdown
_open_lock = threading.Lock()


def enabled() -> bool:
    return settings.CONFIG["server"]["record"]


def _fit(img, size):
    """img letterboxed into size (w, h) — the frame size can change mid-session (tablet rotated)."""
    w, h = size
    ih, iw = img.shape[:2]
    if (iw, ih) == (w, h):
        return img
    s = min(w / iw, h / ih)
    nw, nh = max(1, round(iw * s)), max(1, round(ih * s))
    out = np.zeros((h, w, 3), np.uint8)
    x, y = (w - nw) // 2, (h - nh) // 2
    out[y:y + nh, x:x + nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    return out


class Recorder:
    def __init__(self, name: str):
        self.paths = {kind: settings.OUT_DIR / "recordings" / f"{name}_{kind}.mkv" for kind in KINDS}
        self.fps = settings.CONFIG["server"]["record_fps"]
        self._queue = queue.Queue(maxsize=8)
        self._closing = threading.Event()
        self._writers, self._sizes = {}, {}
        self._last = None              # {kind: frame} written until the next frame arrives
        self._t0 = None
        self._written = 0
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"record-{name}")
        self._thread.start()
        with _open_lock:
            _open.add(self)

    def add(self, original, annotated=None, result=None):
        """Queue one frame; annotated=None draws `result` (live-mode JSON) onto original."""
        if self._closing.is_set():
            return
        try:
            self._queue.put_nowait((time.monotonic(), original, annotated, result))
        except queue.Full:
            pass                        # encoder behind: the previous frame repeats instead

    def close(self):
        """Write what is queued and finish both files (no files if no frame arrived)."""
        self._closing.set()
        self._thread.join(timeout=30)
        with _open_lock:
            _open.discard(self)

    def _loop(self):
        try:
            while not (self._closing.is_set() and self._queue.empty()):
                try:
                    t, original, annotated, result = self._queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                if annotated is None:
                    annotated = draw_annotations(original, result)
                frames = {"original": original, "annotated": annotated}
                if self._last is None:
                    self._start(t, frames)
                else:
                    self._repeat_last(t)
                self._last = frames
            if self._last is not None:
                self._repeat_last(time.monotonic(), at_least=1)
            if self._writers:
                print(f"  Recording: {', '.join(str(p) for p in self.paths.values())}")
        except Exception as e:
            print(f"  [recording] stopped: {type(e).__name__}: {e}")
        finally:
            for writer in self._writers.values():
                writer.release()

    def _start(self, t, frames):
        self.paths["original"].parent.mkdir(parents=True, exist_ok=True)
        for kind, frame in frames.items():
            h, w = frame.shape[:2]
            self._sizes[kind] = (w // 2 * 2, h // 2 * 2)   # MPEG-4 needs even sizes
            writer = cv2.VideoWriter(str(self.paths[kind]), cv2.VideoWriter_fourcc(*"mp4v"),
                                     self.fps, self._sizes[kind])
            if not writer.isOpened():
                raise RuntimeError(f"cannot write {self.paths[kind]}")
            self._writers[kind] = writer
        self._t0 = t

    def _repeat_last(self, t, at_least=0):
        n = max(at_least, round((t - self._t0) * self.fps) - self._written)
        for kind, writer in self._writers.items():
            frame = _fit(self._last[kind], self._sizes[kind])
            for _ in range(n):
                writer.write(frame)
        self._written += n


def close_all():
    """Backend shutdown: finish every recording that is still open."""
    with _open_lock:
        recorders = list(_open)
    for recorder in recorders:
        recorder.close()
