from pathlib import Path
import struct
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest import mock
import zlib

from lyrebot import icons, paper
from lyrebot.theme import HAIR, VEIL, Theme
from lyrebot.widgets import PillButton, Sheet, Words, WritingLine

try:  # run as part of the tests package, or by unittest discovery from the tests folder
    from .test_app import hidden_window
except ImportError:
    from test_app import hidden_window


def pixels(data: bytes) -> tuple[int, int, bytes]:
    """Width, height and the unfiltered pixels of one of the page's PNGs."""
    width, height = struct.unpack(">II", data[16:24])
    depth = {2: 3, 6: 4}[data[25]]
    start = data.index(b"IDAT") + 4
    length = struct.unpack(">I", data[start - 8:start - 4])[0]
    raw = zlib.decompress(data[start:start + length])
    stride = depth * width + 1
    assert all(raw[row * stride] == 0 for row in range(height))  # no filters: rows as they are
    return width, height, b"".join(raw[row * stride + 1:(row + 1) * stride] for row in range(height))


class Drawing(unittest.TestCase):
    def test_lamplight_at_the_head_evening_at_the_foot(self):
        width, height, rgb = pixels(paper.wash(80, 320, 40))
        step = paper.STEP
        self.assertEqual((width, height), (-(-(80 + paper.MARGIN) // step), -(-(320 + paper.MARGIN) // step)))
        at = lambda x, y: tuple(rgb[((y // step) * width + x // step) * 3:((y // step) * width + x // step) * 3 + 3])  # noqa: E731
        lifted = tuple(round(value + paper.LIFT) for value in paper.HEAD)  # the grain takes that back
        for x, y in ((0, 0), (79, 10), (40, 31)):
            self.assertEqual(at(x, y), lifted, (x, y))  # the head: one colour
        middle, foot_left, foot_right = at(4, 170), at(4, 316), at(76, 316)
        self.assertGreater(middle[2], paper.HEAD[2] + 20)  # out of the lamplight: paper again
        self.assertLess(foot_left[1], middle[1])  # evening: rose
        self.assertGreater(foot_right[2], foot_left[2])  # and mauve towards the right
        self.assertEqual(at(4, 400)[1], foot_left[1])  # past the foot it runs on as the evening

    def test_the_clouds_keep_still_while_the_window_is_resized(self):
        narrow, wide = pixels(paper.wash(80, 320, 40)), pixels(paper.wash(200, 320, 40))
        for row in range(10, 22):  # clouded, and above the evening's mauve, which follows the width
            start = 3 * row
            self.assertEqual(narrow[2][start * narrow[0]:(start + 30)], wide[2][start * wide[0]:(start + 30)])

    def test_the_grain_darkens_by_a_level_or_two_with_a_few_fibres(self):
        size, _, rgba = pixels(paper.grain(64))
        alpha = rgba[3::4]
        self.assertEqual(set(rgba[0::4]) | set(rgba[1::4]) | set(rgba[2::4]), {0})  # black, more or less of it
        self.assertEqual(min(alpha), 1)  # nowhere wholly clear
        grain = [a for a in alpha if a <= 1 + paper.GRAIN]
        self.assertAlmostEqual(sum(grain) / len(grain), paper.LIFT, delta=0.05)
        self.assertTrue(any(a > 1 + paper.GRAIN for a in alpha))  # the fibres

    def test_tk_takes_the_grain_at_once(self):
        root = hidden_window(self)
        start = time.perf_counter()
        paper._photo(root, paper.grain())
        self.assertLess(time.perf_counter() - start, 1.0)

    def test_the_grain_is_drawn_once_and_kept(self):
        cache = Path(tempfile.mkdtemp())
        with mock.patch("lyrebot.theme.CACHE", cache), mock.patch.object(paper, "grain", return_value=b"tile") as drawn:
            self.assertEqual(paper.grain_tile(), b"tile")
            self.assertEqual(paper.grain_tile(), b"tile")
        self.assertEqual(drawn.call_count, 1)

    def test_a_solid_piece_takes_the_heads_colour(self):
        self.assertEqual(paper.colour(100, 30, 400, 800, 120), "#%02x%02x%02x" % paper.HEAD)

    def test_a_tint_lets_what_is_under_it_show(self):
        red, green, blue, alpha = icons.rgba(2, 2, [("#D9775729", icons.rect(0, 0, 2, 2))])[:4]
        self.assertEqual((red, green, blue, alpha), (0xD9, 0x77, 0x57, 0x29))


class OnThePage(unittest.TestCase):
    def setUp(self):
        self.root = hidden_window(self)
        self.theme = Theme(self.root)
        cache = Path(tempfile.mkdtemp())
        patcher = mock.patch("lyrebot.theme.CACHE", cache)
        patcher.start()
        self.addCleanup(patcher.stop)

    def page(self):
        page = self.theme.page = paper.Page(self.root, flat=10)
        page.lay(80, 60)
        return page

    def test_each_piece_lays_its_own_slice_of_the_page(self):
        page = self.page()
        sheet = Sheet(self.root, self.theme)
        sheet.pack()
        words = Words(sheet, self.theme, text="风")
        words.pack()
        # Where they stand (a hidden window lays nothing out, so say it)
        for piece, x, y in ((sheet, 7, 5), (words, 3, 2)):
            piece.winfo_x, piece.winfo_y = (lambda x=x: x), (lambda y=y: y)
        page.refresh()
        for piece, (x, y) in ((sheet, (7, 5)), (words, (10, 7))):
            self.assertEqual(page.offset(piece), (x, y))
            [laid] = piece.find_withtag("paper")
            self.assertEqual(piece.coords(laid), [-x, -y])  # the window's corner, off this piece's
            self.assertEqual(piece.itemcget(laid, "image"), str(page.image))
        words.configure(text="风息")
        self.assertEqual(len(words.find_withtag("paper")), 1)  # redrawing keeps the paper

    def test_the_paper_is_opaque_so_a_repaint_only_copies_it(self):
        page = self.page()
        image = page.image
        corners = [(0, 0), (image.width() - 1, image.height() - 1), (5, 50)]
        self.assertTrue(all(not int(image.tk.call(image, "transparency", "get", x, y)) for x, y in corners))
        head = [image.get(x, 2) for x in range(0, 40)]
        mean = tuple(sum(pixel[k] for pixel in head) / len(head) for k in range(3))
        self.assertTrue(all(abs(m - h) < 0.6 for m, h in zip(mean, paper.HEAD)), mean)  # the grain evens out

    def test_a_window_dragged_larger_keeps_paper_under_it(self):
        page = self.page()
        page.lay(100, 100)
        drawn = []
        washing = paper.wash
        with mock.patch.object(paper, "wash", side_effect=lambda *a: drawn.append(a) or washing(*a)):
            for step in range(1, 31):
                size = (100 + 12 * step, 100 + 15 * step)
                page.moved(SimpleNamespace(widget=self.root, width=size[0], height=size[1]))
                deadline = time.monotonic() + 0.5
                while page.soon is not None and time.monotonic() < deadline:
                    self.root.update()
                self.assertGreaterEqual(page.image.width(), size[0])  # paper reaches the right
                self.assertGreaterEqual(page.image.height(), size[1])  # and the foot, all the way
            self.assertLess(len(drawn), 10)  # washed now and then, not on every change
            deadline = time.monotonic() + 1.0
            while page.later is not None and time.monotonic() < deadline:
                self.root.update()
        self.assertEqual(page.size, (460, 550))  # and once more for the size it came to

    def test_words_answer_and_measure_as_a_label_would(self):
        font = self.theme.fonts.body
        words = Words(self.root, self.theme, text="选择乐曲", fg="#123456", font=font)
        label = tk.Label(self.root, text="选择乐曲", font=font)
        self.assertEqual(words.cget("text"), "选择乐曲")
        self.assertEqual(words.cget("fg"), "#123456")
        self.assertEqual((words.winfo_reqwidth(), words.winfo_reqheight()), (label.winfo_reqwidth(), label.winfo_reqheight()))
        words.configure(text="", wraplength=200)
        label.configure(text="")
        self.assertEqual(words.winfo_reqheight(), label.winfo_reqheight())  # empty, a line tall still
        long = "曲" * 40
        words.configure(text=long)
        label.configure(text=long, wraplength=200)
        self.assertGreater(words.winfo_reqheight(), 2 * self.theme.measure(font, "曲"))  # wrapped onto lines
        self.assertLessEqual(words.winfo_reqwidth(), 200)

    def test_a_ghost_button_on_the_page_is_a_veil_and_elsewhere_paper(self):
        self.page()
        on_page = PillButton(self.root, self.theme, "搜索", lambda: None)
        self.assertEqual(on_page.look()[:2], (VEIL, HAIR))
        dialog = tk.Toplevel(self.root)
        self.addCleanup(dialog.destroy)
        elsewhere = PillButton(dialog, self.theme, "好", lambda: None)  # another window: not on this page
        self.assertIsNone(elsewhere.page)
        self.assertEqual(elsewhere.look(), PillButton.LOOKS["ghost"]["normal"])

    def test_a_writing_line_is_written_on_the_ground_it_is_given(self):
        line = WritingLine(self.root, self.theme, hint="搜索曲名", bg="#faeac8")
        self.assertEqual(line.entry.cget("bg"), "#faeac8")
        self.assertEqual(line.hint.cget("bg"), "#faeac8")


if __name__ == "__main__":
    unittest.main()
