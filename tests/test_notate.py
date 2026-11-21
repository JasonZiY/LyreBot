import random
import unittest

from lyrebot.listen import Ear, KEY_AT
from lyrebot.keyboard import SCAN_CODES, STOP_KEY
from lyrebot.notate import NotateError, faithfulness, notate
from lyrebot.score import load_score, parse_score, score_files
from lyrebot.timeline import compile_timeline

try:  # run as part of the tests package, or by unittest discovery from the tests folder
    from .test_arrange import MUSIC
except ImportError:
    from test_arrange import MUSIC


def presses_of(score):
    return sorted((event.at, key) for event in compile_timeline(score).events if event.down for key in event.keys)


def by_hand(presses, seed):
    """As a hand plays: every onset up to ~25 ms early or late, chord keys
    up to 12 ms apart."""
    rng = random.Random(seed)
    onsets = {}
    for at, key in presses:
        onsets.setdefault(round(at, 6), []).append(key)
    played = []
    for at, keys in onsets.items():
        shift = rng.gauss(0, 0.012)
        played += [(at + shift + rng.uniform(0, 0.012), key) for key in keys]
    return sorted(played)


def all_scores():
    return score_files(MUSIC)


class EveryScore(unittest.TestCase):
    def test_every_score_played_comes_back_as_it_was_played(self):
        """Each score in the txt folder, played by LyreBot and by hand, is
        written down and played back: nearly every press comes back within
        50 ms.  This guards the notation against fitting a few songs."""
        exact, hand = {}, {}
        for path in all_scores():
            clean = presses_of(load_score(path))
            exact[path.stem] = notate(clean).faithful
            played = by_hand(clean, path.stem)
            result = notate(played)
            hand[path.stem] = result.faithful
            self.assertAlmostEqual(result.faithful, faithfulness(result.text, played))
        self.assertGreaterEqual(min(exact.values()), 0.98, exact)
        self.assertGreaterEqual(sum(exact.values()) / len(exact), 0.995, exact)
        self.assertGreaterEqual(min(hand.values()), 0.97, hand)
        self.assertGreaterEqual(sum(hand.values()) / len(hand), 0.99, hand)


class Writing(unittest.TestCase):
    def notes(self, text):
        return [line for line in text.splitlines() if line and not line.startswith(("#", "speed=", "roll=", "grace="))]

    def test_steady_eighths_come_back_as_eighths(self):
        presses = [(i * 0.3, "asdfghjq"[i % 8]) for i in range(32)]
        result = notate(presses, "音阶")
        self.assertTrue(result.text.startswith("# 由键盘演奏记录：音阶\n"))
        self.assertIn("speed=100\n", result.text)
        # Where the bar starts is anyone's guess in a plain scale; the notes are eighths throughout.
        self.assertIn("a.s.d.f.g.h.j.q.a.s.d.f.", "".join(self.notes(result.text)).replace("0", ""))
        self.assertEqual(result.faithful, 1.0)
        # The last note is held to the end of its bar, and a bar of rest closes the score.
        self.assertEqual(self.notes(result.text)[-1], "0----")
        self.assertNotIn("0", self.notes(result.text)[-2])

    def test_chords_rolls_and_grace_notes(self):
        beat = 0.6  # 100 BPM
        presses = []
        for bar in range(4):
            start = bar * 4 * beat
            presses += [(start, "z"), (start + 0.008, "d"), (start + 0.004, "g")]  # a chord, not quite together
            presses += [(start + beat + 0.04 * i, key) for i, key in enumerate("qwer")]  # a roll, 40 ms apart
            presses += [(start + 2 * beat, "s"), (start + 2 * beat + 0.045, "a")]  # a grace note
            presses += [(start + 3 * beat, "h")]
        result = notate(presses)
        bars = self.notes(result.text)
        self.assertEqual(bars[0], "zdg-^qwer-s'a-h-")
        self.assertIn("roll=40\n", result.text)
        self.assertIn("grace=45\n", result.text)
        self.assertEqual(result.faithful, 1.0)
        played = presses_of(parse_score(result.text))
        self.assertEqual(sorted(key for _, key in played), sorted(key for _, key in presses))
        for at, key in presses:  # the chord is struck together; everything else as it was played
            self.assertTrue(any(other == key and abs(time - at) <= 0.01 for time, other in played), (at, key))

    def test_a_few_presses_are_written_without_a_beat(self):
        result = notate([(0.0, "a"), (0.5, "s"), (1.25, "d")])
        self.assertIsNone(result.timing)
        self.assertEqual(result.faithful, 1.0)

    def test_nothing_played(self):
        with self.assertRaises(NotateError):
            notate([])
        with self.assertRaises(NotateError):
            notate([(0.0, "1"), (0.1, "k")])  # not lyre keys


class Hearing(unittest.TestCase):
    def test_a_held_key_counts_once(self):
        ear = Ear()
        for now in (1.0, 1.03, 1.06):  # the keyboard repeats a held key
            ear.press(SCAN_CODES["a"], 0x41, False, now)
        ear.release(SCAN_CODES["a"], 0x41)
        ear.press(SCAN_CODES["a"], 0x41, False, 1.5)
        self.assertEqual(ear.presses, [(1.0, "a"), (1.5, "a")])

    def test_shortcuts_other_keys_and_f8(self):
        ear = Ear()
        ear.press(0xA4 and 0x38, 0xA4, False, 0.0)  # Alt
        ear.press(SCAN_CODES["q"], 0x51, False, 0.1)  # Alt+Q is not a note
        ear.release(0x38, 0xA4)
        ear.release(SCAN_CODES["q"], 0x51)
        ear.press(SCAN_CODES["z"], 0x5A, True, 0.2)  # same scan code, but an extended key
        ear.press(0x02, 0x31, False, 0.3)  # the 1 key
        ear.press(SCAN_CODES["w"], 0x57, False, 0.4)
        self.assertEqual(ear.presses, [(0.4, "w")])
        self.assertFalse(ear.stop)
        ear.press(0x42, STOP_KEY, False, 0.5)
        self.assertTrue(ear.stop)

    def test_nothing_is_heard_while_lyrebot_has_the_focus(self):
        focused = [True]
        ear = Ear(lambda: focused[0])
        ear.press(SCAN_CODES["a"], 0x41, False, 0.0)
        ear.release(SCAN_CODES["a"], 0x41)
        focused[0] = False
        ear.press(SCAN_CODES["a"], 0x41, False, 0.5)
        self.assertEqual(ear.presses, [(0.5, "a")])

    def test_every_lyre_key_is_known_by_its_place(self):
        self.assertEqual(sorted(KEY_AT.values()), sorted("qwertyuasdfghjzxcvbnm"))


if __name__ == "__main__":
    unittest.main()
