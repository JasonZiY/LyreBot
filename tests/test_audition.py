"""Hearing a score on the computer, against a pretend winmm: nothing here makes a sound."""

import threading
import unittest

from lyrebot.audition import (ALL_NOTES_OFF, ALL_SOUND_OFF, CONTROL, HARP, INSTRUMENTS, MIDI_MAPPER, NOTE_OF, NOTE_OFF,
                              NOTE_ON, PIANO, PROGRAM, TAIL, VELOCITY, Audition, AuditionError, message)
from lyrebot.player import play
from lyrebot.score import KEYS, parse_score
from lyrebot.timeline import RING, compile_ringing, compile_timeline

try:
    from .test_playback import Clock
except ImportError:
    from test_playback import Clock


class FakeWinMM:
    """Records what would go to the synthesizer, as (what, *details)."""

    def __init__(self, names=("Microsoft GS Wavetable Synth",), refuse=False):
        self.names, self.refuse = list(names), refuse
        self.log = []

    def devices(self):
        return self.names

    def open(self, device):
        self.log.append(("open", device))
        return 0x5150

    def short(self, handle, packed):
        if self.refuse:
            raise AuditionError("refused")
        status, first, second = packed & 0xFF, packed >> 8 & 0xFF, packed >> 16 & 0xFF
        self.log.append(("short", handle, status, first, second))

    def reset(self, handle):
        self.log.append(("reset", handle))

    def close(self, handle):
        self.log.append(("close", handle))

    def notes(self):
        """The notes started and ended, as ("on"/"off", pitch)."""
        kinds = {NOTE_ON: "on", NOTE_OFF: "off"}
        return [(kinds[entry[2]], entry[3]) for entry in self.log if entry[0] == "short" and entry[2] in kinds]


def opened(api=None, **options):
    api = api or FakeWinMM()
    slept = []
    synth = Audition(api=api, stop_key=lambda: False, sleep=slept.append, **options)
    return synth, api, slept


class Pitches(unittest.TestCase):
    def test_the_three_rows_are_three_octaves_of_c_major_from_c3(self):
        self.assertEqual(set(NOTE_OF), KEYS)
        self.assertEqual([NOTE_OF[key] for key in "zxcvbnm"], [48, 50, 52, 53, 55, 57, 59])
        self.assertEqual([NOTE_OF[key] for key in "asdfghj"], [60, 62, 64, 65, 67, 69, 71])
        self.assertEqual([NOTE_OF[key] for key in "qwertyu"], [72, 74, 76, 77, 79, 81, 83])

    def test_short_messages_pack_status_then_data(self):
        self.assertEqual(message(0x90, 60, 96), 0x603C90)
        self.assertEqual(message(PROGRAM, HARP - 1), 0x2EC0)


class Synthesizer(unittest.TestCase):
    def test_it_opens_windows_own_synthesizer_on_the_piano(self):
        synth, api, _ = opened(FakeWinMM(["USB MIDI", "Microsoft GS Wavetable Synth"]))
        self.assertEqual(synth.device, 1)
        self.assertEqual(api.log, [("open", 1), ("short", 0x5150, PROGRAM, PIANO - 1, 0)])
        synth, api, _ = opened(FakeWinMM(["Some Synth"]), program=HARP)  # no GS synth: whatever Windows maps to
        self.assertEqual(api.log, [("open", MIDI_MAPPER), ("short", 0x5150, PROGRAM, HARP - 1, 0)])
        with self.assertRaises(AuditionError):
            opened(FakeWinMM([]))  # nothing to play on at all

    def test_the_instrument_can_change_while_it_plays_and_not_once_it_is_closed(self):
        synth, api, _ = opened()
        synth.send(("a",), True)
        synth.instrument(108)
        synth.send(("s",), True)
        self.assertEqual(api.log[2:5], [("short", 0x5150, NOTE_ON, 60, VELOCITY), ("short", 0x5150, PROGRAM, 107, 0),
                                        ("short", 0x5150, NOTE_ON, 62, VELOCITY)])
        synth.release_all()
        count = len(api.log)
        synth.instrument(1)
        self.assertEqual(len(api.log), count)

    def test_the_instruments_offered_are_distinct_general_midi_ones_piano_first(self):
        programs = [program for program, _ in INSTRUMENTS]
        self.assertEqual(programs[:2], [PIANO, HARP])
        self.assertEqual(len(set(programs)), len(programs))
        self.assertEqual(len({name for _, name in INSTRUMENTS}), len(INSTRUMENTS))
        self.assertTrue(all(1 <= program <= 128 for program in programs))

    def test_a_press_starts_its_note_and_a_release_or_a_new_strike_ends_it(self):
        synth, api, _ = opened()
        synth.send(("a", "g"), True)
        synth.send(("a",), True)  # struck again while it rings
        synth.send(("a",), False)
        synth.send(("a",), False)  # nothing left to end
        synth.send(("z",), False)  # never struck
        self.assertEqual(api.notes(), [("on", 60), ("on", 67), ("off", 60), ("on", 60), ("off", 60)])
        self.assertTrue(all(entry[4] == VELOCITY for entry in api.log if entry[0] == "short" and entry[2] == NOTE_ON))
        self.assertEqual(synth.sounding, {67})

    def test_stopped_part_way_it_falls_silent_at_once_and_closes(self):
        synth, api, slept = opened()
        synth.send(("q",), True)
        del api.log[:]
        synth.release_all()
        self.assertEqual(api.log, [("short", 0x5150, CONTROL, ALL_SOUND_OFF, 0), ("short", 0x5150, CONTROL, ALL_NOTES_OFF, 0),
                                   ("reset", 0x5150), ("close", 0x5150)])
        self.assertEqual(slept, [])
        synth.release_all()  # once closed, nothing more goes out
        synth.send(("q",), True)
        self.assertEqual(len(api.log), 4)

    def test_at_the_end_the_last_notes_fade_before_it_closes(self):
        synth, api, slept = opened()
        synth.send(("q",), True)
        synth.send(("q",), False)
        del api.log[:]
        synth.release_all()
        self.assertEqual(slept, [TAIL])
        self.assertEqual(api.log, [("short", 0x5150, CONTROL, ALL_NOTES_OFF, 0), ("reset", 0x5150), ("close", 0x5150)])

    def test_a_synthesizer_refusing_the_instrument_is_closed_again(self):
        api = FakeWinMM(refuse=True)
        with self.assertRaises(AuditionError):
            opened(api)
        self.assertEqual(api.log[-2:], [("reset", 0x5150), ("close", 0x5150)])

    def test_other_windows_coming_to_the_front_never_stop_it_but_f8_and_its_interrupt_do(self):
        leap = threading.Event()
        held = [False]
        synth = Audition(api=FakeWinMM(), interrupt=leap.is_set, stop_key=lambda: held[0], sleep=lambda _: None)
        self.assertEqual(synth.foreground(), synth.foreground())
        self.assertFalse(synth.stop_pressed())
        held[0] = True
        self.assertTrue(synth.stop_pressed())
        held[0] = False
        leap.set()
        self.assertTrue(synth.stop_pressed())


