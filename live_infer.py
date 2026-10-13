"""
Exit / obstruction detection — live (real-time) runner
======================================================
Treats the input as a live stream: frames are consumed at wall-clock speed
(a recorded file is paced to its native fps and frames are dropped if the
display falls behind, exactly like a camera feed).

  main thread      read newest frame → motion-compensate boxes → draw → show
  worker threads   send the newest frame to the VLM (API latency hidden)

The VLM answer for frame t arrives a few seconds later. Doors are static, so
the boxes are carried from frame t to the current frame by tracking camera
motion frame-to-frame (optical flow + similarity transform) and warping them.

Sources:
  python live_infer.py <video_file>             # recorded video as a live stream
  python live_infer.py <video_file> --loop      # replay it continuously (q to stop)
  python live_infer.py <video_file> --delay 8   # buffered: show 8 s behind live, 10 calls
                                                # in parallel, a result for every ~0.5 s
  python live_infer.py 0                        # webcam 0
  python live_infer.py rtsp://user:pass@ip/...  # IP camera
  python live_infer.py <video_file> --provider local --interval 0.3   # self-hosted vLLM (4090)
  python live_infer.py <video_file> --no-display --stream --loop   # headless: watch in a browser

Options:
  --save results/live.mp4    also record the side-by-side view
  --interval 1.0             min seconds between API calls
  --workers 3                max API calls in flight
  q / Esc quits, space pauses.
"""

import argparse
import bisect
import json
import os
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

import cv2
import numpy as np

from core import depth_check
from core.config import MAX_RESPONSE_TOKENS, PROVIDERS
from core.depth_check import apply_cached_verdicts, needs_depth_check, verify_depth
from core.drawing import caption, draw_detections, draw_status_banner, summarize, text_scale
from core.prompts import DETECTION_TASK, build_system_prompt
from core.qwen_detect import query_frame
from core.stream_view import MJPEGStreamer
from core.video_io import (LiveSource, MotionCompensator, change_signature, resize_for_api,
                           warp_boxes)
from engines.qwen_engine import QwenEngine


# ── Live loop ─────────────────────────────────────────────────────────────────

