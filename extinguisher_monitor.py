"""
Fire-extinguisher monitor — find extinguishers and read their expiry / service dates
=================================================================================

  FAST LOOP (every frame)   YOLOE "fire extinguisher" → tracker (IDs, camera motion)
                            → keep the best full-resolution crop per extinguisher
                              (biggest × sharpest)
  QWEN (per extinguisher)   reads the dates on its labels / tag from that crop, in the
                            background (the video never waits); re-reads when a clearly
                            better view comes along, until two reads agree
  STATUS                    VALID / DUE SOON / EXPIRED / CHECK DATE / UNREADABLE,
                            from core/expiry.py → panel, events, snapshots, report.json

Too-small extinguishers are not sent to Qwen (the hint says "move closer").

Usage:
  python extinguisher_monitor.py IMG_0454.MOV                 # QWEN_URL from .env
  python extinguisher_monitor.py IMG_0454.MOV --qwen-url http://<server-ip>:8010/v1
  python extinguisher_monitor.py tag_photo.jpg                # one photo (or a folder)
  python extinguisher_monitor.py IMG_0454.MOV --no-qwen       # detection only
  python extinguisher_monitor.py IMG_0454.MOV --today 2026-12-01 --monthfirst
  q / Esc quits, space pauses.
"""

import argparse
import json
import os
import queue
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

import core.config  # noqa: F401  (loads .env)
from core.drawing import text_scale
from core.expiry import SAFETY, STATUS_TEXT, assess, fmt_date, parse_answer, reclassify, status_for
from core.prompts import EXTINGUISHER_LOCATE_PROMPT, EXTINGUISHER_PROMPT, EXTINGUISHER_SYSTEM
from core.qwen_assist import first_served_model
from core.rules import box_iou, containment
from core.stream_view import MJPEGStreamer
from core.video_io import LiveSource, MotionCompensator
from engines.qwen_engine import QwenEngine
from exit_monitor import Tracker   # shared IoU tracker with camera-motion compensation

EXT_CLASSES = ["fire extinguisher"]
# similar-looking things YOLOE should label as something else
DECOY_CLASSES = ["fire hose reel", "fire alarm", "red bag", "water bottle", "red trash can"]
STATUS_BGR = {"reading": (160, 160, 160), "valid": (0, 200, 0), "expiring_soon": (0, 200, 255),
              "expired": (0, 0, 255), "unclear": (0, 200, 255), "unreadable": (200, 120, 200)}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
SHARP_REF = 3000.0   # Laplacian variance of a crop (at 256 px high) that counts as sharp
TAG_SIDE = 1000      # the sticker crop is upscaled to about this many px (more pixels per digit)


# ── Helpers ───────────────────────────────────────────────────────────────────

def crop_around(frame, box, side=0.35, top=0.25, bottom=0.15):
    """Extinguisher + margin (tags usually hang beside / below the handle)."""
    H, W = frame.shape[:2]
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    a, b = int(max(0, x1 - side * w)), int(min(W, x2 + side * w))
    c, d = int(max(0, y1 - top * h)), int(min(H, y2 + bottom * h))
    return frame[c:d, a:b].copy()


def sharpness(crop) -> float:
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (max(1, int(g.shape[1] * 256 / g.shape[0])), 256))
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def to_pil(bgr) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))


# ── Detector ──────────────────────────────────────────────────────────────────

class ExtinguisherDetector:
    """YOLOE with text prompts: fire extinguishers (+ decoys that are thrown away)."""

    def __init__(self, model_name: str, conf: float = 0.3, imgsz: int = 640):
        import yoloe_infer as Y
        from ultralytics import YOLOE
        self.names = EXT_CLASSES + DECOY_CLASSES
        self.conf, self.imgsz = conf, imgsz
        self.device = Y._pick_device()
        print(f"  Loading YOLOE {model_name} on {self.device} ...")
        with Y._in_dir(Y.MODELS_DIR):
            self.model = YOLOE(model_name)
            self.model.set_classes(self.names, self.model.get_text_pe(self.names))

    def __call__(self, frame) -> list:
        r = self.model.predict(frame, conf=self.conf, imgsz=self.imgsz,
                               device=self.device, verbose=False)[0]
        return [{"kind": "extinguisher", "label": "fire extinguisher", "box": xyxy, "conf": c}
                for xyxy, cls, c in zip(r.boxes.xyxy.tolist(), r.boxes.cls.int().tolist(),
                                        r.boxes.conf.tolist())
                if self.names[cls] in EXT_CLASSES]


# ── Tracker ───────────────────────────────────────────────────────────────────

def _same_ext(a, b) -> bool:
    """
    Same extinguisher? Lenient on purpose: boxes grow fast when walking up to one, and
    the camera-motion estimate (dominated by nearby walls) can push the predicted box
    sideways when the extinguisher is further away (parallax). Two extinguishers
    side by side are > 1 width apart, so they stay separate.
    """
    if box_iou(a, b) >= 0.2 or max(containment(a, b), containment(b, a)) >= 0.5:
        return True
    return _near(a, b, 0.8, 0.3)


