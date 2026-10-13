"""
Exit / obstruction detection — YOLOE (local, real-time)
=======================================================
Alternative to the Qwen API runners: an open-vocabulary YOLOE model detects
exits and candidate obstacles from text prompts on EVERY frame, locally.
Obstruction is decided by a geometric rule instead of a language model.

Per frame:
  1. YOLOE finds doors, EXIT signs, obstacles (box, chair, trolley, ...) and
     decoy classes (refrigerator, cabinet, ...). Decoys exist only to soak up
     door-like objects so they are not labelled "door"; they are discarded.
  2. A door with an EXIT sign just above it is a confirmed exit. Plain doors
     are ignored unless --all-doors is given (they are prone to false
     positives on pillars, wardrobes, wall panels). Side-by-side leaves of a
     double door are merged into one exit.
  3. Exits are tracked across frames. Camera motion (optical flow, shared with
     live_infer.py) moves the boxes, so an exit stays on screen for a few
     seconds when the detector loses it (e.g. walking up close).
  4. Each exit gets a clearance zone: lower part of the door + the floor strip
     in front of it. An obstacle obstructs the exit if it stands on the floor
     inside the zone (bottom edge in zone) and overlaps it enough.
  5. Status from the share of door width the obstacles cover:
        ≥ OBSTRUCTED_COVERAGE → obstructed, > 0 → partially_obstructed, else clear
     smoothed per tracked exit over the last few frames.

Input is treated as a live stream (same pacing as live_infer.py).

Usage:
  python yoloe_infer.py <video_file>                    # recorded video as live stream
  python yoloe_infer.py <video_file> --loop             # replay continuously
  python yoloe_infer.py 0                               # webcam
  python yoloe_infer.py <video_file> --save results/yoloe.mp4
  python yoloe_infer.py <video_file> --zones            # draw clearance zones (tuning)
  python yoloe_infer.py <video_file> --all-doors        # also doors without an EXIT sign
  python yoloe_infer.py <video_file> --model yoloe-11l-seg.pt   # bigger / more accurate
  q / Esc quits, space pauses.
"""

import argparse
import contextlib
import json
import os
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np
import torch
from ultralytics import YOLOE

from core.drawing import caption, draw_detections, draw_status_banner, summarize, text_scale
from core.rules import (assess, box_iou, clearance_zone, containment, merge_double_doors,
                        to_bbox_dict)
from core.video_io import LiveSource, MotionCompensator

MODELS_DIR = Path(__file__).parent / "models"
PROC_WIDTH = 960   # frames are downscaled to this width for detection + drawing

# ── Prompts (edit freely) ─────────────────────────────────────────────────────
DOOR_CLASSES     = ["door", "double door"]
SIGN_CLASSES     = ["exit sign"]
OBSTACLE_CLASSES = [
    "cardboard box", "chair", "office chair", "stool", "table", "trolley",
    "cart", "trash bin", "ladder", "pallet", "bag", "crate",
]
# Detected so door-like objects get these labels instead of "door"; then ignored
DECOY_CLASSES    = ["refrigerator", "cabinet", "window", "whiteboard", "monitor"]

# ── Thresholds ────────────────────────────────────────────────────────────────
DOOR_CONF          = 0.15   # door without an EXIT sign
SIGNED_DOOR_CONF   = 0.06   # door with an EXIT sign above it
SIGN_CONF          = 0.15
OBSTACLE_CONF      = 0.08

# ── Exit tracking ─────────────────────────────────────────────────────────────
MATCH_IOU          = 0.3    # detection ↔ track association
MIN_HITS           = 2      # frames an exit must be seen before it is shown
HOLD_SIGNED_S      = 4.0    # keep a lost confirmed exit (motion-tracked) this long
HOLD_UNSIGNED_S    = 0.7    # ... and a lost plain door this long

STATUS_RANK      = {"clear": 0, "partially_obstructed": 1, "obstructed": 2}
SMOOTH_FRAMES    = 10       # per-exit status history length
SMOOTH_MIN_SHARE = 0.4      # a status must fill this share of history to be shown


def _pick_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


@contextlib.contextmanager
def _in_dir(path: Path):
    """Ultralytics downloads weights / MobileCLIP into the CWD — keep them in models/."""
    path.mkdir(exist_ok=True)
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


# ── Exit tracker ──────────────────────────────────────────────────────────────

