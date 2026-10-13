"""
Exit / obstruction detection — LocateAnything-3B, live
======================================================
Same live runner as live_infer.py (real-time playback, background calls,
camera-motion-compensated boxes, side-by-side window / browser stream / save,
buffered --delay mode), but frames go to the LocateAnything server
(server/serve_locate.sh) instead of Qwen.

LocateAnything only localises objects, so obstruction is decided by the shared
geometric rule (core/rules.py): an obstacle obstructs a door if it stands
on the floor inside the door's clearance zone; status follows from how much of
the door width is covered. Doors with an EXIT sign just above are labelled
"EXIT door".

Usage:
  python locate_live.py IMG_0450.MOV --url http://<server-ip>:8020
  python locate_live.py IMG_0450.MOV --url ... --mode fast           # parallel box decoding
  python locate_live.py IMG_0450.MOV --url ... --delay 4             # buffered mode
  python locate_live.py IMG_0450.MOV --url ... --no-display --stream # headless → browser
  python locate_live.py 0 --url ...                                  # webcam
  python locate_live.py IMG_0450.MOV --url ... --qwen-url http://<server-ip>:8010/v1
      # + Qwen names any object at the door (open-world) and checks floor vs furniture
  q / Esc quits, space pauses.
"""

import argparse
import os
import time
from pathlib import Path


from core.labels import DOOR_LABELS, OBSTACLE_LABELS, SIGN_LABELS, kind_of, norm_box_to_pixels
from core.qwen_assist import QwenAssist
from core.rules import assess, box_iou, containment, merge_double_doors
from engines.locate_engine import LocateEngine
from live_infer import run_buffered, run_live


# ── LocateAnything query ──────────────────────────────────────────────────────


def make_query_fn(categories: list, door_labels: list, sign_labels: list, mode: str,
                  assist: QwenAssist = None):
    """Build a live_infer-compatible query function around the LocateAnything server.
    With `assist`, Qwen's latest object names are added to the categories and
    obstructions get the background floor-vs-furniture check."""

    def query(engine: LocateEngine, image, _system_prompt, W: int, H: int, _frame=None) -> dict:
        t0 = time.time()
        cats = categories
        if assist:
            assist.note_image(image)
            cats = categories + assist.extra_categories(categories)
        try:
            res = engine.detect_raw(image, cats, mode=mode)
        except Exception as e:
            return {"detections": [], "error": str(e), "latency": time.time() - t0}

        doors, signs, obstacles = [], [], []
        for b in res["boxes"]:
            bb = norm_box_to_pixels(b, W, H)
            box = [bb["x1"], bb["y1"], bb["x2"], bb["y2"]]
            kind = kind_of(b["label"], door_labels, sign_labels)
            if kind == "door":
                doors.append({"box": box, "conf": 1.0, "signed": False, "weak": False})
            elif kind == "sign":
                signs.append(box)
            else:
                obstacles.append({"type": "obstruction", "label": b["label"],
                                  "confidence": None, "bounding_box": bb})

        # Same object under two labels → keep one (label-agnostic overlap)
        deduped = []
        for o in obstacles:
            ob = o["bounding_box"]
            box = [ob["x1"], ob["y1"], ob["x2"], ob["y2"]]
            if all(box_iou(box, [k["bounding_box"][c] for c in ("x1", "y1", "x2", "y2")]) < 0.6
                   for k in deduped):
                deduped.append(o)
        obstacles = deduped

        # EXIT sign just above a door → that door is a confirmed exit
        for d in doors:
            x1, y1, x2, y2 = d["box"]
            dw, dh = x2 - x1, y2 - y1
            d["signed"] = any(x1 - 0.2 * dw <= (s[0] + s[2]) / 2 <= x2 + 0.2 * dw
                              and y1 - 0.5 * dh <= s[3] <= y1 + 0.1 * dh for s in signs)

        # Drop nested duplicates, merge leaves of a double door
        doors.sort(key=lambda d: (d["signed"], (d["box"][2] - d["box"][0])
                                  * (d["box"][3] - d["box"][1])), reverse=True)
        kept = []
        for d in doors:
            if all(box_iou(d["box"], k["box"]) < 0.5 and containment(d["box"], k["box"]) < 0.8
                   for k in kept):
                kept.append(d)
        kept = merge_double_doors(kept)

        exits = []
        for i, d in enumerate(kept, 1):
            x1, y1, x2, y2 = d["box"]
            name = "EXIT door" if d["signed"] else "door"
            exits.append({"type": "exit", "label": f"{name} {i}" if len(kept) > 1 else name,
                          "confidence": None,
                          "bounding_box": {"x1": int(x1), "y1": int(y1),
                                           "x2": int(x2), "y2": int(y2)}})
        blocking = assess(exits, obstacles, W, H)   # sets exit status
        detections = exits + blocking
        out = {"detections": detections, "error": None}
        if assist:
            out["detections"] = assist.check(detections, image, W, H)
            out["note"] = assist.note()
        out["latency"] = time.time() - t0
        return out

    return query