def _near(a, b, kx: float, ky: float) -> bool:
    """Centres within kx box-widths horizontally and ky box-heights vertically."""
    w, h = max(a[2] - a[0], b[2] - b[0]), max(a[3] - a[1], b[3] - b[1])
    dx = abs((a[0] + a[2]) - (b[0] + b[2])) / 2
    dy = abs((a[1] + a[3]) - (b[1] + b[3])) / 2
    return dx < kx * w and dy < ky * h


class ExtTracker(Tracker):
    @staticmethod
    def _same(kind, a, b) -> bool:
        return _same_ext(a, b)

    def update(self, dets: list, now: float, source: str) -> None:
        """
        As Tracker.update, then: a track born this frame next to a track that LOST its
        detection this frame is the same extinguisher (the predicted box drifted, e.g.
        parallax) → it continues the older ID. A neighbour that is still detected is
        never taken over, so extinguishers side by side stay separate.
        """
        before = {t["id"] for t in self.tracks} | {g["id"] for g in self.dead}
        super().update(dets, now, source)
        for t in [t for t in self.tracks if t["id"] not in before]:
            orphans = [o for o in self.tracks + self.dead
                       if o["id"] in before and o["last_seen"] < now and _near(o["box"], t["box"], 1.5, 0.5)]
            if not orphans:
                continue
            o = min(orphans, key=lambda o: abs((o["box"][0] + o["box"][2]) - (t["box"][0] + t["box"][2])))
            o.update(box=list(t["box"]), last_seen=now, hits=max(o["hits"], 2))
            self.tracks.remove(t)
            if o in self.dead:
                self.dead.remove(o)
                self.tracks.append(o)

    def merge_duplicates(self) -> dict:
        """Two tracks on one extinguisher → keep the older ID with the fresher box.
        Returns {removed_id: kept_id}."""
        merged = {}
        ts = sorted(self.tracks, key=lambda t: t["id"])
        for i, a in enumerate(ts):
            if a["id"] in merged:
                continue
            for b in ts[i + 1:]:
                if b["id"] in merged or not _same_ext(a["box"], b["box"]):
                    continue
                if b["last_seen"] > a["last_seen"]:
                    a["box"] = list(b["box"])
                a["hits"] = max(a["hits"], b["hits"])
                a["last_seen"] = max(a["last_seen"], b["last_seen"])
                merged[b["id"]] = a["id"]
        self.tracks = [t for t in self.tracks if t["id"] not in merged]
        return merged


# ── Qwen reader ───────────────────────────────────────────────────────────────

