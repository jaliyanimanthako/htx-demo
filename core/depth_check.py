"""
"Is this obstruction on the floor or on furniture?" — a close-up check by the VLM.
Removes foreground items (on desks / shelves) that only overlap a door in 2D.
Verdicts are cached by position for a few seconds so repeated frames reuse them.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import cv2
from PIL import Image

from core.prompts import DEPTH_PROMPT
from engines.qwen_engine import QwenEngine

VERIFY_DEPTH  = False   # off by default (extra calls); CLI: --verify-depth


_verify_pool = ThreadPoolExecutor(max_workers=8)


# Consecutive frames show the same objects — reuse a verdict for the same label at
# about the same place (normalised IoU) instead of asking again every frame.
VERIFY_CACHE_S   = 5.0


VERIFY_CACHE_IOU = 0.3


_verify_cache: list = []          # (time, label, (x1, y1, x2, y2) normalised, verdict)


_verify_lock = threading.Lock()


def _norm_box(bb: dict, w: int, h: int) -> tuple:
    return (bb["x1"] / w, bb["y1"] / h, bb["x2"] / w, bb["y2"] / h)


def _box_iou(a: tuple, b: tuple) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _cached_verdict(box: tuple):
    """Verdict for an object at about this place (label-agnostic: the model names the
    same pile "boxes" in one frame and "cardboard box" in the next)."""
    now = time.time()
    with _verify_lock:
        _verify_cache[:] = [c for c in _verify_cache if now - c[0] <= VERIFY_CACHE_S]
        for _, _, b, verdict in reversed(_verify_cache):
            if _box_iou(b, box) >= VERIFY_CACHE_IOU:
                return verdict
    return None


def _store_verdict(label: str, box: tuple, verdict: str) -> None:
    with _verify_lock:
        _verify_cache.append((time.time(), label, box, verdict))


def base_crop(frame, bb: dict):
    """Crop around an object's lower part, wide enough to show what it stands on."""
    H, W = frame.shape[:2]
    w, h = bb["x2"] - bb["x1"], bb["y2"] - bb["y1"]
    x1, x2 = int(max(0, bb["x1"] - 0.6 * w)), int(min(W, bb["x2"] + 0.6 * w))
    y1, y2 = int(max(0, bb["y1"] - 0.2 * h)), int(min(H, bb["y2"] + 0.5 * h))
    return Image.fromarray(cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2RGB))


def _apply_verdicts(detections: list) -> list:
    """Drop obstructions marked "furniture"; an exit that lost all of them is clear."""
    obstructions = [d for d in detections if d["type"] == "obstruction"]
    kept = [d for d in detections if d.get("depth_check") != "furniture"]
    blocked = {d.get("blocks_exit") for d in kept if d["type"] == "obstruction"}
    for ex in (d for d in kept if d["type"] == "exit"):
        if ex["label"] not in blocked and any(
                o.get("blocks_exit") == ex["label"] for o in obstructions):
            ex["status"] = "clear"   # all of its obstructions were on furniture
    return kept


def apply_cached_verdicts(detections: list, img_w: int, img_h: int) -> list:
    """Instant, call-free filter: reuse recent depth verdicts for objects at the same place."""
    for d in detections:
        if d["type"] == "obstruction" and "depth_check" not in d:
            verdict = _cached_verdict(_norm_box(d["bounding_box"], img_w, img_h))
            if verdict:
                d["depth_check"] = verdict
    return _apply_verdicts(detections)


def needs_depth_check(detections: list) -> bool:
    return any(d["type"] == "obstruction" and "depth_check" not in d for d in detections)


def verify_depth(engine: QwenEngine, detections: list, frame) -> list:
    """
    Ask the VLM, on a close-up crop, whether each obstruction stands on the floor
    or on furniture; drop the ones on furniture (foreground objects that only
    overlap the door in 2D). Exits left with no obstruction become clear.
    Recent verdicts at the same place are reused without a call.
    """
    H, W = frame.shape[:2]
    detections = apply_cached_verdicts(detections, W, H)
    todo = [d for d in detections if d["type"] == "obstruction" and "depth_check" not in d]

    def ask(ob):
        box = _norm_box(ob["bounding_box"], W, H)
        try:
            ans = engine.query_image(base_crop(frame, ob["bounding_box"]),
                                     "Answer with exactly one word.",
                                     user_text=DEPTH_PROMPT.format(label=ob["label"]))
            verdict = "furniture" if ("raised" in ans.lower() or "furniture" in ans.lower()) else "floor"
            _store_verdict(ob["label"], box, verdict)
            return verdict
        except Exception:
            return None   # verification failed → keep the obstruction (safe side)

    for ob, verdict in zip(todo, _verify_pool.map(ask, todo)):
        if verdict:
            ob["depth_check"] = verdict
    return _apply_verdicts(detections)
