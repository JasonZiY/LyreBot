from collections import Counter
import math
import random
import unittest

from lyrebot import beats
from lyrebot.arrange import arrange
from lyrebot.midi import parse_midi
from lyrebot.score import find_score, load_score, parse_score

try:  # run as part of the tests package, or by unittest discovery from the tests folder
    from .test_arrange import MUSIC, PITCH_OF, name as track_name, notes, signature, smf, song, tempo, written_scores
except ImportError:
    from test_arrange import MUSIC, PITCH_OF, name as track_name, notes, signature, smf, song, tempo, written_scores


def bar_starts(score):
    firsts = {}
    for event in score.events:
        firsts.setdefault(event.line, event.start)
    return sorted(set(firsts.values()))


def performed(score, seed, jitter=0.012, staccato=False, transpose=0, one_part=False):
    """The score as a transcription would give it: real seconds, a little
    timing slop, written against 120 BPM with no time signature.  Optionally
    with a short melody, in another key, or both voices in one part as a
    piano transcription gives them."""
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
                end = max(stop, at + 0.05)
                if staccato and event.part == 0:
                    end = at + min(0.2, max(0.08, 0.4 * (end - at)))
                part = 0 if one_part else min(event.part, 1)
                parts[part].append((round(at * 960), round(end * 960), PITCH_OF[key] + transpose))
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

    def test_a_given_beat_is_tracked(self):
        """A tune in eighth-note triplets at 100 BPM reads as well as 6/8 at
        75; a period given is tracked instead of the one the notes suggest."""
        played = []
        for bar in range(16):
            for beat in range(4):
                at = 0.5 + (bar * 4 + beat) * 0.6
                played += [(at + k * 0.2, at + k * 0.2 + 0.15, 72 + (0, 4, 7)[k] + beat % 2 * 2) for k in range(3)]
            played.append((0.5 + bar * 2.4, 0.5 + bar * 2.4 + 2.4, 36))
        midi = parse_midi(smf([[], notes(0, [(round(s * 960), round(e * 960), p) for s, e, p in played])]))
        self.assertAlmostEqual(beats.find_beats(midi)[1].bpm, 75, delta=1)
        given = beats.find_beats(midi, period=0.6)[1]
        self.assertAlmostEqual(given.bpm, 100, delta=1)
        self.assertEqual(given.signature, "4/4")

    def test_waltz_and_six_eight(self):
        for name, wanted in (("风息的离别诗", "3/4"), ("夜诞的花冠", "6/8")):
            with self.subTest(name):
                score = load_score(find_score(MUSIC, name))
                self.assertEqual(beats.find_beats(performed(score, name))[1].signature, wanted)

    def test_too_few_notes(self):
        self.assertIsNone(beats.find_beats(parse_midi(song((0, [(0, 1, 60), (1, 2, 62)])))))


def groove(bars=40, bpm=90.0, pause_at=None, pause=0.0, drift=0.0, period=20.0):
    """A clean 4/4 groove (a tune in quarters, eighths between, a bass note
    per bar) as a transcription writes it, with a pause before bar
    ``pause_at`` or a tempo swaying ``drift`` (relative) every ``period``
    seconds; and the times of its onsets."""
    def when(beat):
        time = 0.5 + beat * 60 / bpm
        time += drift * period / (2 * math.pi) * math.sin(2 * math.pi * time / period)
        return time + (pause if pause_at is not None and beat >= pause_at * 4 else 0.0)
    played = []
    for bar in range(bars):
        for beat in range(4):
            at = bar * 4 + beat
            played.append((when(at), when(at) + 0.3, 72 + (0, 4, 7, 4)[beat]))
            played.append((when(at + 0.5), when(at + 0.5) + 0.2, 67))
        played.append((when(bar * 4), when(bar * 4 + 4), 36))
    midi = parse_midi(smf([[], notes(0, [(round(s * 960), round(e * 960), p) for s, e, p in played])]))
    return midi, sorted(start for start, _, _ in played)


class SteadyStretches(unittest.TestCase):
    def played_back(self, retimed, times):
        """How far, at most, the rewritten notes fall from when they were played."""
        seconds = beats._seconds_of(retimed)
        back = sorted(seconds(note.start) for note in retimed.notes)
        return max(abs(a - back[0] + times[0] - b) for a, b in zip(back, times))

    def test_a_steady_piece_that_stops_once_keeps_its_tempo_on_either_side(self):
        midi, times = groove(pause_at=20, pause=0.5)
        self.assertFalse(beats.find_beats(midi)[1].steady)  # as a whole it is not one tempo
        retimed, timing = beats.find_beats(midi, playback=True)
        self.assertEqual(timing.stretches, 2)
        self.assertAlmostEqual(timing.bpm, 90, delta=1)
        self.assertLessEqual(len({tempo for _, tempo in retimed.tempos}), 3)  # 90, and what the stop takes
        self.assertLess(self.played_back(retimed, times), 0.02)

    def test_a_swaying_tempo_is_still_followed_beat_by_beat(self):
        midi, times = groove(drift=0.06)
        retimed, timing = beats.find_beats(midi, playback=True)
        self.assertFalse(timing.steady)
        self.assertEqual(timing.stretches, 0)
        self.assertLess(self.played_back(retimed, times), 0.02)

    def test_one_tempo_is_still_one_tempo(self):
        midi, _ = groove()
        self.assertEqual(beats.find_beats(midi, playback=True)[1], beats.find_beats(midi)[1])


