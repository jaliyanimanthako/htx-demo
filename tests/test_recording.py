"""Session recordings (backend/recording.py); no models, GPU or camera needed."""
import struct
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from backend import recording, settings
from backend.live import LiveSession
from backend.qwen_status import QwenUnavailable
from backend.sessions import Session


def read_video(path):
    """→ (frame count, first frame)."""
    cap = cv2.VideoCapture(str(path))
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    return len(frames), frames[0] if frames else None


def packet(seq):
    ok, jpg = cv2.imencode(".jpg", np.zeros((48, 64, 3), dtype=np.uint8))
    assert ok
    return struct.pack("<Id", seq, float(seq)) + jpg.tobytes()


class FakeExitMonitor:
    """Stands in for ExitMonitor in a server session: three frames, then the source ends."""
    def __init__(self, args, on_frame=None, on_event=None, fast_detector=None):
        self.on_frame, self.report_path = on_frame, None

    def run(self):
        frame = np.zeros((48, 64, 3), np.uint8)
        for _ in range(3):
            self.on_frame(frame, frame + 50, frame, {})

    def request_stop(self):
        pass


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        patches = [patch("backend.recording.settings.OUT_DIR", Path(self.dir.name)),
                   patch.dict(settings.CONFIG["server"], {"record": True, "record_fps": 10.0})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.dir.cleanup)

    def test_real_time_length_letterbox_and_drawn_annotations(self):
        rec = recording.Recorder("test")
        exit_box = {"box": [0.1, 0.1, 0.9, 0.9], "status": "clear", "status_text": "CLEAR", "confirmed": True}
        rec.add(np.zeros((480, 640, 3), np.uint8), result={"exits": [exit_box]})
        time.sleep(0.4)
        rec.add(np.zeros((641, 480, 3), np.uint8), result={})     # tablet rotated, odd height
        time.sleep(0.2)
        rec.close()
        n_orig, first_orig = read_video(rec.paths["original"])
        n_ann, first_ann = read_video(rec.paths["annotated"])
        self.assertTrue(5 <= n_orig <= 8, n_orig)                 # ~0.6 s at 10 fps
        self.assertEqual(n_orig, n_ann)
        self.assertEqual(first_orig.shape, (480, 640, 3))         # later frames letterboxed into it
        self.assertLess(first_orig.max(), 30)
        self.assertGreater(first_ann.max(), 100)                  # the exit box is drawn

    def test_no_frames_no_files(self):
        rec = recording.Recorder("empty")
        rec.close()
        self.assertFalse(any(p.exists() for p in rec.paths.values()))

    def test_live_session_is_recorded(self):
        detectors = lambda mode: (lambda frame: [])
        with patch("backend.combined.require_qwen", side_effect=QwenUnavailable()):
            session = LiveSession(detectors, mode="combined", use_qwen=True, options={"hold": 0})
            for seq in range(1, 5):
                session.process(packet(seq))
            session.close()
        for kind, path in session.recorder.paths.items():
            self.assertEqual(path.name, f"{session.out_dir.name}_{kind}.mkv")
            self.assertGreaterEqual(read_video(path)[0], 1)

    def test_live_session_not_recorded_when_off(self):
        settings.CONFIG["server"]["record"] = False
        session = LiveSession(lambda mode: (lambda frame: []), mode="exits", use_qwen=False)
        session.close()
        self.assertIsNone(session.recorder)

    @patch("backend.sessions.resolve_source", side_effect=lambda source: source)
    @patch("backend.sessions.ExitMonitor", FakeExitMonitor)
    def test_stream_sessions_recorded_video_files_not(self, _):
        stream = Session("rtsp://camera:8554/live/x2", False, True, False, False, {}, None)
        stream._run()
        self.assertEqual(stream.status, "finished")
        n, first = read_video(stream.recorder.paths["annotated"])
        self.assertGreaterEqual(n, 1)
        self.assertGreater(first.mean(), 30)                      # the session's annotated frame
        self.assertIn("exits_stream_", stream.recorder.paths["original"].name)
        video = Session("demo.MOV", False, True, False, False, {}, None)
        video._run()
        self.assertIsNone(video.recorder)
        self.assertEqual(list((Path(self.dir.name) / "recordings").glob(f"*{video.id}*")), [])


if __name__ == "__main__":
    unittest.main()
