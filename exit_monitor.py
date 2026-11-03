"""
Exit monitor — real-time exit / obstruction monitoring (3-speed architecture)
============================================================================

  FAST LOOP (every frame)     YOLOE → tracker (IDs, camera-motion) → per-frame rule
                              → exit memory → draw / stream / save
  LOCATEANYTHING (~1/s)       high-quality boxes merged into the tracks (fixes labels,
                              adds what YOLOE missed); Qwen's object names extend its list
  QWEN (on events only)       new door → "is it an exit?"; new obstacle at a door →
                              "floor or furniture?"; obstacles at a door changed /
                              periodic → verdict C/P/B
  EXIT MEMORY                 one record per exit: stable status (a vote over the last
                              --hold seconds; a change needs 60 %), since-when, blockers,
                              snapshot, history → panel + event log + JSON report

Nothing in the fast loop waits for LocateAnything or Qwen — except in OFFLINE mode
(--offline, video files): every frame is processed, LocateAnything runs every
--la-interval seconds of VIDEO time and each Qwen check is waited for, so slow model
servers or a hosted API (e.g. OpenRouter) cost processing time, not missed results.
The result is saved as a video (results/exit_monitor_<video>/<video>_annotated.mp4). Without YOLOE
(--no-yoloe) tracks come from LocateAnything only and are carried between its
results by camera-motion compensation (simpler, ~1-2 s stale).

Usage:
  python exit_monitor.py IMG_0451.MOV                       # URLs from .env (LOCATE_URL, QWEN_URL)
  python exit_monitor.py IMG_0451.MOV --locate-url http://<server-ip>:8020 \\
                                      --qwen-url http://<server-ip>:8010/v1
  python exit_monitor.py IMG_0451.MOV --no-yoloe            # LocateAnything + Qwen only
  python exit_monitor.py IMG_0451.MOV --no-display --stream # headless → browser :8090
  python exit_monitor.py IMG_0451.MOV --offline --no-display --qwen-url openrouter
  python exit_monitor.py rtsp://<server>:8554/cam           # live camera later
  q / Esc quits, space pauses.
"""

import argparse
import collections
import json
import os
import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

import core.config  # noqa: F401  (loads .env)
from core.depth_check import base_crop
from core.drawing import text_scale
from core.labels import DOOR_LABELS, OBSTACLE_LABELS, SIGN_LABELS, kind_of, norm_box_to_pixels
from core.parsing import STATUS_CODES
from core.prompts import CONFIRM_PROMPT, DEPTH_PROMPT, VERDICT_PROMPT
from core.config import is_openrouter
from core.qwen_assist import QwenAssist, qwen_endpoint
from core.rules import assess, box_iou, containment, merge_double_doors
from core.stream_view import MJPEGStreamer
from core.video_io import LiveSource, MotionCompensator, Progress, VideoSaver
from engines.locate_engine import LocateEngine
from engines.qwen_engine import QwenEngine

STATUS_RANK = {"unknown": -1, "clear": 0, "partially_obstructed": 1, "obstructed": 2}
STATUS_TEXT = {"unknown": "CHECKING", "clear": "CLEAR",
               "partially_obstructed": "PARTIAL", "obstructed": "BLOCKED"}
STATUS_BGR = {"unknown": (160, 160, 160), "clear": (0, 200, 0),
              "partially_obstructed": (0, 200, 255), "obstructed": (0, 0, 255)}
OBSTACLE_BGR = (0, 128, 255)
MIN_SHOWN_W = 0.04   # an exit less than this share of the frame width (leaving the picture) isn't drawn
VERDICT_MIN_GAP = 1.5  # s between Qwen verdicts for one exit, however often its zone changes
BLOCKER_HOLD = 0.8   # s an obstacle stays drawn after it was last in an exit's zone (no blinking)
SHOW_MIN_HITS = 4    # an obstacle is drawn once seen in this many frames (no 1-2 frame ghosts)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _crop(frame, box, left=0.25, right=0.25, top=0.25, bottom=0.25) -> Image.Image:
    H, W = frame.shape[:2]
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    a, b = int(max(0, x1 - left * w)), int(min(W, x2 + right * w))
    c, d = int(max(0, y1 - top * h)), int(min(H, y2 + bottom * h))
    return Image.fromarray(cv2.cvtColor(frame[c:d, a:b], cv2.COLOR_BGR2RGB))


def _warp(box, M) -> list:
    pts = np.float32([[box[0], box[1]], [box[2], box[1]], [box[0], box[3]], [box[2], box[3]]])
    pts = cv2.transform(pts[None], M)[0]
    return [*pts.min(axis=0).tolist(), *pts.max(axis=0).tolist()]


