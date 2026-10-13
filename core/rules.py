"""
Obstruction rule (shared)
=========================
Geometry used by detector-based runners (yoloe_infer.py, locate_live.py): each
exit gets a clearance zone (lower part of the door + floor strip in front of
it); an obstacle obstructs the exit if it stands on the floor inside the zone
and overlaps it enough. Status follows from how much of the door width the
obstacles cover.
"""

# ── Obstruction rule ──────────────────────────────────────────────────────────
ZONE_TOP_FRAC       = 0.45  # zone starts this far down the door (0 = top, 1 = bottom)
ZONE_BELOW_FRAC     = 0.25  # zone extends this much of the door height below the door
ZONE_SIDE_FRAC      = 0.15  # zone is widened by this much of the door width each side
MIN_ZONE_OVERLAP    = 0.25  # share of the obstacle box that must lie inside the zone
OBSTRUCTED_COVERAGE = 0.60  # share of door width covered → "obstructed"

# ── Geometry ──────────────────────────────────────────────────────────────────

def box_iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def containment(inner, outer) -> float:
    """Share of `inner` that lies inside `outer`."""
    ix = max(0, min(inner[2], outer[2]) - max(inner[0], outer[0]))
    iy = max(0, min(inner[3], outer[3]) - max(inner[1], outer[1]))
    area = max(1e-6, (inner[2] - inner[0]) * (inner[3] - inner[1]))
    return ix * iy / area


def to_bbox_dict(b, img_w, img_h) -> dict:
    return {"x1": int(max(0, b[0])), "y1": int(max(0, b[1])),
            "x2": int(min(img_w, b[2])), "y2": int(min(img_h, b[3]))}


def clearance_zone(bb: dict, img_w: int, img_h: int) -> dict:
    w, h = bb["x2"] - bb["x1"], bb["y2"] - bb["y1"]
    return {
        "x1": max(0, bb["x1"] - ZONE_SIDE_FRAC * w),
        "x2": min(img_w, bb["x2"] + ZONE_SIDE_FRAC * w),
        "y1": bb["y1"] + ZONE_TOP_FRAC * h,
        "y2": min(img_h, bb["y2"] + ZONE_BELOW_FRAC * h),
    }


def _overlap_frac(box: dict, zone: dict) -> float:
    ix = max(0, min(box["x2"], zone["x2"]) - max(box["x1"], zone["x1"]))
    iy = max(0, min(box["y2"], zone["y2"]) - max(box["y1"], zone["y1"]))
    area = max(1, (box["x2"] - box["x1"]) * (box["y2"] - box["y1"]))
    return ix * iy / area


def _covered_width(intervals: list) -> float:
    total, end = 0.0, None
    for a, b in sorted(intervals):
        if end is None or a > end:
            total += b - a
            end = b
        elif b > end:
            total += b - end
            end = b
    return total


def covers_door_base(ob: dict, door: dict) -> bool:
    """
    Occlusion rule: an object that hides the bottom edge of the door is in front of
    it. Detectors box only the VISIBLE door, so when boxes / chairs hide its base
    the door box ends at their top and the floor-zone test alone misses them.
    True when the object overlaps the door horizontally (≥ 30% of the object's
    width), its top is above the door box's bottom edge (it rises in front of the
    door) and its bottom reaches down to / past that edge.
    """
    dw, dh = door["x2"] - door["x1"], door["y2"] - door["y1"]
    ow = max(1, ob["x2"] - ob["x1"])
    x_overlap = max(0, min(ob["x2"], door["x2"]) - max(ob["x1"], door["x1"]))
    oh = ob["y2"] - ob["y1"]
    return (dw > 0 and dh > 0
            and oh >= 0.15 * dh            # thin items (cables on a desk) don't qualify
            and x_overlap >= 0.3 * ow
            and ob["y1"] < door["y2"] - 0.05 * dh
            and ob["y2"] >= door["y2"] - 0.1 * dh)


def assess(exits: list, obstacles: list, img_w: int, img_h: int) -> list:
    """Sets status/coverage on each exit; returns the obstacles that obstruct an exit."""
    blocking = {}
    for ex in exits:
        bb     = ex["bounding_box"]
        zone   = clearance_zone(bb, img_w, img_h)
        door_w = max(1, bb["x2"] - bb["x1"])
        spans  = []
        for i, ob in enumerate(obstacles):
            ob_bb  = ob["bounding_box"]
            foot_x = (ob_bb["x1"] + ob_bb["x2"]) / 2
            on_floor_in_zone = (zone["y1"] <= ob_bb["y2"] <= zone["y2"]
                                and zone["x1"] <= foot_x <= zone["x2"])
            if (on_floor_in_zone and _overlap_frac(ob_bb, zone) >= MIN_ZONE_OVERLAP) \
                    or covers_door_base(ob_bb, bb):
                a, b = max(ob_bb["x1"], bb["x1"]), min(ob_bb["x2"], bb["x2"])
                if b > a:
                    spans.append((a, b))
                blocking.setdefault(i, {**ob, "blocks_exit": ex["label"]})
        coverage = _covered_width(spans) / door_w
        ex["coverage"] = round(coverage, 2)
        ex["status"] = ("obstructed" if coverage >= OBSTRUCTED_COVERAGE
                        else "partially_obstructed" if spans
                        else "clear")
    return list(blocking.values())


def merge_double_doors(doors: list) -> list:
    """Merge side-by-side door leaves (similar vertical extent, touching) into one."""
    doors = sorted(doors, key=lambda d: d["box"][0])
    merged = []
    for d in doors:
        if merged:
            m = merged[-1]
            a, b = m["box"], d["box"]
            v_overlap = max(0, min(a[3], b[3]) - max(a[1], b[1]))
            v_union   = max(a[3], b[3]) - min(a[1], b[1])
            gap       = b[0] - a[2]
            avg_w     = ((a[2] - a[0]) + (b[2] - b[0])) / 2
            if v_union > 0 and v_overlap / v_union > 0.7 and gap < 0.15 * avg_w:
                m["box"] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                m["conf"] = max(m["conf"], d["conf"])
                m["signed"] = m["signed"] or d["signed"]
                m["weak"] = m["weak"] and d["weak"]
                continue
        merged.append(dict(d))
    return merged