class ExitTracker:
    """
    IoU tracker for exits with camera-motion compensation. Lost exits are kept
    (boxes moved with the camera) for a hold time, longer for confirmed exits.
    """

    def __init__(self):
        self.tracks  = []
        self.next_id = 1

    @staticmethod
    def _warp(box, M):
        pts = np.float32([[box[0], box[1]], [box[2], box[1]],
                          [box[0], box[3]], [box[2], box[3]]])
        pts = cv2.transform(pts[None], M)[0]
        return [*pts.min(axis=0).tolist(), *pts.max(axis=0).tolist()]

    def update(self, doors: list, step_M, now: float) -> list:
        """doors: [{box, conf, signed, weak}] in pixels; step_M: 2x3 motion prev→now (pixels).
        Weak detections only refresh existing signed tracks."""
        for t in self.tracks:
            t["box"] = self._warp(t["box"], step_M)

        unmatched = list(range(len(self.tracks)))
        for d in sorted(doors, key=lambda d: d["conf"], reverse=True):
            best, best_iou = None, MATCH_IOU
            for ti in unmatched:
                iou = box_iou(d["box"], self.tracks[ti]["box"])
                if iou >= best_iou:
                    best, best_iou = ti, iou
            if best is not None and d["weak"] and not self.tracks[best]["signed"]:
                best = None
            if best is None:
                if d["weak"]:
                    continue
                self.tracks.append({"id": self.next_id, "box": d["box"], "conf": d["conf"],
                                    "signed": d["signed"], "hits": 1, "last_seen": now,
                                    "history": deque(maxlen=SMOOTH_FRAMES)})
                self.next_id += 1
                continue
            unmatched.remove(best)
            t = self.tracks[best]
            t["box"] = [0.7 * a + 0.3 * b for a, b in zip(d["box"], t["box"])]
            t["conf"] = d["conf"]
            t["signed"] = t["signed"] or d["signed"]
            t["hits"] += 1
            t["last_seen"] = now

        def alive(t):
            hold = HOLD_SIGNED_S if t["signed"] else HOLD_UNSIGNED_S
            return now - t["last_seen"] <= hold
        self.tracks = [t for t in self.tracks if alive(t)]
        return [t for t in self.tracks if t["hits"] >= MIN_HITS]


def smooth_status(track: dict, status: str) -> str:
    hist = track["history"]
    hist.append(status)
    for s in sorted(STATUS_RANK, key=STATUS_RANK.get, reverse=True):
        if hist.count(s) >= SMOOTH_MIN_SHARE * len(hist):
            return s
    return status


# ── Detector ──────────────────────────────────────────────────────────────────

