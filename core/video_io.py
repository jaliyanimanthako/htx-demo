"""
Video input and camera motion.

  LiveSource          file / webcam / RTSP; files are paced to real time and frames
                      are dropped when processing falls behind (behaves like a camera)
  MotionCompensator   frame-to-frame camera motion (optical flow + similarity
                      transform), accumulated so boxes from an older frame can be
                      mapped onto the current one
"""

import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

MOTION_WIDTH = 480   # frames are downscaled to this width for motion estimation


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
    """

    def __init__(self, source: str, loop: bool = False):
        self.is_file = Path(source).is_file()
        self.loop    = loop and self.is_file
        self.cap     = cv2.VideoCapture(int(source) if source.isdigit() else source)
        if not self.cap.isOpened():
            raise RuntimeError(f"Could not open source: {source}")
        self.fps     = self.cap.get(cv2.CAP_PROP_FPS) or 25.0
        self.width   = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height  = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.idx     = -1       # stream index of the last frame returned (monotonic)
        self.offset  = 0        # stream index where the current pass over the file began
        self.loops   = 0
        self.dropped = 0
        self._t0     = None

    def video_time(self) -> float:
        """Position of the last returned frame within the file, in seconds."""
        return (self.idx - self.offset) / self.fps

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
            ok, frame = self.cap.read()
            if not ok:
                return None, None, 0
            self.idx += 1
            return self.idx, frame, 0

        if self._t0 is None:
            self._t0 = time.time()
        target = int((time.time() - self._t0) * self.fps)
        if target <= self.idx:                      # ahead of real time → wait
            time.sleep((self.idx + 1) / self.fps - (time.time() - self._t0))
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
        self.cap.release()


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
