"""The 简谱 page's score view: drawn from ``jianpu``'s own layout, it knows where each note is and when."""

import struct
import unittest
import zlib

from lyrebot import jianpu
from lyrebot.score import parse_score
from lyrebot.scoreview import flatten, ink
from lyrebot.timeline import compile_ringing

try:
    from .test_app import hidden_window
except ImportError:
    from test_app import hidden_window

SONG = "speed=60\na-s-d-f-\ng-h-j-q-\nw-e-r-t-\nq.w.e.r.t.y.u.q.\n"


def alpha(png):
    """The PNG's size and its pixels' opacity, row by row."""
    width, height = struct.unpack(">II", png[16:24])
    start = png.index(b"IDAT") + 4
    length = struct.unpack(">I", png[start - 8:start - 4])[0]
    raw = zlib.decompress(png[start:start + length])
    stride = 4 * width + 1
    return width, height, [list(raw[row * stride + 1:(row + 1) * stride][3::4]) for row in range(height)]


class Paths(unittest.TestCase):
    def test_moves_lines_and_curves_become_points(self):
        lines = flatten("M1,2 L3,4 Q5,0 7,4 C8,0 9,0 10,4", steps=4)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0][:2], [(1.0, 2.0), (3.0, 4.0)])
        self.assertEqual(lines[0][5], (7.0, 4.0))  # the quadratic ends where it says
        self.assertEqual(lines[0][-1], (10.0, 4.0))
        self.assertAlmostEqual(lines[0][3][1], 2.0)  # half way along the bend: (4 + 0)/2 and back
        self.assertEqual(len(flatten("M0,0 L1,1 M5,5 L6,6")), 2)
        self.assertEqual(flatten("M-1.5,2 L3,-4.25")[0], [(-1.5, 2.0), (3.0, -4.25)])

    def test_ink_is_smoothed_solid_along_the_line_and_clear_away_from_it(self):
        png, left, top, width, height = ink([[(2.5, 5.5), (12.5, 5.5)]], 1.0, "#2B2722")
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        w, h, rows = alpha(png)
        self.assertEqual((w, h), (width, height))
        row = 5 - top
        self.assertEqual(rows[row][7 - left], 255)  # on the line, a level one on a row of pixels: solid
        self.assertEqual(rows[row - 2][7 - left], 0)
        png, left, top, _, _ = ink([[(0, 0), (10, 7)]], 1.2, "#2B2722")  # slanting: some pixels partly covered
        self.assertTrue(any(0 < a < 255 for row in alpha(png)[2] for a in row))


