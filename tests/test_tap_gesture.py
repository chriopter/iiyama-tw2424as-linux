"""Run with python3 -m unittest discover -s tests; no evdev or panel needed."""
import ast
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'apps/wallpanel-api/rootfs/usr/lib/wallpanel/wallpanel_api.py'
tree = ast.parse(SOURCE.read_text())
node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'TapGesture')
scope = {}
exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), scope)
TapGesture = scope['TapGesture']


class GestureTests(unittest.TestCase):
    def setUp(self):
        self.gesture = TapGesture()

    def tap(self, t, x=20, y=20):
        self.assertFalse(self.gesture.frame(True, x, y, t))
        return self.gesture.frame(False, x, y, t + .05)

    def test_only_tenth_completed_tap_opens(self):
        for i in range(9):
            self.assertFalse(self.tap(i * .2))
        self.assertTrue(self.tap(1.8))
        self.assertFalse(self.tap(2))

    def test_timeout(self):
        for i in range(9):
            self.tap(i * .2)
        self.assertFalse(self.tap(6))

    def test_scrolls_never_open(self):
        for i in range(12):
            t = i * .2
            self.gesture.frame(True, 20, 20, t)
            self.gesture.frame(True, 20, 60, t + .05)
            self.assertFalse(self.gesture.frame(False, 20, 60, t + .1))

    def test_outside_touch_resets_sequence(self):
        for i in range(9):
            self.tap(i * .2)
        self.tap(1.9, 100, 100)
        self.assertFalse(self.tap(2))

    def test_swipe_back_into_corner_is_not_a_tap(self):
        self.gesture.frame(True, 20, 20, 0)
        self.gesture.frame(True, 200, 20, .05)
        self.gesture.frame(True, 20, 20, .1)
        self.assertFalse(self.gesture.frame(False, 20, 20, .15))
        self.assertEqual(self.gesture.times, [])

    def test_motion_on_release_is_rejected(self):
        self.gesture.frame(True, 20, 20, 0)
        self.assertFalse(self.gesture.frame(False, 60, 20, .1))
        self.assertEqual(self.gesture.times, [])

    def test_long_press_resets_sequence(self):
        for i in range(9):
            self.tap(i * .2)
        self.gesture.frame(True, 20, 20, 2)
        self.gesture.frame(False, 20, 20, 2.8)
        self.assertFalse(self.tap(3))

    def test_multitouch_or_dark_screen_rejected(self):
        for i in range(9):
            self.tap(i * .2)
        self.gesture.frame(True, 20, 20, 2, allowed=False)
        self.gesture.frame(False, 20, 20, 2.1)
        self.assertFalse(self.tap(2.2))


if __name__ == '__main__':
    unittest.main()
