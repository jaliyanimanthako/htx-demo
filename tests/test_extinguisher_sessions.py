"""Session integration tests without model downloads or a GPU."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
import numpy as np
from backend.sessions import Session, SessionManager, SessionError
from backend.app import SessionRequest
from apps import extinguisher_monitor as X


class ExtinguisherSessionTests(unittest.TestCase):
    def test_frame_pacing_handles_elapsed_deadline(self):
        from core.video_io import LiveSource
        source = object.__new__(LiveSource)
        source.is_file, source._t0, source.fps = True, 0.0, 30.0
        source.idx, source.dropped = 0, 0
        source._advance = MagicMock(return_value=(True, "frame"))
        with patch("core.video_io.time.time", side_effect=[0.0, 0.1]), patch("core.video_io.time.sleep") as sleep:
            source.read()
        sleep.assert_called_once_with(0.0)

    def test_mode_defaults_and_validation(self):
        self.assertEqual(SessionRequest(source="x").mode, "exits")
        with self.assertRaises(ValueError):
            SessionRequest(source="x", mode="invalid")
        with self.assertRaises(SessionError):
            SessionManager().create("x", mode="invalid")

    @patch("backend.sessions.resolve_source", return_value="demo.MOV")
    def test_dispatch_and_models(self, _):
        detector = object()
        s = Session("demo.MOV", True, True, True, False, {}, detector, mode="extinguisher")
        with patch.object(X, "ExtinguisherMonitor") as factory:
            factory.return_value.report_path = None
            s._run()
            self.assertIs(factory.call_args.kwargs["detector"], detector)
            factory.return_value.run.assert_called_once()
            self.assertTrue(s.args.no_qwen)
            self.assertEqual(s.info()["models"], {"yoloe": True, "locate_anything": False, "qwen": False, "qwen_requested": False})
            self.assertEqual(s.info()["mode"], "extinguisher")
            s.stop()
            factory.return_value.request_stop.assert_called_once()

    @patch("backend.sessions.resolve_source", return_value="demo.MOV")
    def test_exit_dispatch_unchanged(self, _):
        s = Session("demo.MOV", True, True, False, False, {}, object())
        with patch("backend.sessions.ExitMonitor") as factory:
            factory.return_value.report_path = None
            s._run()
            factory.return_value.run.assert_called_once()
            self.assertEqual(s.info()["mode"], "exits")

    def test_monitor_publishes_frames_and_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            args = X.build_parser().parse_args(["demo.MOV", "--no-qwen", "--no-display", "--quiet", "--out-dir", directory])
            states = []
            def frame(original, annotated, combined, state):
                states.append(state)
                monitor.request_stop()
            detector = MagicMock(model_name="yoloe-11s-seg.pt", return_value=[])
            monitor = X.ExtinguisherMonitor(args, detector=detector, on_frame=frame)
            source = MagicMock(width=64, height=48, fps=30, idx=0, dropped=0)
            source.read.return_value = (0, np.zeros((48, 64, 3), dtype=np.uint8), 0)
            source.video_time.return_value = 0.0
            with patch.object(X, "LiveSource", return_value=source):
                monitor.run()
            source.read.assert_called_once()
            source.release.assert_called_once()
            self.assertEqual(states[0]["mode"], "extinguisher")
            self.assertEqual(states[0]["extinguishers"], [])
            self.assertFalse(states[0]["date_reading_enabled"])
            self.assertTrue(Path(monitor.report_path).is_file())


if __name__ == "__main__":
    unittest.main()
