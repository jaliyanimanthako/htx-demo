"""
Video input and camera motion.

  LiveSource          file / webcam / stream URL; files are paced to real time and frames
                      are dropped when processing falls behind (behaves like a camera);
                      webcams and streams are read by a background thread that keeps
                      only the newest frame and reconnects when the stream drops
  MotionCompensator   frame-to-frame camera motion (optical flow + similarity
                      transform), accumulated so boxes from an older frame can be
                      mapped onto the current one
"""

import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

MOTION_WIDTH = 480   # frames are downscaled to this width for motion estimation

STREAM_TIMEOUT  = 30.0           # webcam / stream: give up after this long without a frame (s)
RECONNECT_DELAY = 2.0            # pause between reconnect attempts (s)
STREAM_IO_MS    = 5000           # FFmpeg open / read timeout, so a dead stream can't hang a read
CAMERA_SIZE     = (1920, 1080)   # requested from a webcam (it picks the nearest it supports)
# Low-latency input for stream URLs. OpenCV reads OPENCV_FFMPEG_CAPTURE_OPTIONS when a
# capture is opened; setting it drops OpenCV's default (prefer TCP for RTSP), so TCP is
# asked for here. A value already set in the environment wins.
FFMPEG_OPTIONS  = "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay"


MIN_INLIERS  = 15    # below this the motion estimate is ignored


class MotionCompensator:
    """
    Tracks camera motion frame-to-frame (sparse optical flow + RANSAC similarity
    transform) and accumulates it into C, the transform from a fixed origin to
    the current frame. A VLM result for frame t is mapped to the current frame
    with C_now · C_t⁻¹, so large motion over the API latency (walking, zooming)
    is handled as a chain of small, reliable steps.
    """

    def __init__(self, frame_w: int):
        self.scale     = min(1.0, MOTION_WIDTH / frame_w)
        self.prev_gray = None
        self.C         = np.eye(3)   # origin → current frame (small-image coords)
        self.C_ref_inv = None        # inverse of C at the reference (sent) frame

    def _small_gray(self, frame):
        small = cv2.resize(frame, None, fx=self.scale, fy=self.scale,
                           interpolation=cv2.INTER_AREA) if self.scale < 1 else frame
        return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

    def update(self, frame) -> np.ndarray:
        """Fold motion previous → this frame into C; returns a copy of C."""
        gray = self._small_gray(frame)
        if self.prev_gray is not None:
            pts = cv2.goodFeaturesToTrack(self.prev_gray, maxCorners=400,
                                          qualityLevel=0.01, minDistance=8)
            if pts is not None and len(pts) >= MIN_INLIERS:
                nxt, st, _ = cv2.calcOpticalFlowPyrLK(self.prev_gray, gray, pts, None,
                                                      winSize=(21, 21), maxLevel=3)
                good = st.ravel() == 1
                if good.sum() >= MIN_INLIERS:
                    M, inliers = cv2.estimateAffinePartial2D(
                        pts[good], nxt[good], method=cv2.RANSAC, ransacReprojThreshold=3.0)
                    if M is not None and int(inliers.sum()) >= MIN_INLIERS:
                        self.C = np.vstack([M, [0, 0, 1]]) @ self.C
        self.prev_gray = gray
        return self.C.copy()

    def set_reference(self, C_ref: np.ndarray) -> None:
        self.C_ref_inv = np.linalg.inv(C_ref)

    def warp(self, detections: list, img_w: int, img_h: int) -> list:
        if self.C_ref_inv is None:
            return detections
        M = (self.C @ self.C_ref_inv)[:2].astype(np.float32)
        warped = []
        for det in detections:
            bb  = det["bounding_box"]
            pts = np.float32([[bb["x1"], bb["y1"]], [bb["x2"], bb["y1"]],
                              [bb["x1"], bb["y2"]], [bb["x2"], bb["y2"]]]) * self.scale
            pts = cv2.transform(pts[None], M)[0] / self.scale
            x1, y1 = pts.min(axis=0)
            x2, y2 = pts.max(axis=0)
            x1, x2 = int(max(0, x1)), int(min(img_w, x2))
            y1, y2 = int(max(0, y1)), int(min(img_h, y2))
            if x2 - x1 < 4 or y2 - y1 < 4:
                continue   # moved out of view
            warped.append({**det, "bounding_box": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}})
        return warped


