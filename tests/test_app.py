import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from lyrebot.app import (IN_ORDER, SETTLE, SHUFFLE, SINGLE, midi_for, name_problem, next_song, pending_audio,
                         pending_midi, recording_name)

try:  # run as part of the tests package, or by unittest discovery from the tests folder
    from .test_arrange import song
    from .test_transcribe import fake_tools
except ImportError:
    from test_arrange import song
    from test_transcribe import fake_tools

SONGS = [Path("a.txt"), Path("b.txt"), Path("c.txt")]


class NextSong(unittest.TestCase):
    def test_in_order_wraps_around(self):
        self.assertEqual(next_song(SONGS, SONGS[1], IN_ORDER), SONGS[2])
        self.assertEqual(next_song(SONGS, SONGS[2], IN_ORDER), SONGS[0])

    def test_single_mode_next_button_moves_down_the_list(self):
        self.assertEqual(next_song(SONGS, SONGS[0], SINGLE), SONGS[1])

    def test_shuffle_never_repeats_the_current_song(self):
        for _ in range(50):
            self.assertNotEqual(next_song(SONGS, SONGS[0], SHUFFLE), SONGS[0])
        self.assertEqual(next_song(SONGS[:1], SONGS[0], SHUFFLE), SONGS[0])

    def test_empty_list(self):
        self.assertIsNone(next_song([], None, IN_ORDER))