class WhenToFindIt(unittest.TestCase):
    def setUp(self):
        self.score = load_score(find_score(MUSIC, "风息的离别诗"))

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

    def test_a_tune_split_between_two_guessed_instruments_stays_one_melody(self):
        """A transcription hears one tune partly as a flute and partly as a
        guitar; the melody is still the whole tune, and the bass stays under it."""
        tune = [72 + (0, 2, 4, 5, 7, 5, 4, 2)[index % 8] + 12 * (index % 16 >= 8) for index in range(48)]
        beat = 60 / 84
        flute = [(index, pitch) for index, pitch in enumerate(tune) if index // 4 % 2 == 0]
        guitar = [(index, pitch) for index, pitch in enumerate(tune) if index // 4 % 2 == 1]

        def track(label, channel, items, length=0.6):
            return [(0, 0, track_name(label))] + notes(channel, [(round((0.3 + at * beat) * 960),
                                                            round((0.3 + at * beat + length * beat) * 960), pitch)
                                                           for at, pitch in items])
        bass = [(index * 2, 48 + (0, 7, 5, 7)[index % 4]) for index in range(24)]
        midi = parse_midi(smf([[], track("flutes", 0, flute), track("acoustic guitar", 1, guitar),
                               track("electric bass", 2, bass, 1.8)]))
        result = arrange(midi)
        self.assertIsNotNone(result.timing)
        self.assertEqual({part.name: part.role for part in result.parts},
                         {"flutes": "旋律", "acoustic guitar": "旋律", "electric bass": "伴奏"})
        melody = [max(PITCH_OF[key] for key in event.keys) % 12
                  for event in parse_score(result.text).events if event.part == 0 and event.strikes]
        self.assertGreaterEqual(common_length([pitch % 12 for pitch in tune], melody), 0.95 * len(tune))

    def test_the_bar_can_be_fixed_by_hand(self):
        result = arrange(performed(self.score, "c"), meter=4)
        self.assertEqual(result.timing.signature, "4/4")
        self.assertIn("4/4", result.text)
        self.assertIn("节拍是从音符推断的", result.text)
        parse_score(result.text)


def common_length(a, b):
    """Length of the longest common subsequence."""
    previous = [0] * (len(b) + 1)
    for item in a:
        current = [0]
        for index, other in enumerate(b):
            current.append(previous[index] + 1 if item == other else max(previous[index + 1], current[index]))
        previous = current
    return previous[-1]


def tune(score):
    """(seconds, pitch class) of the top note of every melody event."""
    clock = score.tempo_map()
    return [(clock.seconds(event.start), max(PITCH_OF[key] for key in event.keys) % 12)
            for event in score.events if event.part == 0 and event.strikes]


class EverySong(unittest.TestCase):
    def test_every_score_played_on_one_piano_comes_back_in_key_and_on_time(self):
        """Each hand-written score, played with a short melody, in another key
        and with both hands in one part, as a piano recording transcribes:
        the melody comes back in order on the same white keys, and the score
        keeps time with the performance.  This guards the arranger's rules
        against fitting a few songs and failing the rest.  (绵远的诀别 changes
        tempo three times in ten seconds; the beat is lost for a few bars there,
        so a song may fall to 85% on time while the songs on average keep 95%.)

        Each song must keep its tune as it is heard, the top note at every
        moment whichever voice plays it; the melody voice must keep it on
        average.  皎洁的笑颜 alternates its tune with an inner 5 every eighth,
        and the arranger plays those 5s in the second voice: the same keys at
        the same times, but only 84% of the tune in the melody voice."""
        found, top, on_time = {}, {}, {}
        for path in written_scores():
            score = load_score(path)
            transpose = random.Random(path.stem).randint(-5, 6)
            midi = performed(score, path.stem, staccato=True, transpose=transpose, one_part=True)
            arranged = parse_score(arrange(midi).text)
            answer, melody = tune(score), tune(arranged)
            found[path.stem] = common_length([pc for _, pc in answer], [pc for _, pc in melody]) / len(answer)
            tops: dict = {}
            for event in arranged.events:
                if event.strikes:
                    tops[event.start] = max(tops.get(event.start, 0), max(PITCH_OF[key] for key in event.keys))
            top[path.stem] = common_length([pc for _, pc in answer], [tops[at] % 12 for at in sorted(tops)]) / len(answer)
            clock = arranged.tempo_map()
            heard = sorted((clock.seconds(event.start), PITCH_OF[key] % 12)
                           for event in arranged.events if event.strikes for key in event.keys)
            offset = heard[0][0] - min(score.tempo_map().seconds(event.start)
                                       for event in score.events if event.strikes)
            on_time[path.stem] = sum(1 for time, pc in answer
                                     if any(abs(at - offset - time) <= 0.1 and other == pc for at, other in heard)
                                     ) / len(answer)
        self.assertGreaterEqual(min(top.values()), 0.85, top)
        self.assertGreaterEqual(sum(top.values()) / len(top), 0.97, top)
        self.assertGreaterEqual(sum(found.values()) / len(found), 0.95, found)
        self.assertGreaterEqual(min(on_time.values()), 0.85, on_time)
        self.assertGreaterEqual(sum(on_time.values()) / len(on_time), 0.95, on_time)


if __name__ == "__main__":
    unittest.main()
