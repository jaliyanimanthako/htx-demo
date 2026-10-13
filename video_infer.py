"""
Exit / obstruction detection on video — Online VLM runner
=========================================================
Samples frames from a recorded video, sends them to a hosted VLM
(Qwen3-VL via OpenRouter by default) with the exit-detection prompt from
prompts.py, and writes a side-by-side [original | annotated] video plus
per-frame JSON.

Speed-ups:
  - Concurrent API calls (--workers)
  - Frames downscaled before upload (--max-side) and compact JSON response
  - Candidate frames skipped when the scene hasn't changed (--change-thresh)

Box colours: green = clear exit, amber = partially obstructed exit,
red = obstructed exit, orange = the obstructing object.

Boxes from a sent frame are held on screen until the next sent frame,
so the output video plays at the original frame rate.

Supported providers (OpenAI-compatible API):
  OpenRouter    — set OPENROUTER_API_KEY (default provider)
  Alibaba Qwen  — set QWEN_API_KEY and pass --provider qwen
  OpenAI        — set OPENAI_API_KEY and pass --provider openai
  Local vLLM    — pass --provider local (LOCAL_VLM_URL / LOCAL_VLM_MODEL in .env)
  Custom        — pass --base-url and --api-key directly

Usage:
  python video_infer.py <video_path>                       # 1 candidate frame per second
  python video_infer.py <video_path> --play                # play side-by-side when done
  python video_infer.py <video_path> --interval 0.5        # 2 candidate frames per second
  python video_infer.py <video_path> --workers 4           # fewer parallel calls (rate limits)
  python video_infer.py <video_path> --change-thresh 0     # send every candidate frame
  python video_infer.py <video_path> --prompt "..."         # override DETECTION_TASK
  python video_infer.py <video_path> --provider qwen       # DashScope qwen-vl-max
  python video_infer.py <video_path> --out-dir /my/results
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import cv2

from core.config import MAX_RESPONSE_TOKENS, PROVIDERS
from core.drawing import caption, draw_detections, draw_status_banner, summarize, text_scale
from core.prompts import DETECTION_TASK, build_system_prompt
from core.qwen_detect import query_frame
from core.video_io import change_signature, resize_for_api
from core import depth_check
from engines.qwen_engine import QwenEngine


# ── Pass 1: frame selection + concurrent VLM calls ────────────────────────────


def run_inference(engine: QwenEngine, video_path: Path, system_prompt: str,
                  interval: float, workers: int, max_side: int,
                  change_thresh: float, max_gap: float) -> tuple:
    """
    Reads the video once. Every `interval` seconds a candidate frame is checked
    against the last frame sent; it is sent only if the scene changed by more
    than `change_thresh` (mean abs pixel diff, 0-255) or `max_gap` seconds have
    passed. Sent frames are queried concurrently on `workers` threads.

    Returns (video_info, {frame_idx: result}, stats).
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    fps    = cap.get(cv2.CAP_PROP_FPS) or 25.0
    width  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total  = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step   = max(1, round(fps * interval))
    print(f"  {width}x{height} @ {fps:.1f} fps, {total} frames | candidate every {step} frames "
          f"| {workers} workers | {max_side}px | change>{change_thresh} | max gap {max_gap}s")

    futures       = {}
    last_sig      = None
    last_sent_idx = None
    candidates    = 0
    frame_idx     = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            if frame_idx % step == 0:
                candidates += 1
                sig = change_signature(frame)
                changed = (
                    last_sig is None
                    or float(cv2.absdiff(sig, last_sig).mean()) > change_thresh
                    or (frame_idx - last_sent_idx) / fps >= max_gap
                )
                if changed:
                    last_sig, last_sent_idx = sig, frame_idx
                    futures[pool.submit(query_frame, engine,
                                        resize_for_api(frame, max_side),
                                        system_prompt, width, height, frame)] = frame_idx
            frame_idx += 1
        cap.release()

        print(f"  Read {frame_idx} frames: {len(futures)} sent, "
              f"{candidates - len(futures)} skipped (unchanged)\n")

        results = {}
        for done, fut in enumerate(as_completed(futures), 1):
            idx = futures[fut]
            res = results[idx] = fut.result()
            counts = summarize(res["detections"])
            status = f"error: {res['error']}" if res["error"] else (
                f"exits={counts['exits']} obstructed={counts['obstructed']} "
                f"partial={counts['partially_obstructed']} obstructions={counts['obstructions']}")
            print(f"  [{done:>4}/{len(futures)}] frame {idx:>6} ({idx / fps:7.2f}s) "
                  f"[{res['latency']:.1f}s] {status}")

    info  = {"fps": fps, "width": width, "height": height, "frames": frame_idx, "step": step}
    stats = {"candidates": candidates, "sent": len(futures),
             "skipped_unchanged": candidates - len(futures)}
    return info, results, stats