def _area(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _shown_w(box, W) -> float:
    """Visible width of a box as a share of the frame width."""
    return max(0.0, min(W, box[2]) - max(0.0, box[0])) / W


def _is_leaf_of(leaf, door) -> bool:
    """One leaf of a double door: inside the door box, about as tall, clearly narrower.
    (Only then is the whole-door box kept; any other change updates it normally.)"""
    lh, dh = leaf[3] - leaf[1], door[3] - door[1]
    return (containment(leaf, door) >= 0.85 and dh > 0 and abs(lh - dh) <= 0.2 * dh
            and (leaf[2] - leaf[0]) < 0.75 * (door[2] - door[0]))


def _bbdict(box, W, H) -> dict:
    return {"x1": int(max(0, box[0])), "y1": int(max(0, box[1])),
            "x2": int(min(W, box[2])), "y2": int(min(H, box[3]))}


# ── Fast detector ─────────────────────────────────────────────────────────────

class FastDetector:
    """
    YOLOE with text prompts (lazy import so --no-yoloe works without ultralytics).
    tracker="bytetrack" | "botsort": Ultralytics' tracker matches detections frame to
    frame (Kalman filter; a second pass with low-score boxes keeps tracks alive; BoT-SORT
    also compensates camera motion). Each detection then carries "ext_id", which the
    Tracker below follows; None = our own IoU matching only.
    """

    TRACKER_CFG = {"bytetrack": "core/trackers/bytetrack.yaml",
                   "botsort": "core/trackers/botsort.yaml"}

    def __init__(self, model_name: str, imgsz: int = 640, tracker: str = None):
        import yoloe_infer as Y
        from ultralytics import YOLOE
        self.Y, self.imgsz = Y, imgsz
        self.device = Y._pick_device()
        self.names = Y.DOOR_CLASSES + Y.SIGN_CLASSES + Y.OBSTACLE_CLASSES + Y.DECOY_CLASSES
        self.kind = ({n: "door" for n in Y.DOOR_CLASSES} | {n: "sign" for n in Y.SIGN_CLASSES}
                     | {n: "obstacle" for n in Y.OBSTACLE_CLASSES}
                     | {n: "decoy" for n in Y.DECOY_CLASSES})
        print(f"  Loading YOLOE {model_name} on {self.device} ...")
        with Y._in_dir(Y.MODELS_DIR):
            self.model = YOLOE(model_name)
            self.model.set_classes(self.names, self.model.get_text_pe(self.names))
        self.conf = {"door": Y.DOOR_CONF, "sign": Y.SIGN_CONF, "obstacle": Y.OBSTACLE_CONF}
        self.tracker = tracker if tracker in self.TRACKER_CFG else None
        self.reset()

    def reset(self) -> None:
        """New video / camera: the next frame starts the external tracker afresh."""
        self._fresh, self._known = True, set()

    def __call__(self, frame) -> list:
        if self.tracker:
            cfg = str(Path(__file__).resolve().parent / self.TRACKER_CFG[self.tracker])
            r = self.model.track(frame, conf=0.05, imgsz=self.imgsz, device=self.device,
                                 verbose=False, tracker=cfg, persist=not self._fresh)[0]
            self._fresh = False
            ids = r.boxes.id.int().tolist() if r.boxes.id is not None else [None] * len(r.boxes)
        else:
            r = self.model.predict(frame, conf=min(self.conf.values()), imgsz=self.imgsz,
                                   device=self.device, verbose=False)[0]
            ids = [None] * len(r.boxes)
        out = []
        for xyxy, cls, conf, eid in zip(r.boxes.xyxy.tolist(), r.boxes.cls.int().tolist(),
                                        r.boxes.conf.tolist(), ids):
            kind = self.kind[self.names[cls]]
            if kind == "decoy":
                continue
            # a new ID needs the usual per-class confidence; an existing one may continue
            # on a low-score box (that is the point of ByteTrack's second pass)
            if conf < self.conf[kind] and (eid is None or eid not in self._known):
                continue
            if eid is not None:
                self._known.add(eid)
            out.append({"kind": kind, "label": self.names[cls], "box": xyxy, "ext_id": eid})
        return out


# ── Tracker ───────────────────────────────────────────────────────────────────

class Tracker:
    """
    Greedy IoU tracker per kind; boxes follow camera motion between detections.
    - Lost tracks wait in a graveyard for `revive_s` and come back with the same ID
      (and their Qwen verdicts) when re-detected nearby.
    - consolidate() merges tracks of the same thing: door leaves → one door,
      heavily overlapping obstacles → one object.
    """

    def __init__(self, ttl_fast: float = 1.2, ttl_la: float = 4.0, revive_s: float = 6.0):
        self.tracks, self.dead, self.next_id = [], [], 1
        self.ttl_fast, self.ttl_la, self.revive_s = ttl_fast, ttl_la, revive_s

    @staticmethod
    def _same(kind: str, a, b) -> bool:
        if box_iou(a, b) >= 0.3:
            return True
        # a door leaf vs the whole door: one box mostly inside the other
        return kind == "door" and max(containment(a, b), containment(b, a)) >= 0.6

    def find(self, tid: int):
        return next((t for t in self.tracks + self.dead if t["id"] == tid), None)

    def predict(self, M) -> None:
        for t in self.tracks + self.dead:
            t["box"] = _warp(t["box"], M)

    def update(self, dets: list, now: float, source: str) -> None:
        strong = source == "la"
        unmatched = list(range(len(self.tracks)))
        for d in dets:
            eid = d.get("ext_id")             # ByteTrack / BoT-SORT ID (None: IoU matching)
            best, best_iou = None, -1.0
            if eid is not None:
                best = next((ti for ti in unmatched if self.tracks[ti].get("ext_id") == eid
                             and self.tracks[ti]["kind"] == d["kind"]), None)
                ghost = None if best is not None else next(
                    (g for g in self.dead if g.get("ext_id") == eid and g["kind"] == d["kind"]), None)
                if ghost:                      # the external tracker still knows it → same ID
                    self.dead.remove(ghost)
                    ghost.update(box=list(d["box"]), last_seen=now, hits=max(ghost["hits"], 2))
                    self.tracks.append(ghost)
                    continue
            if best is None:
                for ti in unmatched:
                    t = self.tracks[ti]
                    if t["kind"] == d["kind"] and self._same(d["kind"], d["box"], t["box"]):
                        iou = box_iou(d["box"], t["box"])
                        if iou > best_iou:
                            best, best_iou = ti, iou
            if best is None:
                # revive only on real overlap: a lost track's predicted box drifts, and a
                # "detection inside it" test let one exit's ID jump onto another object
                ghost = next((g for g in self.dead if g["kind"] == d["kind"]
                              and box_iou(d["box"], g["box"]) >= 0.3), None)
                if ghost:                      # same object seen again → same ID
                    self.dead.remove(ghost)
                    ghost.update(box=list(d["box"]), last_seen=now,
                                 hits=max(ghost["hits"], 2), ext_id=eid)
                    self.tracks.append(ghost)
                    continue
                self.tracks.append({"id": self.next_id, "kind": d["kind"], "label": d["label"],
                                    "box": list(d["box"]), "hits": 3 if strong else 1,
                                    "first_seen": now, "last_seen": now, "source": source,
                                    "signed": False, "depth": None, "exit_ok": None,
                                    "ext_id": eid})
                self.next_id += 1
                continue
            unmatched.remove(best)
            t = self.tracks[best]
            if eid is not None:
                t["ext_id"] = eid
            w = 0.8 if strong else 0.6
            if d["kind"] == "door" and not strong and _is_leaf_of(d["box"], t["box"]):
                w = 0.0     # only one leaf of a known door seen → keep the whole-door box
            t["box"] = [w * a + (1 - w) * b for a, b in zip(d["box"], t["box"])]
            if strong or t["source"] != "la":
                t["label"] = d["label"]          # LocateAnything labels win
            t["hits"] += 2 if strong else 1
            t["last_seen"] = now
            t["source"] = source if strong else t["source"]

    def consolidate(self) -> dict:
        """Merge duplicate tracks into the older one. Returns {removed_id: kept_id}."""
        merged = {}
        for kind in ("door", "obstacle", "sign"):
            ts = sorted((t for t in self.tracks if t["kind"] == kind), key=lambda t: t["id"])
            gone = set()
            for i, a in enumerate(ts):
                if a["id"] in gone:
                    continue
                for b in ts[i + 1:]:
                    if b["id"] in gone:
                        continue
                    # one of them only predicted (not detected lately): its box may have drifted
                    # or grown, so it merges only on real overlap and the fresh box wins
                    coasting = a["last_seen"] != b["last_seen"]
                    if coasting:
                        dup = box_iou(a["box"], b["box"]) >= 0.3
                    elif kind == "door":
                        dup = (max(containment(a["box"], b["box"]), containment(b["box"], a["box"])) >= 0.6
                               or len(merge_double_doors([
                                   {"box": a["box"], "conf": 1, "signed": False, "weak": False},
                                   {"box": b["box"], "conf": 1, "signed": False, "weak": False}])) == 1)
                    else:
                        dup = box_iou(a["box"], b["box"]) >= 0.5
                    if not dup:
                        continue
                    if coasting:
                        if b["last_seen"] > a["last_seen"]:
                            a["box"] = list(b["box"])
                    elif kind == "door":
                        if b["source"] == "la" and a["source"] != "la":
                            a["box"] = list(b["box"])          # LocateAnything box wins
                        elif a["source"] == b["source"]:       # two leaves → one door
                            a["box"] = [min(a["box"][0], b["box"][0]), min(a["box"][1], b["box"][1]),
                                        max(a["box"][2], b["box"][2]), max(a["box"][3], b["box"][3])]
                    a["hits"] = max(a["hits"], b["hits"])
                    a["last_seen"] = max(a["last_seen"], b["last_seen"])
                    a["signed"] = a["signed"] or b["signed"]
                    if a["exit_ok"] is None:
                        a["exit_ok"] = b["exit_ok"]
                    if a["depth"] is None:
                        a["depth"] = b["depth"]
                    if b["source"] == "la" and a["source"] != "la":
                        a["label"], a["source"] = b["label"], "la"
                    gone.add(b["id"])
                    merged[b["id"]] = a["id"]
            self.tracks = [t for t in self.tracks if t["id"] not in gone]
        return merged

    def prune(self, now: float, W: int, H: int) -> None:
        def _blown_up(t, now):
            """Only predicted lately and now implausibly large (camera walked forward)."""
            b = t["box"]
            return t["last_seen"] < now and ((b[3] - b[1]) > 1.3 * H or _area(b) > 0.8 * W * H)

        def alive(t):
            ttl = self.ttl_la if t["source"] == "la" else self.ttl_fast
            if t["kind"] == "door" and (t["signed"] or t["exit_ok"]):
                ttl *= 2                          # confirmed exits are held longer
            b = t["box"]
            on_screen = b[2] > 0 and b[0] < W and b[3] > 0 and b[1] < H
            # a box only predicted for a while can drift / blow up (camera walking forward)
            return on_screen and not _blown_up(t, now) and now - t["last_seen"] <= ttl
        keep, lost = [], []
        for t in self.tracks:
            if alive(t):
                keep.append(t)
            elif not _blown_up(t, now):       # a blown-up box is not worth reviving
                lost.append(t)
        self.tracks = keep
        self.dead = [g for g in self.dead + lost if now - g["last_seen"] <= self.revive_s]

    def visible(self, kind: str, min_hits: int = 2) -> list:
        return [t for t in self.tracks if t["kind"] == kind and t["hits"] >= min_hits]


# ── Exit memory ───────────────────────────────────────────────────────────────

class ExitRecord:
    def __init__(self, tid: int, now: float, vt: float):
        self.id = tid
        self.status, self.since, self.since_vt = "unknown", now, vt
        self.candidate, self.cand_since = "unknown", now
        self.cands = collections.deque()   # (time, candidate) over the last --hold s → status vote
        self.blockers: list = []
        self.blocker_ids: list = []
        self.zone_seen: dict = {}         # obstacle id → last time it was in the zone (display)
        self.qwen_verdict, self.qwen_t = None, 0.0
        self.verdict_req_t, self.verdict_req_set = -1e9, None
        self.first_vt = self.last_vt = vt
        self.visible = True
        self.snapshot = None
        self.history: list = []


def _vote(cands: list):
    """Most common candidate (ties → the more severe) and its share of the window."""
    counts = collections.Counter(cands)
    winner = max(counts, key=lambda c: (counts[c], STATUS_RANK.get(c, -1)))
    return winner, counts[winner] / len(cands)


def _more_severe(a: str, b: str) -> str:
    return a if STATUS_RANK.get(a, -1) >= STATUS_RANK.get(b, -1) else b


# ── Monitor ───────────────────────────────────────────────────────────────────

class ExitMonitor:
    """
    The full pipeline. Used by the CLI (main()) and by the FastAPI backend:
      on_frame(original, annotated, combined, state)   called once per processed frame
      on_event(event_dict)                             called on each exit status change
      request_stop()                                   ends run() cleanly (writes the report)
    """

    def __init__(self, args, on_frame=None, on_event=None, fast_detector=None):
        """fast_detector: an already-loaded FastDetector to reuse (the backend keeps one)."""
        self.args = args
        self.on_frame, self.on_event = on_frame, on_event
        self._halt = threading.Event()
        self.report_path = None
        if args.locate_url and is_openrouter(args.qwen_url):
            print("  Qwen via OpenRouter → LocateAnything off (YOLOE + Qwen only)")
            args.locate_url = None
        self.fast = None if args.no_yoloe else (
            fast_detector or FastDetector(args.yoloe_model, tracker=args.tracker))
        self.locate = LocateEngine(args.locate_url, max_image_side=args.proc_width,
                                   mode=args.mode) if args.locate_url else None
        self.qwen = None
        self.assist = None
        self.base_cats = DOOR_LABELS + SIGN_LABELS + OBSTACLE_LABELS
        if args.qwen_url:
            endpoint = qwen_endpoint(args.qwen_url, args.qwen_model)   # self-hosted or OpenRouter
            self.qwen = QwenEngine(**endpoint, max_image_side=640, max_tokens=8)
            if self.locate:
                self.assist = QwenAssist(endpoint, name_every=args.name_every,
                                         verify=False, base_categories=self.base_cats)
        if not (self.fast or self.locate):
            raise SystemExit("Need YOLOE or --locate-url (or both).")

        self.tracker = Tracker(ttl_la=max(4.0, 3 * args.la_interval))
        self.merged: dict = {}            # track id merged away → id it was merged into
        self.exits: dict = {}
        self.events: list = []
        self.motion = None
        self.prev_C = None
        self.la_in = None                 # latest frame for the LocateAnything worker
        self.la_out = queue.Queue()
        self.qwen_pool = ThreadPoolExecutor(max_workers=2)       # depth + verdict jobs
        self.confirm_pool = ThreadPoolExecutor(max_workers=1)    # "is it an exit?" never queues
        self.qwen_out = queue.Queue()
        self.pending = set()              # (job kind, track id) in flight
        self._futures = []                # Qwen jobs in flight (offline mode waits for them)
        self._la_last_vt = None           # offline: video time of the last LocateAnything call
        self.full, self.full_scale = None, 1.0
        self._last_size = None
        self._flog = {"la": [], "q_out": [], "q_in": []}   # per-frame log notes
        self._flog_lock = threading.Lock()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.t0 = time.time()

    # ── motion ───────────────────────────────────────────────────────────────
    def _motion_step(self, frame):
        if self.motion is None:
            self.motion = MotionCompensator(frame.shape[1])
        C = self.motion.update(frame)
        step = np.eye(3) if self.prev_C is None else C @ np.linalg.inv(self.prev_C)
        self.prev_C = C
        s = self.motion.scale
        S, Si = np.diag([s, s, 1.0]), np.diag([1 / s, 1 / s, 1.0])
        return (Si @ step @ S)[:2].astype(np.float32), C

    # ── LocateAnything worker ────────────────────────────────────────────────
    def _la_worker(self):
        last = 0.0
        while not self.stop.is_set():
            wait = self.args.la_interval - (time.time() - last)
            if wait > 0:
                time.sleep(min(wait, 0.05))
                continue
            with self.lock:
                job, self.la_in = self.la_in, None
            if job is None:
                time.sleep(0.02)
                continue
            last = time.time()
            out = self._la_detect(*job)
            if out:
                self.la_out.put(out)

    def _la_detect(self, frame, C, W, H):
        """One LocateAnything call → (detections, C at that frame, latency) or None."""
        t = time.time()
        self._note("la", "sent frame")
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        cats = self.base_cats
        if self.assist:
            self.assist.note_image(img)
            cats = cats + self.assist.extra_categories(self.base_cats)
        try:
            res = self.locate.detect_raw(img, cats)
        except Exception as e:
            print(f"  [locate] error: {e}")
            return None
        dets = []
        for b in res["boxes"]:
            bb = norm_box_to_pixels(b, W, H)
            dets.append({"kind": kind_of(b["label"], DOOR_LABELS, SIGN_LABELS),
                         "label": b["label"], "box": [bb["x1"], bb["y1"], bb["x2"], bb["y2"]]})
        self._note("la", f"result back: {len(dets)} boxes ({time.time() - t:.1f}s)")
        return dets, C, res.get("latency_s", 0)

    # ── Qwen jobs ────────────────────────────────────────────────────────────
    def _submit(self, kind: str, tid: int, frame, box, extra=None):
        key = (kind, tid)
        if not self.qwen or key in self.pending:
            return
        self.pending.add(key)
        self._note("q_out", f"{kind}#{tid}")
        # crop from the full-resolution frame when available (sharper for Qwen)
        if self.full is not None:
            k = self.full_scale
            frame, box = self.full.copy(), [v * k for v in box]
        else:
            frame = frame.copy()

        def job():
            try:
                if kind == "confirm":
                    ans = self.qwen.query_image(_crop(frame, box), "Answer yes or no.",
                                                user_text=CONFIRM_PROMPT).lower()
                    val = True if "yes" in ans else False if "no" in ans else None
                elif kind == "depth":
                    bb = _bbdict(box, frame.shape[1], frame.shape[0])
                    ans = self.qwen.query_image(base_crop(frame, bb), "Answer with exactly one word.",
                                                user_text=DEPTH_PROMPT.format(
                                                    label=extra or "object")).lower()
                    val = "furniture" if ("raised" in ans or "furniture" in ans) else "floor"
                else:   # verdict
                    ans = self.qwen.query_image(_crop(frame, box, 0.6, 0.6, 0.1, 0.5),
                                                "Answer with one letter.", user_text=VERDICT_PROMPT)
                    m = re.search(r"\b([CPB])\b", ans.upper())
                    val = STATUS_CODES.get(m.group(1)) if m else None
                self.qwen_out.put((kind, tid, val, extra))
            except Exception as e:
                self.qwen_out.put((kind, tid, None, f"error: {e}"))
        self._futures = [f for f in self._futures if not f.done()]
        self._futures.append((self.confirm_pool if kind == "confirm" else self.qwen_pool).submit(job))

    def _apply_qwen(self, now: float, vt: float):
        while True:
            try:
                kind, tid, val, extra = self.qwen_out.get_nowait()
            except queue.Empty:
                return
            self.pending.discard((kind, tid))
            tid = self.merged.get(tid, tid)
            track = self.tracker.find(tid)
            shown = {True: "yes", False: "no"}.get(val, val) if val is not None else (extra or "none")
            self._note("q_in", f"{kind}#{tid}={shown}")
            if val is None:
                continue
            if kind == "confirm" and track:
                track["exit_ok"] = val
                print(f"  [{vt:6.1f}s] qwen: #{tid} is {'an exit' if val else 'NOT an exit (ignored)'}")
            elif kind == "depth" and track:
                track["depth"] = val
                if val == "furniture":
                    print(f"  [{vt:6.1f}s] qwen: #{tid} {track['label']} is on furniture → ignored")
            elif kind == "verdict" and tid in self.exits:
                rec = self.exits[tid]
                rec.qwen_verdict, rec.qwen_t = val, now

    # ── per-frame log ────────────────────────────────────────────────────────
    def _note(self, key: str, text: str) -> None:
        with self._flog_lock:
            self._flog[key].append(text)

    def _print_frame(self, idx, vt, ms_total, fast_info, la_merged):
        with self._flog_lock:
            la, q_out, q_in = self._flog["la"], self._flog["q_out"], self._flog["q_in"]
            self._flog = {"la": [], "q_out": [], "q_in": []}
        yolo = (f"YOLOE {fast_info[0]:2d} det {fast_info[1]:3.0f}ms" if fast_info
                else "YOLOE off")
        la_txt = "; ".join(la + ([f"merged {la_merged} boxes"] if la_merged else [])) or "-"
        q_txt = " ".join([f"-> {x}" for x in q_out] + [f"<- {x}" for x in q_in]) or "-"
        n_d = len(self.tracker.visible("door"))
        n_o = len(self.tracker.visible("obstacle"))
        print(f"  f{idx:5d} t={vt:6.2f}s {ms_total:4.0f}ms | {yolo} | LA: {la_txt} | "
              f"Qwen: {q_txt} | tracks: {n_d} door, {n_o} obstacle", flush=True)

    # ── per-frame exit logic ─────────────────────────────────────────────────
    def _update_exits(self, frame, W, H, now, vt):
        doors = [t for t in self.tracker.visible("door") if t["exit_ok"] is not False]
        signs = self.tracker.visible("sign")
        for d in doors:
            x1, y1, x2, y2 = d["box"]
            dw, dh = x2 - x1, y2 - y1
            if any(x1 - 0.2 * dw <= (s["box"][0] + s["box"][2]) / 2 <= x2 + 0.2 * dw
                   and y1 - 0.5 * dh <= s["box"][3] <= y1 + 0.1 * dh for s in signs):
                d["signed"] = True
        obstacles = [t for t in self.tracker.visible("obstacle") if t["depth"] != "furniture"]

        ex_dets = [{"type": "exit", "label": f"#{d['id']}", "bounding_box": _bbdict(d["box"], W, H)}
                   for d in doors]
        ob_dets = [{"type": "obstruction", "label": f"#{o['id']}",
                    "bounding_box": _bbdict(o["box"], W, H)} for o in obstacles]
        blocking = assess(ex_dets, ob_dets, W, H)

        by_id = {t["id"]: t for t in self.tracker.tracks}
        visible_ids = set()
        for d, ed in zip(doors, ex_dets):
            tid = d["id"]
            visible_ids.add(tid)
            rec = self.exits.get(tid)
            if rec is None:
                rec = self.exits[tid] = ExitRecord(tid, now, vt)
            rec.visible, rec.last_vt = True, vt
            zone = sorted(int(b["label"][1:]) for b in blocking if b["blocks_exit"] == ed["label"])
            rec.blocker_ids = zone
            for oid in zone:
                rec.zone_seen[oid] = now
            rec.zone_seen = {o: t for o, t in rec.zone_seen.items()
                             if now - t <= BLOCKER_HOLD and o in by_id}
            labels = [by_id[i]["label"] for i in zone if i in by_id]
            rec.blockers = [f"{l} x{labels.count(l)}" if labels.count(l) > 1 else l
                            for l in dict.fromkeys(labels)]

            # Qwen events
            if d["exit_ok"] is None and d["hits"] >= 3:
                self._submit("confirm", tid, frame, d["box"])
            for oid in zone:
                o = by_id.get(oid)
                if o and o["depth"] is None:
                    self._submit("depth", oid, frame, o["box"], extra=o["label"])
            zone_key = tuple(zone)
            if d["exit_ok"] is not False and now - rec.verdict_req_t >= VERDICT_MIN_GAP and (
                    zone_key != rec.verdict_req_set
                    or now - rec.verdict_req_t > self.args.recheck):
                rec.verdict_req_set, rec.verdict_req_t = zone_key, now
                self._submit("verdict", tid, frame, d["box"])

            # candidate = more severe of the per-frame rule and a fresh Qwen verdict
            cand = ed.get("status", "clear")
            if rec.qwen_verdict and now - rec.qwen_t <= self.args.verdict_ttl:
                cand = _more_severe(cand, rec.qwen_verdict)
            if cand != rec.candidate:
                rec.candidate, rec.cand_since = cand, now
            rec.cands.append((now, cand))
            while rec.cands and now - rec.cands[0][0] > self.args.hold:
                rec.cands.popleft()
            # with Qwen, a door gets a status only once confirmed as an exit
            # (or after confirm_timeout s without an answer) — no alerts on TVs/cupboards
            confirmed = (not self.qwen or d["exit_ok"] is True
                         or now - d["first_seen"] >= self.args.confirm_timeout)
            # the per-frame candidate flickers (boxes jitter at the zone edge), so the status
            # is a vote over the last --hold s: the first one is the most common candidate,
            # a change needs 60% of the window
            if confirmed and now - d["first_seen"] >= self.args.hold:
                winner, share = _vote([c for _, c in rec.cands])
                if winner != rec.status and (rec.status == "unknown" or share >= 0.6):
                    self._event(rec, winner, frame, now, vt, d)

        for tid, rec in self.exits.items():
            if tid not in visible_ids and rec.visible:
                rec.visible = False

    def _event(self, rec, status, frame, now, vt, track):
        old = rec.status
        rec.status, rec.since, rec.since_vt = status, now, vt
        what = f" ({', '.join(rec.blockers)})" if rec.blockers and status != "clear" else ""
        name = f"EXIT #{rec.id}" if track["signed"] else f"door #{rec.id}"
        msg = f"{name}: {STATUS_TEXT[old]} -> {STATUS_TEXT[status]}{what}"
        print(f"  [{vt:6.1f}s] {msg}")
        rec.history.append({"video_time_s": round(vt, 2), "status": status,
                            "blockers": list(rec.blockers)})
        self.events.append((vt, msg, status))
        if self.on_event:
            try:
                self.on_event({"video_time_s": round(vt, 2), "exit_id": rec.id,
                               "exit_sign": bool(track["signed"]), "from": old, "to": status,
                               "status_text": STATUS_TEXT[status], "blockers": list(rec.blockers),
                               "text": msg})
            except Exception as e:                      # a sink must never stop detection
                print(f"  on_event error: {e}")
        if self.args.snapshots and status != "unknown":
            path = self.snap_dir / f"exit{rec.id}_{vt:06.1f}s_{status}.jpg"
            x1, y1, x2, y2 = (int(v) for v in track["box"])
            snap = frame.copy()
            cv2.rectangle(snap, (max(0, x1), max(0, y1)), (x2, y2), STATUS_BGR[status], 4)
            cv2.imwrite(str(path), snap)
            rec.snapshot = str(path)

    # ── drawing ──────────────────────────────────────────────────────────────
    def _draw(self, frame, now) -> np.ndarray:
        out = frame.copy()
        s, th = text_scale(out)
        by_id = {t["id"]: t for t in self.tracker.tracks}
        for tid, rec in self.exits.items():
            t = by_id.get(tid)
            if not rec.visible or t is None:
                continue
            if t["exit_ok"] is False or _shown_w(t["box"], out.shape[1]) < MIN_SHOWN_W:
                continue
            x1, y1, x2, y2 = (int(v) for v in t["box"])
            col = STATUS_BGR[rec.status]
            cv2.rectangle(out, (x1, y1), (x2, y2), col, th + 2)
            tag = ("EXIT" if t["signed"] else "door") + f" #{tid}"
            if t["exit_ok"] is None and self.qwen:
                tag += " ?"
            text = f"{tag} {STATUS_TEXT[rec.status]} {now - rec.since:.0f}s"
            self._label(out, text, x1, y1, col, s, th)
        for t in self.tracker.visible("obstacle"):
            if t["depth"] == "furniture":
                continue
            in_zone = any(t["id"] in r.zone_seen for r in self.exits.values() if r.visible)
            if t["hits"] < SHOW_MIN_HITS:
                continue
            if not in_zone:
                continue
            x1, y1, x2, y2 = (int(v) for v in t["box"])
            cv2.rectangle(out, (x1, y1), (x2, y2), OBSTACLE_BGR, th + 1)
            mark = " (floor ok)" if t["depth"] == "floor" else ""
            self._label(out, f"{t['label']} #{t['id']}{mark}",
                        x1, y1, OBSTACLE_BGR, s * 0.85, th)
        return out

    def _state(self, now: float, vt: float, fps: float) -> dict:
        """Snapshot for the web app (GET /state)."""
        by_id = {t["id"]: t for t in self.tracker.tracks}
        exits = []
        for r in sorted(self.exits.values(), key=lambda r: (not r.visible, r.id)):
            if r.status == "unknown" and not r.visible:
                continue
            t = by_id.get(r.id)
            exits.append({"id": r.id, "exit_sign": bool(t and t["signed"]),
                          "status": r.status, "status_text": STATUS_TEXT[r.status],
                          "since_s": round(now - r.since, 1), "blockers": r.blockers,
                          "visible": r.visible})
        return {"video_time_s": round(vt, 2), "fps": round(fps, 1) if fps else None, "exits": exits,
                "events": [{"video_time_s": round(t, 2), "text": m, "status": st}
                           for t, m, st in self.events[-20:]]}

    @staticmethod
    def _label(img, text, x, y, col, s, th):
        (tw, tht), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, s, th)
        y = max(tht + 8, y)
        x = max(0, min(x, img.shape[1] - tw - 6))
        cv2.rectangle(img, (x, y - tht - 8), (x + tw + 6, y), col, -1)
        txt_col = (0, 0, 0) if col == STATUS_BGR["partially_obstructed"] else (255, 255, 255)
        cv2.putText(img, text, (x + 3, y - 5), cv2.FONT_HERSHEY_SIMPLEX, s, txt_col, th, cv2.LINE_AA)

    def _panel(self, width: int, now: float, vt: float, fps: float) -> np.ndarray:
        h = 200
        p = np.full((h, width, 3), 30, np.uint8)
        f = cv2.FONT_HERSHEY_SIMPLEX
        mode = " + ".join(n for n, on in (("YOLOE", self.fast), ("LocateAnything", self.locate),
                                           ("Qwen", self.qwen)) if on)
        speed = "offline" if fps is None else f"{fps:4.1f} fps"
        cv2.putText(p, f"EXITS   t={vt:5.1f}s   {speed}   [{mode}]", (12, 26), f, 0.7,
                    (230, 230, 230), 2, cv2.LINE_AA)
        y = 56
        recs = sorted(self.exits.values(), key=lambda r: (not r.visible, r.id))
        recs = [r for r in recs if r.status != "unknown" or r.visible][:5]
        for r in recs:
            col = STATUS_BGR[r.status] if r.visible else (120, 120, 120)
            blk = ", ".join(r.blockers) if r.blockers and r.status != "clear" else ""
            txt = (f"#{r.id:<3} {STATUS_TEXT[r.status]:<9} {now - r.since:5.0f}s  "
                   f"{'(out of view) ' if not r.visible else ''}{blk}")
            cv2.circle(p, (20, y - 6), 7, col, -1)
            cv2.putText(p, txt[:90], (36, y), f, 0.6, col, 2, cv2.LINE_AA)
            y += 26
        x0 = width // 2 + 20
        cv2.putText(p, "Events", (x0, 56), f, 0.6, (230, 230, 230), 2, cv2.LINE_AA)
        for i, (t, msg, st) in enumerate(self.events[-5:][::-1]):
            cv2.putText(p, f"{t:6.1f}s {msg}"[:70], (x0, 82 + 24 * i), f, 0.5,
                        STATUS_BGR.get(st, (200, 200, 200)), 1, cv2.LINE_AA)
        return p

    # ── main loop ────────────────────────────────────────────────────────────
    def run(self):
        a = self.args
        if a.offline and not Path(a.source).is_file():
            raise SystemExit("  --offline needs a video file (not a camera or stream)")
        src = LiveSource(a.source, loop=a.loop and not a.offline, realtime=not a.offline)
        scale = min(1.0, a.proc_width / src.width)
        W, H = int(src.width * scale) // 2 * 2, int(src.height * scale) // 2 * 2
        stem = Path(a.source).stem if Path(a.source).is_file() else "live"
        out_dir = Path(a.out_dir) / f"exit_monitor_{stem}"
        out_dir.mkdir(parents=True, exist_ok=True)
        self.snap_dir = out_dir
        print(f"  Source: {a.source} | {src.width}x{src.height} @ {src.fps:.1f} fps "
              f"(processing {W}x{H}) | {'OFFLINE (every frame, waits for the models)' if a.offline else 'real time'}"
              f" | output → {out_dir}\n")

        self.start_workers()
        # --stream: simple browser view (+ /stream/original, /stream/annotated, /state).
        # The web app (frontend/) talks to the FastAPI backend instead (backend/).
        streamer = MJPEGStreamer(a.stream).start() if a.stream else None
        half = min(W, 800)
        out_w = 2 * half
        out_h = int(H * half / W) // 2 * 2 + 200
        save = a.save or (str(out_dir / f"{stem}_annotated.mp4") if a.offline else None)
        saver = VideoSaver(save, src.fps) if save else None
        progress = Progress(src.frame_count) if a.offline else None
        win = "Exit monitor (q: quit, space: pause)"
        times = []
        try:
            while not self._halt.is_set():
                idx, frame, skipped = src.read()
                if frame is None:
                    break
                t_frame = time.time()
                vt = src.video_time()
                frame, fast_info, la_merged, now = self.step(frame, vt)

                annotated = self._draw(frame, now)
                times.append(time.time() - t_frame)
                if a.log_frames:
                    self._print_frame(idx, vt, 1000 * times[-1], fast_info, la_merged)
                fps = None if a.offline else 1.0 / max(1e-3, np.mean(times[-30:]))
                if progress:
                    progress.update(idx, vt)
                top = cv2.hconcat([frame, annotated])
                top = cv2.resize(top, (out_w, out_h - 200), interpolation=cv2.INTER_AREA)
                combined = cv2.vconcat([top, self._panel(out_w, now, vt, fps)])

                if self.on_frame:
                    try:
                        self.on_frame(frame, annotated, combined, self._state(now, vt, fps))
                    except Exception as e:
                        print(f"  on_frame error: {e}")
                if streamer:
                    streamer.publish(combined)                       # /stream (all-in-one)
                    streamer.publish(frame, "original")              # /stream/original
                    streamer.publish(annotated, "annotated")         # /stream/annotated
                    streamer.set_state(self._state(now, vt, fps))    # /state (JSON)
                if saver:
                    saver.write(combined if a.save_view == "combined" else annotated, skipped + 1)
                if not a.no_display:
                    cv2.imshow(win, combined)
                    key = cv2.waitKey(1) & 0xFF
                    if key in (ord("q"), 27):
                        break
                    if key == ord(" "):
                        tp = time.time()
                        cv2.waitKey(0)
                        src.pause_clock(time.time() - tp)
        finally:
            self.stop.set()
            if self.assist:
                self.assist.stop()
            src.release()
            if streamer:
                streamer.stop()
            if not a.no_display:
                cv2.destroyAllWindows()
            self.qwen_pool.shutdown(wait=False, cancel_futures=True)
            self.confirm_pool.shutdown(wait=False, cancel_futures=True)
            self._report(out_dir, src, times)
            if saver:
                path = saver.close()
                if path:
                    print(f"  Video : {path}  ({saver.frames} frames @ {src.fps:.1f} fps)")

    # ── one frame (used by run() and by the backend's live mode) ─────────────
    def start_workers(self) -> None:
        """Start the LocateAnything worker (once; offline mode calls it in step())."""
        if self.locate and not self.args.offline and not getattr(self, "_la_started", False):
            threading.Thread(target=self._la_worker, daemon=True).start()
            self._la_started = True

    def step(self, frame, vt: float):
        """
        Process one full-resolution BGR frame shown at video time `vt`.
        Returns (processed_frame, fast_info, la_merged, now); the results are in
        self.tracker / self.exits (see live_result()).
        """
        H0, W0 = frame.shape[:2]
        scale = min(1.0, self.args.proc_width / W0)
        W, H = int(W0 * scale) // 2 * 2, int(H0 * scale) // 2 * 2
        if self._last_size not in (None, (W, H)):
            self.reset()                     # a different source → start over
        self._last_size = (W, H)
        self.full, self.full_scale = frame, 1.0 / scale
        if (W, H) != (W0, H0):
            frame = cv2.resize(frame, (W, H), interpolation=cv2.INTER_AREA)
        now = vt if self.args.offline else time.time()     # offline: the video's clock

        M, C = self._motion_step(frame)
        self.tracker.predict(M)

        fast_info, la_merged = None, 0
        if self.fast:
            t_y = time.time()
            dets = self.fast(frame)
            fast_info = (len(dets), 1000 * (time.time() - t_y))
            doors = merge_double_doors([{"box": d["box"], "conf": 1.0, "signed": False,
                                         "weak": False, "ext_id": d.get("ext_id")}
                                        for d in dets if d["kind"] == "door"])
            dets = [d for d in dets if d["kind"] != "door"] + \
                   [{"kind": "door", "label": "door", "box": d["box"], "ext_id": d.get("ext_id")}
                    for d in doors]
            self.tracker.update(dets, now, "fast")

        if self.locate and self.args.offline:
            if self._la_last_vt is None or vt - self._la_last_vt >= self.args.la_interval:
                self._la_last_vt = vt         # wait for it: merged in this very frame
                out = self._la_detect(frame.copy(), C.copy(), W, H)
                if out:
                    self.la_out.put(out)
        if self.locate:
            if not self.args.offline:
                with self.lock:
                    self.la_in = (frame.copy(), C.copy(), W, H)
            while not self.la_out.empty():
                la_dets, C_ref, _ = self.la_out.get()
                la_merged += len(la_dets)
                M_la = (C @ np.linalg.inv(C_ref))
                s = self.motion.scale
                Mpx = (np.diag([1 / s, 1 / s, 1]) @ M_la @ np.diag([s, s, 1]))[:2].astype(np.float32)
                for d in la_dets:
                    d["box"] = _warp(d["box"], Mpx)
                doors = merge_double_doors([{"box": d["box"], "conf": 1.0, "signed": False,
                                             "weak": False} for d in la_dets if d["kind"] == "door"])
                la_dets = [d for d in la_dets if d["kind"] != "door"] + \
                          [{"kind": "door", "label": "door", "box": d["box"]} for d in doors]
                self.tracker.update(la_dets, now, "la")

        for old, new in self.tracker.consolidate().items():
            self.merged[old] = new
            if old in self.exits:            # keep the older exit record
                rec = self.exits.pop(old)
                if new not in self.exits:
                    rec.id = new
                    self.exits[new] = rec
        self.tracker.prune(now, W, H)
        self._apply_qwen(now, vt)
        self._update_exits(frame, W, H, now, vt)
        if self.args.offline:             # wait for this frame's Qwen checks and apply them now,
            for f in self._futures:       # before drawing (else a desk item or a non-exit door
                f.result()                # shows for exactly one frame)
            self._futures = []
            self._apply_qwen(now, vt)
        return frame, fast_info, la_merged, now

    def reset(self) -> None:
        """Forget tracks and exits (new video / camera). Events and the report are kept."""
        self.tracker = Tracker(ttl_la=max(4.0, 3 * self.args.la_interval))
        self.exits, self.merged = {}, {}
        self.motion, self.prev_C, self._last_size = None, None, None
        self._la_last_vt = None
        if self.fast and hasattr(self.fast, "reset"):
            self.fast.reset()
        with self.lock:
            self.la_in = None
        while not self.la_out.empty():
            self.la_out.get_nowait()

    def live_result(self, W: int, H: int) -> dict:
        """Visible exits + the obstructions at them, boxes normalised to 0..1 (for the web app)."""
        now = time.time()
        by_id = {t["id"]: t for t in self.tracker.tracks}

        def norm(b):
            return [round(max(0.0, b[0]) / W, 4), round(max(0.0, b[1]) / H, 4),
                    round(min(W, b[2]) / W, 4), round(min(H, b[3]) / H, 4)]

        exits, obstacles = [], {}
        for tid, rec in self.exits.items():
            t = by_id.get(tid)
            if not rec.visible or t is None or t["exit_ok"] is False or _shown_w(t["box"], W) < MIN_SHOWN_W:
                continue
            exits.append({"id": tid, "exit_sign": bool(t["signed"]), "confirmed": t["exit_ok"],
                          "status": rec.status, "status_text": STATUS_TEXT[rec.status],
                          "since_s": round(now - rec.since, 1), "blockers": rec.blockers,
                          "box": norm(t["box"])})
            for oid in rec.zone_seen:           # held a moment → no blinking in the web app
                o = by_id.get(oid)
                if o and o["depth"] != "furniture" and o["hits"] >= SHOW_MIN_HITS:
                    obstacles[oid] = {"id": oid, "label": o["label"], "depth": o["depth"],
                                      "exit_id": tid, "box": norm(o["box"])}
        return {"exits": exits, "obstacles": list(obstacles.values())}

    def shutdown(self, out_dir: Path, frames: int, dropped: int, times: list) -> None:
        """Stop background work and write the report (for callers that drive step())."""
        from types import SimpleNamespace
        self.stop.set()
        if self.assist:
            self.assist.stop()
        self.qwen_pool.shutdown(wait=False, cancel_futures=True)
        self.confirm_pool.shutdown(wait=False, cancel_futures=True)
        self._report(out_dir, SimpleNamespace(idx=frames - 1, dropped=dropped), times)

    def request_stop(self) -> None:
        """Ask run() to finish after the current frame (thread-safe)."""
        self._halt.set()

    def _report(self, out_dir: Path, src, times):
        exits = []
        for r in sorted(self.exits.values(), key=lambda r: r.id):
            if r.status == "unknown" and not r.history:
                continue
            exits.append({"id": r.id, "final_status": r.status, "blockers": r.blockers,
                          "first_seen_s": round(r.first_vt, 2), "last_seen_s": round(r.last_vt, 2),
                          "history": r.history, "snapshot": r.snapshot})
        rep = {"source": self.args.source, "frames": src.idx + 1, "dropped": src.dropped,
               "fast_loop_ms": round(1000 * float(np.mean(times)), 1) if times else None,
               "exits": exits,
               "events": [{"video_time_s": round(t, 2), "event": m} for t, m, _ in self.events]}
        path = out_dir / "report.json"
        path.write_text(json.dumps(rep, indent=2))
        self.report_path = path
        print(f"\n  {src.idx + 1} frames ({src.dropped} dropped) | fast loop "
              f"{rep['fast_loop_ms']} ms/frame | {len(exits)} exits | {len(self.events)} events")
        print(f"  Report: {path}")


