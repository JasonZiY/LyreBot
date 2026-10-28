import bisect
from fractions import Fraction as F
from pathlib import Path
import struct
import unittest

from lyrebot.arrange import (
    ArrangeError, Grid, Tone, accompany, arrange, choose_shift, detect_chords, duration_text, mountain,
    outer_line, snap_line, voicing,
)
from lyrebot.midi import MidiError, parse_midi
from lyrebot.score import load_score, parse_score

MUSIC = Path(__file__).resolve().parent.parent / "txt"
PPQ = 480
PITCH_OF = {key: 48 + 12 * row + step
            for row, keys in enumerate(("zxcvbnm", "asdfghj", "qwertyu"))
            for key, step in zip(keys, (0, 2, 4, 5, 7, 9, 11))}


# --- A small Standard MIDI File writer for building test input ----------------

def varlen(value):
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(out))


def meta(kind, payload):
    return bytes([0xFF, kind]) + varlen(len(payload)) + payload


def tempo(bpm):
    return meta(0x51, round(60_000_000 / bpm).to_bytes(3, "big"))


def signature(numerator, denominator):
    return meta(0x58, bytes([numerator, denominator.bit_length() - 1, 24, 8]))


def name(text, encoding="utf-8"):
    return meta(0x03, text.encode(encoding))


def notes(channel, items, velocity=80):
    """(start, end, pitch) in ticks -> timed note-on / note-off events."""
    events = []
    for start, end, pitch in items:
        events.append((start, 1, bytes([0x90 | channel, pitch, velocity])))
        events.append((end, 0, bytes([0x80 | channel, pitch, 0])))
    return events


def smf(tracks, ppq=PPQ, fmt=1):
    """Each track: a list of (tick, order, bytes); a lower order goes first at the same tick."""
    out = b"MThd" + struct.pack(">IHHH", 6, fmt, len(tracks), ppq)
    for events in tracks:
        body, last = b"", 0
        for tick, _, data in sorted(events, key=lambda event: (event[0], event[1])):
            body += varlen(tick - last) + data
            last = tick
        body += varlen(0) + meta(0x2F, b"")
        out += b"MTrk" + struct.pack(">I", len(body)) + body
    return out


def song(*parts, bpm=100, numerator=4, denominator=4, ppq=PPQ):
    """parts: (channel, [(start beat, end beat, pitch)]) -> MIDI bytes."""
    conductor = [(0, -1, tempo(bpm)), (0, -1, signature(numerator, denominator))]
    tracks = [conductor]
    for channel, items in parts:
        tracks.append(notes(channel, [(round(s * ppq), round(e * ppq), p) for s, e, p in items]))
    return smf(tracks, ppq)


def written_scores():
    """The scores in the txt folder transcribed by hand, not arranged from MIDI."""
    return [path for path in sorted(MUSIC.glob("*.txt"))
            if not path.read_text(encoding="utf-8").startswith("# 由 MIDI 自动编配")]


def bars_of(text):
    return [line.split("#")[0].strip() for line in text.splitlines()
            if line.strip() and not line.startswith(("#", "speed="))]


# --- Reading MIDI ---------------------------------------------------------------

