"""Shared frame-driven exit/extinguisher pipelines, with an optional video reader."""
import copy
import json
import threading
import time
from collections import deque
from pathlib import Path

import cv2

from core.video_io import LiveSource
from backend.annotations import draw_annotations
from backend.qwen_status import QwenUnavailable, require_qwen
from exit_monitor import ExitMonitor
import extinguisher_monitor as X


class CombinedMonitor:
    def __init__(self, args, detectors, on_frame=None, on_event=None):
        self.args, self.detectors = copy.copy(args), detectors
        self.on_frame, self.on_event = on_frame, on_event
        self.report_path = None
        self.qwen_active, self.qwen_status = False, "starting"
        self.exits = self.ext = None
        self.events = []
        self._closed = False
        self._times = deque(maxlen=60)
        self._last_state = {}
        self._halt = threading.Event()

    def _event(self, event, mode):
        self.events.append((event["video_time_s"], event["text"], event.get("status", event.get("to", "unknown"))))
        if self.on_event:
            self.on_event({**event, "mode": mode})

    def request_stop(self):
        self._halt.set()

    def start_workers(self):
        if self.exits is not None:
            return
        root = Path(self.args.out_dir)
        for name in ("exits", "extinguishers"):
            (root / name).mkdir(parents=True, exist_ok=True)
        requested = getattr(self.args, "qwen_requested", bool(self.args.qwen_url))
        self.qwen_status = "unavailable" if requested else "disabled"
        if requested or getattr(self.args, "require_qwen", False):
            try:
                self.args.qwen_model = require_qwen(self.args.qwen_url)
                self.qwen_active, self.qwen_status = True, "active"
            except QwenUnavailable:
                if getattr(self.args, "require_qwen", False):
                    raise
                self.args.qwen_url, self.args.qwen_model = None, None
                print("  [combined] Qwen assistance unavailable; detection continues without date verification")
        try:
            self.exits = ExitMonitor(self.args, fast_detector=self.detectors[0],
                on_event=lambda event: self._event(event, "exits"))
            self.exits.snap_dir = root / "exits"
            ext_args = X.build_parser().parse_args([self.args.source])
            ext_args.proc_width = self.args.proc_width
            ext_args.qwen_model = self.args.qwen_model
            ext_args.qwen_url = self.args.qwen_url
            ext_args.no_qwen = not self.args.qwen_url
            ext_args.snapshots = self.args.snapshots
            ext_args.out_dir = str(root / "extinguishers")
            ext_args.no_display, ext_args.log_frames = True, False
            self.ext = X.ExtinguisherMonitor(ext_args, detector=self.detectors[1],
                on_event=lambda event: self._event(event, "extinguisher"))
            self.ext.snap_dir = root / "extinguishers"
            self.exits.start_workers()
        except BaseException:
            # A failed second constructor must not leave the first pipeline alive.
            self.shutdown(root, 0, 0, [])
            raise

    def step(self, original, vt):
        self.start_workers()
        started = time.time()
        frame, fast, merged, now = self.exits.step(original, vt)
        ext_frame, ext_fast, _ = self.ext.step(original, vt)
        if frame.shape != ext_frame.shape:
            raise ValueError("Combined monitor processing dimensions differ")
        self._times.append(time.time() - started)
        height, width = frame.shape[:2]
        self._last_state = {**self.exits._state(now, vt, len(self._times) / max(sum(self._times), 1e-6)),
                            **self.ext.live_result(width, height), **self.availability()}
        count = (fast[0] if fast else 0) + (ext_fast[0] if ext_fast else 0)
        millis = (fast[1] if fast else 0) + (ext_fast[1] if ext_fast else 0)
        return frame, (count, millis), merged, now

    def availability(self):
        return {"mode": "combined", "date_reading_enabled": self.qwen_active,
                "qwen_active": self.qwen_active, "qwen_status": self.qwen_status}

    def live_result(self, width, height):
        return {**self.exits.live_result(width, height), **self.ext.live_result(width, height),
                **self.availability()}

    def reset(self):
        self.exits.reset()
        self.ext.reset()
        self._last_state = {}

    def shutdown(self, root, frames, dropped, times):
        if self._closed:
            return
        self._closed = True
        root = Path(root)
        try:
            if self.exits is not None:
                self.exits.shutdown(root / "exits", frames, dropped, list(times))
        finally:
            if self.ext is not None:
                self.ext.shutdown(root / "extinguishers", frames, dropped, list(times))
        if self.exits is not None and self.ext is not None:
            exit_report = json.loads(self.exits.report_path.read_text())
            ext_report = json.loads(self.ext.report_path.read_text())
            report = {**exit_report, "mode": "combined", "extinguishers": ext_report["extinguishers"],
                      "events": sorted(
                          [{**event, "mode": "exits"} for event in exit_report["events"]] +
                          [{**event, "mode": "extinguisher"} for event in ext_report["events"]],
                          key=lambda event: event["video_time_s"])}
            self.report_path = root / "report.json"
            self.report_path.write_text(json.dumps(report, indent=2))

    def run(self):
        source = None
        frames = dropped = 0
        try:
            self.start_workers()
            source = LiveSource(self.args.source, loop=self.args.loop)
            while not self._halt.is_set():
                _, original, skipped = source.read()
                if original is None:
                    break
                frame, _, _, now = self.step(original, source.video_time())
                height, width = frame.shape[:2]
                annotated = draw_annotations(frame, self.live_result(width, height))
                frames += 1
                dropped += skipped
                self.on_frame(frame, annotated, cv2.hconcat([frame, annotated]), self._last_state)
        finally:
            if source is not None:
                source.release()
            # Initialization failure has already cleaned its partial resources.
            if self.exits is not None and self.ext is not None:
                self.shutdown(Path(self.args.out_dir), frames, dropped, list(self._times))