class Ringing(unittest.TestCase):
    def notes(self, text, speed=1.0, ring=RING):
        """(key, from, to) in seconds, for each note of the ringing timeline."""
        started, out = {}, []
        for event in compile_ringing(parse_score(text), speed, ring).events:
            for key in event.keys:
                if event.down:
                    started[key] = event.at
                else:
                    out.append((key, round(started.pop(key), 6), round(event.at, 6)))
        self.assertEqual(started, {})  # every note ends
        return sorted(out, key=lambda note: (note[1], note[0]))

    def test_a_note_rings_for_its_length_or_the_ring_whichever_is_longer(self):
        self.assertEqual(self.notes("speed=120\na.s----"), [("a", 0, 2.0), ("s", 0.25, 2.25)])
        self.assertEqual(self.notes("speed=60\na-----"), [("a", 0, 5.0)])
        self.assertEqual(self.notes("speed=60\na-s-", speed=2), [("a", 0, 2.0), ("s", 0.5, 2.5)])

    def test_striking_the_same_key_again_ends_it_at_that_very_moment(self):
        self.assertEqual(self.notes("speed=120\na.a.a-"), [("a", 0, 0.25), ("a", 0.25, 0.5), ("a", 0.5, 2.5)])
        events = compile_ringing(parse_score("speed=120\na.a-")).events
        self.assertEqual([(event.at, event.down) for event in events][:3], [(0.0, True), (0.25, False), (0.25, True)])

    def test_the_strikes_are_the_tapped_ones_and_the_length_the_same(self):
        text = "speed=96\n(a.s.d.)-f-g.h.^adg-|z----\ns'd-.f.g-0-|x----\nspeed=>72\nq----|^zbn----\n"
        score = parse_score(text)
        tapped, ringing = compile_timeline(score), compile_ringing(score)
        self.assertEqual([(e.at, e.keys) for e in tapped.events if e.down], [(e.at, e.keys) for e in ringing.events if e.down])
        self.assertEqual(ringing.duration, tapped.duration)
        self.assertEqual(ringing.events[-1].at, ringing.duration)  # a long last note ends with the score
        short = compile_ringing(parse_score("speed=120\na.s."))
        self.assertEqual(short.duration, 0.5)
        self.assertEqual(short.events[-1].at, 2.25)  # a short one rings on past the end


class Heard(unittest.TestCase):
    def test_the_player_drives_it_and_always_closes_it(self):
        clock = Clock()
        synth, api, _ = opened()
        timeline = compile_ringing(parse_score("speed=120\na.a.d-\n"))
        reason = play(timeline, synth, synth.foreground(), threading.Event(), clock=clock, sleep=clock.sleep)
        self.assertEqual(reason, "done")
        # a at 0 and again at 0.25 s, ending the first; d at 0.5 s; each then rings its 2 s
        self.assertEqual(api.notes(), [("on", 60), ("off", 60), ("on", 60), ("on", 64), ("off", 60), ("off", 64)])
        self.assertGreaterEqual(clock.now, 2.5)
        self.assertEqual(api.log[-2:], [("reset", 0x5150), ("close", 0x5150)])

        cancel = threading.Event()
        synth, api, _ = opened()
        stop_at = lambda: clock.now >= 0.3  # noqa: E731  F8 in the middle
        clock.now = 0.0
        synth.stop_key = stop_at
        self.assertEqual(play(timeline, synth, synth.foreground(), cancel, clock=clock, sleep=clock.sleep), "stopped")
        self.assertIn(("short", 0x5150, CONTROL, ALL_SOUND_OFF, 0), api.log)
        self.assertEqual(api.log[-1], ("close", 0x5150))


if __name__ == "__main__":
    unittest.main()