class Reading(unittest.TestCase):
    def test_running_status_and_note_on_with_zero_velocity(self):
        body = (varlen(0) + bytes([0x90, 60, 90]) + varlen(480) + bytes([60, 0])  # running status, off as on/0
                + varlen(0) + bytes([62, 90]) + varlen(240) + bytes([0x80, 62, 0]))
        data = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480) + b"MTrk" + struct.pack(">I", len(body) + 4) \
            + body + varlen(0) + meta(0x2F, b"")
        midi = parse_midi(data)
        self.assertEqual([(n.start, n.end, n.pitch) for n in midi.notes], [(0, 480, 60), (480, 720, 62)])

    def test_overlapping_repeats_of_one_pitch_pair_up_in_order(self):
        events = [(0, 1, bytes([0x90, 60, 80])), (100, 1, bytes([0x90, 60, 80])),
                  (200, 0, bytes([0x80, 60, 0])), (300, 0, bytes([0x80, 60, 0]))]
        midi = parse_midi(smf([events]))
        self.assertEqual([(n.start, n.end) for n in midi.notes], [(0, 200), (100, 300)])

    def test_meta_events_and_programs(self):
        track = [(0, -1, name("钢琴右手", "gbk")), (0, -1, tempo(90)), (0, -1, signature(3, 4)),
                 (0, 0, bytes([0xC2, 73]))] + notes(2, [(0, 480, 72)])
        midi = parse_midi(smf([track]))
        self.assertEqual(midi.track_names, ("钢琴右手",))
        self.assertEqual(midi.tempos, ((0, 666_667),))
        self.assertEqual(midi.time_signatures, ((0, 3, 4),))
        self.assertEqual(midi.programs, {(0, 2): 73})
        self.assertEqual(midi.notes[0].channel, 2)

    def test_rejects_other_files(self):
        with self.assertRaises(MidiError):
            parse_midi(b"RIFF....WAVEfmt ")
        smpte = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 0xE728)
        with self.assertRaises(MidiError):
            parse_midi(smpte)


# --- Pieces of the arranger -----------------------------------------------------

def tone(start, end, pitch, part=1):
    return Tone(F(start), F(end), pitch, part)


class Pieces(unittest.TestCase):
    def test_durations_use_symbols_when_they_can(self):
        self.assertEqual([duration_text(F(x)) for x in ("1", "3/2", "7/4", "7/8", "3", "1/3", "4/3")],
                         ["-", "-.", "-.~", ".~*", "---", "{1/3}", "-{1/3}"])

    def test_top_line_skips_notes_under_a_held_higher_note(self):
        line = outer_line([tone(0, 2, 76), tone(0, 1, 67), tone(1, 2, 72), tone(2, 3, 74), tone(2, 3, 71)], top=True)
        self.assertEqual([(t.start, t.pitch) for t in line], [(0, 76), (2, 74)])

    def test_bottom_line_keeps_a_held_bass(self):
        line = outer_line([tone(0, 4, 36), tone(1, 2, 52), tone(4, 5, 55), tone(4, 5, 43)], top=False)
        self.assertEqual([t.pitch for t in line], [36, 43])

    def test_transposes_e_flat_major_to_c(self):
        scale = [tone(i, i + 1, 63 + step) for i, step in enumerate((0, 2, 4, 5, 7, 9, 11, 12))]
        self.assertEqual(choose_shift(scale, [])[0], -3)

    def test_d_minor_lands_on_a_minor(self):
        scale = [tone(i, i + 1, 62 + step) for i, step in enumerate((0, 2, 3, 5, 7, 8, 10))]
        shift, share = choose_shift(scale, [])
        self.assertEqual(((62 + shift) % 12, share), (9, 1.0))

    def test_pentatonic_tunes_sit_on_c_d_e_g_a(self):
        tune = [tone(i, i + 1, 66 + step) for i, step in enumerate((0, 2, 4, 7, 9, 7, 4, 2, 0))]  # G♭ pentatonic
        shift, _ = choose_shift(tune, [])
        self.assertEqual({(t.pitch + shift) % 12 for t in tune}, {0, 2, 4, 7, 9})

    def test_black_keys_move_away_from_their_neighbours(self):
        self.assertEqual(snap_line([69, 68, 69]), [69, 67, 69])  # A G♯ A -> A G A
        self.assertEqual(snap_line([72, 70, 69]), [72, 71, 69])  # C B♭ A -> C B A
        self.assertEqual(snap_line([64, 66, 67]), [64, 65, 67])  # E F♯ G -> E F G

    def test_played_onsets_snap_to_sixteenths_or_triplets(self):
        grid = Grid([F(0), F(49, 100), F(103, 100), F(134, 100), F(167, 100)], F(1, 480))
        self.assertFalse(grid.exact)
        self.assertEqual([grid.onset(F(x)) for x in ("49/100", "103/100", "134/100", "167/100")],
                         [F(1, 2), F(1), F(4, 3), F(5, 3)])

    def test_written_files_keep_their_tuplets(self):
        onsets = [F(i, 5) for i in range(10)] + [F(2), F(5, 2)]
        grid = Grid(onsets, F(1, 480))
        self.assertTrue(grid.exact)
        self.assertEqual(grid.onset(F(3, 5)), F(3, 5))

    def test_a_beat_of_unrelated_subdivisions_snaps_to_32nds(self):
        flourish = [F(43, 12), F(59, 16), F(19, 5)]  # placed by hand before the downbeat
        grid = Grid([F(0), F(1), F(3, 2), F(2), F(7, 3), *flourish, F(4)], F(1, 256))
        self.assertTrue(grid.exact)
        self.assertEqual([grid.onset(x) for x in flourish], [F(29, 8), F(15, 4), F(15, 4)])
        self.assertEqual(grid.onset(F(7, 3)), F(7, 3))  # a plain triplet elsewhere is kept

    def test_left_hand_notes_leave_a_two_handed_melody(self):
        right = [(i / 2, i / 2 + 0.25, (76, 74, 72, 74)[i % 4]) for i in range(16) if i % 4 != 2]
        left = [(i, i + 0.5, pitch) for i in range(8) for pitch in (45, 52)]
        result = arrange(parse_midi(song((0, right + left))))
        self.assertEqual(result.strays, 4)  # the left hand shows through where the right hand rests
        melody = bars_of(result.text)[0].split("|")[0]
        self.assertFalse(set(melody) & set("zxcvbnm"), melody)

    def test_left_hand_chords_between_staccato_notes_leave_the_melody(self):
        right = [(i, i + 0.25, (84, 86, 88, 89)[i % 4]) for i in range(17)]  # C6 D6 E6 F6, short
        left = [(i + 0.5, i + 0.75, pitch) for i in range(16) for pitch in (72, 76)]  # C5 E5 in between
        result = arrange(parse_midi(song((0, right + left))), accompaniment="chords")
        self.assertEqual(result.strays, 16)
        melody = "".join(bar.split("|")[0] for bar in bars_of(result.text))
        self.assertEqual(set(melody) - set("0-.~"), set("qwer"), melody)


