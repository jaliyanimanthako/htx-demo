"""
Turn VLM answers into detections.

Detections are dicts:
  {"type": "exit", "label", "status": clear|partially_obstructed|obstructed,
   "bounding_box": {"x1","y1","x2","y2"} (pixels), "confidence"}
  {"type": "obstruction", "label", "blocks_exit", "rests_on", "bounding_box", ...}

parse_response() accepts the compact E/O line format (what the prompts ask for)
and both JSON layouts models sometimes fall back to; validate() then drops
obstructions that are not physically at an exit and repeated boxes.
"""

import re

from engines.qwen_engine import QwenEngine

def norm_to_pixel(bbox: list, img_w: int, img_h: int, scale: float = 999) -> dict:
    """Convert normalised [x1,y1,x2,y2] (0-`scale`) to pixel coordinates."""
    x1, x2 = sorted((bbox[0], bbox[2]))
    y1, y2 = sorted((bbox[1], bbox[3]))
    return {"x1": max(0, min(img_w, int(x1 / scale * img_w))),
            "y1": max(0, min(img_h, int(y1 / scale * img_h))),
            "x2": max(0, min(img_w, int(x2 / scale * img_w))),
            "y2": max(0, min(img_h, int(y2 / scale * img_h)))}


def extract_detections(llm_result: dict, img_w: int, img_h: int) -> list:
    """Pull detections with a valid 4-value bbox out of the VLM JSON."""
    detections = []
    for det in llm_result.get("detections", []):
        bbox = det.get("bbox")
        if not (isinstance(bbox, list) and len(bbox) == 4):
            continue
        try:
            box = norm_to_pixel([float(v) for v in bbox], img_w, img_h)
        except (TypeError, ValueError):
            continue
        entry = {
            "type":         str(det.get("type", "object")).lower(),
            "label":        str(det.get("label", "object")),
            "confidence":   det.get("confidence"),
            "bounding_box": box,
        }
        if det.get("status"):
            entry["status"] = str(det["status"]).lower()
        if det.get("blocks_exit"):
            entry["blocks_exit"] = str(det["blocks_exit"])
        detections.append(entry)
    return detections


STATUS_CODES = {"C": "clear", "P": "partially_obstructed", "B": "obstructed",
                "O": "obstructed"}


_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")


# "rests_on" words in obstruction lines: only these count as being at the exit
AT_EXIT_SUPPORT  = {"floor", "door", "doorway", "ground", "threshold"}


OFF_EXIT_SUPPORT = {"desk", "table", "counter", "shelf", "cabinet", "cupboard", "bench",
                    "worktop", "sill", "windowsill", "furniture", "foreground", "wall"}


def parse_compact(text: str, img_w: int, img_h: int) -> list:
    """
    Parse the compact line format (prompts.OUTPUT_FORMAT_COMPACT):
        E <C|P|B> x1 y1 x2 y2
        O <exit #> <rests_on> <label> x1 y1 x2 y2   (rests_on optional)
        NONE
    Obstructions resting on furniture (desk, shelf, ...) are dropped.
    Coordinates are 0-1000. Malformed lines are skipped.
    """
    exits, obstructions = [], []
    for raw in text.splitlines():
        line = raw.strip().strip("`*-• ").strip()
        if len(line) < 2 or line[0].upper() not in "EO" or line[1].isalpha():
            continue   # NONE, prose, "Example:", etc.
        tokens = [t for t in re.split(r"[\s,;:|()\[\]]+", line[1:]) if t]
        nums = [i for i, t in enumerate(tokens) if _NUMBER.match(t)]
        if len(nums) < 4:
            continue
        coord_idx = nums[-4:]
        box  = norm_to_pixel([float(tokens[i]) for i in coord_idx], img_w, img_h, scale=1000)
        head = [t for i, t in enumerate(tokens) if i not in coord_idx]
        if line[0].upper() == "E":
            code = head[0][0].upper() if head else "C"
            if head and head[0].lower().startswith("part"):
                code = "P"
            exits.append({"type": "exit", "label": "door", "confidence": None,
                          "status": STATUS_CODES.get(code, "clear"), "bounding_box": box})
        else:
            n = int(float(head.pop(0))) if head and _NUMBER.match(head[0]) else 1
            support = None
            if head and head[0].lower() in AT_EXIT_SUPPORT | OFF_EXIT_SUPPORT:
                support = head.pop(0).lower()
            if support in OFF_EXIT_SUPPORT:
                continue   # model says it sits on furniture / in the foreground
            label = " ".join(head) or "obstruction"
            ob = {"type": "obstruction", "label": label, "confidence": None,
                  "bounding_box": box}
            if support:
                ob["rests_on"] = support
            obstructions.append((n, ob))

    if len(exits) > 1:
        for i, ex in enumerate(exits, 1):
            ex["label"] = f"door {i}"
    for n, ob in obstructions:
        if 1 <= n <= len(exits):
            ob["blocks_exit"] = exits[n - 1]["label"]
    return exits + [ob for _, ob in obstructions]


