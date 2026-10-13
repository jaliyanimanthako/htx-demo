"""
Qwen helping a detector (LocateAnything): names objects at the door (open-world
categories) in the background, and runs the floor-vs-furniture check.
"""

import json
import re
import threading
import time
import urllib.request

import cv2
import numpy as np

from core import depth_check
from core.prompts import NAME_PROMPT
from engines.qwen_engine import QwenEngine

class QwenAssist:
    """
    Runs beside LocateAnything, never blocking it:
      - every `name_every` s, Qwen names the objects at the door in the latest frame;
        those names are added to LocateAnything's categories for `name_ttl` s
        (open-world: anything Qwen can name can then be located)
      - obstructions not seen before get a close-up floor-vs-furniture check; the
        verdict is cached by position, so later frames drop desk items instantly
    """

    def __init__(self, url: str, model: str = None, name_every: float = 2.5,
                 name_ttl: float = 10.0, max_names: int = 6, verify: bool = True,
                 verify_blocking: bool = True, base_categories: list = ()):
        model = model or first_served_model(url)
        self.base = list(base_categories)
        self.last_names = None       # Qwen's latest answer (shown on each frame line)
        self.engine = QwenEngine("EMPTY", model, url, max_image_side=640, max_tokens=60)
        self.name_every, self.name_ttl, self.max_names = name_every, name_ttl, max_names
        self.verify = verify
        self.verify_blocking = verify_blocking
        self._names = {}             # name → last time Qwen said it
        self._latest = None          # latest PIL image sent to LocateAnything
        self._lock = threading.Lock()
        self._verifying = False
        self._stop = threading.Event()
        threading.Thread(target=self._name_loop, daemon=True).start()

    # names ------------------------------------------------------------------
    def note_image(self, image) -> None:
        with self._lock:
            self._latest = image

    @staticmethod
    def covered_by(n: str, base: list) -> bool:
        """True if the base list already finds this object
        ("box"/"cardboard" ⊆ words of "cardboard box"; "office chair" ⊇ "chair")."""
        base_sets = [set(b.split()) for b in base]
        words = set(n.split())
        return words <= set().union(*base_sets) or any(bs <= words for bs in base_sets)

    def extra_categories(self, base: list) -> list:
        """Qwen's recent names that the base list doesn't already cover."""
        now = time.time()
        with self._lock:
            self._names = {n: t for n, t in self._names.items() if now - t <= self.name_ttl}
            names = list(self._names)
        return [n for n in names if not self.covered_by(n, base)]

    def note(self) -> str:
        """Short text for the per-frame log line."""
        if self.last_names is None:
            return "qwen: (waiting)"
        extra = self.extra_categories(self.base)
        return (f"qwen: {', '.join(self.last_names) or 'none'}"
                + (f" (+searching: {', '.join(extra)})" if extra else ""))

    def _name_loop(self) -> None:
        while not self._stop.wait(self.name_every):
            with self._lock:
                image = self._latest
            if image is None:
                continue
            try:
                ans = self.engine.query_image(image, "You are a fire-safety inspector.",
                                              user_text=NAME_PROMPT)
            except Exception:
                continue
            names = []
            for part in re.split(r"[,\n;]+", ans.lower()):
                n = re.sub(r"[^a-z\- ]", "", part).strip()
                if n and n != "none" and len(n.split()) <= 3 and "door" not in n \
                        and "sign" not in n and n not in names:
                    names.append(n)
            names = names[: self.max_names]
            self.last_names = names
            if names:
                now = time.time()
                with self._lock:
                    for n in names:
                        self._names[n] = now

    # depth check --------------------------------------------------------------
    def check(self, detections: list, image, W: int, H: int) -> list:
        """
        Floor-vs-furniture check on obstructions not verified recently.
        blocking (default): crops are checked in parallel before the result is
          returned (+~0.3-0.7 s on Qwen-4B) — every shown result is filtered.
        background: result returned at once; verdicts only reach later frames via the
          position cache, which misses when the camera moves between results.
        """
        detections = depth_check.apply_cached_verdicts(detections, W, H)
        if not self.verify or not depth_check.needs_depth_check(detections):
            return detections
        if self.verify_blocking:
            return self._verify_now(detections, image, W, H)
        if self._verifying:
            return detections
        self._verifying = True
        iw, ih = image.size
        sx, sy = iw / W, ih / H
        scaled = [{**d, "bounding_box": {"x1": int(d["bounding_box"]["x1"] * sx),
                                         "y1": int(d["bounding_box"]["y1"] * sy),
                                         "x2": int(d["bounding_box"]["x2"] * sx),
                                         "y2": int(d["bounding_box"]["y2"] * sy)}}
                  for d in detections]
        frame = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)

        def run():
            try:   # verdicts land in video_infer's position cache → used by next frames
                depth_check.verify_depth(self.engine, scaled, frame)
            finally:
                self._verifying = False
        threading.Thread(target=run, daemon=True).start()
        return detections

    def _verify_now(self, detections: list, image, W: int, H: int) -> list:
        iw, ih = image.size
        sx, sy = iw / W, ih / H
        scaled = [{**d, "bounding_box": {"x1": int(d["bounding_box"]["x1"] * sx),
                                         "y1": int(d["bounding_box"]["y1"] * sy),
                                         "x2": int(d["bounding_box"]["x2"] * sx),
                                         "y2": int(d["bounding_box"]["y2"] * sy)}}
                  for d in detections]
        frame = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        checked = depth_check.verify_depth(self.engine, scaled, frame)
        # map verdicts back onto the full-resolution detections (same order, minus drops)
        kept_ids = {id(d) for d in checked}
        out = []
        for orig, sc in zip(detections, scaled):
            if id(sc) in kept_ids:
                if "depth_check" in sc:
                    orig["depth_check"] = sc["depth_check"]
                if orig["type"] == "exit":
                    orig["status"] = sc.get("status", orig.get("status"))
                out.append(orig)
        return out

    def stop(self) -> None:
        self._stop.set()


def first_served_model(url: str) -> str:
    with urllib.request.urlopen(url.rstrip("/") + "/models", timeout=5) as r:
        return json.load(r)["data"][0]["id"]