# --- Whole arrangements ---------------------------------------------------------

class Harmony(unittest.TestCase):
    BARS = [(F(4 * i), F(4)) for i in range(4)]

    def chords(self, *notes, melody=(), bars=None):
        others = [tone(s, e, p, 2) for s, e, p in notes]
        bass = outer_line(others, top=False)
        spans = detect_chords([tone(s, e, p) for s, e, p in melody], others, bass, bars or self.BARS)
        return [(span.name, span.start, span.end) for span in spans]

    def block(self, start, length, *pitches):
        return [(start, start + length, pitch) for pitch in pitches]

    def test_one_chord_per_bar(self):
        notes = (self.block(0, 4, 48, 52, 55) + self.block(4, 4, 45, 52, 60)
                 + self.block(8, 4, 41, 57, 60) + self.block(12, 4, 43, 59, 62))
        self.assertEqual([name for name, _, _ in self.chords(*notes)], ["C", "Am", "F", "G"])

    def test_a_change_on_the_third_beat(self):
        notes = self.block(0, 2, 48, 52, 55) + self.block(2, 2, 43, 50, 59)
        self.assertEqual(self.chords(*notes, bars=[(F(0), F(4))]), [("C", 0, 2), ("G", 2, 4)])

    def test_passing_notes_in_the_melody_do_not_change_the_chord(self):
        run = [(i / 2, i / 2 + 0.5, (72, 74, 76, 77, 79, 77, 76, 74)[i]) for i in range(8)]
        notes = self.block(0, 4, 48, 55, 64)
        self.assertEqual(self.chords(*notes, melody=run, bars=[(F(0), F(4))]), [("C", 0, 4)])

    def test_the_bass_picks_between_chords_sharing_two_notes(self):
        notes = self.block(0, 4, 45, 64, 72)  # A under E and C: A minor, not C
        self.assertEqual(self.chords(*notes, bars=[(F(0), F(4))])[0][0], "Am")

    def test_voicings_and_patterns(self):
        self.assertEqual(voicing(0), [48, 55, 60, 64, 67])  # C3 G3 C4 E4 G4
        self.assertEqual(voicing(3), [53, 48, 57, 60, 65])  # F3 C3 A3 C4 F4
        self.assertEqual([mountain(n) for n in (8, 6, 4, 2, 1)],
                         [[0, 1, 2, 3, 4, 3, 2, 1], [0, 1, 2, 3, 2, 1], [0, 1, 2, 1], [0, 1], [0]])

    def test_broken_chords_play_only_where_the_source_does_and_stay_under_the_melody(self):
        from lyrebot.arrange import ChordSpan
        spans = [ChordSpan(F(0), F(8), 0)]
        bars = [(F(0), F(4)), (F(4), F(4))]
        source = [tone(0, 4, 48, 2)]  # the source accompaniment stops after one bar
        line = accompany(spans, bars, source, [(F(0), 79)], F(1, 2))
        self.assertEqual("".join(key for _, _, key in line), "zbadgdab")
        low = accompany(spans, bars, source, [(F(0), 65)], F(1, 2))  # melody on F4
        self.assertEqual("".join(key for _, _, key in low), "zbacbcab")  # E4 and G4 drop an octave

    def test_the_kept_accompaniment_plays_what_the_source_played(self):
        from lyrebot.arrange import Change, keep_accompaniment
        others = [tone(0, 0.5, 48, 2),  # C3
                  tone(0.5, 1, 64, 2), tone(0.5, 1, 67, 2),  # E4 G4
                  tone(1, 2, 61, 2), tone(1, 2, 64, 2), tone(1, 2, 69, 2),  # C♯4 E4 A4: the C♯ is left out
                  tone(2, 3, 54, 2),  # F♯3 alone: moves to F3
                  *(tone(3, 4, pitch, 2) for pitch in (48, 52, 55, 60, 64))]  # five notes: lowest and top two
        line, changes = keep_accompaniment(others, [(F(0), 84)])
        self.assertEqual([(start, keys) for start, _, keys in line],
                         [(0, "z"), (F(1, 2), "dg"), (1, "dh"), (2, "v"), (3, "zad")])
        self.assertEqual(changes, [Change(F(2), 1, 54, 53)])
        line, _ = keep_accompaniment(others[:3], [(F(0), 65)])  # under F4, E4 and G4 drop an octave
        self.assertEqual([keys for _, _, keys in line], ["z", "cb"])

    def test_every_written_score_gets_an_accompaniment_below_its_melody(self):
        for path in written_scores():
            with self.subTest(path.stem):
                original = load_score(path)
                parts = ([], [])
                for event in original.events:
                    for key in event.keys:
                        parts[min(event.part, 1)].append((event.start, event.start + event.duration, PITCH_OF[key]))
                score = parse_score(arrange(parse_midi(song((0, parts[0]), (1, parts[1]), bpm=original.bpm))).text)
                melody = sorted((e.start, max(PITCH_OF[k] for k in e.keys)) for e in score.events
                                if e.part == 0 and e.strikes)
                starts = [start for start, _ in melody]
                for event in score.events:
                    if event.part != 1 or not event.strikes:
                        continue
                    self.assertTrue(set(event.keys) <= set("zxcvbnmasdfghj"), event)
                    index = bisect.bisect_right(starts, event.start) - 1
                    if index >= 0 and event.start - melody[index][0] < 4:
                        self.assertLessEqual(PITCH_OF[event.keys[0]], melody[index][1] - 3, (path.stem, event))


