from fractions import Fraction
from pathlib import Path
import shutil
import tempfile
import unittest

from lyrebot.jianpu import _main, describe, export, render_html, sheet_of
from lyrebot.score import load_score, score_files

TXT = Path(__file__).resolve().parent.parent / "txt"


def bars(text, name="曲子"):
    """Each bar's staves as text (see ``describe``)."""
    sheet = sheet_of(text, name)
    return [" | ".join(describe(staff) for staff in bar.staves) for bar in sheet.bars]


def written_length(symbol):
    value = Fraction(1, 2 ** symbol.lines) * (Fraction(3, 2) if symbol.dot else 1) + symbol.dashes
    return value * (symbol.step if symbol.tuplet is not None else 1)


class Notation(unittest.TestCase):
    def test_eighths_beam_by_beat_and_a_syncopation_stays_one_note(self):
        self.assertEqual(bars("speed=100\nq-q.q-g.q.w.|zb----\n"), ["1' 1'_ 1' 5_ 1'_ 2'_ | 1,+5, - - -"])

    def test_dotted_and_held_notes(self):
        self.assertEqual(bars("speed=100\nq-.e.t--|z----\n"), ["1'. 3'_ 5' - | 1, - - -"])

    def test_a_note_held_over_the_bar_line_goes_on_grey_under_a_tie(self):
        self.assertEqual(bars("speed=100\na--s--|z----\n--d--|x----\n"),
                         ["1 - 2 - | 1, - - -", "~2 - 3 - | 2, - - -"])

    def test_a_note_cut_at_a_beat_is_tied_into_the_next(self):
        sheet = sheet_of("speed=100\na-.s-.d-|z----\n", "x")
        self.assertEqual(describe(sheet.bars[0].staves[0]), "1. 2_ ~2 3")

    def test_rests_are_one_a_beat(self):
        self.assertEqual(bars("speed=100\n0--a-.s.|z----\n"), ["0 0 1. 2_ | 1, - - -"])

    def test_triplets_are_marked_and_written_as_eighths(self):
        sheet = sheet_of("speed=100\n(a.s.d.)-f-g--|z----\n", "x")
        self.assertEqual(describe(sheet.bars[0].staves[0]), "1_ 2_ 3_ 4 5 -")
        self.assertEqual(sheet.bars[0].tuplets[0], [(0, 1)])

    def test_a_triplet_whose_last_note_is_held_is_tied_at_its_edge(self):
        sheet = sheet_of("speed=100\n(n.c.n.)-+---|z----\n", "x")
        self.assertEqual(describe(sheet.bars[0].staves[0]), "6,_ 3,_ 6,_ ~6, - -")
        self.assertEqual(sheet.bars[0].tuplets[0], [(0, 1)])

    def test_quarter_triplets_span_two_beats(self):
        sheet = sheet_of("speed=100\n(a-s-d-)--f--|z----\n", "x")
        self.assertEqual(describe(sheet.bars[0].staves[0]), "1 2 3 4 -")
        self.assertEqual(sheet.bars[0].tuplets[0], [(0, 2)])

    def test_grace_notes_and_rolls(self):
        symbols = sheet_of("speed=100\ns'a-^gda-0--|z----\n", "x").bars[0].staves[0]
        self.assertEqual(symbols[0].graces, (("s",),))
        self.assertEqual(symbols[1].keys, ("a", "d", "g"))
        self.assertEqual(symbols[1].roll, -1)
        self.assertEqual(sheet_of("speed=100\n^adg----|z----\n", "x").bars[0].staves[0][0].roll, 1)

    def test_six_eight_beams_in_threes(self):
        sheet = sheet_of("# 6/8 拍\nspeed=100\na.s.d.f-g.|z---\n", "x")
        self.assertEqual(sheet.meter, (6, 8))
        first, second = sheet.bars[0].staves[0][:3], sheet.bars[0].staves[0][3:]
        self.assertEqual(describe(first) + " / " + describe(second), "1_ 2_ 3_ / 4 5_")

    def test_a_bar_of_another_length_shows_its_meter(self):
        sheet = sheet_of("speed=100\na----|z----\na--|z--\na----|z----\na----|z----\n", "x")
        self.assertEqual([bar.meter for bar in sheet.bars], [None, (2, 4), (4, 4), None])

    def test_tempo_changes_are_printed_where_they_happen(self):
        sheet = sheet_of("speed=96\na----|z----\nspeed=>72\na----|z----\n", "x")
        self.assertEqual(sheet.tempo, "♩=96")
        self.assertEqual(sheet.bars[1].tempo, "♩=96→72")


