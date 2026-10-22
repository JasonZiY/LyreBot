from fractions import Fraction as F
from pathlib import Path
import math
import unittest

from lyrebot.score import ScoreError, Strike, load_score, parse_score

MUSIC = Path(__file__).resolve().parent.parent / "music"


def notes(text, part=0):
    """(start, duration, keys) of the pitched events in one part."""
    return [(e.start, e.duration, e.keys) for e in parse_score(text).events if e.part == part and e.strikes]


class Durations(unittest.TestCase):
    def test_symbols_add_up(self):
        self.assertEqual(notes("speed=60\na-s.d~f*g--h-.j.~*"), [
            (0, 1, ("a",)), (1, F(1, 2), ("s",)), (F(3, 2), F(1, 4), ("d",)), (F(7, 4), F(1, 8), ("f",)),
            (F(15, 8), 2, ("g",)), (F(31, 8), F(3, 2), ("h",)), (F(43, 8), F(7, 8), ("j",)),
        ])

    def test_braces_are_exact_and_mix_with_symbols(self):
        self.assertEqual(notes("speed=60\na{1/3}s{2}d-{1/3}"),
                         [(0, F(1, 3), ("a",)), (F(1, 3), 2, ("s",)), (F(7, 3), F(4, 3), ("d",))])

    def test_chords_and_rests(self):
        score = parse_score("speed=60\nadg-0.q.")
        self.assertEqual([(e.start, e.keys) for e in score.events], [(0, ("a", "d", "g")), (1, ()), (F(3, 2), ("q",))])

    def test_leading_duration_extends_previous_note_across_lines(self):
        score = parse_score("speed=60\na-s-|z--\n--d-|---")
        self.assertEqual([(e.start, e.duration, e.keys) for e in score.events if e.part == 0],
                         [(0, 1, ("a",)), (1, 3, ("s",)), (4, 1, ("d",))])
        self.assertEqual([(e.duration, e.keys) for e in score.events if e.part == 1], [(5, ("z",))])
        self.assertEqual(score.total_beats, 5)


class Groups(unittest.TestCase):
    def test_triplet_fits_into_the_group_duration(self):
        self.assertEqual(notes("speed=60\n(a.s.d.)-f-"),
                         [(0, F(1, 3), ("a",)), (F(1, 3), F(1, 3), ("s",)), (F(2, 3), F(1, 3), ("d",)), (1, 1, ("f",))])

    def test_uneven_and_nested_groups(self):
        self.assertEqual(notes("speed=60\n(a-s.)-"), [(0, F(2, 3), ("a",)), (F(2, 3), F(1, 3), ("s",))])
        self.assertEqual(notes("speed=60\n(a-(s.d.f.)-)-"),
                         [(0, F(1, 2), ("a",)), (F(1, 2), F(1, 6), ("s",)), (F(2, 3), F(1, 6), ("d",)),
                          (F(5, 6), F(1, 6), ("f",))])

    def test_quintuplet_with_rest_and_chord(self):
        found = notes("speed=60\n(a~s~0~dg~h~)..")
        self.assertEqual([start for start, _, _ in found], [0, F(1, 5), F(3, 5), F(4, 5)])
        self.assertEqual(found[2][2], ("d", "g"))


class Ornaments(unittest.TestCase):
    def test_roll_spreads_keys_in_written_order(self):
        event = parse_score("speed=60\n^adg-").events[0]
        self.assertEqual(event.strikes, (Strike(("a",), 0), Strike(("d",), 30), Strike(("g",), 60)))
        down = parse_score("speed=60\nroll=50\n^gda-").events[0]
        self.assertEqual([(s.keys, s.delay_ms) for s in down.strikes], [(("g",), 0), (("d",), 50), (("a",), 100)])

    def test_grace_notes_come_first_and_delay_the_main_note(self):
        event = parse_score("speed=60\ns'a-").events[0]
        self.assertEqual(event.strikes, (Strike(("s",), 0), Strike(("a",), 60)))
        event = parse_score("speed=60\ngrace=40\nd's'^adg-").events[0]
        self.assertEqual([(s.keys, s.delay_ms) for s in event.strikes],
                         [(("d",), 0), (("s",), 40), (("a",), 80), (("d",), 110), (("g",), 140)])
        self.assertEqual(event.duration, 1)


