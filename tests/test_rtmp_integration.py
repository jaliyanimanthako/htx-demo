"""Shared configuration and external-camera API contracts, without GPU or network."""
import copy
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from apps.exit_monitor import build_parser
from backend import settings
from backend.app import app
from backend.sessions import Session, SessionError, resolve_source
from core.inspection_config import load_config


class RtmpIntegrationTests(unittest.TestCase):
    def test_selected_toml_is_shared_by_cli_and_backend(self):
        from apps.fire_safety_inspection import load_config as cli_config
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'machine.toml'
            file.write_text('''[server]
port = 8200
[processing]
proc_width = 640
[exits]
hold = 2.5
[sources.external_camera]
url = "rtsp://relay:8554/live/x2"
''')
            with patch.dict('os.environ', {'HTX_CONFIG': str(file)}):
                cfg = load_config()
                self.assertEqual(cfg, cli_config())
                self.assertEqual(cfg['server']['port'], 8200)
                with patch.object(settings, 'CONFIG', cfg):
                    args = settings.apply_monitor_config(build_parser().parse_args(['live']), 'exits', {'hold': 3.0})
                    self.assertEqual(args.proc_width, 640)
                    self.assertEqual(args.hold, 3.0)
                    self.assertEqual(resolve_source('external-camera'), 'rtsp://relay:8554/live/x2')

    def test_source_discovery_does_not_expose_stream_credentials(self):
        cfg = copy.deepcopy(settings.CONFIG)
        cfg['sources']['external_camera']['url'] = 'rtsp://user:secret@relay:8554/live'
        with patch.object(settings, 'CONFIG', cfg), patch.object(settings, 'TOKEN', 'token'):
            client = TestClient(app)
            self.assertEqual(client.get('/api/sources').status_code, 401)
            response = client.get('/api/sources', headers={'Authorization': 'Bearer token'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['sources'][0]['configured'], True)
            self.assertNotIn('secret', response.text)
            self.assertNotIn('rtsp://', response.text)

    def test_missing_external_camera_has_actionable_error(self):
        cfg = copy.deepcopy(settings.CONFIG)
        cfg['sources']['external_camera']['url'] = ''
        with patch.object(settings, 'CONFIG', cfg):
            with self.assertRaisesRegex(SessionError, 'sources.external_camera.url'):
                resolve_source('external-camera')

    def test_external_session_stops_while_waiting_for_first_frame(self):
        cfg = copy.deepcopy(settings.CONFIG)
        cfg['sources']['external_camera']['url'] = 'rtsp://relay:8554/live'
        entered = threading.Event()
        def waiting_source(*args, halt, **kwargs):
            entered.set()
            if not halt.wait(2):
                raise AssertionError('stop event was not propagated')
            raise RuntimeError('Stopped while waiting for stream')
        with tempfile.TemporaryDirectory() as directory, patch.object(settings, 'CONFIG', cfg), \
             patch.object(settings, 'OUT_DIR', Path(directory)), \
             patch('backend.combined.LiveSource', side_effect=waiting_source):
            session = Session('external-camera', True, True, False, False, {},
                              (lambda _: [], lambda _: []), mode='combined')
            self.assertFalse(session.args.loop)
            session.start()
            self.assertTrue(entered.wait(2))
            session.stop()
            session.thread.join(3)
            self.assertFalse(session.thread.is_alive())
            self.assertEqual(session.status, 'stopped')
            self.assertIsNone(session.error_code)

    def test_backend_qwen_selection_matches_shared_config(self):
        from backend.qwen_status import require_qwen, QwenUnavailable
        cfg = copy.deepcopy(settings.CONFIG)
        cfg['models']['qwen_model'] = '2b'
        with patch.object(settings, 'CONFIG', cfg), \
             patch('backend.qwen_status.first_served_model', return_value='Qwen/Qwen3-VL-4B-Instruct'):
            with self.assertRaises(QwenUnavailable):
                require_qwen('http://model/v1')
            cfg['models']['qwen_model'] = ''
            self.assertEqual(require_qwen('http://model/v1'), 'Qwen/Qwen3-VL-4B-Instruct')

    def test_invalid_nested_config_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'bad.toml'
            file.write_text('[sources.external_camera]\nurl = 42\n')
            with self.assertRaisesRegex(SystemExit, 'sources.external_camera.url'):
                load_config(str(file))
