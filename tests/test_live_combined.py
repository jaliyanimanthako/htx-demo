"""Combined camera transport uses existing pipelines; no model download/hardware needed."""
import asyncio
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from backend.app import app, live
from backend.live import LiveSession, parse_frame
from backend.sessions import SessionManager, SessionError
from backend.qwen_status import QwenUnavailable


def packet(seq=1):
    ok, jpg = cv2.imencode('.jpg', np.zeros((48,64,3), dtype=np.uint8))
    assert ok
    return struct.pack('<Id', seq, float(seq)) + jpg.tobytes()


class CombinedLiveTests(unittest.TestCase):
    def test_optional_qwen_both_detectors_normalized_results_and_no_camera_snapshots(self):
        calls = []
        def detectors(mode):
            calls.append(mode)
            if mode == 'exits':
                return lambda frame: [{'kind':'door','label':'door','box':[2,2,38,46]},
                    {'kind':'obstacle','label':'chair','box':[8,25,28,46]}]
            return lambda frame: [{'kind':'extinguisher','label':'fire extinguisher','box':[40,5,60,44]}]
        with tempfile.TemporaryDirectory() as directory, patch('backend.live.settings.OUT_DIR', Path(directory)), \
             patch('backend.combined.require_qwen', side_effect=QwenUnavailable()):
            session = LiveSession(detectors, mode='combined', use_qwen=True, options={'hold':0})
            self.assertEqual(session.ready_message()['mode'], 'combined')
            self.assertFalse(session.models['qwen'])
            self.assertIn('Qwen', session.skipped)
            try:
                for seq in range(1,5):
                    result = session.process(packet(seq))
                self.assertEqual(result['seq'], 4)
                self.assertEqual(result['t'], 4.0)
                self.assertEqual(result['mode'], 'combined')
                self.assertTrue(result['exits']);self.assertTrue(result['extinguishers'])
                self.assertFalse(result['qwen_active']);self.assertFalse(result['date_reading_enabled'])
                self.assertTrue(result['events'] == [] or all('status' in e for e in result['events']))
                for item in result['exits']:
                    self.assertIsNone(item['confirmed'])
                    self.assertTrue(all(0<=x<=1 for x in item['box']))
                for item in result['extinguishers']:
                    self.assertIsNone(item['safe']);self.assertIsNone(item['due']);self.assertFalse(item['confirmed'])
                self.assertFalse(session.monitor.exits.args.snapshots)
                self.assertFalse(session.monitor.ext.args.snapshots)
                session.reset()
                for seq in range(5,70):session.process(packet(seq))
                self.assertEqual(len(session.times),60)
                self.assertEqual(len(session.monitor._times),60)
            finally:session.close()
            self.assertEqual(list(Path(directory).rglob('*.jpg')), [])
        self.assertEqual(calls,['exits','extinguisher'])

    def test_available_qwen_enables_both_existing_assistants(self):
        with tempfile.TemporaryDirectory() as directory, patch('backend.live.settings.OUT_DIR', Path(directory)), \
             patch('backend.combined.require_qwen', return_value='test-model'), \
             patch('apps.exit_monitor.QwenEngine') as exit_engine, patch('apps.extinguisher_monitor.QwenEngine') as ext_engine:
            session=LiveSession(lambda mode: lambda frame: [],mode='combined')
            try:
                self.assertTrue(session.models['qwen'])
                self.assertTrue(session.process(packet())['date_reading_enabled'])
                self.assertTrue(exit_engine.called);self.assertEqual(ext_engine.call_count,2)
            finally:session.close()

    def test_bearer_authentication_and_disconnect_cleanup(self):
        session=MagicMock()
        session.ready_message.return_value={'type':'ready','mode':'combined'}
        session.process.return_value={'type':'result','mode':'combined','seq':1}
        with patch('backend.app.settings.TOKEN','camera-token'), \
             patch('backend.app.manager.create_live',return_value=session) as create, \
             patch('backend.app.manager.close_live') as close:
            with TestClient(app).websocket_connect('/api/live',headers={'Authorization':'Bearer camera-token'}) as socket:
                socket.send_json({'type':'start','mode':'combined','use_yoloe':True,'use_locate':False,'use_qwen':True})
                self.assertEqual(socket.receive_json()['type'],'ready')
                socket.send_bytes(packet())
                self.assertEqual(socket.receive_json()['seq'],1)
            create.assert_called_once_with('combined',True,False,True,{})
            close.assert_called_once_with(session)
            with self.assertRaises(WebSocketDisconnect) as caught:
                with TestClient(app).websocket_connect('/api/live',headers={'Authorization':'Bearer wrong'}):pass
            self.assertEqual(caught.exception.code,4401)

    def test_slot_remains_occupied_until_live_cleanup_finishes(self):
        manager=SessionManager()
        session=MagicMock(id='camera')
        session.close.side_effect=lambda: self.assertIn('camera',manager.live)
        manager.live['camera']=session
        manager.close_live(session)
        self.assertNotIn('camera',manager.live)

    def test_camera_cannot_take_over_other_running_session(self):
        manager=SessionManager()
        manager.live['other']=SimpleNamespace(id='other')
        with self.assertRaises(SessionError):manager.create_live(mode='combined')


class CancelledLiveStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_creation_releases_result_of_model_initialization(self):
        started,finish=threading.Event(),threading.Event()
        created=object()
        def create(*args):
            started.set();finish.wait(timeout=2);return created
        ws=MagicMock()
        ws.headers={}
        async def accept():pass
        async def receive():return {'type':'start','mode':'combined'}
        async def close():pass
        ws.accept=accept;ws.receive_json=receive;ws.close=close
        with patch('backend.app.settings.TOKEN',''),patch('backend.app.manager.create_live',side_effect=create), \
             patch('backend.app.manager.close_live') as cleanup:
            task=asyncio.create_task(live(ws))
            self.assertTrue(await asyncio.to_thread(started.wait,1))
            task.cancel();finish.set()
            with self.assertRaises(asyncio.CancelledError):await task
            cleanup.assert_called_once_with(created)


if __name__=='__main__':unittest.main()