class YOLOEExitDetector:
    def __init__(self, model_name: str, imgsz: int, device: str = None,
                 all_doors: bool = False):
        self.device    = device or _pick_device()
        self.imgsz     = imgsz
        self.all_doors = all_doors
        self.names  = DOOR_CLASSES + SIGN_CLASSES + OBSTACLE_CLASSES + DECOY_CLASSES
        self.kind   = ({n: "door" for n in DOOR_CLASSES} | {n: "sign" for n in SIGN_CLASSES}
                       | {n: "obstacle" for n in OBSTACLE_CLASSES}
                       | {n: "decoy" for n in DECOY_CLASSES})
        print(f"Loading YOLOE: {model_name} on {self.device} ({len(self.names)} prompts)")
        with _in_dir(MODELS_DIR):
            self.model = YOLOE(model_name)
            self.model.set_classes(self.names, self.model.get_text_pe(self.names))
        self.tracker = ExitTracker()
        self.motion  = None
        self.prev_C  = None
        self.min_conf = min(DOOR_CONF, SIGNED_DOOR_CONF, SIGN_CONF, OBSTACLE_CONF)

    def _motion_step(self, frame):
        """2x3 transform previous frame → this frame, in full-resolution pixels."""
        if self.motion is None:
            self.motion = MotionCompensator(frame.shape[1])
        C = self.motion.update(frame)
        step = np.eye(3) if self.prev_C is None else C @ np.linalg.inv(self.prev_C)
        self.prev_C = C
        s = self.motion.scale   # C works on downscaled frames
        S, S_inv = np.diag([s, s, 1.0]), np.diag([1 / s, 1 / s, 1.0])
        return (S_inv @ step @ S)[:2].astype(np.float32)

    def __call__(self, frame, now: float) -> list:
        h, w = frame.shape[:2]
        step_M = self._motion_step(frame)

        r = self.model.predict(frame, conf=self.min_conf, imgsz=self.imgsz,
                               device=self.device, verbose=False)[0]
        doors, signs, obstacles = [], [], []
        for xyxy, cls, conf in zip(r.boxes.xyxy.tolist(), r.boxes.cls.int().tolist(),
                                   r.boxes.conf.tolist()):
            name = self.names[cls]
            kind = self.kind[name]
            if kind == "door":
                doors.append({"box": xyxy, "conf": conf, "label": name})
            elif kind == "sign" and conf >= SIGN_CONF:
                signs.append({"box": xyxy, "conf": conf})
            elif kind == "obstacle" and conf >= OBSTACLE_CONF:
                obstacles.append({"type": "obstruction", "label": name,
                                  "confidence": round(conf, 2),
                                  "bounding_box": to_bbox_dict(xyxy, w, h)})

        # Same object under two prompts ("chair" / "office chair") → keep the best
        obstacles.sort(key=lambda o: o["confidence"], reverse=True)
        deduped = []
        for o in obstacles:
            ob = o["bounding_box"]
            box = [ob["x1"], ob["y1"], ob["x2"], ob["y2"]]
            if all(box_iou(box, [k["bounding_box"][c] for c in ("x1", "y1", "x2", "y2")]) < 0.6
                   for k in deduped):
                deduped.append(o)
        obstacles = deduped

        # A door is "signed" if an EXIT sign sits just above its top edge
        for d in doors:
            x1, y1, x2, y2 = d["box"]
            dw, dh = x2 - x1, y2 - y1
            d["signed"] = any(
                x1 - 0.2 * dw <= (s["box"][0] + s["box"][2]) / 2 <= x2 + 0.2 * dw
                and y1 - 0.5 * dh <= s["box"][3] <= y1 + 0.1 * dh
                for s in signs)
        # Low-confidence plain doors are "weak": they may only refresh an existing
        # confirmed exit (e.g. up close, once the sign is out of view)
        doors = [d for d in doors if d["conf"] >= SIGNED_DOOR_CONF]
        for d in doors:
            d["weak"] = not d["signed"] and d["conf"] < DOOR_CONF

        # Drop doors nested inside a better/larger door (single leaf of a double door)
        doors.sort(key=lambda d: (d["signed"], d["conf"]), reverse=True)
        kept = []
        for d in doors:
            if all(box_iou(d["box"], k["box"]) < 0.5 and containment(d["box"], k["box"]) < 0.8
                   for k in kept):
                kept.append(d)
        kept = merge_double_doors(kept)

        exits = []
        for t in self.tracker.update(kept, step_M, now):
            if not (t["signed"] or self.all_doors):
                continue   # tracked anyway, so a sign seen later upgrades it
            bb = to_bbox_dict(t["box"], w, h)
            if bb["x2"] - bb["x1"] < 8 or bb["y2"] - bb["y1"] < 8:
                continue
            lost = t["last_seen"] < now
            label = ("EXIT door" if t["signed"] else "door") + (" (tracked)" if lost else "")
            exits.append({"type": "exit", "label": label, "confidence": round(t["conf"], 2),
                          "bounding_box": bb, "track_id": t["id"], "_track": t})

        blocking = assess(exits, obstacles, w, h)
        for ex in exits:
            ex["status"] = smooth_status(ex.pop("_track"), ex["status"])
        return exits + blocking


# ── Live loop ─────────────────────────────────────────────────────────────────

