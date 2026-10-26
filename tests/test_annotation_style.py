"""Rendering checks for the annotations sent to recorded-video clients."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from exit_monitor import ExitMonitor
from extinguisher_monitor import ExtinguisherMonitor, ExtRecord

GRAY, GREEN, AMBER, RED = (160, 160, 160), (0, 200, 0), (0, 200, 255), (0, 0, 255)


class AnnotationStyleTests(unittest.TestCase):
    def test_exit_status_palette_and_labels_keep_internal_ids(self):
        monitor = ExitMonitor.__new__(ExitMonitor)
        track = dict(id=30, signed=True, exit_ok=True, box=[20, 80, 120, 180])
        obstacle = dict(id=44, label='chair', depth=None, box=[45, 110, 80, 170])
        record = SimpleNamespace(visible=True, status='unknown', since=0, blocker_ids=[44])
        monitor.exits = {30: record}
        monitor.tracker = SimpleNamespace(tracks=[track, obstacle], visible=lambda _: [obstacle])
        monitor.qwen = object()
        monitor._label = Mock()
        for status, color in [('unknown', GRAY), ('clear', GREEN),
                              ('partially_obstructed', AMBER), ('obstructed', RED)]:
            with self.subTest(status=status):
                record.status = status
                monitor._label.reset_mock()
                image = monitor._draw(np.zeros((240, 320, 3), dtype=np.uint8), 5)
                self.assertEqual(tuple(image[100, 20]), color)
                self.assertEqual(tuple(image[130, 45]), RED)
                labels = [call.args[1] for call in monitor._label.call_args_list]
                self.assertTrue(labels[0].startswith('EXIT '))
                self.assertEqual(labels[1], 'chair')
                self.assertTrue(all('#' not in label for label in labels))
                self.assertEqual(track['id'], 30)
                self.assertEqual(record.blocker_ids, [44])
        # Qwen confirmation still pending: even a provisional clear exit stays gray.
        track['exit_ok'], record.status = None, 'clear'
        self.assertEqual(tuple(monitor._draw(np.zeros((240, 320, 3), dtype=np.uint8), 5)[100, 20]), GRAY)
        self.assertIn('CHECKING', monitor._label.call_args_list[-2].args[1])
        # Geometric exit status remains available when Qwen is absent.
        monitor.qwen = None
        self.assertEqual(tuple(monitor._draw(np.zeros((240, 320, 3), dtype=np.uint8), 5)[100, 20]), GREEN)

    def test_extinguisher_processing_verification_and_status_palette(self):
        monitor = ExtinguisherMonitor.__new__(ExtinguisherMonitor)
        track = dict(id=52, box=[20, 80, 120, 180])
        record = ExtRecord(52, 0, 0)
        monitor.recs = {52: record}
        monitor.tracker = SimpleNamespace(tracks=[track])
        monitor.reader = object()
        cases = [('reading', False, 0, GRAY), ('unclear', False, 0, GRAY),
                 ('unreadable', False, 0, GRAY), ('valid', False, 0, GRAY),
                 ('expired', False, 0, GRAY), ('valid', True, 1, GRAY),
                 ('valid', True, 0, GREEN), ('expiring_soon', True, 0, AMBER),
                 ('expired', True, 0, RED)]
        from unittest.mock import patch
        for status, confirmed, pending, color in cases:
            with self.subTest(status=status, confirmed=confirmed, pending=pending), \
                 patch('extinguisher_monitor._label') as label:
                record.status, record.confirmed, record.pending = status, confirmed, pending
                image = monitor.draw(np.zeros((240, 320, 3), dtype=np.uint8))
                self.assertEqual(tuple(image[100, 20]), color)
                self.assertNotIn('#', label.call_args.args[1])
                self.assertTrue(label.call_args.args[1].startswith('Extinguisher '))
                self.assertEqual(record.id, 52)
        monitor.reader = None
        self.assertEqual(tuple(monitor.draw(np.zeros((240, 320, 3), dtype=np.uint8))[100, 20]), GRAY)


if __name__ == '__main__':
    unittest.main()