class Tempo(unittest.TestCase):
    def test_jump_between_measures(self):
        score = parse_score("speed=60\na-\nspeed=120\na-")
        self.assertEqual(score.tempo_changes, 1)
        self.assertAlmostEqual(score.duration_seconds, 1.5)

    def test_glide_reaches_target_at_next_mark(self):
        score = parse_score("speed=120\na-a-\nspeed=>60\na-a-\nspeed=120\na-")
        tempo = score.tempo_map()
        self.assertAlmostEqual(tempo.seconds(F(2)), 1.0)
        # 120 -> 60 over two beats: integral of 60 / bpm.
        glide = 60 * 2 / (60 - 120) * math.log(60 / 120)
        self.assertAlmostEqual(tempo.seconds(F(4)), 1.0 + glide)
        self.assertAlmostEqual(tempo.seconds(F(5)), 1.0 + glide + 0.5)
        self.assertLess(tempo.seconds(F(3)) - 1.0, glide / 2)  # first half of a slowdown is quicker

    def test_glide_with_explicit_start_runs_to_the_end(self):
        score = parse_score("speed=100\na-\nspeed=80>40\na-a-")
        self.assertAlmostEqual(score.duration_seconds, 0.6 + 60 * 2 / -40 * math.log(40 / 80))

    def test_speed_factor_scales_everything(self):
        score = parse_score("speed=120\na-\nspeed=>60\na-a-")
        self.assertAlmostEqual(score.tempo_map(2.0).seconds(score.total_beats), score.duration_seconds / 2)

    def test_later_mark_at_same_place_wins(self):
        self.assertEqual(parse_score("speed=60\nspeed=90\na-").bpm, 90)


class Errors(unittest.TestCase):
    def check(self, text, fragment):
        with self.assertRaises(ScoreError) as caught:
            parse_score(text)
        self.assertIn(fragment, str(caught.exception))

    def test_messages_name_the_problem(self):
        self.check("a-", "speed=")
        self.check("speed=60\na", "缺少时值")
        self.check("speed=60\naa-", "重复")
        self.check("speed=60\nk-", "无法识别")
        self.check("speed=60\na-|a--", "长度不一致")
        self.check("speed=60\na-|a-\na-", "应有 2 个声部")
        self.check("speed=60\n--", "没有可延长")
        self.check("speed=60\n(a.s.", "右括号")
        self.check("speed=60\n(a.s.)", "括号组后面")
        self.check("speed=60\na-)", "多余")
        self.check("speed=60\n^a-", "琶音")
        self.check("speed=60\ns'0-", "倚音")
        self.check("speed=60\na{0}", "大于 0")
        self.check("speed=60\na{1.5}", "分数")
        self.check("speed=0\na-", "1 到 1000")
        self.check("speed=>60\na-", "前面需要")
        self.check("speed=60\nvolume=3\na-", "未知的指令")
        self.check("speed=60\n", "没有小节")

    def test_errors_show_line_and_part(self):
        self.check("speed=60\n\n# note\na-|z-x\n", "第 4 行第 2 声部")


class Files(unittest.TestCase):
    def test_every_bundled_score_loads(self):
        files = sorted(MUSIC.glob("*.txt"))
        self.assertTrue(files)
        for path in files:
            with self.subTest(path.name):
                self.assertGreater(load_score(path).notes, 0)

    def test_comments_whitespace_and_bom(self):
        score = parse_score("﻿# title\nspeed = 60\n a - s . | z - x . # comment\n")
        self.assertEqual(score.parts, 2)
        self.assertEqual(score.total_beats, F(3, 2))


if __name__ == "__main__":
    unittest.main()