def _near_exit(ob: dict, ex: dict) -> bool:
    """
    Geometric sanity check: an obstruction must line up with the exit
    horizontally (door ± 25% of its width) and its bottom edge must sit around
    the door's base (lower half of the door down to 30% of its height below).
    Rejects items elsewhere in the frame that the model attached to an exit.
    """
    o, e = ob["bounding_box"], ex["bounding_box"]
    w, h = e["x2"] - e["x1"], e["y2"] - e["y1"]
    overlaps_x = min(o["x2"], e["x2"] + 0.25 * w) > max(o["x1"], e["x1"] - 0.25 * w)
    base_ok = e["y1"] + 0.5 * h <= o["y2"] <= e["y2"] + 0.3 * h
    return overlaps_x and base_ok


MAX_OBSTRUCTIONS = 8


def _bb_iou(a: dict, b: dict) -> float:
    ix = max(0, min(a["x2"], b["x2"]) - max(a["x1"], b["x1"]))
    iy = max(0, min(a["y2"], b["y2"]) - max(a["y1"], b["y1"]))
    inter = ix * iy
    union = ((a["x2"] - a["x1"]) * (a["y2"] - a["y1"])
             + (b["x2"] - b["x1"]) * (b["y2"] - b["y1"]) - inter)
    return inter / union if union > 0 else 0.0


def validate(detections: list) -> list:
    """Drop obstructions not physically at an exit or duplicating one already kept
    (small models sometimes repeat a line with shifted numbers); keep exit status
    consistent."""
    exits = [d for d in detections if d["type"] == "exit"]
    by_label = {ex["label"]: ex for ex in exits}
    kept, n_obs = [], 0
    for d in detections:
        if d["type"] != "obstruction":
            kept.append(d)
            continue
        if n_obs >= MAX_OBSTRUCTIONS or any(
                k["type"] == "obstruction"
                and _bb_iou(k["bounding_box"], d["bounding_box"]) > 0.5 for k in kept):
            continue
        targets = [by_label[d["blocks_exit"]]] if d.get("blocks_exit") in by_label else exits
        near = [ex for ex in targets if _near_exit(d, ex)]
        if not near:
            continue
        d["blocks_exit"] = near[0]["label"]
        if near[0]["status"] == "clear":   # model said clear but listed a blocker at the door
            near[0]["status"] = "partially_obstructed"
        kept.append(d)
        n_obs += 1
    return kept


def parse_exits_json(data: dict, img_w: int, img_h: int) -> list:
    """
    JSON some models write instead of the compact lines (seen from Qwen3-VL-4B):
      {"exits": [{"status": "P", "box": [x1,y1,x2,y2]}],
       "obstructions": [{"exit": 1, "rests_on": "floor", "label": "...", "box": [...]}]}
    Same meaning as E/O lines, 0-1000 coordinates.
    """
    def box_of(item):
        b = item.get("box") or item.get("bbox") or item.get("bbox_2d")
        if isinstance(b, list) and len(b) == 4:
            try:
                return norm_to_pixel([float(v) for v in b], img_w, img_h, scale=1000)
            except (TypeError, ValueError):
                return None
        return None

    exits = []
    for ex in data.get("exits") or []:
        bb = box_of(ex)
        if not bb:
            continue
        code = str(ex.get("status", "C")).strip()
        if len(code) <= 2:
            status = STATUS_CODES.get(code[:1].upper(), "clear")
        elif code.lower().startswith("part"):
            status = "partially_obstructed"
        elif code.lower().startswith(("obstr", "block")):
            status = "obstructed"
        else:
            status = "clear"
        exits.append({"type": "exit", "label": "door", "confidence": None,
                      "status": status, "bounding_box": bb})
    if len(exits) > 1:
        for i, ex in enumerate(exits, 1):
            ex["label"] = f"door {i}"
    out = list(exits)
    for ob in data.get("obstructions") or []:
        bb = box_of(ob)
        support = str(ob.get("rests_on", "")).lower().strip()
        if not bb or support in OFF_EXIT_SUPPORT:
            continue
        d = {"type": "obstruction", "label": str(ob.get("label", "obstruction")),
             "confidence": None, "bounding_box": bb}
        if support:
            d["rests_on"] = support
        try:
            n = int(ob.get("exit", 1))
            if 1 <= n <= len(exits):
                d["blocks_exit"] = exits[n - 1]["label"]
        except (TypeError, ValueError):
            pass
        out.append(d)
    return out


def parse_response(text: str, img_w: int, img_h: int) -> list:
    """Detections from a VLM answer: compact E/O lines (asked for), or either JSON
    layout a model may fall back to."""
    if '"detections"' in text:
        return validate(extract_detections(QwenEngine.parse_json(text), img_w, img_h))
    if '"exits"' in text or '"obstructions"' in text:
        try:
            return validate(parse_exits_json(QwenEngine.parse_json(text), img_w, img_h))
        except Exception:
            pass
    return validate(parse_compact(text, img_w, img_h))