# ── Pass 2: render side-by-side video ─────────────────────────────────────────


def render_side_by_side(video_path: Path, out_path: Path, info: dict,
                        results: dict, max_out_width: int = 2560) -> None:
    """Writes [original | annotated]; boxes are held until the next sent frame."""
    width, height = info["width"], info["height"]
    scale = min(1.0, max_out_width / (2 * width))
    out_w = int(2 * width * scale) // 2 * 2
    out_h = int(height * scale) // 2 * 2

    cap    = cv2.VideoCapture(str(video_path))
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"),
                             info["fps"], (out_w, out_h))
    current   = []
    frame_idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_idx in results:
            current = results[frame_idx]["detections"]

        annotated = frame.copy()
        s, thick  = text_scale(annotated)
        banner_h  = cv2.getTextSize("Exits", cv2.FONT_HERSHEY_SIMPLEX, s, thick)[0][1]
        # Labels avoid the banner area; banner drawn last so boxes never cover it
        draw_detections(annotated, current, top_margin=banner_h + 2 * (banner_h // 2 + 4))
        draw_status_banner(annotated, current)
        caption(frame, "ORIGINAL")
        caption(annotated, "ANNOTATED")

        combined = cv2.hconcat([frame, annotated])
        if combined.shape[1] != out_w or combined.shape[0] != out_h:
            combined = cv2.resize(combined, (out_w, out_h), interpolation=cv2.INTER_AREA)
        writer.write(combined)
        frame_idx += 1

    cap.release()
    writer.release()


def play_video(path: Path, fps: float) -> None:
    """Play the output in a window. q / Esc to quit, space to pause."""
    cap   = cv2.VideoCapture(str(path))
    delay = max(1, int(1000 / fps))
    title = f"{path.name}  (q: quit, space: pause)"
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        cv2.imshow(title, frame)
        key = cv2.waitKey(delay) & 0xFF
        if key in (ord("q"), 27):
            break
        if key == ord(" "):
            cv2.waitKey(0)
    cap.release()
    cv2.destroyAllWindows()


def process_video(engine: QwenEngine, video_path: Path, out_dir: Path, system_prompt: str,
                  interval: float = 1.0, workers: int = 8, max_side: int = 640,
                  change_thresh: float = 6.0, max_gap: float = 10.0) -> Path:
    t0 = time.time()
    info, results, stats = run_inference(engine, video_path, system_prompt, interval,
                                         workers, max_side, change_thresh, max_gap)
    t_api = time.time() - t0

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"side_by_side_{video_path.stem}.mp4"
    render_side_by_side(video_path, out_path, info, results)

    fps = info["fps"]
    frame_results = [{
        "frame":      idx,
        "time_s":     round(idx / fps, 3),
        "latency_s":  round(res["latency"], 2),
        "counts":     summarize(res["detections"]),
        "detections": res["detections"],
        **({"error": res["error"]} if res["error"] else {}),
    } for idx, res in sorted(results.items())]

    json_path = out_dir / f"{video_path.stem}_detections.json"
    with open(json_path, "w") as f:
        json.dump({
            "video":        video_path.name,
            "fps":          fps,
            "width":        info["width"],
            "height":       info["height"],
            "sample_every": info["step"],
            **stats,
            "frames":       frame_results,
        }, f, indent=2)

    print(f"\n  {info['frames']} frames ({info['frames'] / fps:.1f}s of video) | "
          f"{stats['sent']} API calls in {t_api:.1f}s | total {time.time() - t0:.1f}s")
    print(f"  Video saved : {out_path}")
    print(f"  JSON saved  : {json_path}")
    return out_path


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args():
    parser = argparse.ArgumentParser(
        description="Exit + exit-obstruction detection on video using a VLM API (Qwen3-VL, GPT-4o, etc.)"
    )
    parser.add_argument("input", help="Video file path")
    parser.add_argument("--prompt",   default=None,
                        help="Detection prompt (overrides DETECTION_TASK in prompts.py)")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Seconds of video between candidate frames (default: 1.0)")
    parser.add_argument("--workers",  type=int, default=8,
                        help="Concurrent API calls (default: 8; lower if rate-limited)")
    parser.add_argument("--max-side", type=int, default=640,
                        help="Longest edge of the frame sent to the API in px (default: 640)")
    parser.add_argument("--change-thresh", type=float, default=6.0,
                        help="Min scene change (mean abs diff, 0-255) to send a candidate "
                             "frame (default: 6.0; 0 sends every candidate)")
    parser.add_argument("--max-gap",  type=float, default=10.0,
                        help="Always send a frame at least every N seconds (default: 10)")
    parser.add_argument("--verify-depth", action="store_true",
                        help="Close-up floor-vs-furniture check on each obstruction "
                             "(fewer false alarms, extra calls)")
    parser.add_argument("--play",     action="store_true",
                        help="Play the side-by-side video when done")
    parser.add_argument("--provider", default="openrouter", choices=list(PROVIDERS),
                        help="API provider preset (default: openrouter)")
    parser.add_argument("--model",    default=None,
                        help="Override model name (e.g. qwen/qwen3-vl-32b-instruct, gpt-4o)")
    parser.add_argument("--base-url", default=None,
                        help="Override API base URL (for custom endpoints)")
    parser.add_argument("--api-key",  default=None,
                        help="API key (defaults to env var for the chosen provider)")
    parser.add_argument("--out-dir",  default="results",
                        help="Output directory (default: results/)")
    return parser.parse_args()


def main():
    args   = _parse_args()
    preset = PROVIDERS[args.provider]
    depth_check.VERIFY_DEPTH = args.verify_depth

    api_key = args.api_key or os.environ.get(preset["env_key"]) or preset.get("api_key")
    if not api_key:
        print(f"Error: API key not found. Set {preset['env_key']} in .env or pass --api-key.")
        sys.exit(1)

    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"Invalid video path: {input_path}")
        sys.exit(1)

    engine = QwenEngine(api_key=api_key,
                        model_name=args.model or preset["model"],
                        base_url=args.base_url or preset["base_url"],
                        max_image_side=args.max_side,
                        max_tokens=MAX_RESPONSE_TOKENS,
                        extra_body=preset.get("extra_body"))
    system_prompt = build_system_prompt(args.prompt or DETECTION_TASK)

    print(f"\n{'='*60}\n  {input_path.name}\n{'='*60}")
    out_path = process_video(engine, input_path, Path(args.out_dir), system_prompt,
                             interval=args.interval, workers=args.workers,
                             max_side=args.max_side, change_thresh=args.change_thresh,
                             max_gap=args.max_gap)
    engine.cleanup()
    print(f"\nDone! Results saved in '{args.out_dir}/'")

    if args.play:
        cap = cv2.VideoCapture(str(out_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        cap.release()
        play_video(out_path, fps)


if __name__ == "__main__":
    main()
