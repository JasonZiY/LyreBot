import threading
import unittest

from lyrebot.keyboard import Keyboard, KeyboardError, SCAN_CODES
from lyrebot.player import play
from lyrebot.score import parse_score
from lyrebot.timeline import KeyEvent, Timeline, compile_timeline


def presses(text, speed=1.0):
    return [(round(e.at, 6), e.keys) for e in compile_timeline(parse_score(text), speed).events if e.down]


def releases(text):
    return [(round(e.at, 6), e.keys) for e in compile_timeline(parse_score(text)).events if not e.down]


class Taps(unittest.TestCase):
    def test_short_taps_on_the_beat(self):
        self.assertEqual(presses("speed=120\na-s.d."), [(0, ("a",)), (0.5, ("s",)), (0.75, ("d",))])
        self.assertEqual(releases("speed=120\na-s.d."), [(0.035, ("a",)), (0.535, ("s",)), (0.785, ("d",))])

    def test_fast_repeats_release_halfway(self):
        self.assertEqual(releases("speed=600\na*a*a*"), [(0.00625, ("a",)), (0.01875, ("a",)), (0.0375, ("a",))])

    def test_same_key_in_two_parts_is_one_press(self):
        self.assertEqual(presses("speed=60\na-|ad-"), [(0, ("a", "d"))])

    def test_continuation_does_not_press_again(self):
        self.assertEqual(presses("speed=60\na-|z-\n-|x-"), [(0, ("a", "z")), (1, ("x",))])

    def test_speed_factor(self):
        self.assertEqual(presses("speed=60\na-s-", speed=2), [(0, ("a",)), (0.5, ("s",))])

    def test_duration_includes_final_rest(self):
        self.assertAlmostEqual(compile_timeline(parse_score("speed=60\na-0--")).duration, 3)


class OrnamentTiming(unittest.TestCase):
    def test_roll_and_grace_use_milliseconds(self):
        self.assertEqual(presses("speed=60\n^adg-"), [(0, ("a",)), (0.03, ("d",)), (0.06, ("g",))])
        self.assertEqual(presses("speed=30\n^adg-"), [(0, ("a",)), (0.03, ("d",)), (0.06, ("g",))])
        self.assertEqual(presses("speed=60\ns'a-"), [(0, ("s",)), (0.06, ("a",))])

    def test_ornaments_are_squeezed_into_half_a_short_note(self):
        # A 1/8 beat at 120 BPM lasts 62.5 ms, so the 60 ms roll is squeezed to 31.25 ms.
        self.assertEqual(presses("speed=120\n^adg*0-.~*"), [(0, ("a",)), (0.015625, ("d",)), (0.03125, ("g",))])

    def test_triplet_timing(self):
        self.assertEqual(presses("speed=60\n(a.s.d.)-"), [(0, ("a",)), (0.333333, ("s",)), (0.666667, ("d",))])


class FakeKeyboard:
    def __init__(self, clock, stop_at=None, switch_at=None):
        self.clock, self.stop_at, self.switch_at = clock, stop_at, switch_at
        self.sent, self.released = [], 0

    def foreground(self):
        return 2 if self.switch_at is not None and self.clock.now >= self.switch_at else 1

    def stop_pressed(self):
        return self.stop_at is not None and self.clock.now >= self.stop_at

    def send(self, keys, down):
        self.sent.append((round(self.clock.now, 3), keys, down))

    def release_all(self):
        self.released += 1


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 0.0005)


TIMELINE = Timeline((KeyEvent(0.0, ("a",), True), KeyEvent(0.035, ("a",), False),
                     KeyEvent(1.0, ("s",), True), KeyEvent(1.035, ("s",), False)), 2.0)


class Player(unittest.TestCase):
    def run_with(self, timeline=TIMELINE, **options):
        clock = Clock()
        keyboard = FakeKeyboard(clock, **options)
        reason = play(timeline, keyboard, 1, threading.Event(), clock=clock, sleep=clock.sleep)
        return reason, keyboard, clock

    def test_plays_on_time_and_waits_for_the_end(self):
        reason, keyboard, clock = self.run_with()
        self.assertEqual(reason, "done")
        self.assertEqual([at for at, _, _ in keyboard.sent], [0.0, 0.035, 1.0, 1.035])
        self.assertGreaterEqual(clock.now, 2.0)
        self.assertEqual(keyboard.released, 1)

    def test_f8_stops(self):
        reason, keyboard, _ = self.run_with(stop_at=0.5)
        self.assertEqual(reason, "stopped")
        self.assertEqual(len(keyboard.sent), 2)
        self.assertEqual(keyboard.released, 1)

    def test_window_switch_stops(self):
        reason, keyboard, _ = self.run_with(switch_at=0.5)
        self.assertEqual(reason, "focus")
        self.assertEqual(keyboard.released, 1)

    def test_a_stall_delays_the_rest_instead_of_rushing(self):
        clock = Clock()
        keyboard = FakeKeyboard(clock)
        original = keyboard.send

        def stalling_send(keys, down):
            original(keys, down)
            if keys == ("a",) and not down:
                clock.now += 1.5  # the computer freezes past the next note
        keyboard.send = stalling_send
        play(TIMELINE, keyboard, 1, threading.Event(), clock=clock, sleep=clock.sleep)
        times = [at for at, _, _ in keyboard.sent]
        self.assertAlmostEqual(times[3] - times[2], 0.035, places=2)  # the s tap keeps its shape

    def test_release_happens_even_after_an_error(self):
        clock = Clock()
        keyboard = FakeKeyboard(clock)
        keyboard.send = lambda keys, down: (_ for _ in ()).throw(KeyboardError("no"))
        with self.assertRaises(KeyboardError):
            play(TIMELINE, keyboard, 1, threading.Event(), clock=clock, sleep=clock.sleep)
        self.assertEqual(keyboard.released, 1)


class FakeUser32:
    def __init__(self, accept=True):
        self.accept, self.batches = accept, []

    def send_input(self, inputs):
        self.batches.append([(item.ki.wScan, item.ki.dwFlags) for item in inputs])
        return len(inputs) if self.accept else 0

    def foreground(self):
        return 7

    def key_held(self, key):
        return key == 0x77


class KeyboardOutput(unittest.TestCase):
    def test_scan_codes_and_release_tracking(self):
        api = FakeUser32()
        keyboard = Keyboard(api)
        keyboard.send(("a", "q"), True)
        keyboard.send(("a",), False)
        keyboard.send(("z",), False)  # never pressed, so not sent
        keyboard.release_all()
        self.assertEqual(api.batches, [[(0x1E, 0x8), (0x10, 0x8)], [(0x1E, 0xA)], [(0x10, 0xA)]])
        self.assertEqual(keyboard.held, set())
        self.assertTrue(keyboard.stop_pressed())

    def test_rejected_input_is_reported(self):
        keyboard = Keyboard(FakeUser32(accept=False))
        with self.assertRaises(KeyboardError):
            keyboard.send(("a",), True)
        keyboard.release_all()
        self.assertEqual(keyboard.held, set())

    def test_all_21_keys_have_distinct_scan_codes(self):
        self.assertEqual(len(set(SCAN_CODES.values())), 21)


if __name__ == "__main__":
    unittest.main()