class View(unittest.TestCase):
    def view(self, width):
        from lyrebot.scoreview import ScoreView
        from lyrebot.theme import Theme
        root = hidden_window(self)
        theme = Theme(root)
        clicked = []
        view = ScoreView(root, theme, clicked.append)
        view.configure(width=width)
        view.show(jianpu.sheet_of(SONG, "小曲"))
        score = parse_score(SONG)
        view.time(score, 1.0, sorted(e.at for e in compile_ringing(score).events if e.down))
        return view, clicked

    def test_every_note_is_somewhere_and_sounds_when_written(self):
        view, _ = self.view(400)
        self.assertEqual(len(view.notes), 20)
        times = sorted(note.at for note in view.notes)
        self.assertEqual(times[:12], [float(n) for n in range(12)])
        self.assertEqual(times[12:], [12 + n / 2 for n in range(8)])
        for note in view.notes:
            self.assertLess(note.box[0], note.box[2])
            self.assertEqual(note.start, note.at)  # a written score: its keys press where it is written

    def test_a_wider_view_puts_more_bars_on_a_line(self):
        narrow, _ = self.view(300)
        wide, _ = self.view(1200)
        self.assertGreater(len(narrow.rows), len(wide.rows))
        self.assertGreater(narrow.extent, wide.extent)

    def test_it_is_measured_at_once_and_drawn_a_few_systems_at_a_time_those_in_view_first(self):
        from unittest import mock
        view, _ = self.view(300)
        view.configure(height=60)
        with mock.patch("lyrebot.scoreview.SLICE", 0):  # one system a turn
            view.lay_out()
        self.assertEqual(len(view.notes), 20)  # every note known, where and when
        self.assertEqual(len(view.pending), len(view.rows) - 1)  # but only the first system drawn
        drawn = {int(tag[1:]) for item in view.find_withtag("digit") for tag in view.gettags(item)
                 if tag.startswith("n")}
        first, end = 0, next(entry[3] for entry in view.pending)
        self.assertEqual(drawn, set(range(first, end)))
        view.away = True  # its page turned: nothing more is drawn
        view.draw_some()
        self.assertEqual(len(view.pending), len(view.rows) - 1)
        view.away = False
        view.heard(view.notes[-1].at, follow=False)  # a note not drawn yet is lit once it is
        view.finish()
        self.assertEqual(view.pending, [])
        lit = view.lit[0]
        digits = [item for item in view.find_withtag(f"n{lit}") if view.type(item) == "text"]
        self.assertTrue(digits)
        from lyrebot.theme import ORANGE
        self.assertTrue(all(view.itemcget(item, "fill") == ORANGE for item in digits))

    def test_only_the_curves_of_the_layout_shown_are_kept(self):
        view, _ = self.view(300)
        view.finish()
        kept = len(view.curves)
        for width in (340, 380, 420, 460, 300):
            view.configure(width=width)
            view.lay_out()
            view.finish()
        self.assertEqual(len(view.curves), kept)  # (each picture holds Windows' drawing objects)

    def test_four_bars_a_line_fits_the_widest_line_and_the_tallest_four_lines(self):
        from lyrebot.scoreview import ScoreView
        from lyrebot.theme import Theme
        root = hidden_window(self)
        view = ScoreView(root, Theme(root), lambda index: None)
        text = "speed=60\n" + "a.s.d.f.g.h.j.q.\n" * 5 + "a-s-d-f-\n" * 6 + "^adg-^sfh-q-w-\n" * 7
        view.show(jianpu.sheet_of(text, "十八小节"))  # 18 bars: lines of 4, 4, 4, 4 and 2
        width, height = view.four_bars()
        view.most = 4
        view.configure(width=width)
        view.lay_out()
        self.assertEqual([len(row) for row in jianpu.layout_rows(view.sheet, 20, width / view.scale - 10, 4)],
                         [4, 4, 4, 4, 2])
        self.assertEqual(len(view.rows), 5)
        tallest = max(view.rows[at + 3][1] - view.rows[at][0] for at in range(2))
        fades = 2 * view.theme.px(18)
        self.assertGreaterEqual(height, tallest + fades)
        self.assertLess(height, tallest + fades + 3)  # just fitting
        view.configure(width=width - 30)  # any narrower and a line of four does not fit
        view.lay_out()
        self.assertGreater(len(view.rows), 5)
        short, _ = self.view(300)
        _, all_of_it = short.four_bars()  # four lines or fewer: as tall as all of it
        short.most = 4
        short.configure(width=short.four_bars()[0])
        short.lay_out()
        self.assertEqual(all_of_it, short.extent)

    def test_every_bar_is_numbered_in_gold_and_the_one_heard_in_orange(self):
        from lyrebot.theme import GOLD, ORANGE
        view, _ = self.view(400)
        view.finish()
        numbers = {view.itemcget(item, "text"): item for item in view.find_withtag("number")}
        self.assertEqual(sorted(numbers, key=int), ["1", "2", "3", "4"])
        self.assertTrue(all(view.itemcget(item, "fill") == GOLD for item in numbers.values()))
        view.heard(5.5, follow=False)  # in the second bar
        self.assertEqual(view.itemcget(numbers["2"], "fill"), ORANGE)
        view.heard(12.5, follow=False)
        self.assertEqual((view.itemcget(numbers["2"], "fill"), view.itemcget(numbers["4"], "fill")), (GOLD, ORANGE))
        view.heard(None)
        self.assertTrue(all(view.itemcget(item, "fill") == GOLD for item in numbers.values()))

    def test_a_click_names_the_note_under_it(self):
        from types import SimpleNamespace
        view, clicked = self.view(400)
        index = next(i for i, note in enumerate(view.notes) if note.at == 6.0)
        left, top, right, bottom = view.notes[index].box
        view.click(SimpleNamespace(x=(left + right) / 2, y=(top + bottom) / 2))
        self.assertEqual(clicked, [index])
        view.click(SimpleNamespace(x=-40, y=-40))
        self.assertEqual(clicked, [index])

    def test_a_note_starts_from_the_nearest_key_pressed(self):
        view, _ = self.view(400)
        score = parse_score(SONG)
        presses = [0.02, 1.0, 2.1, 30.0]  # as a recording's keys might be pressed a hair off the page
        view.time(score, 1.0, presses)
        starts = {note.at: note.start for note in view.notes}
        self.assertEqual((starts[0.0], starts[1.0], starts[2.0], starts[3.0]), (0.02, 1.0, 2.1, 3.0))  # 3 s: none near

    def test_the_notes_sounding_light_and_a_held_note_lights_as_the_one_it_goes_on_from(self):
        from lyrebot.scoreview import ScoreView
        from lyrebot.theme import Theme
        root = hidden_window(self)
        view = ScoreView(root, Theme(root), lambda index: None)
        # Two parts: a held 5 beats across the bar line, then s, then a rest; under it z held 7 beats.
        text = "speed=60\na---|z---\n--s-0-|----\n"
        view.show(jianpu.sheet_of(text, "延音"))
        score = parse_score(text)
        view.time(score, 1.0, [0.0, 5.0])

        def note(staff, at, struck=True):
            return next(i for i, n in enumerate(view.notes) if (n.staff, n.at, n.struck) == (staff, at, struck))
        a, z, s = note(0, 0.0), note(1, 0.0), note(0, 5.0)
        note(0, 3.0, struck=False)  # the a's going on, written pale in the second bar
        self.assertEqual(view.sounding(1.0), [a, z])
        self.assertEqual(view.sounding(4.0), [a, z])  # held on into the second bar: lit as where they began
        self.assertEqual(view.sounding(5.5), [s, z])
        self.assertEqual(view.sounding(6.5), [z])  # a rest over the z
        self.assertEqual(view.sounding(7.5), [])
        view.heard(5.5, follow=False)
        self.assertEqual(view.lit, [s, z])
        view.heard(None)
        self.assertEqual(view.lit, [])


if __name__ == "__main__":
    unittest.main()
