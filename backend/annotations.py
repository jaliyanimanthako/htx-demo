"""Recorded-video counterpart of the tablet's CameraDetectionOverlay.

Use the same normalized live results, status rules and labels as parseCameraResult;
never use the CLI monitors' annotated frames for the tablet combined demo.
"""
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def annotation_color(status):
    """RGB values matching Android AnnotationColors.kt."""
    if status in ("clear", "valid"):
        return (0, 200, 0)
    if status in ("partially_obstructed", "expiring_soon"):
        return (255, 200, 0)
    if status in ("obstructed", "expired", "obstacle"):
        return (255, 0, 0)
    return (160, 160, 160)


def annotation_items(result):
    """Presentation only: keep tracking IDs and raw inference results untouched."""
    items = []
    assistance = result.get("qwen_active", result.get("date_reading_enabled", False)) is True
    dates = bool(result.get("date_reading_enabled")) and result.get("qwen_active") is not False
    for item in result.get("exits", []):
        checking = assistance and not item.get("confirmed", False)
        status = "unknown" if checking else item["status"]
        label = "CHECKING" if checking else item["status_text"]
        items.append((item["box"], f"EXIT · {label}", status))
    for item in result.get("obstacles", []):
        items.append((item["box"], item["label"], "obstacle"))
    for item in result.get("extinguishers", []):
        reading = dates and (item.get("reading", False) or item["status"] == "reading")
        verified = dates and item.get("confirmed", False)
        status = "reading" if reading else "unverified" if not verified else item["status"]
        if not dates:
            label = "Dates not verified"
        elif reading:
            label = "READING"
        elif not verified:
            label = item["status_text"] + " · unverified"
        else:
            label = item["status_text"]
        items.append((item["box"], f"Extinguisher · {label}", status))
    return items


@lru_cache(maxsize=16)
def _font(size):
    for name in ("DejaVuSans-Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    # Pillow's bundled font keeps Unicode labels working on minimal servers.
    return ImageFont.load_default(size=size)


def draw_annotations(frame, result):
    """Thin status outlines, white labels, 85%-black backgrounds; no IDs/timers.

    Text and strokes scale with source resolution; Android displays the resulting
    frame at the recording's aspect ratio. Returns a new BGR frame.
    """
    items = annotation_items(result)
    if not items:
        return frame.copy()
    height, width = frame.shape[:2]
    scale = max(0.5, height / 720)
    font = _font(max(10, round(14 * scale)))
    stroke = max(1, round(2.5 * scale))
    pad = max(3, round(6 * scale))
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(image, "RGBA")
    for box, label, status in items:
        left, top, right, bottom = (
            min(width - 1, max(0, box[0] * width)),
            min(height - 1, max(0, box[1] * height)),
            min(width - 1, max(0, box[2] * width)),
            min(height - 1, max(0, box[3] * height)),
        )
        if right <= left or bottom <= top:
            continue
        draw.rectangle((left, top, right, bottom), outline=(*annotation_color(status), 255), width=stroke)
        text_box = draw.textbbox((0, 0), label, font=font)
        label_width = text_box[2] - text_box[0] + 2 * pad
        label_height = text_box[3] - text_box[1] + 2 * pad
        x = max(0, min(left, width - label_width))
        y = max(0, min(top - label_height - 4 * scale, height - label_height))
        draw.rectangle((x, y, x + label_width, y + label_height), fill=(0, 0, 0, 217))
        draw.text((x + pad - text_box[0], y + pad - text_box[1]), label, font=font, fill=(255, 255, 255, 255))
    return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
