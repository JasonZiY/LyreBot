"""Exercise the desktop workflow in hidden windows, with no OS key output."""

from pathlib import Path
import os
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import lyrebot
from lyrebot.app import APP_TITLE, LyreBotApp
from lyrebot.playback import PlaybackResult, compile_timeline
from lyrebot.score import load_score
from lyrebot.windows_input import WindowInfo


PROJECT = Path(__file__).resolve().parent.parent


class BundledScoreTests(unittest.TestCase):
    def test_launcher_and_import_use_lyrebot_name(self):
        launcher = (PROJECT / "Start LyreBot.cmd").read_text(encoding="utf-8")
        self.assertEqual(lyrebot.__name__, "lyrebot")
        self.assertIn("python -m lyrebot", launcher)
        self.assertIn("py -3 -m lyrebot", launcher)

    def test_all_music_scores_compile(self):
        scores = list((PROJECT / "music").glob("*.txt"))
        for path in scores:
            with self.subTest(score=path.name):
                score = load_score(path)
                timeline = compile_timeline(score)
                self.assertAlmostEqual(timeline.duration_seconds, score.duration_seconds)


class DesktopTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.music = Path(directory.name)
        (self.music / "乙_好曲.txt").write_text("speed=60\na-s-d-f-\n", encoding="utf-8")
        (self.music / "甲_坏曲.txt").write_text("speed=60\nk-\n", encoding="utf-8")
        self.app = LyreBotApp(self.root, music_dir=self.music)

    def choose(self, stem):
        index = [path.stem for path in self.app.songs].index(stem)
        self.app.song_list.selection_clear(0, "end")
        self.app.song_list.selection_set(index)
        self.app.select_song()

    def finish_worker(self):
        self.app.worker.join(timeout=2)
        self.assertFalse(self.app.worker.is_alive(), "Playback worker did not stop")
        self.app._poll()

    def test_lists_music_folder_and_flags_bad_scores(self):
        self.assertEqual(self.root.title(), APP_TITLE)
        self.assertEqual(sorted(self.app.song_list.get(0, "end")), ["乙_好曲", "甲_坏曲"])
        self.choose("甲_坏曲")
        self.assertIsNone(self.app.score)
        self.assertIn("曲谱有误", self.app.summary.get())
        self.choose("乙_好曲")
        self.assertIsNotNone(self.app.score)

    def test_start_without_valid_score_never_creates_sender(self):
        with patch("lyrebot.app.WindowsInput") as sender, patch("lyrebot.app.messagebox.showinfo") as info:
            self.app.start()
            sender.assert_not_called()
            info.assert_called_once()
        self.assertIsNone(self.app.worker)

    def test_stop_during_countdown_never_reaches_player(self):
        self.choose("乙_好曲")
        backend = Mock()
        backend.stop_requested.return_value = True
        with patch("lyrebot.app.WindowsInput", return_value=backend), patch("lyrebot.app.play_timeline") as player:
            self.app.start()
            self.finish_worker()
            player.assert_not_called()
        backend.release_all.assert_called_once()
        self.assertEqual(self.app.status.get(), "已在倒计时中停止。")

    def test_own_window_in_front_sends_nothing(self):
        self.choose("乙_好曲")
        backend = Mock()
        backend.stop_requested.return_value = False
        with patch("lyrebot.app.WindowsInput", return_value=backend), \
                patch("lyrebot.app.time.perf_counter", side_effect=[100.0, 106.0]), \
                patch("lyrebot.app.get_foreground_window", return_value=WindowInfo(7, APP_TITLE, os.getpid())), \
                patch("lyrebot.app.play_timeline") as player:
            self.app.start()
            self.finish_worker()
            player.assert_not_called()
        self.assertIn("前台仍是 LyreBot", self.app.status.get())

    def test_plays_to_foreground_window_with_speed_percent(self):
        self.choose("乙_好曲")
        self.app.speed.set("200")
        backend = Mock()
        backend.stop_requested.return_value = False
        with patch("lyrebot.app.WindowsInput", return_value=backend), \
                patch("lyrebot.app.time.perf_counter", side_effect=[100.0, 106.0]), \
                patch("lyrebot.app.get_foreground_window", return_value=WindowInfo(42, "原神", 1)), \
                patch("lyrebot.app.play_timeline", return_value=PlaybackResult("completed", 2)) as player:
            self.app.start()
            self.finish_worker()
        arguments = player.call_args.args
        self.assertEqual(arguments[2], 42)
        self.assertEqual(arguments[0].duration_seconds, 2)  # 4 beats at 120 BPM
        self.assertEqual(self.app.status.get(), "演奏完成。")
        backend.release_all.assert_called_once()


if __name__ == "__main__":
    unittest.main()