class Folders(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.midi, self.scores = self.root / "midi", self.root / "txt"
        self.midi.mkdir()
        self.scores.mkdir()

    def window(self):
        """A hidden Tk root, closed after the test with its timers cancelled.

        Tk's event loop is shared by every root in the thread, so a timer left
        behind would fire during a later test's update() and complain.
        """
        import tkinter as tk
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"no display: {error}")

        def close():
            for job in root.tk.splitlist(root.tk.call("after", "info")):
                root.after_cancel(job)
            root.destroy()
        self.addCleanup(close)
        root.withdraw()
        return root

    def add_midi(self, name, age=10.0):
        path = self.midi / name
        path.write_bytes(song((0, [(0, 1, 72), (1, 2, 74), (2, 3, 76), (3, 4, 77)])))
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
        return path

    def test_only_midi_files_without_a_score_are_pending(self):
        new = self.add_midi("新歌.mid")
        self.add_midi("旧歌.MIDI")
        (self.scores / "旧歌.txt").write_text("speed=60\na-\n", encoding="utf-8")
        (self.midi / "说明.txt").write_text("not a MIDI file", encoding="utf-8")
        self.assertEqual(pending_midi(self.midi, self.scores, {}), [new])

    def test_files_still_copying_and_known_failures_wait(self):
        fresh = self.add_midi("fresh.mid", age=0)
        self.assertEqual(pending_midi(self.midi, self.scores, {}), [])
        self.assertEqual(pending_midi(self.midi, self.scores, {}, now=time.time() + SETTLE + 0.1), [fresh])
        broken = self.add_midi("broken.mid")
        failed = {broken: broken.stat().st_mtime}
        self.assertNotIn(broken, pending_midi(self.midi, self.scores, failed))
        os.utime(broken, None)  # replaced with a new copy: try again once it settles
        self.assertIn(broken, pending_midi(self.midi, self.scores, failed, now=time.time() + SETTLE + 0.1))

    def add_audio(self, name, age=10.0):
        path = self.midi / name
        path.write_bytes(b"RIFF")
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
        return path

    def test_audio_waits_for_transcription_until_it_has_a_midi_or_score(self):
        new = self.add_audio("新录的.wav")
        self.add_audio("转过的.mp3")
        self.add_midi("转过的.mid")
        self.add_audio("编过的.flac")
        (self.scores / "编过的.txt").write_text("speed=60\na-\n", encoding="utf-8")
        self.add_audio("还在录.wav.part")
        fresh = self.add_audio("刚放进来.ogg", age=0)
        self.assertEqual(pending_audio(self.midi, self.scores, {}), [new])
        self.assertIn(fresh, pending_audio(self.midi, self.scores, {}, now=time.time() + SETTLE + 0.1))
        self.assertNotIn(new, pending_audio(self.midi, self.scores, {new: new.stat().st_mtime}))

    def test_a_recording_is_named_after_its_song(self):
        self.assertEqual(recording_name(" 飞鼠进行曲 "), "飞鼠进行曲-录音")
        self.assertEqual(recording_name("飞鼠进行曲-录音"), "飞鼠进行曲-录音")
        self.assertTrue(recording_name("").startswith("录音 "))

    def test_recording_names(self):
        self.add_midi("旧歌.mid")
        (self.scores / "曲谱.txt").write_text("speed=60\na-\n", encoding="utf-8")
        self.assertIsNone(name_problem("新歌 (live)", self.scores, self.midi))
        for taken in ("旧歌", "曲谱"):
            self.assertIn("已经有", name_problem(taken, self.scores, self.midi))
        for bad in ("", "a/b", "问号?", "结尾点."):
            self.assertIn("不能", name_problem(bad, self.scores, self.midi))

    def test_the_window_transcribes_new_audio_then_arranges_it(self):
        from unittest import mock
        from lyrebot.app import App
        root = self.window()
        tools_dir = self.root / "tools"
        tools_dir.mkdir()
        tools = fake_tools(tools_dir)
        (tools_dir / "answer.mid").write_bytes(song((0, [(0, 1, 72), (1, 2, 74), (2, 3, 76), (3, 4, 77)])))
        self.add_audio("录的歌.wav")
        with mock.patch("lyrebot.app.find_tools", return_value=tools):
            app = App(root, self.scores, self.midi, tool_dirs=[])
            app.transcribe_next()
            self.assertFalse(app.record_button.enabled)  # one transcription at a time
            app.transcriber.join(20)
            app.poll()
        self.assertTrue((self.midi / "录的歌.mid").exists())
        target = self.scores / "录的歌.txt"
        self.assertTrue(target.exists())
        self.assertEqual(app.selected(), target)
        root.update()  # the selection event that follows must not wipe the note
        self.assertIn("刚从录音编配好 · ", app.status_label.cget("text"))
        self.assertTrue(app.record_button.enabled)

        (tools_dir / "mode.txt").write_text("broken")
        broken = self.add_audio("坏的.wav")
        with mock.patch("lyrebot.app.find_tools", return_value=tools):
            app.transcribe_next()
            app.transcriber.join(20)
            app.poll()
            self.assertIn("无法转录", app.status_label.cget("text"))
            app.transcribe_next()  # a failure is not retried until the file changes
            self.assertIsNone(app.transcriber)
        self.assertEqual(app.failed_audio, {broken: broken.stat().st_mtime})

    def test_recording_needs_the_tools_and_a_name(self):
        from unittest import mock
        from lyrebot.app import App
        root = self.window()
        app = App(root, self.scores, self.midi, tool_dirs=[self.root / "no tools here"])
        with mock.patch("lyrebot.app.messagebox.showinfo") as told:
            app.record()
        told.assert_called_once()
        self.assertIsNone(app.worker)
        self.add_midi("旧歌-录音.mid")
        answers = ["旧歌", "a:b", None]
        with mock.patch("lyrebot.app.find_tools", return_value=fake_tools(self.root)), \
                mock.patch("lyrebot.app.simpledialog.askstring", side_effect=answers) as asked:
            app.record()  # two names refused, then cancelled
        self.assertEqual(asked.call_count, 3)
        self.assertIn("已经有叫「旧歌-录音」", asked.call_args_list[1].args[1])
        self.assertIn("不能", asked.call_args_list[2].args[1])
        self.assertEqual(asked.call_args_list[1].kwargs["initialvalue"], "旧歌")  # the title is kept to edit
        self.assertIsNone(app.worker)
        self.assertFalse(app.recording)

    def test_a_score_finds_its_midi_by_name(self):
        source = self.add_midi("Literature.mid")
        self.assertEqual(midi_for(self.scores / "literature.txt", self.midi), source)
        self.assertIsNone(midi_for(self.scores / "别的.txt", self.midi))
        self.assertIsNone(midi_for(self.scores / "a.txt", self.root / "missing"))

    def test_the_window_arranges_a_new_midi_and_selects_it(self):
        from lyrebot.app import App
        root = self.window()
        self.add_midi("曲子.mid")
        (self.midi / "坏的.mid").write_bytes(b"not a midi file")
        os.utime(self.midi / "坏的.mid", (time.time() - 10, time.time() - 10))
        app = App(root, self.scores, self.midi)
        app.arrange_next()  # the broken file sorts first and fails
        self.assertIn("无法编配", app.status_label.cget("text"))
        app.arrange_next()
        target = self.scores / "曲子.txt"
        self.assertTrue(target.exists())
        self.assertEqual(app.selected(), target)
        root.update()  # the selection event that follows must not wipe the note
        self.assertIn("刚从 MIDI 编配好 · ", app.status_label.cget("text"))
        (self.scores / "另一首.txt").write_text("speed=60\na-\n", encoding="utf-8")
        app.refresh()
        app.select(self.scores / "另一首.txt")
        app.select(target)  # the note was for that moment only
        self.assertNotIn("编配好", app.status_label.cget("text"))
        app.arrange_next()  # nothing left: the failure is not retried, the score is not redone
        self.assertEqual(sorted(path.name for path in self.scores.iterdir()), ["另一首.txt", "曲子.txt"])

        from unittest import mock
        arranged = target.read_text(encoding="utf-8")
        target.write_text(arranged.replace("speed=", "# 手改过\nspeed=", 1), encoding="utf-8")
        with mock.patch("lyrebot.app.messagebox.askyesno", return_value=False):
            app.rearrange()  # declined: the edit stays
        self.assertIn("手改过", target.read_text(encoding="utf-8"))
        with mock.patch("lyrebot.app.messagebox.askyesno", return_value=True):
            app.rearrange()
        self.assertEqual(target.read_text(encoding="utf-8"), arranged)
        self.assertIn("已重新编配", app.status_label.cget("text"))


if __name__ == "__main__":
    unittest.main()