class ReadingCleanup(unittest.TestCase):
    RECORDING = "# 由 MIDI 自动编配：曲子-录音.mid\n# 拍号 4/4\n"

    def test_a_recording_is_written_on_sixteenths_and_one_tempo(self):
        text = self.RECORDING + "speed=100\na{7/8}s{9/8}d--|z----\nspeed=110\na----|z----\n"
        sheet = sheet_of(text, "曲子-录音")
        self.assertEqual(sheet.title, "曲子")
        self.assertEqual(describe(sheet.bars[0].staves[0]), "1 2 3 -")
        self.assertEqual(sheet.tempo, "♩≈100")
        self.assertIsNone(sheet.bars[1].tempo)
        self.assertIn("节奏为读谱整理过，演奏以曲谱为准", sheet.notes)

    def test_a_note_squeezed_out_becomes_a_grace_note(self):
        symbols = sheet_of(self.RECORDING + "speed=100\na-s*d{7/8}f--|z----\n", "曲子-录音").bars[0].staves[0]
        self.assertEqual(describe(symbols), "1 3 4 -")
        self.assertEqual(symbols[1].graces, (("s",),))

    def test_triplets_the_arranger_wrote_are_kept(self):
        sheet = sheet_of(self.RECORDING + "speed=100\n(a.s.d.)-f---|z----\n", "曲子-录音")
        self.assertEqual(describe(sheet.bars[0].staves[0]), "1_ 2_ 3_ 4 - -")

    def test_a_hand_written_score_is_left_as_written(self):
        sheet = sheet_of("speed=100\na{7/8}s{9/8}d--|z----\n", "曲子")
        self.assertNotIn("节奏为读谱整理过，演奏以曲谱为准", sheet.notes)
        self.assertEqual(sum(written_length(s) for s in sheet.bars[0].staves[0]), 4)
        self.assertIn(Fraction(7, 8), [s.start for s in sheet.bars[0].staves[0] if s.struck])

    def test_keys_played_as_one_part_split_into_two_hands(self):
        text = "# 由键盘演奏记录：曲子-演奏\nspeed=100\nzq-w-e-r-\nb.e.w.q.t--\n"
        sheet = sheet_of(text, "曲子-演奏")
        self.assertEqual(sheet.staves, 2)
        self.assertEqual([describe(staff) for staff in sheet.bars[0].staves], ["1' 2' 3' 4'", "1, - - -"])
        self.assertEqual([describe(staff) for staff in sheet.bars[1].staves], ["0_ 3'_ 2'_ 1'_ 5' -", "5, - - -"])


class EveryScore(unittest.TestCase):
    def test_every_score_writes_out_whole_and_true(self):
        """Every bar's symbols fill it exactly, each written value is its length, and a
        score not tidied for reading strikes each key when the score does (a MIDI file's
        ornament may be written a little off the grid it misses)."""
        paths = score_files(TXT)
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path.stem):
                sheet = sheet_of(path.read_text(encoding="utf-8"), path.stem)
                for bar in sheet.bars:
                    for staff in bar.staves:
                        at = Fraction(0)
                        for symbol in staff:
                            self.assertEqual(symbol.start, at, f"bar {bar.number}")
                            self.assertEqual(written_length(symbol), symbol.length, f"bar {bar.number}")
                            at += symbol.length
                        self.assertEqual(at, bar.length, f"bar {bar.number}")
                if sheet.kind in ("hand", "midi"):
                    score = load_score(path)
                    for part in range(score.parts):
                        wanted = sorted((e.start, _main(e)[0]) for e in score.events if e.part == part and e.strikes)
                        written = sorted((bar.start + s.start, s.keys) for bar in sheet.bars
                                         for s in bar.staves[part] if s.struck and s.keys)
                        self.assertEqual([keys for _, keys in written], [keys for _, keys in wanted])
                        slack = 0 if sheet.kind == "hand" else Fraction(1, 16)
                        self.assertLessEqual(max(abs(a[0] - b[0]) for a, b in zip(written, wanted)), slack)
                html = render_html(sheet)
                self.assertIn("<svg", html)


class Export(unittest.TestCase):
    def test_export_writes_a_page_per_sheet(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        score = folder / "小曲.txt"
        score.write_text("speed=90\n" + "a-s-d-f-|z----\n" * 40, encoding="utf-8")
        target = export(score, folder / "out" / "小曲.html")
        html = target.read_text(encoding="utf-8")
        self.assertIn("<title>小曲 · 诗琴简谱</title>", html)
        self.assertGreaterEqual(html.count("<svg"), 1)
        self.assertIn("1=C", html)


if __name__ == "__main__":
    unittest.main()