def build_parser() -> argparse.ArgumentParser:
    """CLI options; the backend uses the same parser for its defaults."""
    ap = argparse.ArgumentParser(description="Real-time exit / obstruction monitor")
    ap.add_argument("source", help="Video file, webcam index or stream URL")
    ap.add_argument("--locate-url", default=os.environ.get("LOCATE_URL"),
                    help="LocateAnything server (default: $LOCATE_URL)")
    ap.add_argument("--qwen-url", default=os.environ.get("QWEN_URL"),
                    help="Qwen server: an address (http://host:8010/v1), 'openrouter', or a name "
                         "from .env such as 'gpu' / 'mac' (default: $QWEN_URL; see qwen_server.py)")
    ap.add_argument("--qwen-model", default=None,
                    help="Model name (default: $QWEN_MODEL, the served model, or "
                         "qwen/qwen3-vl-8b-instruct on OpenRouter)")
    ap.add_argument("--no-yoloe", action="store_true",
                    help="LocateAnything + Qwen only (no per-frame detector)")
    ap.add_argument("--yoloe-model", default="yoloe-11s-seg.pt")
    ap.add_argument("--tracker", choices=["ours", "bytetrack", "botsort"], default="ours",
                    help="Frame-to-frame matching of YOLOE detections: our IoU tracker (default), "
                         "or Ultralytics ByteTrack / BoT-SORT (camera-motion compensation); "
                         "exit logic, LocateAnything merging and IDs stay ours either way")
    ap.add_argument("--mode", default="hybrid", choices=["fast", "hybrid", "slow"],
                    help="LocateAnything decoding mode")
    ap.add_argument("--la-interval", type=float, default=1.0,
                    help="Seconds between LocateAnything calls (default: 1.0)")
    ap.add_argument("--name-every", type=float, default=3.0,
                    help="Seconds between Qwen object-naming calls (default: 3)")
    ap.add_argument("--hold", type=float, default=1.5,
                    help="Status = vote over this many seconds; a change needs 60%% of it "
                         "(default: 1.5)")
    ap.add_argument("--recheck", type=float, default=8.0,
                    help="Re-ask Qwen for a verdict at least this often per exit (default: 8 s)")
    ap.add_argument("--verdict-ttl", type=float, default=6.0,
                    help="How long a Qwen verdict counts (default: 6 s)")
    ap.add_argument("--confirm-timeout", type=float, default=6.0,
                    help="Show a door's status without Qwen's exit confirmation after this "
                         "many seconds (default: 6)")
    ap.add_argument("--proc-width", type=int, default=960)
    ap.add_argument("--no-snapshots", dest="snapshots", action="store_false")
    ap.add_argument("--quiet", dest="log_frames", action="store_false",
                    help="No per-frame log line (events only)")
    ap.add_argument("--stream", type=int, nargs="?", const=8090, default=None, metavar="PORT")
    ap.add_argument("--save", default=None, help="Record the output view to this .mp4")
    ap.add_argument("--offline", action="store_true",
                    help="Video files: process every frame, run LocateAnything on the video's "
                         "clock and wait for each Qwen check (nothing missed with slow servers "
                         "or OpenRouter); saves the result video (default: results/"
                         "exit_monitor_<video>/<video>_annotated.mp4)")
    ap.add_argument("--save-view", choices=["combined", "annotated"], default="combined",
                    help="What the saved video shows: original | annotated + status panel "
                         "(combined, default) or the annotated frame only")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--no-display", action="store_true")
    ap.add_argument("--out-dir", default="results")
    return ap


def main():
    args = build_parser().parse_args()
    print(f"  LocateAnything: {args.locate_url or 'OFF'} | Qwen: {args.qwen_url or 'OFF'} | "
          f"YOLOE: {'OFF' if args.no_yoloe else args.yoloe_model}")
    ExitMonitor(args).run()


if __name__ == "__main__":
    main()
