from collections import Counter
import random
import unittest

from lyrebot import beats
from lyrebot.arrange import arrange
from lyrebot.midi import parse_midi
from lyrebot.score import load_score, parse_score

try:  # run as part of the tests package, or by unittest discovery from the tests folder
    from .test_arrange import PITCH_OF, notes, signature, smf, song, tempo, written_scores
except ImportError:
    from test_arrange import PITCH_OF, notes, signature, smf, song, tempo, written_scores


def bar_starts(score):
    firsts = {}
    for event in score.events:
        firsts.setdefault(event.line, event.start)
    return sorted(set(firsts.values()))


def performed(score, seed, jitter=0.012):
    """The score as a transcription would give it: real seconds, a little
    timing slop, written against 120 BPM with no time signature."""
    clock = score.tempo_map()
    rng = random.Random(seed)
    shift, parts = {}, {0: [], 1: []}
    for event in score.events:
        if not event.strikes:
            continue
        begin = clock.seconds(event.start) + shift.setdefault(event.start, rng.gauss(0, jitter))
        stop = clock.seconds(event.start + event.duration)
        for strike in event.strikes:
            for key in strike.keys:
                at = max(0.0, begin + strike.delay_ms / 1000 + rng.gauss(0, 0.003))
                parts[min(event.part, 1)].append((round(at * 960), round(max(stop, at + 0.05) * 960), PITCH_OF[key]))
    return parse_midi(smf([[]] + [notes(channel, sorted(found)) for channel, found in parts.items() if found]))


def bars_found(score, midi):
    """(found bar / true bar, share of found bar lines on a true one)."""
    retimed, timing = beats.find_beats(midi)
    clock = score.tempo_map()
    starts = bar_starts(score)
    length = Counter(b - a for a, b in zip(starts, starts[1:])).most_common(1)[0][0]
    seconds, original = beats._seconds_of(retimed), beats._seconds_of(midi)
    first_tick = min(note.start for note in retimed.notes)
    first_time = min(original(note.start) for note in midi.notes)
    step = timing.numerator * 4 * beats.PPQ // timing.denominator
    lines = [seconds(tick) - seconds(first_tick) + first_time
             for tick in range(0, max(note.start for note in retimed.notes) + 1, step)]
    truth = [clock.seconds(start) for start in starts]
    on = sum(1 for line in lines if min(abs(line - true) for true in truth) < 0.08) / len(lines)
    true_bar = clock.seconds(starts[0] + length) - clock.seconds(starts[0])
    return timing.numerator * 4 / timing.denominator * 60 / timing.bpm / true_bar, on


class FindingTheBeat(unittest.TestCase):
    def test_hand_written_scores_get_their_bars_back(self):
        """Performed with 12 ms of slop, most scores get their exact bar lines
        back, and the rest bars of double or half the length, still on the
        original lines.  (绵远的诀别, with four tempo changes in 80 seconds, is
        the one allowed to miss.)"""
        right = close = 0
        for path in written_scores():
            score = load_score(path)
            ratio, on = bars_found(score, performed(score, path.stem))
            right += abs(ratio - 1) < 0.05 and on > 0.8
            close += abs(ratio - 2) < 0.1 or abs(ratio - 0.5) < 0.05
        self.assertGreaterEqual(right, 14)
        self.assertGreaterEqual(right + close, 16)

    def test_steady_four_four_with_a_pickup(self):
        groove = []
        for bar in range(12):
            for beat in range(4):
                at = 0.5 + (bar * 4 + beat) * 60 / 90  # 90 BPM, after a half-second pickup
                groove.append((at, at + 0.3, 72 + (0, 4, 7, 4)[beat]))
                groove.append((at + 1 / 3, at + 0.6, 67))  # eighths in between
            groove.append((0.5 + bar * 4 * 60 / 90, 0.5 + (bar * 4 + 4) * 60 / 90, 36))  # a bass note per bar
        groove.append((0.5 - 60 / 90 / 2, 0.5, 79))  # the pickup
        midi = parse_midi(smf([[], notes(0, [(round(s * 960), round(e * 960), p) for s, e, p in groove])]))
        retimed, timing = beats.find_beats(midi)
        self.assertAlmostEqual(timing.bpm, 90, delta=1)
        self.assertTrue(timing.steady)
        self.assertEqual(timing.signature, "4/4")
        bass = sorted(note.start for note in retimed.notes if note.pitch == 36)
        self.assertEqual({tick % (4 * beats.PPQ) for tick in bass}, {0})  # every bass note on a downbeat

    def test_waltz_and_six_eight(self):
        for name, wanted in (("风息的离别诗", "3/4"), ("夜诞的花冠", "6/8")):
            with self.subTest(name):
                score = load_score(MUSIC / f"{name}.txt")
                self.assertEqual(beats.find_beats(performed(score, name))[1].signature, wanted)

    def test_too_few_notes(self):
        self.assertIsNone(beats.find_beats(parse_midi(song((0, [(0, 1, 60), (1, 2, 62)])))))


class WhenToFindIt(unittest.TestCase):
    def setUp(self):
        self.score = load_score(MUSIC / "风息的离别诗.txt")

    def test_only_files_without_a_beat_of_their_own(self):
        self.assertIsNotNone(arrange(performed(self.score, "a")).timing)
        written = parse_midi(song((0, [(i, i + 1, 72 + i % 5) for i in range(16)])))
        self.assertIsNone(arrange(written).timing)  # on a written grid already

    def test_a_time_signature_means_the_file_knows_its_beat(self):
        midi = performed(self.score, "b")
        with_meter = parse_midi(smf([[(0, -1, tempo(110)), (0, -1, signature(3, 4))],
                                     notes(0, [(n.start, n.end, n.pitch) for n in midi.notes])]))
        self.assertIsNone(arrange(with_meter).timing)
        self.assertIsNotNone(arrange(with_meter, beats="notes").timing)
        self.assertIsNone(arrange(midi, beats="midi").timing)

    def test_a_recording_keeps_the_accompaniment_it_was_played_with(self):
        result = arrange(performed(self.score, "d"))
        self.assertIn("第 2 声部是原曲伴奏弹的音", result.text)
        written = parse_midi(song((0, [(i, i + 1, 72 + i % 5) for i in range(16)]), (1, [(0, 16, 48)])))
        self.assertIn("第 2 声部是按和弦重写的分解和弦", arrange(written).text)
        source = {PITCH_OF[key] % 12 for event in self.score.events if event.part == 1 for key in event.keys}
        kept = {PITCH_OF[key] % 12 for event in parse_score(result.text).events if event.part == 1
                for key in event.keys}
        self.assertLessEqual(kept, source)

    def test_the_bar_can_be_fixed_by_hand(self):
        result = arrange(performed(self.score, "c"), meter=4)
        self.assertEqual(result.timing.signature, "4/4")
        self.assertIn("4/4", result.text)
        self.assertIn("节拍是从音符推断的", result.text)
        parse_score(result.text)


MUSIC = written_scores()[0].parent

if __name__ == "__main__":
    unittest.main()
