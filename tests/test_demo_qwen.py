"""Optional and strict Qwen contracts without running models."""
import unittest
from unittest.mock import patch, MagicMock
from fastapi import HTTPException
from backend.app import SessionRequest, create_session
from backend.qwen_status import QwenUnavailable, require_qwen
from backend.sessions import Session


class DemoQwenTests(unittest.TestCase):
    def test_missing_url_and_empty_model_are_unavailable(self):
        with self.assertRaises(QwenUnavailable):
            require_qwen("")
        with patch("backend.qwen_status.first_served_model", return_value=""):
            with self.assertRaises(QwenUnavailable):
                require_qwen("http://model/v1")

    @patch("backend.app.manager.create")
    @patch("backend.app.require_qwen", side_effect=QwenUnavailable())
    def test_required_qwen_fails_before_session_created(self, check, create):
        with self.assertRaises(HTTPException) as caught:
            create_session(SessionRequest(source="demo.MOV", mode="combined", require_qwen=True))
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.detail, {"code": "qwen_unavailable"})
        create.assert_not_called()

    @patch("backend.app.manager.create")
    @patch("backend.app.require_qwen", return_value="model")
    def test_ready_demo_preserves_both_detectors(self, check, create):
        create.return_value.info.return_value = {"id": "demo"}
        self.assertEqual(create_session(SessionRequest(source="demo.MOV", mode="combined",
            use_locate=False, require_qwen=True)), {"id": "demo"})
        self.assertTrue(create.call_args.args[2])  # YOLOE
        self.assertTrue(create.call_args.args[4])  # Qwen
        self.assertEqual(create.call_args.kwargs["mode"], "combined")

    @patch("backend.sessions.resolve_source", return_value="demo.MOV")
    def test_startup_race_publishes_typed_terminal_error(self, _):
        session = Session("demo.MOV", True, True, False, True, {}, (object(), object()), "combined")
        with patch("backend.combined.CombinedMonitor") as factory:
            factory.return_value.run.side_effect = QwenUnavailable("offline")
            session._run()
        self.assertEqual(session.info()["error_code"], "qwen_unavailable")
        self.assertTrue(session.done)

    @patch("backend.sessions.resolve_source", return_value="demo.MOV")
    def test_generic_errors_and_cli_exit_are_terminal_not_qwen(self, _):
        for error in [RuntimeError("detector failed"), SystemExit("CLI failure")]:
            session = Session("demo.MOV", True, True, False, True, {}, (object(), object()), "combined")
            with patch("backend.combined.CombinedMonitor") as factory:
                factory.return_value.run.side_effect = error
                session._run()
            self.assertEqual(session.info()["error_code"], "demo_failed")
            self.assertTrue(session.done)

    @patch("backend.app.manager.create")
    @patch("backend.app.require_qwen")
    def test_existing_optional_qwen_clients_unchanged(self, check, create):
        create_session(SessionRequest(source="demo.MOV", use_qwen=False))
        check.assert_not_called()

    def test_combined_initialization_discovery_failure_is_typed(self):
        from backend.combined import CombinedMonitor
        from types import SimpleNamespace
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(out_dir=directory, qwen_url="http://model/v1", require_qwen=True)
            monitor = CombinedMonitor(args, (None, None), lambda *args: None, lambda *args: None)
            with patch("backend.qwen_status.first_served_model", side_effect=OSError("offline")):
                with self.assertRaises(QwenUnavailable):
                    monitor.run()

    @patch("backend.sessions.resolve_source", return_value="demo.MOV")
    def test_duplicate_creation_reuses_owned_session_under_lock(self, _):
        from backend.sessions import SessionManager
        manager = SessionManager()
        manager._detector = MagicMock(return_value=object())
        with patch.object(Session, "start", lambda session: session):
            first = manager.create("demo.MOV", mode="combined", use_qwen=False,
                demo_client_id="00000000-0000-0000-0000-000000000001")
            retry = manager.create("demo.MOV", mode="combined", use_qwen=False,
                demo_client_id="00000000-0000-0000-0000-000000000001")
        self.assertIs(first, retry)
        self.assertEqual(len(manager.sessions), 1)
        self.assertEqual(first.info()["demo_client_id"], "00000000-0000-0000-0000-000000000001")

    def test_websocket_carries_qwen_code_in_all_terminal_messages(self):
        from fastapi.testclient import TestClient
        from backend.app import app
        session = MagicMock()
        session.info.return_value = {"id": "demo", "status": "error", "mode": "combined",
            "state": {}, "error_code": "qwen_unavailable"}
        session.events_since.return_value = []
        session.done = True
        with patch("backend.app.settings.TOKEN", ""), patch("backend.app.manager.get", return_value=session):
            with TestClient(app).websocket_connect("/api/sessions/demo/events") as socket:
                hello, state, end = [socket.receive_json() for _ in range(3)]
        self.assertEqual(hello["session"]["error_code"], "qwen_unavailable")
        self.assertEqual(state["error_code"], "qwen_unavailable")
        self.assertEqual(end["session"]["error_code"], "qwen_unavailable")

    def run_combined(self, available, configured=True):
        import tempfile
        import numpy as np
        from pathlib import Path
        from backend.combined import CombinedMonitor
        from apps.exit_monitor import build_parser, ExitMonitor
        captured, states = [], []
        def make_exit(*args, **kwargs):
            result = ExitMonitor(*args, **kwargs)
            captured.append(result)
            return result
        with tempfile.TemporaryDirectory() as directory:
            args = build_parser().parse_args(["demo.MOV"])
            args.out_dir, args.qwen_url = directory, "http://model/v1" if configured else None
            args.qwen_requested, args.require_qwen = True, False
            args.locate_url, args.no_yoloe, args.hold = None, False, 0
            # Real tracking/rules/date-state code, deterministic detector inputs.
            exit_dets = [{"kind":"door", "label":"door", "box":[2,2,38,46]},
                         {"kind":"obstacle", "label":"chair", "box":[8,25,28,46]}]
            ext_dets = [{"kind":"extinguisher", "label":"fire extinguisher", "box":[40,5,60,44]}]
            detectors = (lambda frame: [] if available else exit_dets,
                         lambda frame: [] if available else ext_dets)
            def publish(original, annotated, comparison, state):
                states.append(state)
                if len(states) >= 4:
                    monitor.request_stop()
            monitor = CombinedMonitor(args, detectors, publish, lambda event: None)
            source = MagicMock()
            source.read.return_value = (0, np.zeros((48,64,3), dtype=np.uint8), 0)
            source.video_time.return_value = 1.0
            with patch("backend.combined.LiveSource", return_value=source), \
                 patch("backend.combined.require_qwen", **({"return_value":"test-model"} if available else {"side_effect":QwenUnavailable()})), \
                 patch("backend.combined.ExitMonitor", side_effect=make_exit), \
                 patch("apps.exit_monitor.QwenEngine") as exit_engine, \
                 patch("apps.extinguisher_monitor.QwenEngine") as tag_engine:
                monitor.run()
                self.assertEqual(exit_engine.called, available)
                self.assertEqual(tag_engine.call_count, 2 if available else 0)
            live = captured[0].live_result(64,48)
            self.assertTrue(Path(monitor.report_path).exists())
            self.assertEqual(bool(args.qwen_url), configured)  # no mutation of session preference
            return monitor, states[-1], live

    def test_optional_unreachable_or_unconfigured_qwen_keeps_both_detectors(self):
        for configured in (True, False):
            with self.subTest(configured=configured):
                monitor, state, exits = self.run_combined(False, configured)
                self.assertFalse(monitor.qwen_active)
                self.assertEqual(state["qwen_status"], "unavailable")
                self.assertFalse(state["date_reading_enabled"])
                self.assertTrue(state["exits"])
                self.assertTrue(state["extinguishers"])
                for item in state["extinguishers"]:
                    self.assertIsNone(item["due"])
                    self.assertIsNone(item["safe"])
                    self.assertFalse(item["confirmed"])
                    self.assertEqual(item["dates"], {})
                for item in exits["exits"]:
                    self.assertIsNone(item["confirmed"])
                for item in exits["obstacles"]:
                    self.assertIsNone(item["depth"])

    def test_mocked_available_qwen_enables_both_assistance_pipelines(self):
        monitor, state, _ = self.run_combined(True)
        self.assertTrue(monitor.qwen_active)
        self.assertTrue(state["qwen_active"])
        self.assertTrue(state["date_reading_enabled"])

    @patch("backend.sessions.resolve_source", return_value="demo.MOV")
    def test_degraded_session_info_and_duplicate_retry_keep_preference(self, _):
        from backend.sessions import SessionManager
        manager = SessionManager()
        manager._detector = MagicMock(return_value=object())
        with patch.object(Session, "start", lambda session: session):
            session = manager.create("demo.MOV", mode="combined", use_qwen=True, demo_client_id="owner")
            session.monitor = MagicMock(qwen_active=False, report_path=None)
            retry = manager.create("demo.MOV", mode="combined", use_qwen=True, demo_client_id="owner")
        self.assertIs(session, retry)
        self.assertFalse(session.info()["models"]["qwen"])
        self.assertTrue(session.info()["models"]["qwen_requested"])


if __name__ == "__main__":
    unittest.main()