def run_live(engine, source: str, system_prompt: str, interval: float = 1.0,
             workers: int = 3, max_side: int = 640, change_thresh: float = 6.0,
             max_gap: float = 10.0, display: bool = True, save_path: Path = None,
             log_path: Path = None, max_display_width: int = 1600,
             loop: bool = False, drain_timeout: float = 60.0,
             stream_port: int = None, query_fn=None) -> list:
    """query_fn(engine, image, system_prompt, W, H) → {"detections","error","latency"};
    defaults to the Qwen call (query_frame). Other detectors plug in here."""
    query_fn = query_fn or query_frame
    src = LiveSource(source, loop=loop)
    W, H, fps = src.width, src.height, src.fps
    print(f"  Source: {source} | {W}x{H} @ {fps:.1f} fps | "
          f"{'file paced as live' if src.is_file else 'live device'}"
          f"{' (looping)' if src.loop else ''}")
    print(f"  API: ≤{workers} in flight, ≥{interval}s apart, {max_side}px, "
          f"change>{change_thresh}, max gap {max_gap}s\n")

    scale  = min(1.0, max_display_width / (2 * W))
    out_w  = int(2 * W * scale) // 2 * 2
    out_h  = int(H * scale) // 2 * 2
    writer = None
    if save_path:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        writer = cv2.VideoWriter(str(save_path), cv2.VideoWriter_fourcc(*"mp4v"),
                                 fps, (out_w, out_h))

    streamer    = MJPEGStreamer(stream_port).start() if stream_port else None
    motion      = MotionCompensator(W)
    pool        = ThreadPoolExecutor(max_workers=workers)
    in_flight   = {}         # future → (frame_idx, video_time_s, motion C at that frame)
    applied_idx = -1         # frame index of the result currently shown
    applied_t   = None       # wall time that result arrived
    applied_lat = 0.0
    detections  = []         # boxes in reference-frame coords
    last_sig    = None
    last_sent_t = -1e9
    log         = []
    t_start     = time.time()
    window      = "Exit detection — live (q: quit, space: pause)"
    combined    = None
    user_quit   = False

    # Depth check runs in the background: a result is shown as soon as the main
    # call returns (filtered by cached verdicts), then refined when the check is done.
    vpool     = ThreadPoolExecutor(max_workers=1)
    verifying = {}           # future → (frame_idx, log entry)

    def collect(now: float) -> None:
        """Log finished API calls; apply the newest successful one."""
        nonlocal applied_idx, applied_t, applied_lat, detections
        for vfut in [f for f in verifying if f.done()]:
            v_idx, entry = verifying.pop(vfut)
            try:
                checked = vfut.result()
            except Exception:
                continue
            entry["detections"], entry["counts"] = checked, summarize(checked)
            if v_idx == applied_idx:
                detections = checked
        for fut in [f for f in in_flight if f.done()]:
            f_idx, f_vt, f_C, f_frame = in_flight.pop(fut)
            res = fut.result()
            if depth_check.VERIFY_DEPTH and not res["error"]:
                res["detections"] = apply_cached_verdicts(res["detections"], W, H)
            counts = summarize(res["detections"])
            entry = {"frame": f_idx, "video_time_s": round(f_vt, 3),
                     "latency_s": round(res["latency"], 2), "counts": counts,
                     "detections": res["detections"],
                     **({"error": res["error"]} if res["error"] else {})}
            log.append(entry)
            # At most one background check at a time — it shares the GPU with the main
            # calls. Frames that arrive meanwhile reuse its verdicts via the cache.
            if (depth_check.VERIFY_DEPTH and f_frame is not None and not res["error"]
                    and not verifying and needs_depth_check(res["detections"])):
                vfut = vpool.submit(verify_depth, engine,
                                    [dict(d) for d in res["detections"]], f_frame)
                verifying[vfut] = (f_idx, entry)
            status = f"error: {res['error']}" if res["error"] else (
                f"exits={counts['exits']} obstructed={counts['obstructed']} "
                f"partial={counts['partially_obstructed']} obstructions={counts['obstructions']}")
            if res.get("note"):
                status += f" | {res['note']}"
            print(f"  frame {f_idx:>6} (video {f_vt:7.2f}s) [{res['latency']:.1f}s] {status}")
            if f_idx > applied_idx and not res["error"]:
                applied_idx, applied_t, applied_lat = f_idx, now, res["latency"]
                detections = res["detections"]
                motion.set_reference(f_C)

    try:
        while True:
            idx, frame, skipped = src.read()
            if frame is None:
                break
            now = time.time()
            vt  = src.video_time()
            C_now = motion.update(frame)

            # ── Collect finished API calls (newest frame wins) ───────────────
            collect(now)

            # ── Maybe send this frame ────────────────────────────────────────
            if len(in_flight) < workers and now - last_sent_t >= interval:
                sig = change_signature(frame)
                if (last_sig is None
                        or float(cv2.absdiff(sig, last_sig).mean()) > change_thresh
                        or now - last_sent_t >= max_gap):
                    last_sig, last_sent_t = sig, now
                    # no frame → query_frame skips the blocking depth check;
                    # it runs afterwards in the background (see collect)
                    fut = pool.submit(query_fn, engine, resize_for_api(frame, max_side),
                                      system_prompt, W, H)
                    in_flight[fut] = (idx, vt, C_now,
                                      frame.copy() if depth_check.VERIFY_DEPTH else None)

            # ── Carry boxes to the current frame and draw ────────────────────
            current = motion.warp(detections, W, H) if detections else []

            annotated = frame.copy()
            s, thick  = text_scale(annotated)
            banner_h  = cv2.getTextSize("Exits", cv2.FONT_HERSHEY_SIMPLEX, s, thick)[0][1]
            draw_detections(annotated, current, top_margin=banner_h + 2 * (banner_h // 2 + 4))
            draw_status_banner(annotated, current)
            if applied_t is None:
                info = "waiting for first result..."
            else:
                info = (f"updated {now - applied_t:.1f}s ago | "
                        f"result age {now - applied_t + applied_lat:.1f}s")
            caption(frame, f"LIVE  t={vt:.1f}s")
            caption(annotated, f"ANNOTATED | {info}")

            combined = cv2.hconcat([frame, annotated])
            if combined.shape[1] != out_w or combined.shape[0] != out_h:
                combined = cv2.resize(combined, (out_w, out_h), interpolation=cv2.INTER_AREA)

            if streamer:
                streamer.publish(combined)
            if writer:
                for _ in range(skipped + 1):   # repeat to keep real-time duration
                    writer.write(combined)

            if display:
                cv2.imshow(window, combined)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    user_quit = True
                    break
                if key == ord(" "):
                    t_pause = time.time()
                    cv2.waitKey(0)
                    src.pause_clock(time.time() - t_pause)

        # Stream ended: keep the answers already paid for
        if in_flight and not user_quit:
            print(f"\n  Stream ended — waiting for {len(in_flight)} in-flight API call(s)...")
            wait(list(in_flight), timeout=drain_timeout)
            collect(time.time())
    finally:
        src.release()
        vpool.shutdown(wait=False, cancel_futures=True)
        pool.shutdown(wait=False, cancel_futures=True)
        if streamer:
            streamer.stop()
        if writer:
            writer.release()
        if display:
            cv2.destroyAllWindows()

    elapsed = time.time() - t_start
    print(f"\n  Ran {elapsed:.1f}s | {src.idx + 1} frames, {src.dropped} dropped"
          f"{f', {src.loops} loop(s)' if src.loop else ''} | {len(log)} API results")
    if save_path:
        print(f"  Video saved : {save_path}")
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w") as f:
            json.dump({"source": source, "fps": fps, "width": W, "height": H,
                       "frames": sorted(log, key=lambda r: r["frame"])}, f, indent=2)
        print(f"  JSON saved  : {log_path}")
    return log


# ── Buffered (delayed) live loop ──────────────────────────────────────────────


def run_buffered(engine, source: str, system_prompt: str, delay: float = 8.0,
                 interval: float = 0.5, workers: int = 10, max_side: int = 640,
                 change_thresh: float = 6.0, max_gap: float = 10.0, display: bool = True,
                 save_path: Path = None, log_path: Path = None, loop: bool = False,
                 max_display_width: int = 1600, drain_timeout: float = 60.0,
                 stream_port: int = None, query_fn=None) -> list:
    """
    Delayed live view. Frames are read in real time and queued; up to `workers`
    API calls run on frames ahead of the display, which plays `delay` seconds
    behind the stream. If delay > API latency, every displayed frame already
    has a result from at most `interval` seconds earlier in the video (boxes
    are still motion-compensated across that small gap).
    """
    query_fn = query_fn or query_frame
    src = LiveSource(source, loop=loop)
    W, H, fps = src.width, src.height, src.fps
    print(f"  Source: {source} | {W}x{H} @ {fps:.1f} fps | "
          f"{'file paced as live' if src.is_file else 'live device'}"
          f"{' (looping)' if src.loop else ''}")
    print(f"  Buffered: display {delay}s behind | ≤{workers} API calls in flight, "
          f"≥{interval}s apart | {max_side}px | change>{change_thresh}\n")

    half_w = max_display_width // 2
    disp_scale = min(1.0, half_w / W)
    dw, dh = int(W * disp_scale) // 2 * 2, int(H * disp_scale) // 2 * 2
    writer = None
    if save_path:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        writer = cv2.VideoWriter(str(save_path), cv2.VideoWriter_fourcc(*"mp4v"),
                                 fps, (2 * dw, dh))

    streamer  = MJPEGStreamer(stream_port).start() if stream_port else None
    motion    = MotionCompensator(W)
    pool      = ThreadPoolExecutor(max_workers=workers)
    in_flight = {}             # future → (frame_idx, video_time_s, C)
    results   = {}             # frame_idx → (detections, C)
    result_ix = []             # sorted frame indices with results
    buffer    = deque()        # (frame_idx, video_time_s, small_frame, C, t_read)
    log       = []
    last_sig, last_sent_t = None, -1e9
    stream_done = user_quit = False
    shown = late = 0
    t_start = time.time()
    window  = "Exit detection — buffered live (q: quit, space: pause)"

    def collect() -> None:
        for fut in [f for f in in_flight if f.done()]:
            f_idx, f_vt, f_C = in_flight.pop(fut)
            res = fut.result()
            counts = summarize(res["detections"])
            log.append({"frame": f_idx, "video_time_s": round(f_vt, 3),
                        "latency_s": round(res["latency"], 2), "counts": counts,
                        "detections": res["detections"],
                        **({"error": res["error"]} if res["error"] else {})})
            status = f"error: {res['error']}" if res["error"] else (
                f"exits={counts['exits']} obstructed={counts['obstructed']} "
                f"partial={counts['partially_obstructed']} obstructions={counts['obstructions']}")
            if res.get("note"):
                status += f" | {res['note']}"
            print(f"  frame {f_idx:>6} (video {f_vt:7.2f}s) [{res['latency']:.1f}s] "
                  f"in flight {len(in_flight):>2} | {status}")
            if not res["error"]:
                results[f_idx] = (res["detections"], f_C)
                bisect.insort(result_ix, f_idx)

    def render(entry, n_skipped: int) -> bool:
        """Draw + show one buffered frame. Returns False if the user quit."""
        nonlocal shown, late
        idx, vt, small, C, _ = entry
        pos = bisect.bisect_right(result_ix, idx) - 1
        annotated = small.copy()
        current = []
        if pos >= 0:
            k = result_ix[pos]
            dets, C_k = results[k]
            current = warp_boxes(dets, C @ np.linalg.inv(C_k), motion.scale,
                                  disp_scale, dw, dh)
            gap = (idx - k) / fps
            if gap > 2 * interval + 0.5:
                late += 1
            info = f"result from {gap:.1f}s earlier in video"
        else:
            info = "waiting for first result..."
        s, thick = text_scale(annotated)
        banner_h = cv2.getTextSize("Exits", cv2.FONT_HERSHEY_SIMPLEX, s, thick)[0][1]
        draw_detections(annotated, current, top_margin=banner_h + 2 * (banner_h // 2 + 4))
        draw_status_banner(annotated, current)
        live = small.copy()
        caption(live, f"DELAYED {delay:.0f}s  t={vt:.1f}s")
        caption(annotated, f"ANNOTATED | {info} | {len(in_flight)} in flight")
        combined = cv2.hconcat([live, annotated])
        shown += 1
        if streamer:
            streamer.publish(combined)
        if writer:
            for _ in range(n_skipped + 1):
                writer.write(combined)
        if display:
            cv2.imshow(window, combined)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                return False
            if key == ord(" "):
                t_pause = time.time()
                cv2.waitKey(0)
                src.pause_clock(time.time() - t_pause)
        return True

    try:
        while not (stream_done and not buffer):
            now = time.time()

            # ── Read + queue the newest stream frame ─────────────────────────
            if not stream_done:
                idx, frame, _ = src.read()
                if frame is None:
                    stream_done = True
                    print(f"\n  Stream ended — playing out the {delay:.0f}s buffer...")
                else:
                    now = time.time()
                    vt  = src.video_time()
                    C   = motion.update(frame)
                    small = cv2.resize(frame, (dw, dh), interpolation=cv2.INTER_AREA)
                    buffer.append((idx, vt, small, C, now))

                    if len(in_flight) < workers and now - last_sent_t >= interval:
                        sig = change_signature(frame)
                        if (last_sig is None
                                or float(cv2.absdiff(sig, last_sig).mean()) > change_thresh
                                or now - last_sent_t >= max_gap):
                            last_sig, last_sent_t = sig, now
                            fut = pool.submit(query_fn, engine,
                                              resize_for_api(frame, max_side),
                                              system_prompt, W, H, frame.copy())
                            in_flight[fut] = (idx, vt, C)
            else:
                time.sleep(1 / fps)

            collect()

            # ── Show frames whose delay has elapsed (newest one if behind) ───
            ready = []
            while buffer and buffer[0][4] <= time.time() - delay:
                ready.append(buffer.popleft())
            if ready and not render(ready[-1], len(ready) - 1):
                user_quit = True
                break

        if in_flight and not user_quit:
            wait(list(in_flight), timeout=drain_timeout)
            collect()
    finally:
        src.release()
        pool.shutdown(wait=False, cancel_futures=True)
        if streamer:
            streamer.stop()
        if writer:
            writer.release()
        if display:
            cv2.destroyAllWindows()

    lat = [r["latency_s"] for r in log if "error" not in r]
    print(f"\n  Ran {time.time() - t_start:.1f}s | {src.idx + 1} frames read, {shown} shown | "
          f"{len(log)} API results, avg latency {np.mean(lat) if lat else 0:.1f}s, "
          f"max {max(lat) if lat else 0:.1f}s")
    if late:
        print(f"  {late} frames showed a result older than expected — "
              f"raise --delay or --workers")
    if save_path:
        print(f"  Video saved : {save_path}")
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w") as f:
            json.dump({"source": source, "fps": fps, "width": W, "height": H,
                       "delay_s": delay, "frames": sorted(log, key=lambda r: r["frame"])},
                      f, indent=2)
        print(f"  JSON saved  : {log_path}")
    return log


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args():
    parser = argparse.ArgumentParser(
        description="Live exit + obstruction detection (video file as live stream, webcam or RTSP)"
    )
    parser.add_argument("source", help="Video file, webcam index (0) or stream URL")
    parser.add_argument("--prompt",   default=None,
                        help="Detection prompt (overrides DETECTION_TASK in prompts.py)")
    parser.add_argument("--delay",    type=float, default=0.0,
                        help="Buffered mode: show the stream this many seconds behind live "
                             "so results are ready for each frame (e.g. 8). 0 = no delay")
    parser.add_argument("--interval", type=float, default=None,
                        help="Min seconds between API calls (default: 1.0; 0.5 with --delay)")
    parser.add_argument("--workers",  type=int, default=None,
                        help="Max API calls in flight (default: 3; 10 with --delay)")
    parser.add_argument("--max-side", type=int, default=640,
                        help="Longest edge of the frame sent to the API in px (default: 640)")
    parser.add_argument("--change-thresh", type=float, default=6.0,
                        help="Min scene change (mean abs diff, 0-255) to send a frame (default: 6.0)")
    parser.add_argument("--max-gap",  type=float, default=10.0,
                        help="Always send a frame at least every N seconds (default: 10)")
    parser.add_argument("--save",     default=None,
                        help="Also record the side-by-side view to this .mp4")
    parser.add_argument("--loop",     action="store_true",
                        help="Replay a video file continuously (q to stop)")
    parser.add_argument("--verify-depth", action="store_true",
                        help="Close-up floor-vs-furniture check on each obstruction "
                             "(fewer false alarms, extra calls)")
    parser.add_argument("--stream",   type=int, nargs="?", const=8090, default=None,
                        metavar="PORT",
                        help="Serve the side-by-side view to a browser at http://<host>:PORT "
                             "(default port 8090) — for headless servers")
    parser.add_argument("--no-display", action="store_true",
                        help="Run without a window (e.g. headless with --save)")
    parser.add_argument("--provider", default="openrouter", choices=list(PROVIDERS),
                        help="API provider preset (default: openrouter)")
    parser.add_argument("--model",    default=None,
                        help="Override model name (e.g. qwen/qwen3-vl-32b-instruct)")
    parser.add_argument("--base-url", default=None,
                        help="Override API base URL (for custom endpoints)")
    parser.add_argument("--api-key",  default=None,
                        help="API key (defaults to env var for the chosen provider)")
    parser.add_argument("--out-dir",  default="results",
                        help="Where the JSON log is written (default: results/)")
    return parser.parse_args()


def main():
    args   = _parse_args()
    preset = PROVIDERS[args.provider]
    depth_check.VERIFY_DEPTH = args.verify_depth

    api_key = args.api_key or os.environ.get(preset["env_key"]) or preset.get("api_key")
    if not api_key:
        print(f"Error: API key not found. Set {preset['env_key']} in .env or pass --api-key.")
        sys.exit(1)

    engine = QwenEngine(api_key=api_key,
                        model_name=args.model or preset["model"],
                        base_url=args.base_url or preset["base_url"],
                        max_image_side=args.max_side,
                        max_tokens=MAX_RESPONSE_TOKENS,
                        extra_body=preset.get("extra_body"))
    system_prompt = build_system_prompt(args.prompt or DETECTION_TASK)

    stem = Path(args.source).stem if Path(args.source).is_file() else "live"
    common = dict(max_side=args.max_side, change_thresh=args.change_thresh,
                  max_gap=args.max_gap, display=not args.no_display, loop=args.loop,
                  stream_port=args.stream,
                  save_path=Path(args.save) if args.save else None,
                  log_path=Path(args.out_dir) / f"{stem}_live_detections.json")
    if args.delay > 0:
        run_buffered(engine, args.source, system_prompt, delay=args.delay,
                     interval=args.interval or 0.5, workers=args.workers or 10, **common)
    else:
        run_live(engine, args.source, system_prompt,
                 interval=args.interval or 1.0, workers=args.workers or 3, **common)
    engine.cleanup()


if __name__ == "__main__":
    main()
