from fractions import Fraction
from pathlib import Path
import tempfile
import unittest

from lyrebot.score import MAX_FILE_BYTES, Score, ScoreError, load_score, parse_score


class ScoreParserTests(unittest.TestCase):
    def test_exact_punctuation_durations_chords_and_rests(self):
        score = parse_score("speed=120\nhjq-.a.~s~*0*\n")
        self.assertEqual(score.bpm, 120)
        self.assertEqual(score.parts, 1)
        self.assertEqual(score.measures, 1)
        self.assertEqual(score.events[0].keys, ("h", "j", "q"))
        self.assertEqual(
            [event.duration for event in score.events],
            [Fraction(3, 2), Fraction(3, 4), Fraction(3, 8), Fraction(1, 8)],
        )
        self.assertEqual(
            [event.start for event in score.events],
            [Fraction(0), Fraction(3, 2), Fraction(9, 4), Fraction(21, 8)],
        )
        self.assertEqual(score.events[-1].keys, ())
        self.assertEqual(score.total_beats, Fraction(11, 4))
        self.assertEqual(score.duration_seconds, 1.375)

    def test_simultaneous_parts_share_absolute_timeline(self):
        score = parse_score("speed=60\na.s.g-|z--\nf--|x.c.v-\n")
        self.assertEqual(score.total_beats, 4)
        self.assertEqual(score.measures, 2)
        self.assertEqual(score.parts, 2)
        upper = [event for event in score.events if event.part == 0]
        lower = [event for event in score.events if event.part == 1]
        self.assertEqual([event.start for event in upper], [0, Fraction(1, 2), 1, 2])
        self.assertEqual([event.start for event in lower], [0, 2, Fraction(5, 2), 3])
        self.assertEqual([event.keys for event in lower], [("z",), ("x",), ("c",), ("v",)])
        self.assertEqual(score.events, tuple(sorted(score.events, key=lambda event: (event.start, event.part))))

    def test_cross_measure_ties_extend_correct_parts_without_reattack(self):
        score = parse_score("speed=90\nq--|z--\n-.w.|--\n0--|x--\n")
        upper = [event for event in score.events if event.part == 0]
        lower = [event for event in score.events if event.part == 1]
        self.assertEqual(len(score.events), 5)
        self.assertEqual(upper[0].start, 0)
        self.assertEqual(upper[0].duration, Fraction(7, 2))
        self.assertEqual(upper[0].line, 2)
        self.assertEqual(upper[1].start, Fraction(7, 2))
        self.assertEqual(upper[1].line, 3)
        self.assertEqual(lower[0].duration, 4)
        self.assertEqual(lower[1].start, 4)
        self.assertEqual(score.total_beats, 6)

    def test_rest_can_continue_across_multiple_measures(self):
        score = parse_score("speed=100\n0-\n-\n.a.\n")
        self.assertEqual(len(score.events), 2)
        self.assertEqual(score.events[0].keys, ())
        self.assertEqual(score.events[0].duration, Fraction(5, 2))
        self.assertEqual(score.events[1].start, Fraction(5, 2))
        self.assertEqual(score.total_beats, 3)

    def test_exact_tuplets_and_fractional_continuation(self):
        score = parse_score("speed=120\na{1/3}s{1/3}d{1/3}|z-\n{2/3}f{1/3}|x{1}\n")
        upper = [event for event in score.events if event.part == 0]
        self.assertEqual([event.start for event in upper], [0, Fraction(1, 3), Fraction(2, 3), Fraction(5, 3)])
        self.assertEqual(upper[2].duration, 1)
        self.assertEqual(score.total_beats, 2)
        self.assertEqual(score.duration_seconds, 1)

    def test_explicit_integer_duration_and_reduced_fraction(self):
        score = parse_score("speed=80\nas{2}0{2/4}\n")
        self.assertEqual(score.events[0].duration, 2)
        self.assertEqual(score.events[1].duration, Fraction(1, 2))

    def test_comments_whitespace_and_original_line_numbers(self):
        score = parse_score("# title\n\nspeed = 120 # bpm\n\n a - s . 0 . # measure one\n")
        self.assertEqual(score.total_beats, 2)
        self.assertEqual(score.events[0].line, 5)
        self.assertEqual(score.measures, 1)

    def test_utf8_bom_and_unicode_filename(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            path = Path(directory) / "琴仙乐谱_测试.txt"
            path.write_text("speed=99\na-\n", encoding="utf-8-sig")
            score = load_score(path)
        self.assertEqual(score.bpm, 99)
        self.assertEqual(score.events[0].keys, ("a",))
        self.assertEqual(parse_score("\ufeffspeed=99\na-\n"), score)

    def test_tempo_accepts_decimals_and_scientific_notation(self):
        self.assertEqual(parse_score("speed=99.5\na-").bpm, 99.5)
        self.assertEqual(parse_score("speed=1.2e2\na-").bpm, 120)
        self.assertEqual(parse_score("speed=1000\na-").bpm, 1000)

    def test_invalid_tempos(self):
        for tempo in ("", "0", "-1", "0.999", "1e-300", "1000.1", "nan", "NaN", "inf", "1e309", "1e-999", "1_20", "fast", "１２３"):
            with self.subTest(tempo=tempo):
                with self.assertRaisesRegex(ScoreError, "第 1 行"):
                    parse_score(f"speed={tempo}\na-\n")

    def test_missing_header_and_missing_measures(self):
        for source in ("", "# comment only", "a-", "speed=120"):
            with self.subTest(source=source):
                with self.assertRaises(ScoreError):
                    parse_score(source)

    def test_rejects_malformed_tokens(self):
        malformed = (
            "a", "a-s", "A-", "k-", "a#-", "aa-", "asda-", "00-", "0a-",
            "a/", "a{0}", "a{1/0}", "a{0/3}", "a{-1}", "a{1/-3}",
            "a{1.5}", "a{}", "a{1/3", "a{1//3}", "a{1/3}.", "a-{1/3}",
            "a{1}{2}", "a-{-1}", "a-}", "a()", "a{１/３}",
        )
        for measure in malformed:
            with self.subTest(measure=measure):
                with self.assertRaisesRegex(ScoreError, "第 2 行，第 1 声部"):
                    parse_score(f"speed=120\n{measure}\n")

    def test_leading_continuation_requires_predecessor_in_same_part(self):
        for measure in ("-", "{1/3}", "a-|-", "-|a-"):
            with self.subTest(measure=measure):
                with self.assertRaisesRegex(ScoreError, "前面必须有同一声部的音符"):
                    parse_score(f"speed=120\n{measure}\n")

    def test_mismatched_parts_report_line_and_lengths(self):
        with self.assertRaisesRegex(ScoreError, r"第 2 行：.*声部 1 = 1 拍，声部 2 = 2 拍"):
            parse_score("speed=120\na-|z--\n")
        with self.assertRaisesRegex(ScoreError, "第 3 行：应为 2 个声部"):
            parse_score("speed=120\na-|z-\ns-\n")
        with self.assertRaisesRegex(ScoreError, "第 3 行：应为 1 个声部"):
            parse_score("speed=120\na-\ns-|x-\n")

    def test_empty_parts_and_excess_parts_are_rejected(self):
        for measure in ("a-|", "|a-", "a-||z-", "a-|s-|d-"):
            with self.subTest(measure=measure):
                with self.assertRaises(ScoreError):
                    parse_score(f"speed=120\n{measure}\n")

    def test_final_rests_are_preserved(self):
        score = parse_score("speed=120\na-\n0---\n")
        self.assertEqual(score.total_beats, 4)
        self.assertEqual(score.events[-1].keys, ())
        self.assertEqual(score.events[-1].start, 1)
        self.assertEqual(score.events[-1].duration, 3)

    def test_variable_measure_lengths_are_accepted(self):
        score = parse_score("speed=120\na.\ns---\nd-\n")
        self.assertEqual(score.total_beats, Fraction(9, 2))
        self.assertEqual([event.start for event in score.events], [0, Fraction(1, 2), Fraction(7, 2)])

    def test_total_playback_length_is_limited_to_24_hours(self):
        self.assertEqual(parse_score("speed=60\na{86400}\n").duration_seconds, 86400)
        self.assertEqual(parse_score("speed=1\na{1440}\n").duration_seconds, 86400)
        for measure in ("a{86401}", "a{86400}a{1/3}", "a{" + "9" * 1000 + "/3}"):
            with self.subTest(measure=measure[:30]):
                with self.assertRaisesRegex(ScoreError, "第 2 行：.*24 小时"):
                    parse_score(f"speed=60\n{measure}\n")

    def test_fraction_beyond_python_integer_limit_has_helpful_error(self):
        with self.assertRaises(ScoreError) as caught:
            parse_score("speed=60\na{" + "9" * 5000 + "/3}\n")
        self.assertIn("第 2 行", str(caught.exception))
        self.assertIn("过大", str(caught.exception))

    def test_runtime_property_rejects_invalid_manually_constructed_scores(self):
        invalid = (
            Score(float("nan"), (), Fraction(1), 1, 1),
            Score(float("inf"), (), Fraction(1), 1, 1),
            Score(1e-300, (), Fraction(1), 1, 1),
            Score(120, (), Fraction(10 ** 1000), 1, 1),
            Score(120, (), Fraction(-1), 1, 1),
        )
        for score in invalid:
            with self.subTest(bpm=score.bpm):
                with self.assertRaises(ScoreError):
                    _ = score.duration_seconds

    def test_mid_score_tempo_change(self):
        score = parse_score("speed=60\na-s-|z--\nspeed=120\nd-f-|x--\n")
        self.assertEqual(score.bpm, 60)
        self.assertEqual(score.measures, 2)
        self.assertEqual(score.tempo_changes, ((Fraction(2), 120.0),))
        # 2 beats at 60 BPM + 2 beats at 120 BPM.
        self.assertEqual(score.duration_seconds, 3.0)

    def test_tempo_change_before_first_measure_replaces_header(self):
        score = parse_score("speed=60\nspeed=90\na-\n")
        self.assertEqual(score.bpm, 90)
        self.assertEqual(score.tempo_changes, ())

    def test_invalid_mid_score_tempo_reports_line(self):
        with self.assertRaisesRegex(ScoreError, "第 3 行"):
            parse_score("speed=60\na-\nspeed=0\ns-\n")

    def test_file_size_and_encoding_errors_are_helpful(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            path = Path(directory) / "score.txt"
            path.write_bytes(b"#" * (MAX_FILE_BYTES + 1))
            with self.assertRaisesRegex(ScoreError, "2 MiB"):
                load_score(path)
            path.write_bytes(b"speed=60\na-\n\xff")
            with self.assertRaisesRegex(ScoreError, "UTF-8"):
                load_score(path)


if __name__ == "__main__":
    unittest.main()