def _parse_args():
    ap = argparse.ArgumentParser(
        description="Live exit + obstruction detection with LocateAnything-3B")
    ap.add_argument("source", help="Video file, webcam index (0) or stream URL")
    ap.add_argument("--url", default=os.environ.get("LOCATE_URL", "http://localhost:8020"),
                    help="LocateAnything server (default: $LOCATE_URL or http://localhost:8020)")
    ap.add_argument("--mode", default="hybrid", choices=["fast", "hybrid", "slow"],
                    help="fast = parallel box decoding, hybrid (default), slow = autoregressive")
    ap.add_argument("--obstacles", default=",".join(OBSTACLE_LABELS),
                    help="Comma-separated obstacle categories to locate")
    ap.add_argument("--qwen-url", default=os.environ.get("QWEN_URL"),
                    help="Qwen vLLM server (e.g. http://<server-ip>:8010/v1): names objects "
                         "at the door for LocateAnything + floor-vs-furniture check")
    ap.add_argument("--qwen-model", default=None, help="Default: whatever the server serves")
    ap.add_argument("--name-every", type=float, default=2.5,
                    help="Seconds between Qwen object-naming calls (default: 2.5)")
    ap.add_argument("--no-verify", action="store_true",
                    help="With --qwen-url: names only, skip the floor-vs-furniture check")
    ap.add_argument("--verify-background", action="store_true",
                    help="Run the check in the background (no added latency, but with a "
                         "moving camera most verdicts arrive too late to apply)")
    ap.add_argument("--delay",    type=float, default=0.0,
                    help="Buffered mode: show the stream this many seconds behind live")
    ap.add_argument("--interval", type=float, default=0.2,
                    help="Min seconds between calls (default: 0.2)")
    ap.add_argument("--workers",  type=int, default=1,
                    help="Calls in flight (default: 1 — the server runs one frame at a "
                         "time, so more workers only queue and add latency)")
    ap.add_argument("--max-side", type=int, default=960,
                    help="Longest edge of the frame sent in px (default: 960; 1280 is "
                         "slower to upload/process for little gain)")
    ap.add_argument("--change-thresh", type=float, default=6.0)
    ap.add_argument("--max-gap",  type=float, default=10.0)
    ap.add_argument("--stream",   type=int, nargs="?", const=8090, default=None, metavar="PORT",
                    help="Serve the side-by-side view to a browser (default port 8090)")
    ap.add_argument("--save",     default=None, help="Also record the side-by-side view")
    ap.add_argument("--loop",     action="store_true", help="Replay a video file continuously")
    ap.add_argument("--no-display", action="store_true", help="Run without a window")
    ap.add_argument("--out-dir",  default="results", help="JSON log directory")
    return ap.parse_args()


def main():
    args = _parse_args()
    engine = LocateEngine(args.url, max_image_side=args.max_side, mode=args.mode)
    obstacles = [c.strip() for c in args.obstacles.split(",") if c.strip()]
    categories = DOOR_LABELS + SIGN_LABELS + obstacles
    stem = Path(args.source).stem if Path(args.source).is_file() else "live"
    assist = None
    if args.qwen_url:
        assist = QwenAssist(args.qwen_url, args.qwen_model, name_every=args.name_every,
                            verify=not args.no_verify,
                            verify_blocking=not args.verify_background,
                            base_categories=categories)
        print(f"  Qwen assist: {assist.engine.model_name} | names every {args.name_every}s"
              f"{'' if args.no_verify else ' | floor-vs-furniture check'}")
    else:
        qwen_only = [f for f, used in (("--name-every", args.name_every != 2.5),
                                       ("--no-verify", args.no_verify),
                                       ("--verify-background", args.verify_background),
                                       ("--qwen-model", bool(args.qwen_model))) if used]
        print("  Qwen assist: OFF (pass --qwen-url http://<server>:8010/v1, or set QWEN_URL "
              "in .env, to turn it on)")
        if qwen_only:
            print(f"  WARNING: {', '.join(qwen_only)} only work together with --qwen-url — ignored")
    query_fn = make_query_fn(categories, DOOR_LABELS, SIGN_LABELS, args.mode, assist)
    print(f"  {len(categories)} categories | mode={args.mode} | {args.max_side}px")

    common = dict(max_side=args.max_side, change_thresh=args.change_thresh,
                  max_gap=args.max_gap, display=not args.no_display, loop=args.loop,
                  stream_port=args.stream, query_fn=query_fn,
                  save_path=Path(args.save) if args.save else None,
                  log_path=Path(args.out_dir) / f"{stem}_locate_detections.json")
    if args.delay > 0:
        run_buffered(engine, args.source, "", delay=args.delay,
                     interval=args.interval, workers=args.workers, **common)
    else:
        run_live(engine, args.source, "", interval=args.interval,
                 workers=args.workers, **common)


if __name__ == "__main__":
    main()