class LiveSource:
    """
    Wraps cv2.VideoCapture. Files are paced to wall-clock time and frames that
    the consumer is too slow for are dropped, so a file behaves like a camera.

    Webcams and stream URLs are read by a background thread that keeps only the
    newest frame, so latency can't build up when processing is slower than the
    camera. It reconnects when the stream drops and gives up after `timeout` s
    without a frame. Once `halt` (a threading.Event) is set, read() returns end of
    stream, also when it is already waiting for a frame.
    """

    def __init__(self, source: str, loop: bool = False, halt: threading.Event = None,
                 timeout: float = STREAM_TIMEOUT):
        self.source  = source
        self.is_file = Path(source).is_file()
        self.loop    = loop and self.is_file
        self.idx     = -1       # stream index of the last frame returned (monotonic)
        self.offset  = 0        # stream index where the current pass over the file began
        self.loops   = 0
        self.dropped = 0
        self._t0     = None
        if not self.is_file:
            self._start_live(halt, timeout)
            return
        self.cap     = cv2.VideoCapture(source)
        if not self.cap.isOpened():
            raise RuntimeError(f"Could not open source: {source}")
        self.fps     = self.cap.get(cv2.CAP_PROP_FPS) or 25.0
        self.width   = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height  = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    def video_time(self) -> float:
        """Position of the last returned frame within the file, in seconds
        (webcam / stream: wall-clock time since the first frame)."""
        if not self.is_file:
            return self._t_last - self._t_first if self._t_last else 0.0
        return (self.idx - self.offset) / self.fps

    # ── webcam / stream URL ──────────────────────────────────────────────────
    def _start_live(self, halt, timeout: float) -> None:
        self.timeout    = timeout
        self.reconnects = 0
        self.ended      = False     # grabber gave up (no frames for `timeout` s)
        self._halt      = halt
        self._stop      = threading.Event()
        self._cond      = threading.Condition()
        self._frame     = None      # newest frame
        self._count     = 0         # frames grabbed so far
        self._t_frame   = None      # arrival time of the newest frame
        self._t_first   = None
        self._t_last    = None      # arrival time of the frame last returned by read()
        self._cap_fps   = None
        self._thread    = threading.Thread(target=self._grab_loop, daemon=True,
                                           name="live-source")
        self._thread.start()
        with self._cond:
            while not (self._count or self.ended or self._halted()):
                self._cond.wait(0.2)
            frame, cap_fps = self._frame, self._cap_fps
        if frame is None:
            self.release()
            if self._halted():
                raise RuntimeError(f"Stopped while waiting for {self.source}")
            raise RuntimeError(f"No frames from {self.source} after {timeout:.0f} s — is the "
                               "camera streaming? (see docs/live-camera.md)")
        self.height, self.width = frame.shape[:2]
        self.fps = cap_fps if cap_fps and 1 <= cap_fps <= 120 else 30.0   # RTSP can report 90000

    def _halted(self) -> bool:
        return self._halt is not None and self._halt.is_set()

    def _open(self):
        if self.source.isdigit():
            cap = cv2.VideoCapture(int(self.source))
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))   # 1080p30 over V4L2
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_SIZE[0])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_SIZE[1])
        else:
            os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", FFMPEG_OPTIONS)
            cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG,
                                   [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, STREAM_IO_MS,
                                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, STREAM_IO_MS])
        if not cap.isOpened():
            cap.release()
            return None
        self._cap_fps = cap.get(cv2.CAP_PROP_FPS)
        return cap

    def _grab_loop(self) -> None:
        """Background thread: read continuously, keep the newest frame, reconnect on failure."""
        cap, t_lost, waiting = None, time.time(), False   # "lost" since start until the first frame
        while not self._stop.is_set():
            if cap is None:
                cap = self._open()
            ok, frame = cap.read() if cap is not None else (False, None)
            now = time.time()
            if ok:
                if t_lost is not None and self._count:
                    print(f"  stream back after {now - t_lost:.1f}s")
                t_lost = None
                with self._cond:
                    self._frame, self._t_frame = frame, now
                    self._t_first = self._t_first or now
                    self._count += 1
                    self._cond.notify_all()
                continue
            if cap is not None:
                cap.release()
                cap = None
            if self._stop.is_set():
                break
            if t_lost is None:
                t_lost = now
                self.reconnects += 1
                print(f"  stream lost — reconnecting ({self.source})...")
            elif not self._count and not waiting:
                waiting = True
                print(f"  waiting for {self.source} (up to {self.timeout:.0f}s)...")
            if now - t_lost >= self.timeout:
                print(f"  no frames for {self.timeout:.0f}s — giving up")
                break
            self._stop.wait(RECONNECT_DELAY)
        if cap is not None:
            cap.release()
        with self._cond:
            self.ended = True
            self._cond.notify_all()

    def _read_live(self):
        with self._cond:
            while self._count <= self.idx + 1 and not (self.ended or self._halted()):
                self._cond.wait(0.2)
            if self._halted() or self._count <= self.idx + 1:   # halted, or ended with nothing new
                return None, None, 0
            skipped = self._count - self.idx - 2         # grabbed since the last read, minus this one
            self.idx, frame, self._t_last = self._count - 1, self._frame, self._t_frame
        self.dropped += skipped
        return self.idx, frame, skipped

    def _advance(self, decode: bool):
        ok = self.cap.grab()
        if not ok and self.loop and self.idx >= self.offset:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            self.offset = self.idx + 1
            self.loops += 1
            ok = self.cap.grab()
        if not ok:
            return False, None
        self.idx += 1
        if not decode:
            return True, None
        return self.cap.retrieve()

    def read(self):
        """Returns (frame_idx, frame, n_skipped) or (None, None, 0) at end of stream."""
        if not self.is_file:
            return self._read_live()

        if self._t0 is None:
            self._t0 = time.time()
        target = int((time.time() - self._t0) * self.fps)
        if target <= self.idx:                      # ahead of real time → wait
            time.sleep(max(0.0, (self.idx + 1) / self.fps - (time.time() - self._t0)))
            target = self.idx + 1
        skipped = 0
        while self.idx + 1 < target:                # behind real time → drop frames
            if not self._advance(decode=False)[0]:
                return None, None, 0
            skipped += 1
        ok, frame = self._advance(decode=True)
        if not ok:
            return None, None, 0
        self.dropped += skipped
        return self.idx, frame, skipped

    def pause_clock(self, seconds: float) -> None:
        if self._t0 is not None:
            self._t0 += seconds

    def release(self):
        if self.is_file:
            self.cap.release()
            return
        self._stop.set()                                 # the thread releases its capture
        with self._cond:
            self._cond.notify_all()
        self._thread.join(timeout=STREAM_IO_MS / 1000 + 1)