class Arranging(unittest.TestCase):
    def test_finds_the_melody_and_leaves_out_drums(self):
        melody = [(i, i + 1, 74 + (0, 2, 4, 5)[i % 4]) for i in range(16)]  # D major-ish, high, one at a time
        pad = [(i * 4, i * 4 + 4, p) for i in range(4) for p in (78, 81, 86)]  # sustained chords above it
        bass = [(i * 2, i * 2 + 2, 38) for i in range(8)]
        drums = [(i / 2, i / 2 + 0.1, 36 + i % 3) for i in range(32)]
        result = arrange(parse_midi(song((0, melody), (1, pad), (2, bass), (9, drums))))
        roles = {part.number: part.role for part in result.parts}
        self.assertEqual(roles, {1: "旋律", 2: "伴奏", 3: "伴奏", 4: "未使用（鼓）"})
        self.assertEqual(result.shift, -2)  # D major -> C major
        score = parse_score(result.text)
        self.assertEqual(score.parts, 2)
        self.assertEqual(score.total_beats, 16)

    def test_melody_can_be_chosen_by_hand(self):
        high = [(i, i + 1, 84) for i in range(8)]
        low = [(i, i + 1, 60 + (0, 2, 4, 5, 7, 5, 4, 2)[i]) for i in range(8)]
        result = arrange(parse_midi(song((0, high), (1, low))), melody=[2])
        # A melody one octave wide is centred near C5, so it moves up to the high row.
        self.assertEqual(bars_of(result.text)[0].split("|")[0], "q-w-e-r-")

    def test_unknown_part_numbers_are_refused(self):
        with self.assertRaises(ArrangeError):
            arrange(parse_midi(song((0, [(0, 1, 60)]))), melody=[3])

    def test_bars_follow_the_time_signature_and_ties_cross_bar_lines(self):
        melody = [(0, 2, 60), (2, 5, 64), (5, 6, 67)]
        result = arrange(parse_midi(song((0, melody), numerator=3)))
        self.assertEqual(bars_of(result.text), ["q--e-", "--t-"])

    def test_silences_and_staccato(self):
        melody = [(0, 0.5, 60), (1, 1.5, 62), (2, 2.4, 64), (4, 5, 65)]  # staccato, then a two-beat rest
        result = arrange(parse_midi(song((0, melody))))
        self.assertEqual(bars_of(result.text), ["q-w-e.0-.", "r-0---"])

    def test_tempo_changes_land_on_bar_lines(self):
        conductor = [(0, -1, tempo(120)), (0, -1, signature(4, 4)), (4 * PPQ, -1, tempo(80))]
        track = notes(0, [(i * PPQ, (i + 1) * PPQ, 60 + (i % 3) * 2) for i in range(8)])
        text = arrange(parse_midi(smf([conductor, track]))).text
        self.assertEqual([line for line in text.splitlines() if line.startswith("speed=")],
                         ["speed=120", "speed=80"])

    def test_black_keys_are_snapped_and_noted(self):
        melody = [(0, 1, 72), (1, 2, 71), (2, 3, 69), (3, 4, 68), (4, 8, 69)]  # C B A G♯ A, in A minor
        result = arrange(parse_midi(song((0, melody))))
        self.assertEqual(result.shift, 0)
        first = [line for line in result.text.splitlines() if not line.startswith(("#", "speed="))][0]
        self.assertTrue(first.startswith("q-j-h-g-"))
        self.assertIn("# 改动 ♯5→g", first)

    def test_a_song_that_changes_key_is_transposed_one_key_at_a_time(self):
        tune = (0, 2, 4, 5, 7, 9, 11, 12, 12, 11, 9, 7, 5, 4, 2, 0)  # up and down the major scale
        in_g = [(i, i + 1, 67 + tune[i % 16]) for i in range(32)]
        in_b_flat = [(32 + i, 33 + i, 70 + tune[i % 16]) for i in range(32)]
        bass = [(i, i + 2, (43 if i < 32 else 46) + 7 * (i % 4 == 2)) for i in range(0, 64, 2)]
        result = arrange(parse_midi(song((0, in_g + in_b_flat), (1, bass))), accompaniment="bass")
        self.assertEqual([(key.start, key.shift) for key in result.keys], [(0, 5), (32, 2)])
        self.assertEqual((result.changes, result.white_share), ([], 1.0))
        bars = bars_of(result.text)
        self.assertEqual(bars[:8], bars[8:])  # both halves land on the same white keys
        self.assertIn("# 第 1–8 小节相当于 G 大调 / E 小调，升 5 个半音", result.text)
        self.assertIn("# 第 9–16 小节相当于 B♭ 大调 / G 小调，升 2 个半音", result.text)
        ninth = [line for line in result.text.splitlines() if not line.startswith(("#", "speed="))][8]
        self.assertIn("# 转调，从这里起升 2 个半音", ninth)

    def test_a_few_bars_leaning_to_the_dominant_keep_the_key(self):
        tune = [(i, i + 1, 72 + (0, 2, 4, 5, 7, 5, 4, 2)[i % 8]) for i in range(16)]
        lean = [(16 + i, 17 + i, (74, 78, 81, 79)[i % 4]) for i in range(8)]  # D F♯ A G, twice
        home = [(24 + start, 24 + end, pitch) for start, end, pitch in tune]
        result = arrange(parse_midi(song((0, tune + lean + home))))
        self.assertEqual([key.shift for key in result.keys], [0])
        self.assertEqual(len(result.changes), 2)  # the two F♯s move to a white key instead
        self.assertNotIn("转调", result.text)

    def test_a_borrowed_ending_keeps_the_key_and_the_final_chord(self):
        tune = [(i, i + 1, 72 + (0, 4, 7, 11, 12, 7, 4, 2)[i % 8]) for i in range(32)]  # eight bars in C
        roots = [48, 55] * 4 + [56, 58] * 2 + [48]  # C G ..., then A♭ B♭ A♭ B♭ (♭VI ♭VII), then C
        chords = [(bar * 4, bar * 4 + 4, root + step) for bar, root in enumerate(roots) for step in (0, 4, 7)]
        for bar, root in enumerate(roots[8:12], start=8):
            tune += [(bar * 4 + i, bar * 4 + i + 1, root + 12 + step) for i, step in enumerate((0, 4, 7, 4))]
        tune.append((48, 52, 72))
        result = arrange(parse_midi(song((0, tune), (1, chords))))
        self.assertEqual([key.shift for key in result.keys], [0])
        self.assertNotIn("转调", result.text)
        self.assertEqual(result.chords[-1].name, "C")

    def test_borrowed_notes_fill_bars_where_the_melody_rests(self):
        melody = [(i, i + 1, (72, 74, 76, 77)[i % 4]) for i in (*range(4), *range(8, 12))]
        pad = [(4, 8, 64), (4, 8, 67)]  # a sustained chord while the melody rests
        bass = [(i, i + 1, 36 + (i % 2) * 7) for i in range(12)]
        result = arrange(parse_midi(song((0, melody), (1, pad), (2, bass))), accompaniment="bass")
        bars = [line.split("|") for line in bars_of(result.text)]
        self.assertEqual([bar[0] for bar in bars], ["q-w-e-r-", "g----", "q-w-e-r-"])  # the pad's top note
        self.assertEqual([bar[1] for bar in bars], ["z-b-z-b-"] * 3)  # the bass is untouched
        unfilled = arrange(parse_midi(song((0, melody), (1, pad), (2, bass))), fill=False, accompaniment="bass")
        self.assertEqual(bars_of(unfilled.text)[1].split("|")[0], "0----")

    def test_the_bass_line_keeps_its_shape_across_two_rows(self):
        broken = [(i / 2, i / 2 + 0.5, 33 + step) for i, step in enumerate((0, -5, 7, 5, 12, 7))]  # A1 E1 E2 D2 A2 E2
        result = arrange(parse_midi(song((0, [(0, 3, 76)]), (1, broken), numerator=3)), accompaniment="bass")
        self.assertEqual(bars_of(result.text)[0].split("|")[1], "n.c.d.s.h.d.")

    def test_round_trip_through_every_score(self):
        """Each score, moved to E♭ with the melody an octave up, the
        accompaniment an octave down and a drum track added, comes back with
        the same melody and the same lowest accompaniment line."""
        for path in written_scores():
            with self.subTest(path.stem):
                original = load_score(path)
                top, bottom, melody, accompaniment = {}, {}, [], []
                for event in original.events:
                    if not event.strikes:
                        continue
                    pitches = [PITCH_OF[key] for key in event.keys]
                    items = [(event.start, event.start + event.duration, pitch) for pitch in pitches]
                    if event.part == 0:
                        top[event.start] = max(pitches)
                        melody += [(s, e, p + 3 + 12) for s, e, p in items]
                    else:
                        bottom[event.start] = min([*pitches, bottom.get(event.start, 127)])
                        accompaniment += [(s, e, p + 3 - 12) for s, e, p in items]
                drums = [(i, i + F(1, 8), 42) for i in range(int(original.total_beats))]
                midi = parse_midi(song((0, melody), (1, accompaniment), (9, drums), bpm=original.bpm))
                result = arrange(midi, fill=False, accompaniment="bass")
                self.assertEqual(result.shift, -3)
                self.assertTrue(result.exact)
                self.assertEqual((result.folded, result.changes), (0, []))
                score = parse_score(result.text)
                lines = [{event.start: pick(PITCH_OF[key] for key in event.keys)
                          for event in score.events if event.part == part and event.strikes}
                         for part, pick in ((0, max), (1, min))]
                self.assertEqual(lines[0], top)
                # Where the accompaniment rests, inner notes of melody chords join the bass
                # line, so only compare the accompaniment's own onsets.  The bass line stays
                # below the high row: higher notes come down an octave.
                self.assertEqual({start: lines[1].get(start) for start in bottom},
                                 {start: pitch - 12 * (pitch > 71) for start, pitch in bottom.items()})


