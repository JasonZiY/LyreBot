import ctypes
import ctypes.wintypes
from types import SimpleNamespace
import unittest
from unittest import mock

from lyrebot import chrome
from lyrebot.chrome import (HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT, HTCAPTION, HTCLIENT, HTLEFT, HTRIGHT, HTTOP,
                            HTTOPLEFT, HTTOPRIGHT, Frame)


class Hits(unittest.TestCase):
    def setUp(self):
        self.frame = Frame(edge=6, caption=40)
        self.frame.buttons = [(360, 5, 390, 35)]

    def hit(self, x, y):
        return self.frame.hit(x, y, 400, 800)

    def test_the_bar_moves_the_window_but_its_buttons_are_pressed(self):
        self.assertEqual(self.hit(200, 20), HTCAPTION)
        self.assertEqual(self.hit(20, 39), HTCAPTION)
        self.assertEqual(self.hit(375, 20), HTCLIENT)  # a button
        self.assertEqual(self.hit(375, 5), HTCLIENT)  # its top row, though the edge is near
        self.assertEqual(self.hit(200, 40), HTCLIENT)  # under the bar: the page

    def test_the_edges_and_corners_resize(self):
        self.assertEqual(self.hit(2, 300), HTLEFT)
        self.assertEqual(self.hit(397, 300), HTRIGHT)
        self.assertEqual(self.hit(200, 2), HTTOP)
        self.assertEqual(self.hit(200, 799), HTBOTTOM)
        self.assertEqual(self.hit(2, 2), HTTOPLEFT)
        self.assertEqual(self.hit(10, 1), HTTOPLEFT)  # a corner reaches a little along each side
        self.assertEqual(self.hit(399, 8), HTTOPRIGHT)
        self.assertEqual(self.hit(2, 795), HTBOTTOMLEFT)
        self.assertEqual(self.hit(390, 799), HTBOTTOMRIGHT)
        self.assertEqual(self.hit(6, 300), HTCLIENT)  # just inside: the page

    def test_a_point_on_a_screen_left_of_the_main_one_is_read_as_negative(self):
        if chrome._user32 is None:
            self.skipTest("Windows only")

        def window_rect(_, box):
            rect = box._obj
            rect.left, rect.top, rect.right, rect.bottom = -900, 100, -500, 900
            return 1
        self.frame.hwnd = 1
        with mock.patch.object(chrome, "_user32", SimpleNamespace(GetWindowRect=window_rect)):
            point = lambda x, y: ((y & 0xFFFF) << 16) | (x & 0xFFFF)  # noqa: E731
            self.assertEqual(self.frame.at(point(-700, 120)), HTCAPTION)
            self.assertEqual(self.frame.at(point(-898, 500)), HTLEFT)
            self.assertEqual(self.frame.at(point(-700, 500)), HTCLIENT)


class Scaling(unittest.TestCase):
    def test_moved_to_a_screen_scaled_otherwise_it_is_drawn_for_it_with_the_size_windows_suggests(self):
        frame = Frame(edge=6, caption=40)
        suggested = ctypes.wintypes.RECT(-1500, 60, -1150, 760)
        frame.dpi_changed(96 | (96 << 16), ctypes.addressof(suggested))  # by Win+Shift+arrow, say
        self.assertEqual((frame.dpi, frame.rescaled, frame.suggested), (96, 1, (-1500, 60, 350, 700)))

    def test_dragged_across_it_keeps_its_size_and_picture_until_let_go(self):
        frame = Frame(edge=6, caption=40)
        suggested = ctypes.wintypes.RECT(-1500, 60, -1150, 760)
        frame.moving = True  # WM_ENTERSIZEMOVE: a drag
        frame.dpi_changed(96 | (96 << 16), ctypes.addressof(suggested))
        self.assertEqual((frame.dpi, frame.rescaled, frame.suggested), (96, 0, None))  # nothing in mid-drag
        frame.let_go()  # WM_EXITSIZEMOVE
        self.assertEqual((frame.moving, frame.rescaled, frame.suggested), (False, 1, None))  # now: drawn for it
        frame.moving = True
        frame.let_go()  # a drag that crossed nothing
        self.assertEqual(frame.rescaled, 1)


class Adopting(unittest.TestCase):
    def test_elsewhere_the_window_keeps_its_system_frame(self):
        window = mock.Mock()
        with mock.patch.object(chrome, "available", return_value=False):
            self.assertFalse(chrome.adopt(window, Frame(5, 40)))
        window.overrideredirect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
