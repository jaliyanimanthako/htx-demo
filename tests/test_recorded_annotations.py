"""Recorded rendering and native camera consume the same presentation fixtures."""
import copy
import json
from pathlib import Path
import unittest

import numpy as np

from backend.annotations import annotation_color, annotation_items, draw_annotations

CASES = json.loads((Path(__file__).parent / 'fixtures' / 'annotation_cases.json').read_text())


class RecordedAnnotationTests(unittest.TestCase):
    def test_recorded_style_matches_the_live_camera_contract(self):
        for case in CASES:
            with self.subTest(case=case['name']):
                wire = copy.deepcopy(case['wire'])
                actual = annotation_items(wire)
                self.assertEqual(len(actual), len(case['expected']))
                for (_, label, status), expected in zip(actual, case['expected']):
                    self.assertEqual(label, expected['label'])
                    self.assertEqual(status, expected['status'])
                    self.assertEqual('%02X%02X%02X' % annotation_color(status), expected['color'])
                    self.assertNotIn('#', label)
                    self.assertFalse(any(character.isdigit() for character in label))
                self.assertEqual(wire, case['wire'])

    def test_thin_outline_white_text_and_translucent_black_label(self):
        case = next(case for case in CASES if case['name'] == 'exit_clear')
        original = np.full((720, 960, 3), 180, dtype=np.uint8)
        image = draw_annotations(original, case['wire'])
        # The 2px outline is green; the box's interior remains the source image.
        self.assertEqual(tuple(image[300, 96]), (0, 200, 0))
        self.assertEqual(tuple(image[300, 98]), (180, 180, 180))
        self.assertEqual(tuple(image[300, 300]), (180, 180, 180))
        # Labels above the box have white text over black at 85% opacity,
        # rather than the legacy solid green label with black/white text.
        label = image[175:214, 96:300]
        self.assertTrue(np.any(np.all(label == 255, axis=2)))
        self.assertTrue(np.any(np.all((label >= 26) & (label <= 28), axis=2)))
        self.assertFalse(np.any(np.all(label == (0, 200, 0), axis=2)))
        self.assertTrue(np.all(original == 180))

    def test_empty_and_degenerate_boxes_do_not_change_the_frame(self):
        frame = np.full((72, 96, 3), 100, dtype=np.uint8)
        self.assertTrue(np.array_equal(frame, draw_annotations(frame, {})))
        wire = copy.deepcopy(CASES[0]['wire'])
        wire['exits'][0]['box'] = [2, 2, 3, 3]
        self.assertTrue(np.array_equal(frame, draw_annotations(frame, wire)))


if __name__ == '__main__':
    unittest.main()
