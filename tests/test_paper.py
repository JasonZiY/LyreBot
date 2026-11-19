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
        across, down = paper.STEP, paper.STEP_DOWN
        self.assertEqual((width, height), (-(-(80 + paper.MARGIN) // across), -(-(320 + paper.MARGIN) // down)))

        def at(x, y):
            spot = ((y // down) * width + x // across) * 3
            return tuple(rgb[spot:spot + 3])
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
        clouded = [row for row in range(narrow[1]) if 88 < (row + 0.5) * paper.STEP_DOWN < 192]  # under the
        self.assertGreater(len(clouded), 20)  # lamplight's edge, above the evening's mauve, which follows the width
        for row in clouded:
            first, second = narrow[2][3 * row * narrow[0]:][:30], wide[2][3 * row * wide[0]:][:30]
            self.assertEqual(first, second, row)
        self.assertGreater(len(set(narrow[2][3 * clouded[0] * narrow[0]:][:3 * narrow[0]])), 3)  # and clouded

    def test_down_the_page_the_colours_change_by_less_than_a_level_a_step(self):
        width, height, rgb = pixels(paper.wash(80, 800, 110))
        column = [rgb[3 * row * width:3 * row * width + 3] for row in range(height)]
        steps = [max(abs(a - b) for a, b in zip(upper, lower)) for upper, lower in zip(column, column[1:])]
        self.assertLessEqual(max(steps), 1)  # where the lamplight gives way to the paper too: no bands

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

    def drag(self, page, sizes):
        """Drag the window through ``sizes``, letting each quick wash happen; the washes made, in order."""
        drawn = []
        washing = paper.wash
        with mock.patch.object(paper, "wash", side_effect=lambda *a: drawn.append(a[:2]) or washing(*a)):
            for size in sizes:
                page.moved(SimpleNamespace(widget=self.root, width=size[0], height=size[1]))
                deadline = time.monotonic() + 0.5
                while page.soon is not None and time.monotonic() < deadline:
                    self.root.update()
                self.assertGreaterEqual(page.image.width(), size[0])  # paper reaches the right
                self.assertGreaterEqual(page.image.height(), size[1])  # and the foot, all the way
            deadline = time.monotonic() + 1.0
            while page.later is not None and time.monotonic() < deadline:
                self.root.update()
        return drawn

    def test_dragged_wider_the_paper_is_washed_only_now_and_then(self):
        page = self.page()
        page.lay(100, 300)
        drawn = self.drag(page, [(100 + 12 * step, 300) for step in range(1, 31)])
        self.assertLess(len(drawn), 6)  # when it outgrows the margin, and once it has settled
        self.assertEqual(page.size, (460, 300))

    def test_dragged_taller_the_colours_stretch_with_it(self):
        page = self.page()
        page.lay(100, 300)
        heights = [300 + 9 * step for step in range(1, 21)] + [480 - 11 * step for step in range(1, 21)]
        drawn = self.drag(page, [(100, height) for height in heights])
        self.assertEqual([height for _, height in drawn], heights)  # a wash for each height, as it went
        self.assertEqual(page.size, (100, 260))

    def test_a_quick_drag_is_washed_at_most_so_often(self):
        page = self.page()
        page.lay(100, 300)
        drawn = []
        washing = paper.wash
        with mock.patch.object(paper, "wash", side_effect=lambda *a: drawn.append(a[:2]) or washing(*a)):
            for height in range(301, 341):  # forty heights before the window has a moment to wash
                page.moved(SimpleNamespace(widget=self.root, width=100, height=height))
            deadline = time.monotonic() + 1.0
            while (page.soon is not None or page.later is not None) and time.monotonic() < deadline:
                self.root.update()
        self.assertLessEqual(len(drawn), 2)  # caught up with where it had got to, not each step
        self.assertEqual(page.size, (100, 340))

    def test_marks_are_pressed_into_the_paper_and_go_where_the_window_puts_them(self):
        page = self.page()  # for a window 80 x 60
        clean, beside = page.image.get(70, 50), page.image.get(69, 50)
        mark = tk.PhotoImage(master=self.root, width=6, height=4)
        mark.put("#102030", to=(0, 0, 6, 4))
        page.press(mark, lambda width, height: (width - 10, height - 10))
        page.press(mark, lambda width, height: None)  # nowhere for it yet: nothing pressed
        page.refresh()
        self.assertEqual(page.image.get(70, 50), (16, 32, 48))  # pressed into the picture itself
        self.assertEqual(page.image.get(69, 50), beside)  # and nowhere else
        edition = page.edition
        page.wanted = (60, 60)  # dragged narrower: the paper is not washed again,
        page.refresh()
        self.assertEqual(page.image.get(70, 50), clean)  # the paper is put back from under the mark
        self.assertEqual(page.image.get(50, 50), (16, 32, 48))  # and it is pressed in where it now goes
        self.assertGreater(page.edition, edition)  # (what was cut from the paper is cut again)
        edition = page.edition
        page.refresh()
        self.assertEqual(page.edition, edition)  # where it is already, it is left alone
        page.lay(60, 60)
        self.assertEqual(page.image.get(50, 50), (16, 32, 48))  # a fresh wash has it pressed in too

    def test_a_length_of_rule_is_laid_along_its_box_and_follows_the_window(self):
        page = self.page()  # for a window 80 x 60
        clean = page.image.get(65, 3)
        run = tk.PhotoImage(master=self.root, width=4, height=2)
        run.put("#102030", to=(0, 0, 4, 2))
        page.press(run, lambda width, height: (10, 2, width - 20, 2))  # along the top, 10 in from either side
        page.refresh()
        self.assertEqual({page.image.get(x, y) for x in range(10, 70) for y in (2, 3)}, {(16, 32, 48)})
        self.assertNotEqual(page.image.get(9, 2), (16, 32, 48))
        self.assertNotEqual(page.image.get(70, 2), (16, 32, 48))
        page.wanted = (70, 60)  # narrower: the rule is shorter, the paper back where it ran
        page.refresh()
        self.assertEqual(page.image.get(59, 3), (16, 32, 48))
        self.assertEqual(page.image.get(65, 3), clean)

    def test_what_hangs_over_the_page_lies_over_every_piece_it_crosses(self):
        page = self.page()
        ribbon = tk.PhotoImage(master=self.root, width=4, height=20)
        ribbon.put("#D97757", to=(0, 0, 4, 20))
        page.hang(ribbon, lambda width, height: (width - 20, 5))  # for a window 80 wide: at (60, 5)
        crossed, apart = Sheet(self.root, self.theme), Sheet(self.root, self.theme)
        for piece, x, w in ((crossed, 50, 30), (apart, 0, 30)):  # where they stand, and how big they are
            piece.winfo_x, piece.winfo_y = (lambda x=x: x), (lambda: 0)
            piece.winfo_width, piece.winfo_height = (lambda w=w: w), (lambda: 10)
        page.refresh()
        [hung] = crossed.find_withtag("over")
        self.assertEqual(crossed.coords(hung), [10, 5])  # at (60, 5) in the window, off the piece's corner
        self.assertEqual(crossed.itemcget(hung, "image"), str(ribbon))
        self.assertEqual(apart.find_withtag("over"), ())  # it does not reach this one
        crossed.create_line(0, 0, 9, 9)
        crossed.delete("!paper")  # clearing a drawing leaves the paper and what hangs over it
        [laid] = crossed.find_withtag("paper&&!over")
        self.assertEqual(crossed.find_withtag("over"), (hung,))
        crossed.create_line(0, 0, 9, 9)  # drawn anew, and the paper laid again:
        page.refresh()
        self.assertEqual(crossed.find_all(), (laid, crossed.find_all()[1], hung))  # paper, drawing, ribbon

    def test_a_fade_is_the_paper_at_one_edge_clearing_to_nothing_at_the_other(self):
        page = self.page()
        strip = paper.fade(self.root, page.image, 4, 20, 30, 10, solid_at_top=False)  # at a list's foot
        png = strip.tk.call(strip, "data", "-format", "png")
        width, height, depth, rgba = paper._pixels(png if isinstance(png, bytes) else bytes(png, "latin-1"))
        self.assertEqual((width, height, depth), (30, 10, 4))
        alpha = [rgba[row * width * 4 + 3] for row in range(height)]
        self.assertEqual(alpha, sorted(alpha))  # clearer towards the list, paper at the edge
        self.assertGreater(alpha[-1], 240)
        self.assertLess(alpha[0], 15)
        self.assertGreaterEqual(min(alpha), 1)  # never wholly clear, for Tk's sake
        foot = tuple(rgba[(9 * width + 5) * 4:(9 * width + 5) * 4 + 3])
        self.assertEqual(foot, tuple(page.image.get(4 + 5, 20 + 9)))  # the very paper that lies there
        self.assertIsNone(paper.fade(self.root, page.image, 4, 10_000, 30, 10, solid_at_top=True))  # off the page

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
