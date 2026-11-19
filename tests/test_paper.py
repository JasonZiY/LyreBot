from pathlib import Path
import struct
import tempfile
import tkinter as tk
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
    """Width, height and the unfiltered RGB rows of a page's PNG."""
    width, height = struct.unpack(">II", data[16:24])
    start = data.index(b"IDAT") + 4
    length = struct.unpack(">I", data[start - 8:start - 4])[0]
    raw = zlib.decompress(data[start:start + length])
    stride = 3 * width + 1
    assert all(raw[row * stride] == 0 for row in range(height))  # no filters: rows as they are
    return width, height, b"".join(raw[row * stride + 1:(row + 1) * stride] for row in range(height))


class Drawing(unittest.TestCase):
    def test_lamplight_at_the_head_evening_at_the_foot(self):
        width, height, rgb = pixels(paper.draw(60, 200, 40))
        self.assertEqual((width, height), (60, 200))
        at = lambda x, y: rgb[(y * width + x) * 3:(y * width + x) * 3 + 3]  # noqa: E731
        for x, y in ((0, 0), (59, 10), (30, 39)):  # the head: one colour, give or take the grain
            self.assertTrue(all(abs(a - b) <= 1 for a, b in zip(at(x, y), paper.HEAD)), (x, y, at(x, y)))
        middle, foot_left, foot_right = at(5, 110), at(2, 198), at(57, 198)
        self.assertGreater(middle[2], paper.HEAD[2] + 20)  # out of the lamplight: paper again
        self.assertLess(foot_left[1], middle[1])  # evening: rose
        self.assertGreater(foot_right[2], foot_left[2])  # and mauve towards the right

    def test_the_same_size_draws_the_same_page(self):
        self.assertEqual(paper.draw(40, 60, 10), paper.draw(40, 60, 10))

    def test_a_solid_piece_takes_the_heads_colour(self):
        self.assertEqual(paper.colour(100, 30, 400, 800, 120), "#%02x%02x%02x" % paper.HEAD)

    def test_pages_are_kept_on_disk_but_only_a_few(self):
        cache = Path(tempfile.mkdtemp())
        with mock.patch("lyrebot.theme.CACHE", cache), mock.patch.object(paper, "KEEP", 2), \
                mock.patch.object(paper, "draw", wraps=paper.draw) as drawn:
            first = paper.picture(20, 30, 5)
            self.assertEqual(paper.picture(20, 30, 5), first)
            self.assertEqual(drawn.call_count, 1)  # the second time it was read back
            for height in (31, 32, 33):
                paper.picture(20, height, 5)
        self.assertEqual(len(list(cache.glob("page-*.png"))), 2)

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
            self.assertEqual(piece.coords(piece.find_withtag("paper")[0]), [-x, -y])
        words.configure(text="风息")
        self.assertEqual(len(words.find_withtag("paper")), 1)  # redrawing keeps the paper

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
