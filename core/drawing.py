"""
Drawing helpers: status-coloured boxes, summary banner, captions.
Colours (BGR): green = clear exit, amber = partially obstructed, red = obstructed,
orange = obstructing object.
"""

import hashlib

import cv2

# BGR
EXIT_STATUS_COLORS = {
    "clear":                (0, 200, 0),     # green
    "partially_obstructed": (0, 200, 255),   # amber
    "obstructed":           (0, 0, 255),     # red
}


OBSTRUCTION_COLOR = (0, 128, 255)            # orange


def _label_color(label: str) -> tuple:
    """Stable BGR colour per label."""
    h = hashlib.md5(label.lower().encode("utf-8")).digest()
    return (60 + h[0] % 196, 60 + h[1] % 196, 60 + h[2] % 196)


def _det_color(det: dict) -> tuple:
    if det["type"] == "exit":
        return EXIT_STATUS_COLORS.get(det.get("status"), EXIT_STATUS_COLORS["clear"])
    if det["type"] == "obstruction":
        return OBSTRUCTION_COLOR
    return _label_color(det["label"])


def _det_text(det: dict) -> str:
    if det["type"] == "exit":
        status = det.get("status", "clear").replace("_", " ").upper()
        text   = f"EXIT: {det['label']} [{status}]"
    elif det["type"] == "obstruction":
        text = f"OBSTRUCTION: {det['label']}"
    else:
        text = det["label"]
    if isinstance(det.get("confidence"), (int, float)):
        text += f" {det['confidence']:.2f}"
    return text


def summarize(detections: list) -> dict:
    exits = [d for d in detections if d["type"] == "exit"]
    return {
        "exits":                len(exits),
        "obstructed":           sum(d.get("status") == "obstructed" for d in exits),
        "partially_obstructed": sum(d.get("status") == "partially_obstructed" for d in exits),
        "obstructions":         sum(d["type"] == "obstruction" for d in detections),
    }


def text_scale(frame) -> tuple:
    """Font scale / thickness relative to a 720p frame."""
    s = max(0.4, frame.shape[0] / 720)
    return 0.6 * s, max(1, round(2 * s))


def draw_status_banner(frame, detections: list) -> int:
    """Draw the summary banner at the top; returns its height in pixels."""
    s = summarize(detections)
    blocked = s["obstructed"] + s["partially_obstructed"]
    text = (f"Exits: {s['exits']} | Obstructed: {s['obstructed']} | "
            f"Partial: {s['partially_obstructed']} | Obstructions: {s['obstructions']}")
    color = (0, 0, 200) if blocked else (0, 150, 0)
    scale, thick = text_scale(frame)
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
    pad = th // 2 + 4
    cv2.rectangle(frame, (0, 0), (tw + 2 * pad, th + 2 * pad), color, -1)
    cv2.putText(frame, text, (pad, th + pad), cv2.FONT_HERSHEY_SIMPLEX, scale,
                (255, 255, 255), thick, cv2.LINE_AA)
    return th + 2 * pad


def draw_detections(frame, detections: list, top_margin: int = 0) -> None:
    """top_margin: pixels reserved at the top (status banner) that labels avoid."""
    img_h, img_w = frame.shape[:2]
    scale, thick = text_scale(frame)

    # Exits first so obstruction boxes sit on top
    for det in sorted(detections, key=lambda d: d["type"] != "exit"):
        bb    = det["bounding_box"]
        color = _det_color(det)
        text  = _det_text(det)

        cv2.rectangle(frame, (bb["x1"], bb["y1"]), (bb["x2"], bb["y2"]), color, thick + 1)
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
        lh = th + 8
        # Label above the box; if that would hit the banner/top edge, put it inside
        label_top = bb["y1"] - lh
        if label_top < top_margin:
            label_top = max(top_margin, bb["y1"])
        label_x = max(0, min(bb["x1"], img_w - tw - 6))
        cv2.rectangle(frame, (label_x, label_top), (label_x + tw + 6, label_top + lh), color, -1)
        text_color = (0, 0, 0) if color == EXIT_STATUS_COLORS["partially_obstructed"] else (255, 255, 255)
        cv2.putText(frame, text, (label_x + 3, label_top + th + 3),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, text_color, thick, cv2.LINE_AA)


def caption(frame, text: str) -> None:
    scale, thick = text_scale(frame)
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
    y = frame.shape[0] - 10
    cv2.rectangle(frame, (0, y - th - 10), (tw + 16, frame.shape[0]), (0, 0, 0), -1)
    cv2.putText(frame, text, (8, y - 4), cv2.FONT_HERSHEY_SIMPLEX, scale,
                (255, 255, 255), thick, cv2.LINE_AA)
