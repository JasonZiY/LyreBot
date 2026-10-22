from fractions import Fraction as F
from types import SimpleNamespace as NS
import unittest

from lyrebot.playback import InputEvent, Timeline, PlaybackError, compile_timeline, play_timeline


def note(start, duration, keys, part=1):
    return NS(start=F(start), duration=F(duration), keys=tuple(keys), part=part, line=1)


def score(events, beats=4, bpm=60):
    return NS(events=tuple(events), total_beats=F(beats), bpm=bpm)


class FakeClock:
    now = 0.0

    def __call__(self):
        return self.now


class FakeStopEvent:
    def __init__(self, clock, cancel_at=None):
        self.clock = clock
        self.cancel_at = cancel_at
        self.waits = []

    def is_set(self):
        return self.cancel_at is not None and self.clock.now >= self.cancel_at

    def wait(self, seconds):
        self.waits.append(seconds)
        self.clock.now += seconds
        return self.is_set()


class FakeBackend:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []
        self.owned = set()
        self.release_count = 0
        self.focus_lost_at = None
        self.modifiers_at = None
        self.stop_at = None
        self.send_failure = False
        self.send_delay = 0
        self.cleanup_failure = False

    def foreground_window(self):
        return 0 if self.focus_lost_at is not None and self.clock.now >= self.focus_lost_at else 123

    def stop_requested(self):
        return self.stop_at is not None and self.clock.now >= self.stop_at

    def held_modifiers(self):
        return ("Ctrl",) if self.modifiers_at is not None and self.clock.now >= self.modifiers_at else ()

    def send_keys(self, keys, down):
        self.calls.append((self.clock.now, keys, down))
        if down:
            self.owned.update(keys)
        else:
            self.owned.difference_update(keys)
        if self.send_failure:
            raise OSError("injected output failure")
        self.clock.now += self.send_delay

    def release_all(self):
        self.release_count += 1
        if self.cleanup_failure:
            raise OSError("injected cleanup failure")
        self.owned.clear()


class TimelineTests(unittest.TestCase):
    def test_note_lengths_control_rhythm_but_taps_are_short(self):
        timeline = compile_timeline(score([note(0, 1, "a"), note(2, 2, "s")], bpm=120))
        self.assertEqual(timeline.events, (
            InputEvent(0.0, ("a",), True), InputEvent(0.035, ("a",), False),
            InputEvent(1.0, ("s",), True), InputEvent(1.035, ("s",), False),
        ))
        self.assertEqual(timeline.duration_seconds, 2.0)

    def test_chords_and_two_parts_merge_onsets(self):
        timeline = compile_timeline(score([note(0, 1, "sa"), note(0, 2, "ad", part=2)]))
        self.assertEqual(timeline.events, (InputEvent(0.0, ("a", "s", "d"), True),
                                         InputEvent(0.035, ("a", "s", "d"), False)))

    def test_fast_repetition_releases_before_next_attack(self):
        timeline = compile_timeline(score([note(0, F(1, 100), "a"),
                                          note(F(1, 100), F(1, 100), "a")]))
        self.assertEqual(timeline.events[1].at, 0.005)
        self.assertLess(timeline.events[1].at, timeline.events[2].at)
        self.assertEqual(timeline.events[2].at, 0.01)
        self.assertEqual(timeline.events[3].at, 0.02)

    def test_releases_sort_before_presses_at_equal_time(self):
        timeline = compile_timeline(score([note(0, 1, "a"), note(F(7, 200), 1, "s")]))
        at_boundary = [event for event in timeline.events if event.at == 0.035]
        self.assertEqual([event.down for event in at_boundary], [False, True])

    def test_short_note_never_held_beyond_duration(self):
        timeline = compile_timeline(score([note(0, F(1, 100), "q")]))
        self.assertEqual(timeline.events[1].at, 0.01)

    def test_tempo_override_scales_entire_score(self):
        timeline = compile_timeline(score([note(1, 1, "q")]), bpm=120)
        self.assertEqual(timeline.events[0].at, 0.5)
        self.assertEqual(timeline.duration_seconds, 2.0)

    def test_tempo_changes_and_override_scale_together(self):
        changing = NS(events=(note(0, 1, "a"), note(2, 1, "s"), note(3, 1, "d")),
                      total_beats=F(4), bpm=60, tempo_changes=((F(2), 120.0),))
        timeline = compile_timeline(changing)
        self.assertEqual([event.at for event in timeline.events if event.down], [0.0, 2.0, 2.5])
        self.assertEqual(timeline.duration_seconds, 3.0)
        doubled = compile_timeline(changing, bpm=120)
        self.assertEqual([event.at for event in doubled.events if event.down], [0.0, 1.0, 1.25])
        self.assertEqual(doubled.duration_seconds, 1.5)

    def test_invalid_tempo_tap_and_keys_rejected(self):
        for tempo in (0, -1, 1001, 10 ** 1000, float("nan"), float("inf")):
            with self.subTest(tempo=tempo), self.assertRaises(ValueError):
                compile_timeline(score([]), bpm=tempo)
        for tap in (0, -1, float("nan"), float("inf")):
            with self.subTest(tap=tap), self.assertRaises(ValueError):
                compile_timeline(score([]), tap_ms=tap)
        with self.assertRaises(ValueError):
            compile_timeline(score([note(0, 1, "i")]))

    def test_duration_limit_includes_tempo_override(self):
        with self.assertRaisesRegex(ValueError, "24 小时"):
            compile_timeline(score([], beats=2000), bpm=1)
        with self.assertRaises(ValueError):
            compile_timeline(score([], beats=10 ** 1000))

    def test_tiny_release_and_collapsed_onset_are_rejected(self):
        tiny = F(1, 10 ** 40)
        with self.assertRaisesRegex(ValueError, "过小，无法表示"):
            compile_timeline(score([note(1, tiny, "a")]))
        with self.assertRaisesRegex(ValueError, "起音过于接近"):
            compile_timeline(score([note(1, 1, "a"), note(1 + tiny, 1, "s")]))