class TagReader:
    """
    Two Qwen calls per read:
      1. locate   where on the extinguisher crop is the dated sticker / tag? (0-1000 boxes)
      2. read     a tight crop of each sticker (up to 2), from the full-resolution frame,
                  upscaled → "DATE <date> | <words next to it>" lines
    Reading the whole extinguisher at once gives the sticker only ~100 px and digits
    get misread (2026 → 2024).
    """

    def __init__(self, url: str, model: str = None, max_side: int = 1280):
        try:
            model = model or first_served_model(url)
        except OSError as e:
            raise SystemExit(f"\n  Cannot reach Qwen at {url} ({getattr(e, 'reason', e)}).\n"
                             "  Check the port on the server: cat ~/qwen-vllm/port  "
                             "(or bash server/status.sh)") from None
        self.locator = QwenEngine("EMPTY", model, url, max_image_side=max_side, max_tokens=60)
        self.engine = QwenEngine("EMPTY", model, url, max_image_side=max_side, max_tokens=160)

    def locate(self, crop_bgr) -> list:
        """→ sticker boxes in crop pixels (largest first)."""
        ans = self.locator.query_image(to_pil(crop_bgr), "You locate things in images.",
                                       user_text=EXTINGUISHER_LOCATE_PROMPT)
        h, w = crop_bgr.shape[:2]
        boxes = []
        for m in re.findall(r"(\d+)\D+?(\d+)\D+?(\d+)\D+?(\d+)", ans):
            x1, y1, x2, y2 = (min(1000, int(v)) for v in m)
            if x2 > x1 and y2 > y1:
                boxes.append([x1 * w / 1000, y1 * h / 1000, x2 * w / 1000, y2 * h / 1000])
        return sorted(boxes, key=lambda b: -(b[2] - b[0]) * (b[3] - b[1]))

    def read(self, crop_bgr, dayfirst: bool) -> tuple:
        """→ (raw answer, parsed {"dates", "none"}, seconds, [sticker crops sent])"""
        t = time.time()
        boxes = self.locate(crop_bgr)[:2]
        if not boxes:
            return "NONE no_tag (no dated sticker found)", parse_answer("NONE no_tag"), \
                time.time() - t, []
        H, W = crop_bgr.shape[:2]
        raws, tags = [], []
        for x1, y1, x2, y2 in boxes:
            mx, my = (x2 - x1) / 8, (y2 - y1) / 8
            tag = crop_bgr[int(max(0, y1 - my)):int(min(H, y2 + my)),
                           int(max(0, x1 - mx)):int(min(W, x2 + mx))]
            if min(tag.shape[:2]) < 24:
                raws.append("NONE too_small")
                continue
            k = min(4.0, TAG_SIDE / max(tag.shape[:2]))
            if k > 1:
                tag = cv2.resize(tag, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
            tags.append(tag)
            raws.append(self.engine.query_image(to_pil(tag), EXTINGUISHER_SYSTEM,
                                                user_text=EXTINGUISHER_PROMPT).strip())
        raw = "\n".join(raws)
        return raw, parse_answer(raw, dayfirst), time.time() - t, tags


# ── Per-extinguisher memory ───────────────────────────────────────────────────

class ExtRecord:
    def __init__(self, tid: int, now: float, vt: float):
        self.id = tid
        self.first_seen, self.first_vt, self.last_vt = now, vt, vt
        self.close_since = None          # first time it was big enough to read
        self.visible = True
        self.best = None                 # best crop since the last read: (quality, crop, vt)
        self.last_read_q, self.last_read_t = 0.0, 0.0
        self.pending = 0                 # reads in flight
        self.submitted = 0
        self.reads: list = []
        self.status, self.due, self.basis, self.precision = "reading", None, None, None
        self.raw_dates: list = []        # what was read (for the panel / report)
        self.dates: dict = {}            # kind → {"date", "text", "raw"} agreed by the reads
        self.reason = None               # why unreadable
        self.confirmed = False
        self.hint = None                 # "move closer" etc.
        self.snapshot = None


class ExtinguisherMonitor:
    def __init__(self, args, detector=None):
        self.args = args
        self.today = date.fromisoformat(args.today) if args.today else date.today()
        self.det = detector or ExtinguisherDetector(args.yoloe_model, args.conf)
        self.reader = None
        if args.qwen_url and not args.no_qwen:
            self.reader = TagReader(args.qwen_url, args.qwen_model, args.read_side)
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.q_out = queue.Queue()
        self.events: list = []
        self.snap_dir = Path(args.out_dir)
        self._log = []
        self.reset()

    def _note(self, text: str) -> None:
        if self.args.log_frames:                 # per-frame log line (CLI); off in the backend
            self._log.append(text)

    def reset(self) -> None:
        """Forget tracks and extinguishers (new video / camera). Events are kept."""
        self.tracker = ExtTracker(ttl_fast=1.5, revive_s=8.0)
        self.recs: dict = {}
        self.merged: dict = {}            # track id merged away → id it was merged into
        self.motion, self.prev_C, self._last_size = None, None, None
        self._vt = 0.0

    # ── one frame ────────────────────────────────────────────────────────────
    def step(self, frame, vt: float):
        """Full-resolution BGR frame → (processed frame, (n dets, ms), now)."""
        H0, W0 = frame.shape[:2]
        scale = min(1.0, self.args.proc_width / W0)
        W, H = int(W0 * scale) // 2 * 2, int(H0 * scale) // 2 * 2
        if self._last_size not in (None, (W, H)):
            self.reset()
        self._last_size = (W, H)
        self._vt = vt
        full, k = frame, W0 / W
        if (W, H) != (W0, H0):
            frame = cv2.resize(frame, (W, H), interpolation=cv2.INTER_AREA)
        now = time.time()

        if self.motion is None:
            self.motion = MotionCompensator(W)
        C = self.motion.update(frame)
        step = np.eye(3) if self.prev_C is None else C @ np.linalg.inv(self.prev_C)
        self.prev_C = C
        s = self.motion.scale
        M = (np.diag([1 / s, 1 / s, 1.0]) @ step @ np.diag([s, s, 1.0]))[:2].astype(np.float32)
        self.tracker.predict(M)

        t = time.time()
        dets = self.det(frame)
        info = (len(dets), 1000 * (time.time() - t))
        self.tracker.update(dets, now, "fast")
        for old, new in self.tracker.merge_duplicates().items():
            self._merge_records(old, new, vt)
        self.tracker.prune(now, W, H)

        self._apply_reads(now, vt, frame)
        self._update(full, k, frame, now, vt)
        return frame, info, now

    def _update(self, full, k, frame, now, vt):
        a = self.args
        seen = set()
        for t in self.tracker.visible("extinguisher", min_hits=3):
            tid = t["id"]
            seen.add(tid)
            rec = self.recs.get(tid)
            if rec is None:
                rec = self.recs[tid] = ExtRecord(tid, now, vt)
                print(f"  [{vt:6.1f}s] new extinguisher #{tid}")
            rec.visible, rec.last_vt = True, vt

            # best crop since the last read, from the full-resolution frame
            box_full = [v * k for v in t["box"]]
            short = min(box_full[2] - box_full[0], box_full[3] - box_full[1])
            if short < a.min_px:
                rec.hint = "move closer"
                continue
            if rec.close_since is None:
                rec.close_since = now
            crop = crop_around(full, box_full)
            if crop.size == 0:
                continue
            sharp = sharpness(crop)
            q = short * min(1.0, sharp / SHARP_REF)
            rec.hint = "hold steady" if sharp < 0.4 * SHARP_REF else None
            if rec.best is None or q > rec.best[0]:
                rec.best = (q, crop, vt)

            if self.reader and self._should_read(rec, now):
                self._submit(rec, now, vt, frame, t["box"])
        for tid, rec in self.recs.items():
            if tid not in seen:
                rec.visible = False

    def _merge_records(self, old: int, new: int, vt: float) -> None:
        self.merged[old] = new
        gone = self.recs.pop(old, None)
        if gone is None:
            return
        keep = self.recs.get(new)
        if keep is None:
            gone.id = new
            self.recs[new] = gone
            return
        keep.first_seen, keep.first_vt = min(keep.first_seen, gone.first_seen), min(keep.first_vt, gone.first_vt)
        keep.reads = sorted(keep.reads + gone.reads, key=lambda r: r["video_time_s"])
        keep.pending += gone.pending
        keep.submitted += gone.submitted
        if gone.best and (keep.best is None or gone.best[0] > keep.best[0]):
            keep.best = gone.best
        keep.snapshot = keep.snapshot or gone.snapshot
        if gone.reads:
            self._decide(keep, time.time(), vt)

    def _should_read(self, rec, now) -> bool:
        """
        - first read: once it has been close enough for --settle s (a sharper view may come)
        - then: while unconfirmed, keep reading the best new view (every --reread s), but
          after a "no date" answer only when the view is clearly better (1.3×)
        - stop when two reads agree, after --max-reads reads with dates, or 3× that in total
        """
        a = self.args
        if rec.pending >= a.parallel or rec.confirmed or rec.best is None or rec.close_since is None:
            return False
        dated = sum(1 for r in rec.reads if r["dates"])
        if dated >= a.max_reads or len(rec.reads) >= 3 * a.max_reads:
            return False
        if not rec.submitted:
            return now - rec.close_since >= a.settle
        q = rec.best[0]
        if q >= 1.3 * rec.last_read_q:
            return True
        if rec.reads and not rec.reads[-1]["dates"]:
            return False
        return now - rec.last_read_t >= a.reread and q >= 0.8 * rec.last_read_q

    def _submit(self, rec, now, vt, frame, box):
        q, crop, crop_vt = rec.best
        rec.best = None
        rec.pending += 1
        rec.submitted += 1
        rec.last_read_q, rec.last_read_t = q, now
        n = rec.submitted
        path = None
        if self.args.snapshots:
            path = self.snap_dir / f"ext{rec.id}_read{n}_{crop_vt:06.1f}s.jpg"
            cv2.imwrite(str(path), crop)
        if rec.snapshot is None and self.args.snapshots:
            snap = frame.copy()
            x1, y1, x2, y2 = (int(v) for v in box)
            cv2.rectangle(snap, (x1, y1), (x2, y2), (0, 0, 255), 3)
            rec.snapshot = str(self.snap_dir / f"ext{rec.id}_scene.jpg")
            cv2.imwrite(rec.snapshot, snap)
        self._note(f"-> read#{rec.id}.{n} ({crop.shape[1]}x{crop.shape[0]})")

        def job():
            try:
                raw, parsed, secs, tags = self.reader.read(crop, not self.args.monthfirst)
                if path:
                    for i, tag in enumerate(tags):
                        cv2.imwrite(str(path).replace(".jpg", f"_tag{i + 1}.jpg"), tag)
                self.q_out.put((rec.id, n, raw, parsed, secs, q, crop_vt, path))
            except Exception as e:
                self.q_out.put((rec.id, n, f"error: {e}", None, 0.0, q, crop_vt, path))
        self.pool.submit(job)

    def _apply_reads(self, now, vt, frame):
        a = self.args
        while True:
            try:
                tid, n, raw, parsed, secs, q, crop_vt, path = self.q_out.get_nowait()
            except queue.Empty:
                return
            tid = self.merged.get(tid, tid)
            rec = self.recs.get(tid)
            if rec is None:                                   # after a reset
                continue
            rec.pending = max(0, rec.pending - 1)
            if parsed is None:
                self._note(f"<- read#{tid}.{n} {raw}")
                continue
            one = assess(parsed["dates"], self.today, a.service_months, a.life_years, a.soon_days)
            rec.reads.append({"n": n, "video_time_s": round(crop_vt, 2), "seconds": round(secs, 2),
                              "quality": round(q, 1), "raw": raw.strip(), "crop": str(path) if path else None,
                              "dates": [{**d, "date": d["date"].isoformat()} for d in parsed["dates"]],
                              "none": parsed["none"], "due": one["due"].isoformat() if one["due"] else None,
                              "basis": one["basis"], "precision": one["precision"]})
            shown = "; ".join(f"{d['kind']} {d['raw']} [{d['words'] or '-'}]" if d.get("words") is not None
                              else f"{d['kind']} {d['raw']}" for d in parsed["dates"]) or f"NONE {parsed['none']}"
            self._note(f"<- read#{tid}.{n} {shown} ({secs:.1f}s)")
            self._decide(rec, now, vt)

    def _decide(self, rec, now, vt):
        """Combine all reads of one extinguisher into its status."""
        a = self.args
        old = (rec.status, rec.due, rec.confirmed)
        dues = [r for r in rec.reads if r["due"]]
        if dues:
            votes = Counter(r["due"] for r in dues)
            due, count = votes.most_common(1)[0]
            if list(votes.values()).count(count) > 1:         # tie → the best-quality read
                due = max((r for r in dues if votes[r["due"]] == count), key=lambda r: r["quality"])["due"]
            best = next(r for r in dues if r["due"] == due)
            rec.due, rec.basis, rec.precision = date.fromisoformat(due), best["basis"], best["precision"]
            rec.status = status_for(rec.due, self.today, a.soon_days)
            rec.raw_dates = [f"{d['kind']} {d['raw']}" for d in best["dates"]]
            rec.dates = self._agreed_dates([r for r in dues if r["due"] == due])
            rec.raw_dates = [f"{k} {v['raw']}" for k, v in rec.dates.items()]
            # explain the due date with the most explicit agreed date (next service written
            # on the tag beats "last service + 12 months" when both give the same date)
            agreed = [{"kind": k, "date": date.fromisoformat(v["date"]), "precision": v["precision"]}
                      for k, v in rec.dates.items()]
            one = assess(agreed, self.today, a.service_months, a.life_years, a.soon_days)
            if one["due"] == rec.due:
                rec.basis, rec.precision = one["basis"], one["precision"]
            rec.confirmed = count >= 2
            rec.reason = None
        elif any(r["dates"] for r in rec.reads):
            rec.status = "unclear"
            last = next(r for r in reversed(rec.reads) if r["dates"])
            rec.raw_dates = [f"{d['kind']} {d['raw']}" for d in last["dates"]]
            rec.dates = self._agreed_dates([last])
        else:
            rec.status, rec.reason = "unreadable", rec.reads[-1]["none"]
        if (rec.status, rec.due, rec.confirmed) != old:
            self._event(rec, vt)

    def _agreed_dates(self, reads: list) -> dict:
        """Per kind, the date most of these reads agree on → {kind: {date, text, raw}}."""
        by_kind = {}
        for r in reads:
            ds = [{**d, "date": date.fromisoformat(d["date"])} for d in r["dates"]]
            for d in reclassify(ds, self.today):
                by_kind.setdefault(d["kind"], []).append(d)
        out = {}
        for kind, ds in by_kind.items():
            top = Counter(d["date"] for d in ds).most_common(1)[0][0]
            d = next(d for d in ds if d["date"] == top)
            out[kind] = {"date": top.isoformat(), "text": fmt_date(top, d["precision"]),
                         "raw": d["raw"], "precision": d["precision"]}
        known = {v["date"] for k, v in out.items() if k != "UNKNOWN"}
        if out.get("UNKNOWN", {}).get("date") in known:      # same date, words missed once
            del out["UNKNOWN"]
        return out

    def _describe(self, rec) -> str:
        if rec.status in ("valid", "expiring_soon", "expired"):
            agree = sum(r["due"] == rec.due.isoformat() for r in rec.reads)
            return (f"{STATUS_TEXT[rec.status]}{'' if rec.confirmed else '?'} — due "
                    f"{fmt_date(rec.due, rec.precision)} ({rec.basis}; {agree}/{len(rec.reads)} "
                    f"reads agree{'' if rec.confirmed else ', unconfirmed'})")
        if rec.status == "unclear":
            return f"{STATUS_TEXT[rec.status]} — read {', '.join(rec.raw_dates)} (meaning not written)"
        if rec.status == "unreadable":
            return f"{STATUS_TEXT[rec.status]} — {rec.reason} ({len(rec.reads)} reads)"
        return STATUS_TEXT[rec.status]

    def _event(self, rec, vt):
        msg = f"EXTINGUISHER #{rec.id}: {self._describe(rec)}"
        print(f"  [{vt:6.1f}s] {msg}")
        self.events.append((vt, msg, rec.status))

    # ── backend live mode (same interface as ExitMonitor) ────────────────────
    def start_workers(self) -> None:
        """Nothing to start: reads run in the thread pool when needed."""

    def _info(self, rec) -> dict:
        safe, safe_text = SAFETY[rec.status]
        return {"id": rec.id, "status": rec.status, "status_text": STATUS_TEXT[rec.status],
                "safe": safe, "safe_text": safe_text, "confirmed": rec.confirmed,
                "reading": rec.pending > 0, "visible": rec.visible,
                "due": rec.due.isoformat() if rec.due else None,
                "due_text": fmt_date(rec.due, rec.precision) if rec.due else None,
                "basis": rec.basis, "dates": rec.dates, "dates_read": rec.raw_dates,
                "reason": rec.reason, "reads": len(rec.reads),
                "agree": sum(r["due"] == rec.due.isoformat() for r in rec.reads) if rec.due else 0,
                "hint": rec.hint, "text": self._describe(rec)}

    def live_result(self, W: int, H: int) -> dict:
        """
        extinguishers   the ones in view, with boxes normalised to 0..1 (drawn on the video)
        inventory       every extinguisher seen in this session (the side panel)
        """
        by_id = {t["id"]: t for t in self.tracker.tracks}
        out = []
        for tid, rec in sorted(self.recs.items()):
            t = by_id.get(tid)
            if not rec.visible or t is None:
                continue
            b = t["box"]
            out.append({**self._info(rec),
                        "box": [round(max(0.0, b[0]) / W, 4), round(max(0.0, b[1]) / H, 4),
                                round(min(W, b[2]) / W, 4), round(min(H, b[3]) / H, 4)]})
        return {"extinguishers": out,
                "inventory": [self._info(r) for _, r in sorted(self.recs.items())]}

    def shutdown(self, out_dir: Path, frames: int, dropped: int, times: list) -> None:
        """Finish reads in flight and write the report (for callers that drive step())."""
        self.snap_dir = out_dir
        self._finish_reads(out_dir)
        self._report(out_dir, {"frames": frames, "dropped": dropped,
                               "fast_loop_ms": round(1000 * float(np.mean(times)), 1) if times else None})

    # ── drawing ──────────────────────────────────────────────────────────────
    def draw(self, frame) -> np.ndarray:
        out = frame.copy()
        s, th = text_scale(out)
        by_id = {t["id"]: t for t in self.tracker.tracks}
        for tid, rec in self.recs.items():
            t = by_id.get(tid)
            if not rec.visible or t is None:
                continue
            x1, y1, x2, y2 = (int(v) for v in t["box"])
            col = STATUS_BGR[rec.status]
            cv2.rectangle(out, (x1, y1), (x2, y2), col, th + 2)
            text = f"EXT #{tid} {STATUS_TEXT[rec.status]}"
            if rec.due:
                text += f" {fmt_date(rec.due, rec.precision)}" + ("" if rec.confirmed else "?")
            if rec.pending:
                text += " ..."
            elif rec.hint and rec.status in ("reading", "unreadable"):
                text += f" ({rec.hint})"
            _label(out, text, x1, y1, col, s, th)
        return out

    def panel(self, width: int, vt: float, fps: float) -> np.ndarray:
        p = np.full((200, width, 3), 30, np.uint8)
        f = cv2.FONT_HERSHEY_SIMPLEX
        mode = "YOLOE" + (" + Qwen" if self.reader else " (no Qwen)")
        cv2.putText(p, f"EXTINGUISHERS   t={vt:5.1f}s   {fps:4.1f} fps   [{mode}]   today {self.today}",
                    (12, 26), f, 0.7, (230, 230, 230), 2, cv2.LINE_AA)
        y = 56
        for r in sorted(self.recs.values(), key=lambda r: (not r.visible, r.id))[:5]:
            col = STATUS_BGR[r.status] if r.visible else (120, 120, 120)
            txt = f"#{r.id:<3} {self._describe(r)}{'' if r.visible else '  (out of view)'}"
            cv2.circle(p, (20, y - 6), 7, col, -1)
            cv2.putText(p, txt[:95], (36, y), f, 0.55, col, 2, cv2.LINE_AA)
            y += 26
        x0 = width // 2 + 20
        cv2.putText(p, "Events", (x0, 56), f, 0.6, (230, 230, 230), 2, cv2.LINE_AA)
        for i, (t, msg, st) in enumerate(self.events[-5:][::-1]):
            cv2.putText(p, f"{t:6.1f}s {msg}"[:75], (x0, 82 + 24 * i), f, 0.5,
                        STATUS_BGR.get(st, (200, 200, 200)), 1, cv2.LINE_AA)
        return p

    # ── video / camera ───────────────────────────────────────────────────────
    def run(self):
        a = self.args
        src = LiveSource(a.source, loop=a.loop)
        stem = Path(a.source).stem if Path(a.source).is_file() else "live"
        out_dir = Path(a.out_dir) / f"extinguisher_{stem}"
        out_dir.mkdir(parents=True, exist_ok=True)
        self.snap_dir = out_dir
        print(f"  Source: {a.source} | {src.width}x{src.height} @ {src.fps:.1f} fps | "
              f"today {self.today} | output → {out_dir}\n")
        streamer = MJPEGStreamer(a.stream).start() if a.stream else None
        writer, win, times = None, "Extinguisher monitor (q: quit, space: pause)", []
        try:
            while True:
                idx, frame, skipped = src.read()
                if frame is None:
                    break
                t0 = time.time()
                vt = src.video_time()
                frame, info, now = self.step(frame, vt)
                annotated = self.draw(frame)
                times.append(time.time() - t0)
                fps = 1.0 / max(1e-3, np.mean(times[-30:]))
                if a.log_frames:
                    log, self._log = self._log, []
                    vis = [r for r in self.recs.values() if r.visible]
                    print(f"  f{idx:5d} t={vt:6.2f}s {1000 * times[-1]:4.0f}ms | YOLOE {info[0]} det "
                          f"{info[1]:3.0f}ms | Qwen: {' '.join(log) or '-'} | "
                          f"{', '.join(f'#{r.id} {STATUS_TEXT[r.status]}' for r in vis) or 'none in view'}",
                          flush=True)
                H, W = frame.shape[:2]
                half = min(W, 800)
                top = cv2.resize(cv2.hconcat([frame, annotated]), (2 * half, int(H * half / W) // 2 * 2),
                                 interpolation=cv2.INTER_AREA)
                combined = cv2.vconcat([top, self.panel(2 * half, vt, fps)])
                if a.save and writer is None:
                    Path(a.save).parent.mkdir(parents=True, exist_ok=True)
                    writer = cv2.VideoWriter(a.save, cv2.VideoWriter_fourcc(*"mp4v"), src.fps,
                                             (combined.shape[1], combined.shape[0]))
                if writer:
                    for _ in range(skipped + 1):
                        writer.write(combined)
                if streamer:
                    streamer.publish(combined)
                    streamer.publish(frame, "original")
                    streamer.publish(annotated, "annotated")
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
            src.release()
            if writer:
                writer.release()
            if streamer:
                streamer.stop()
            if not a.no_display:
                cv2.destroyAllWindows()
            self._finish_reads(out_dir)
            self._report(out_dir, {"frames": src.idx + 1, "dropped": src.dropped,
                                   "fast_loop_ms": round(1000 * float(np.mean(times)), 1) if times else None})

    def _finish_reads(self, out_dir):
        """Wait for reads still in flight (a few seconds at most) so the report has them."""
        deadline = time.time() + 15
        while any(r.pending for r in self.recs.values()) and time.time() < deadline:
            time.sleep(0.1)
            self._apply_reads(time.time(), self._vt, None)
        self.pool.shutdown(wait=False, cancel_futures=True)

    # ── photos ───────────────────────────────────────────────────────────────
    def run_images(self, paths: list):
        """Each photo: detect extinguishers and read each one; no extinguisher found →
        read the whole photo (a close-up of a tag)."""
        a = self.args
        out_dir = Path(a.out_dir) / "extinguisher_photos"
        out_dir.mkdir(parents=True, exist_ok=True)
        self.snap_dir = out_dir
        results = []
        for p in paths:
            img = cv2.imread(str(p))
            if img is None:
                print(f"  {p.name}: cannot read the image")
                continue
            scale = min(1.0, a.proc_width / img.shape[1])
            small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            dets = self.det(small)
            targets = [(i + 1, [v / scale for v in d["box"]]) for i, d in enumerate(dets)] \
                or [(0, None)]
            annotated = img.copy()
            s, th = text_scale(annotated)
            for n, box in targets:
                crop = crop_around(img, box) if box else img
                cv2.imwrite(str(out_dir / f"{p.stem}_ext{n}.jpg"), crop)
                entry = {"photo": p.name, "extinguisher": n if box else "whole photo",
                         "box": [round(v) for v in box] if box else None}
                if self.reader:
                    raw, parsed, secs, tags = self.reader.read(crop, not a.monthfirst)
                    for i, tag in enumerate(tags):
                        cv2.imwrite(str(out_dir / f"{p.stem}_ext{n}_tag{i + 1}.jpg"), tag)
                    one = assess(parsed["dates"], self.today, a.service_months, a.life_years,
                                 a.soon_days)
                    status = one["status"] if parsed["dates"] else "unreadable"
                    entry.update(raw=raw.strip(), seconds=round(secs, 2), status=status,
                                 due=one["due"].isoformat() if one["due"] else None,
                                 basis=one["basis"], none=parsed["none"],
                                 dates=[{**d, "date": d["date"].isoformat()} for d in parsed["dates"]])
                    due = fmt_date(one["due"], one["precision"]) if one["due"] else "-"
                    print(f"  {p.name} [{entry['extinguisher']}] {STATUS_TEXT[status]:<10} due {due:<10} "
                          f"{one['basis'] or parsed['none'] or ''} | Qwen ({secs:.1f}s): "
                          f"{' / '.join(raw.strip().splitlines())}")
                else:
                    status = "reading"
                    print(f"  {p.name} [{entry['extinguisher']}] detected (no Qwen)")
                if box:
                    x1, y1, x2, y2 = (int(v) for v in box)
                    cv2.rectangle(annotated, (x1, y1), (x2, y2), STATUS_BGR[status], th + 2)
                    _label(annotated, f"EXT {n} {STATUS_TEXT[status]}", x1, y1, STATUS_BGR[status], s, th)
                results.append(entry)
            cv2.imwrite(str(out_dir / f"{p.stem}_annotated.jpg"), annotated)
        path = out_dir / "report.json"
        path.write_text(json.dumps({"today": self.today.isoformat(), "results": results}, indent=2))
        print(f"\n  Report: {path}")

    def _report(self, out_dir: Path, stats: dict):
        exts = []
        for r in sorted(self.recs.values(), key=lambda r: r.id):
            exts.append({"id": r.id, "status": r.status, "status_text": STATUS_TEXT[r.status],
                         "safe": SAFETY[r.status][0], "safe_text": SAFETY[r.status][1], "dates": r.dates,
                         "due": r.due.isoformat() if r.due else None, "basis": r.basis,
                         "confirmed": r.confirmed, "dates_read": r.raw_dates, "reason": r.reason,
                         "first_seen_s": round(r.first_vt, 2), "last_seen_s": round(r.last_vt, 2),
                         "scene": r.snapshot, "reads": r.reads})
        rep = {"source": self.args.source, "today": self.today.isoformat(), **stats,
               "extinguishers": exts,
               "events": [{"video_time_s": round(t, 2), "event": m} for t, m, _ in self.events]}
        path = out_dir / "report.json"
        path.write_text(json.dumps(rep, indent=2))
        print(f"\n  {stats['frames']} frames ({stats['dropped']} dropped) | fast loop "
              f"{stats['fast_loop_ms']} ms/frame | {len(exts)} extinguishers")
        for r in sorted(self.recs.values(), key=lambda r: r.id):
            print(f"    #{r.id}: {self._describe(r)}")
        print(f"  Report: {path}")


def _label(img, text, x, y, col, s, th):
    (tw, tht), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, s, th)
    y = max(tht + 8, y)
    x = max(0, min(x, img.shape[1] - tw - 6))
    cv2.rectangle(img, (x, y - tht - 8), (x + tw + 6, y), col, -1)
    txt = (0, 0, 0) if col in (STATUS_BGR["expiring_soon"], STATUS_BGR["reading"]) else (255, 255, 255)
    cv2.putText(img, text, (x + 3, y - 5), cv2.FONT_HERSHEY_SIMPLEX, s, txt, th, cv2.LINE_AA)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Fire-extinguisher detection + expiry-date reading")
    ap.add_argument("source", help="Video file, webcam index, stream URL, photo or folder of photos")
    ap.add_argument("--qwen-url", default=os.environ.get("QWEN_URL"),
                    help="Qwen vLLM server, e.g. http://host:8010/v1 (default: $QWEN_URL)")
    ap.add_argument("--qwen-model", default=None)
    ap.add_argument("--no-qwen", action="store_true", help="Detection only (no date reading)")
    ap.add_argument("--yoloe-model", default="yoloe-11s-seg.pt")
    ap.add_argument("--conf", type=float, default=0.3, help="YOLOE confidence (default: 0.3)")
    ap.add_argument("--proc-width", type=int, default=960)
    ap.add_argument("--read-side", type=int, default=1280,
                    help="Longest side of the crop sent to Qwen (default: 1280)")
    ap.add_argument("--min-px", type=int, default=120,
                    help="Don't read extinguishers narrower than this in the original frame "
                         "(default: 120 px) — 'move closer'")
    ap.add_argument("--settle", type=float, default=0.7,
                    help="Seconds close enough before the first read (default: 0.7)")
    ap.add_argument("--reread", type=float, default=0.3,
                    help="Seconds between confirming reads (default: 0.3)")
    ap.add_argument("--parallel", type=int, default=2,
                    help="Reads in flight per extinguisher (default: 2)")
    ap.add_argument("--max-reads", type=int, default=5,
                    help="Reads with dates per extinguisher; stops earlier when two agree "
                         "(default: 5)")
    # rules
    ap.add_argument("--today", default=None, help="Pretend today is YYYY-MM-DD (testing)")
    ap.add_argument("--monthfirst", action="store_true",
                    help="Dates like 05/06/2027 are month/day (default: day/month)")
    ap.add_argument("--service-months", type=int, default=12,
                    help="A service is valid this long when only the last-service date is "
                         "known (default: 12)")
    ap.add_argument("--life-years", type=int, default=10,
                    help="Service life from the manufacture date (default: 10)")
    ap.add_argument("--soon-days", type=int, default=30,
                    help="DUE SOON when due within this many days (default: 30)")
    # output
    ap.add_argument("--no-snapshots", dest="snapshots", action="store_false")
    ap.add_argument("--quiet", dest="log_frames", action="store_false",
                    help="No per-frame log line (events only)")
    ap.add_argument("--stream", type=int, nargs="?", const=8090, default=None, metavar="PORT")
    ap.add_argument("--save", default=None, help="Record the output view to this .mp4")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--no-display", action="store_true")
    ap.add_argument("--out-dir", default="results")
    return ap


def main():
    args = build_parser().parse_args()
    if not args.qwen_url and not args.no_qwen:
        print("  No --qwen-url / QWEN_URL → detection only (dates are not read)")
    print(f"  Qwen: {'OFF' if args.no_qwen or not args.qwen_url else args.qwen_url} | "
          f"YOLOE: {args.yoloe_model}")
    mon = ExtinguisherMonitor(args)
    src = Path(args.source)
    if src.is_dir():
        photos = sorted(p for p in src.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
        mon.run_images(photos)
    elif src.suffix.lower() in IMAGE_SUFFIXES:
        mon.run_images([src])
    else:
        mon.run()


if __name__ == "__main__":
    main()