def run(detector: YOLOEExitDetector, source: str, display: bool = True,
        save_path: Path = None, log_path: Path = None, loop: bool = False,
        show_zones: bool = False, max_display_width: int = 1600,
        proc_width: int = PROC_WIDTH) -> list:
    src = LiveSource(source, loop=loop)
    fps = src.fps
    proc_scale = min(1.0, proc_width / src.width)
    W, H = int(src.width * proc_scale) // 2 * 2, int(src.height * proc_scale) // 2 * 2
    print(f"  Source: {source} | {src.width}x{src.height} @ {fps:.1f} fps "
          f"(processing at {W}x{H}) | "
          f"{'file paced as live' if src.is_file else 'live device'}"
          f"{' (looping)' if src.loop else ''}\n")

    scale  = min(1.0, max_display_width / (2 * W))
    out_w  = int(2 * W * scale) // 2 * 2
    out_h  = int(H * scale) // 2 * 2
    writer = None
    if save_path:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        writer = cv2.VideoWriter(str(save_path), cv2.VideoWriter_fourcc(*"mp4v"),
                                 fps, (out_w, out_h))

    log, infer_ms = [], deque(maxlen=30)
    last_status, t_start = None, time.time()
    window = "Exit detection — YOLOE (q: quit, space: pause)"

    try:
        while True:
            idx, frame, skipped = src.read()
            if frame is None:
                break
            vt = src.video_time()
            if proc_scale < 1:
                frame = cv2.resize(frame, (W, H), interpolation=cv2.INTER_AREA)

            t1 = time.time()
            detections = detector(frame, now=time.time())
            infer_ms.append((time.time() - t1) * 1000)

            counts = summarize(detections)
            log.append({"frame": idx, "video_time_s": round(vt, 3), "counts": counts,
                        "detections": detections})
            status = (counts["exits"], counts["obstructed"], counts["partially_obstructed"])
            if status != last_status:
                print(f"  {vt:7.2f}s  exits={counts['exits']} obstructed={counts['obstructed']} "
                      f"partial={counts['partially_obstructed']} obstructions={counts['obstructions']}")
                last_status = status

            annotated = frame.copy()
            if show_zones:
                for ex in (d for d in detections if d["type"] == "exit"):
                    z = clearance_zone(ex["bounding_box"], W, H)
                    cv2.rectangle(annotated, (int(z["x1"]), int(z["y1"])),
                                  (int(z["x2"]), int(z["y2"])), (255, 255, 0), 2)
            s, thick = text_scale(annotated)
            banner_h = cv2.getTextSize("Exits", cv2.FONT_HERSHEY_SIMPLEX, s, thick)[0][1]
            draw_detections(annotated, detections, top_margin=banner_h + 2 * (banner_h // 2 + 4))
            draw_status_banner(annotated, detections)
            avg = sum(infer_ms) / len(infer_ms)
            caption(frame, f"LIVE  t={vt:.1f}s")
            caption(annotated, f"YOLOE | {avg:.0f} ms/frame (~{1000 / avg:.0f} fps) | "
                                f"dropped {src.dropped}")

            combined = cv2.hconcat([frame, annotated])
            if combined.shape[1] != out_w or combined.shape[0] != out_h:
                combined = cv2.resize(combined, (out_w, out_h), interpolation=cv2.INTER_AREA)
            if writer:
                for _ in range(skipped + 1):
                    writer.write(combined)
            if display:
                cv2.imshow(window, combined)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                if key == ord(" "):
                    t_pause = time.time()
                    cv2.waitKey(0)
                    src.pause_clock(time.time() - t_pause)
    finally:
        src.release()
        if writer:
            writer.release()
        if display:
            cv2.destroyAllWindows()

    avg = sum(infer_ms) / max(1, len(infer_ms))
    print(f"\n  Ran {time.time() - t_start:.1f}s | {src.idx + 1} frames, {src.dropped} dropped | "
          f"~{avg:.0f} ms/frame")
    if save_path:
        print(f"  Video saved : {save_path}")
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w") as f:
            json.dump({"source": source, "fps": fps, "width": W, "height": H,
                       "frames": log}, f)
        print(f"  JSON saved  : {log_path}")
    return log


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args():
    parser = argparse.ArgumentParser(
        description="Real-time exit + obstruction detection with YOLOE (local, no API)"
    )
    parser.add_argument("source", help="Video file, webcam index (0) or stream URL")
    parser.add_argument("--model",  default="yoloe-11s-seg.pt",
                        help="YOLOE weights in models/ (yoloe-11s/m/l-seg.pt; downloaded on first use)")
    parser.add_argument("--imgsz",  type=int, default=640, help="Inference size (default: 640)")
    parser.add_argument("--device", default=None, help="cuda / mps / cpu (default: auto)")
    parser.add_argument("--loop",   action="store_true", help="Replay a video file continuously")
    parser.add_argument("--all-doors", action="store_true",
                        help="Also treat doors without an EXIT sign as exits")
    parser.add_argument("--proc-width", type=int, default=PROC_WIDTH,
                        help=f"Processing width in px (default: {PROC_WIDTH})")
    parser.add_argument("--zones",  action="store_true",
                        help="Draw each exit's clearance zone (for tuning the rule)")
    parser.add_argument("--save",   default=None, help="Also record the side-by-side view")
    parser.add_argument("--no-display", action="store_true", help="Run without a window")
    parser.add_argument("--out-dir", default="results", help="JSON log directory")
    return parser.parse_args()


def main():
    args = _parse_args()
    detector = YOLOEExitDetector(args.model, args.imgsz, args.device, args.all_doors)
    stem = Path(args.source).stem if Path(args.source).is_file() else "live"
    run(detector, args.source, display=not args.no_display,
        save_path=Path(args.save) if args.save else None,
        log_path=Path(args.out_dir) / f"{stem}_yoloe_detections.json",
        loop=args.loop, show_zones=args.zones, proc_width=args.proc_width)


if __name__ == "__main__":
    main()