class CommandLine(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.folder, ignore_errors=True))

    def run_main(self, *args):
        import contextlib, io
        from lyrebot.arrange import main
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main([str(arg) for arg in args])
        return code, output.getvalue()

    def test_format_0_files_split_by_channel_and_never_overwrite(self):
        track = [(0, -1, tempo(90))] + notes(0, [(i * PPQ, (i + 1) * PPQ, 72 + i % 3) for i in range(8)]) \
            + notes(9, [(i * PPQ, i * PPQ + 60, 36) for i in range(8)])
        source = self.folder / "曲子.mid"
        source.write_bytes(smf([track], fmt=0))
        target = self.folder / "曲子.txt"
        code, printed = self.run_main(source, "-o", target)
        self.assertEqual(code, 0)
        self.assertIn("未使用（鼓）", printed)
        self.assertEqual(parse_score(target.read_text(encoding="utf-8")).bpm, 90)
        self.run_main(source, "-o", target)
        self.assertTrue((self.folder / "曲子 (2).txt").exists())

    def test_list_writes_nothing_and_bad_files_fail(self):
        source = self.folder / "a.mid"
        source.write_bytes(song((0, [(0, 1, 60), (1, 2, 62)])))
        code, printed = self.run_main(source, "--list", "-o", self.folder / "a.txt")
        self.assertEqual(code, 0)
        self.assertIn("旋律", printed)
        self.assertFalse((self.folder / "a.txt").exists())
        broken = self.folder / "b.mid"
        broken.write_bytes(b"not a midi file")
        code, printed = self.run_main(broken, "-o", self.folder / "b.txt")
        self.assertEqual(code, 1)
        self.assertIn("无法编配", printed)


if __name__ == "__main__":
    unittest.main()
