"""Combined session contracts; no model downloads or GPU required."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import json
import numpy as np
from backend.combined import CombinedMonitor
from backend.sessions import SessionManager, Session
from backend.app import SessionRequest
from exit_monitor import ExitMonitor, build_parser
import extinguisher_monitor as X


class CombinedSessionTests(unittest.TestCase):
    def test_api_accepts_combined(self):
        self.assertEqual(SessionRequest(source="demo.MOV", mode="combined").mode, "combined")

    @patch("backend.sessions.resolve_source", return_value="demo.MOV")
    def test_manager_uses_both_detectors(self, _):
        manager = SessionManager()
        manager._detector = MagicMock(side_effect=["exit-detector", "ext-detector"])
        with patch.object(Session, "start", lambda session: session):
            session = manager.create("demo.MOV", mode="combined", use_qwen=False)
        self.assertEqual(session._fast, ("exit-detector", "ext-detector"))
        self.assertEqual([call.args[0] for call in manager._detector.call_args_list], ["exits", "extinguisher"])

    def test_shared_clean_frame_aligned_draw_and_report(self):
        with tempfile.TemporaryDirectory() as directory:
            args = build_parser().parse_args(["demo.MOV"])
            args.out_dir, args.qwen_url, args.locate_url = directory, None, None
            args.no_yoloe, args.loop, args.log_frames = False, True, False
            inputs, states = [], []
            def detect(frame):
                inputs.append(frame.copy())
                return []
            def publish(original, annotated, combined, state):
                self.assertEqual(original.shape, annotated.shape)
                self.assertEqual(combined.shape[1], 2 * original.shape[1])
                states.append(state)
                monitor.request_stop()
            monitor = CombinedMonitor(args, (detect, detect), publish, lambda _: None)
            source = MagicMock()
            source.read.return_value = (0, np.zeros((48, 64, 3), dtype=np.uint8), 0)
            source.video_time.return_value = 1.25
            with patch("backend.combined.LiveSource", return_value=source) as factory, \
                 patch.object(ExitMonitor, "_draw", side_effect=AssertionError("Legacy exit renderer used")), \
                 patch.object(X.ExtinguisherMonitor, "draw", side_effect=AssertionError("Legacy extinguisher renderer used")):
                monitor.run()
            factory.assert_called_once_with("demo.MOV", loop=True)
            source.read.assert_called_once()
            source.release.assert_called_once()
            self.assertEqual(len(inputs), 2)
            self.assertTrue(all(not frame.any() for frame in inputs))
            self.assertEqual(states[0]["mode"], "combined")
            self.assertIn("exits", states[0]); self.assertIn("extinguishers", states[0])
            self.assertEqual(states[0]["video_time_s"], 1.25)
            report = json.loads(monitor.report_path.read_text())
            self.assertEqual(report["mode"], "combined")
            self.assertIn("exits", report); self.assertIn("extinguishers", report)

    def test_full_extinguisher_label(self):
        from types import SimpleNamespace
        monitor = object.__new__(X.ExtinguisherMonitor)
        monitor.reader = None
        monitor.tracker = SimpleNamespace(tracks=[{"id": 1, "box": [1, 1, 30, 40]}])
        monitor.recs = {1: SimpleNamespace(visible=True, status="reading", due=None, pending=0, hint=None)}
        with patch.object(X, "_label") as label:
            monitor.draw(np.zeros((48, 64, 3), dtype=np.uint8))
        self.assertEqual(label.call_args.args[1], "Extinguisher DETECTED - dates unchecked")


if __name__ == "__main__":
    unittest.main()
