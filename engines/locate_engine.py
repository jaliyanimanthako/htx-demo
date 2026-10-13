"""
LocateEngine — client for the LocateAnything-3B server (server/locate_server.py)
================================================================================
    engine = LocateEngine("http://<server-ip>:8020")
    dets = engine.detect(pil_image, ["door", "chair", "box"])
    # → [{"label": "door", "bounding_box": {"x1","y1","x2","y2"} (pixels)}, ...]
"""

import base64
import io
import re
import time

import requests
from PIL import Image


# Model output: <ref>label</ref><box><x1><y1><x2><y2></box>[<box>...</box>...]
# with <box>None</box> for a category that is not in the image.
_TOKEN = re.compile(r"<ref>(.*?)</ref>|<box>\s*<(\d+)>\s*<(\d+)>\s*<(\d+)>\s*<(\d+)>\s*</box>")


def parse_answer(answer: str) -> list:
    """Labelled 0-1000 boxes from a LocateAnything answer; malformed boxes dropped."""
    boxes, label = [], "object"
    for m in _TOKEN.finditer(answer):
        if m.group(1) is not None:
            label = m.group(1).strip() or "object"
            continue
        x1, y1, x2, y2 = (int(g) for g in m.groups()[1:])
        if x2 - x1 < 3 or y2 - y1 < 3:      # e.g. <723><725><280><999> from fast mode
            continue
        boxes.append({"label": label, "x1": x1, "y1": y1, "x2": x2, "y2": y2})
    return boxes


class LocateEngine:
    def __init__(self, base_url: str, max_image_side: int = 1280, timeout: float = 60.0,
                 mode: str = "hybrid"):
        """
        Args:
            base_url:        e.g. "http://<server-ip>:8020"
            max_image_side:  images are downscaled to this before upload
            mode:            "fast" (parallel boxes) | "hybrid" (default) | "slow"
        """
        self.base_url = base_url.rstrip("/")
        self.max_image_side = max_image_side
        self.timeout = timeout
        self.mode = mode
        self.session = requests.Session()
        info = self.session.get(f"{self.base_url}/health", timeout=10).json()
        print(f"LocateEngine ready: {info.get('model')} on {info.get('gpu')} @ {self.base_url}")

    def _encode(self, image: Image.Image) -> str:
        w, h = image.size
        if max(w, h) > self.max_image_side:
            s = self.max_image_side / max(w, h)
            image = image.resize((int(w * s), int(h * s)), Image.LANCZOS)
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=90)
        return base64.b64encode(buf.getvalue()).decode()

    def _post(self, path: str, payload: dict) -> dict:
        t0 = time.time()
        r = self.session.post(f"{self.base_url}{path}", json=payload, timeout=self.timeout)
        r.raise_for_status()
        out = r.json()
        out["boxes"] = parse_answer(out.get("answer", ""))   # client-side parse (ref labels)
        out["round_trip_s"] = round(time.time() - t0, 3)
        return out

    @staticmethod
    def _to_pixels(boxes: list, w: int, h: int) -> list:
        return [{"label": b["label"],
                 "bounding_box": {"x1": int(b["x1"] / 1000 * w), "y1": int(b["y1"] / 1000 * h),
                                  "x2": int(b["x2"] / 1000 * w), "y2": int(b["y2"] / 1000 * h)}}
                for b in boxes]

    def detect_raw(self, image: Image.Image, categories: list, **kw) -> dict:
        """Full server response (answer text, 0-1000 boxes, latency)."""
        return self._post("/detect", {"image": self._encode(image), "categories": categories,
                                      "mode": kw.get("mode", self.mode),
                                      **{k: v for k, v in kw.items() if k != "mode"}})

    def detect(self, image: Image.Image, categories: list, **kw) -> list:
        res = self.detect_raw(image, categories, **kw)
        return self._to_pixels(res["boxes"], *image.size)

    def ground(self, image: Image.Image, phrase: str, multi: bool = True, **kw) -> list:
        res = self._post("/ground", {"image": self._encode(image), "phrase": phrase,
                                     "multi": multi, "mode": kw.get("mode", self.mode)})
        return self._to_pixels(res["boxes"], *image.size)