def warp_boxes(detections: list, M: np.ndarray, motion_scale: float,
                out_scale: float, out_w: int, out_h: int) -> list:
    """Map boxes (source pixels) through M (3x3, motion coords) into output pixels."""
    M2 = M[:2].astype(np.float32)
    warped = []
    for det in detections:
        bb  = det["bounding_box"]
        pts = np.float32([[bb["x1"], bb["y1"]], [bb["x2"], bb["y1"]],
                          [bb["x1"], bb["y2"]], [bb["x2"], bb["y2"]]]) * motion_scale
        pts = cv2.transform(pts[None], M2)[0] / motion_scale * out_scale
        x1, y1 = pts.min(axis=0)
        x2, y2 = pts.max(axis=0)
        x1, x2 = int(max(0, x1)), int(min(out_w, x2))
        y1, y2 = int(max(0, y1)), int(min(out_h, y2))
        if x2 - x1 >= 4 and y2 - y1 >= 4:
            warped.append({**det, "bounding_box": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}})
    return warped


def change_signature(frame):
    """Small blurred grayscale thumbnail used to measure scene change."""
    gray = cv2.cvtColor(cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA),
                        cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gray, (5, 5), 0)


def resize_for_api(frame, max_side: int) -> Image.Image:
    h, w = frame.shape[:2]
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        frame = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