class PlaybackTests(unittest.TestCase):
    def setup_playback(self):
        clock = FakeClock()
        backend = FakeBackend(clock)
        stop = FakeStopEvent(clock)
        timeline = compile_timeline(score([note(0, 1, "a"), note(2, 1, "s")]))
        return clock, backend, stop, timeline

    def test_success_uses_fixed_deadlines_and_always_cleans_up(self):
        clock, backend, stop, timeline = self.setup_playback()
        backend.send_delay = 0.002
        result = play_timeline(timeline, backend, 123, stop, _clock=clock)
        self.assertEqual(result.reason, "completed")
        self.assertAlmostEqual(backend.calls[2][0], 2.0)
        self.assertAlmostEqual(result.elapsed_seconds, 4.0)
        self.assertEqual(backend.release_count, 1)
        self.assertFalse(backend.owned)
        self.assertLessEqual(max(stop.waits), 0.01)

    def test_cancel_during_rest_is_prompt(self):
        clock, backend, stop, timeline = self.setup_playback()
        stop.cancel_at = 0.2
        result = play_timeline(timeline, backend, 123, stop, _clock=clock)
        self.assertEqual(result.reason, "stopped")
        self.assertLessEqual(result.elapsed_seconds, 0.211)
        self.assertEqual(len(backend.calls), 2)
        self.assertEqual(backend.release_count, 1)

    def test_backend_emergency_stop(self):
        clock, backend, stop, timeline = self.setup_playback()
        backend.stop_at = 0.01
        result = play_timeline(timeline, backend, 123, stop, _clock=clock)
        self.assertEqual(result.reason, "stopped")
        self.assertFalse(backend.owned)

    def test_focus_loss_during_rest_stops_before_next_note(self):
        clock, backend, stop, timeline = self.setup_playback()
        backend.focus_lost_at = 0.2
        with self.assertRaisesRegex(PlaybackError, "失去焦点"):
            play_timeline(timeline, backend, 123, stop, _clock=clock)
        self.assertEqual(len(backend.calls), 2)
        self.assertLessEqual(clock.now, 0.211)
        self.assertEqual(backend.release_count, 1)

    def test_modifier_during_rest_stops_before_next_note(self):
        clock, backend, stop, timeline = self.setup_playback()
        backend.modifiers_at = 0.2
        with self.assertRaisesRegex(PlaybackError, "Ctrl"):
            play_timeline(timeline, backend, 123, stop, _clock=clock)
        self.assertEqual(len(backend.calls), 2)

    def test_invalid_initial_target_never_sends(self):
        clock, backend, stop, timeline = self.setup_playback()
        with self.assertRaises(PlaybackError):
            play_timeline(timeline, backend, 0, stop, _clock=clock)
        self.assertEqual(backend.calls, [])
        self.assertEqual(backend.release_count, 1)

    def test_keydown_failure_still_releases_owned_keys(self):
        clock, backend, stop, timeline = self.setup_playback()
        backend.send_failure = True
        with self.assertRaisesRegex(PlaybackError, "injected output failure"):
            play_timeline(timeline, backend, 123, stop, _clock=clock)
        self.assertEqual(backend.release_count, 1)
        self.assertFalse(backend.owned)

    def test_excessive_lateness_does_not_dump_backlog(self):
        clock, backend, stop, timeline = self.setup_playback()
        backend.send_delay = 0.2
        with self.assertRaisesRegex(PlaybackError, "150 ms"):
            play_timeline(timeline, backend, 123, stop, _clock=clock)
        self.assertEqual(len(backend.calls), 1)
        self.assertFalse(backend.owned)

    def test_short_stall_cannot_dump_dense_note_backlog(self):
        clock, backend, stop, _ = self.setup_playback()
        timeline = compile_timeline(score([note(F(i, 20), F(1, 20), key)
                                           for i, key in enumerate("asdf")]))
        backend.send_delay = 0.12
        with self.assertRaisesRegex(PlaybackError, "积压"):
            play_timeline(timeline, backend, 123, stop, _clock=clock)
        self.assertEqual(len(backend.calls), 1)
        self.assertFalse(backend.owned)

    def test_cleanup_failure_is_reported(self):
        clock, backend, stop, timeline = self.setup_playback()
        backend.cleanup_failure = True
        stop.cancel_at = 0
        with self.assertRaisesRegex(PlaybackError, "松开按键失败"):
            play_timeline(timeline, backend, 123, stop, _clock=clock)

    def test_progress_callback_reports_elapsed_seconds(self):
        clock, backend, stop, timeline = self.setup_playback()
        values = []
        play_timeline(timeline, backend, 123, stop, _clock=clock, on_progress=values.append)
        self.assertEqual(values[0], 0)
        self.assertEqual(values[-1], timeline.duration_seconds)
        self.assertTrue(all(left <= right for left, right in zip(values, values[1:])))


if __name__ == "__main__":
    unittest.main()
