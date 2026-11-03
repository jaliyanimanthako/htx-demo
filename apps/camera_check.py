"""
Camera / stream check
=====================
Opens a source exactly the way every runner does (core/video_io.py:LiveSource)
and shows it without loading any model, so the camera link can be checked on its
own before running detection (docs/live-camera.md).

Shows the resolution, the rate frames arrive at, frames dropped (newer ones
arrived while one was being shown) and reconnects; prints a summary on exit.

Usage:
  python run.py camera-check rtsp://<windows-host>:8554/live/x2    # Pocket 3 via MediaMTX
  python run.py camera-check rtsp://<windows-host>:8554/live/x2 --clock   # measure latency
  python run.py camera-check 0                                     # webcam 0 (USB webcam mode)
  python run.py camera-check rtsp://... --no-display --seconds 30  # headless: numbers only
  q / Esc quits.

--clock draws a large clock on the window. Point the camera at the window: the
filmed clock lags the drawn one by the end-to-end (glass-to-glass) latency.
"""

import argparse
import time
from collections import deque

import cv2

from core.drawing import caption
from core.video_io import LiveSource


def _rate(recent) -> float:
    """Frames per second at the source, from (wall time, frame index) pairs."""
    (t0, i0), (t1, i1) = recent[0], recent[-1]
    return (i1 - i0) / (t1 - t0) if t1 > t0 else 0.0


def _draw_clock(frame, now: float) -> None:
    text = time.strftime("%H:%M:%S", time.localtime(now)) + f".{int(now % 1 * 1000):03d}"
    scale = frame.shape[1] / 600
    thick = max(2, round(2 * scale))
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
    cv2.rectangle(frame, (0, 0), (tw + 24, th + 24), (0, 0, 0), -1)
    cv2.putText(frame, text, (12, th + 12), cv2.FONT_HERSHEY_SIMPLEX, scale,
                (255, 255, 255), thick, cv2.LINE_AA)


def main():
    ap = argparse.ArgumentParser(description="Show a camera / stream without models (link check)")
    ap.add_argument("source", help="Video file, webcam index (0) or stream URL")
    ap.add_argument("--seconds", type=float, default=0,
                    help="Stop after this many seconds (default: run until q)")
    ap.add_argument("--clock", action="store_true",
                    help="Draw a large clock; film the window to measure end-to-end latency")
    ap.add_argument("--no-display", action="store_true",
                    help="No window; print the numbers every 5 s")
    ap.add_argument("--max-width", type=int, default=1280,
                    help="Window width cap in px (default: 1280)")
    a = ap.parse_args()

    src = LiveSource(a.source)
    print(f"  Source: {a.source} | {src.width}x{src.height} @ {src.fps:.1f} fps (reported) | "
          f"{'file paced as live' if src.is_file else 'live device'}\n")
    recent  = deque(maxlen=60)   # (wall time, frame index) of the last reads
    t_start = t_print = time.time()
    window  = "Camera check (q: quit)"
    try:
        while not a.seconds or time.time() - t_start < a.seconds:
            idx, frame, _ = src.read()
            if frame is None:
                print("  Stream ended")
                break
            now = time.time()
            recent.append((now, idx))
            stats = (f"{src.width}x{src.height} | {_rate(recent):4.1f} fps | "
                     f"dropped {src.dropped} | reconnects {getattr(src, 'reconnects', 0)}")
            if a.no_display:
                if now - t_print >= 5:
                    print(f"  t={src.video_time():6.1f}s  {stats}")
                    t_print = now
                continue
            if frame.shape[1] > a.max_width:
                s = a.max_width / frame.shape[1]
                frame = cv2.resize(frame, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
            if a.clock:
                _draw_clock(frame, time.time())
            caption(frame, stats)
            cv2.imshow(window, frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
    finally:
        src.release()
        if not a.no_display:
            cv2.destroyAllWindows()

    elapsed = time.time() - t_start
    print(f"\n  Ran {elapsed:.1f}s | {src.idx + 1} frames from the source "
          f"({(src.idx + 1) / max(elapsed, 1e-3):.1f} fps), {src.dropped} dropped | "
          f"{getattr(src, 'reconnects', 0)} reconnects")


if __name__ == "__main__":
    main()
