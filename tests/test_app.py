import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from lyrebot.app import (IN_ORDER, REGIONS, SETTLE, SHUFFLE, SINGLE, in_columns, midi_for, name_problem, next_song,
                         pending_audio, pending_midi, read_library, recording_name, score_name, song_files)

try:  # run as part of the tests package, or by unittest discovery from the tests folder
    from .test_arrange import song
    from .test_transcribe import fake_tools
except ImportError:
    from test_arrange import song
    from test_transcribe import fake_tools

SONGS = [Path("a.txt"), Path("b.txt"), Path("c.txt")]
_TK = None  # the one Tk interpreter the window tests share


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


class TypedSpeed(unittest.TestCase):
    def test_a_percentage_may_be_written_several_ways(self):
        from lyrebot.app import parse_speed
        for text, percent in (("135", 135.0), (" 87.5% ", 87.5), ("90％", 90.0), ("25", 25.0), ("300%", 300.0)):
            self.assertEqual(parse_speed(text), percent)

    def test_what_cannot_be_played_is_refused(self):
        from lyrebot.app import parse_speed
        for text in ("", "%", "快", "24.9", "300.1", "-100", "nan", "inf"):
            self.assertIsNone(parse_speed(text))


class Folders(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.midi, self.scores = self.root / "midi", self.root / "txt"
        self.midi.mkdir()
        self.scores.mkdir()

    def window(self):
        """A hidden window for the app, closed after the test with its timers cancelled.

        Every test's window sits on one Tk interpreter, made once: creating
        and destroying a Tk root per test now and then leaves Tcl unable to
        start the next one ("invalid command name tcl_findLibrary"), and the
        test would quietly be skipped.  Timers are shared by the interpreter,
        so one left behind would fire during a later test's update() and
        complain.  The window's images are freed on the spot: left to the
        garbage collector, they can be freed in a later test's worker thread,
        where each one waits a second for a main loop that isn't running (a
        20 s join then times out).
        """
        import gc
        import tkinter as tk
        global _TK
        if _TK is None:
            try:
                _TK = tk.Tk()
            except tk.TclError as error:
                self.skipTest(f"no display: {error}")
            _TK.withdraw()
        root = tk.Toplevel(_TK)

        def close():
            for job in root.tk.splitlist(root.tk.call("after", "info")):
                root.after_cancel(job)
            root.destroy()
            gc.collect()
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
        self.add_midi("老歌.mid")
        (self.scores / "老歌-MIDI.txt").write_text("speed=60\na-\n", encoding="utf-8")
        self.assertEqual(pending_midi(self.midi, self.scores, {}), [new])

    def test_a_midi_file_makes_a_midi_score_and_a_recording_keeps_its_name(self):
        self.assertEqual(score_name(self.add_midi("飞鼠进行曲.mid")), "飞鼠进行曲-MIDI")
        self.assertEqual(score_name(self.add_midi("Literature-MIDI.mid")), "Literature-MIDI")
        self.assertEqual(score_name(self.add_midi("飞鼠进行曲-录音.mid")), "飞鼠进行曲-录音")  # its audio is gone
        self.add_audio("录音 0101-1200.wav")
        self.assertEqual(score_name(self.add_midi("录音 0101-1200.mid")), "录音 0101-1200")
        self.add_audio("别人的歌.MP3")
        self.assertEqual(score_name(self.add_midi("别人的歌.mid")), "别人的歌")  # transcribed from that MP3

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

    def test_the_window_writes_down_the_keys_played(self):
        from unittest import mock
        from lyrebot.app import App
        root = self.window()
        app = App(root, self.scores, self.midi)
        presses = [(i * 0.3, "asdfghjq"[i % 8]) for i in range(24)]
        heard = []

        def listen(cancel, report):
            report("listening", 1.0, 3)
            heard.append(app.keys_button.enabled)  # live, to stop with
            return presses

        with mock.patch("lyrebot.app.listen", side_effect=listen), \
                mock.patch("lyrebot.app.simpledialog.askstring", return_value="小调"):
            app.listen()
            self.assertTrue(app.listening)
            self.assertFalse(app.button.enabled)
            app.worker.join(20)
            app.poll()
        target = self.scores / "小调-演奏.txt"
        self.assertTrue(target.read_text(encoding="utf-8").startswith("# 由键盘演奏记录：小调-演奏\n"))
        self.assertEqual(heard, [True])
        self.assertFalse(app.listening)
        self.assertEqual(app.selected(), target)
        root.update()  # the selection event that follows must not wipe the note
        self.assertIn("刚从演奏记录好，100% 的按键照原样弹回", app.status_label.cget("text"))
        self.assertTrue(app.keys_button.enabled)

        with mock.patch("lyrebot.app.listen", return_value=[]), \
                mock.patch("lyrebot.app.simpledialog.askstring", return_value=""):
            app.quiet_until = 0
            app.listen()
            app.worker.join(20)
            app.poll()
        self.assertIn("没有记下", app.status_label.cget("text"))

    def test_the_keys_played_are_named_after_their_song(self):
        self.assertEqual(recording_name("飞鼠进行曲", "-演奏"), "飞鼠进行曲-演奏")
        self.assertTrue(recording_name("", "-演奏").startswith("演奏 "))

    def test_a_renamed_score_keeps_its_midi_and_recording(self):
        """Renaming 录音 0101-1200.txt must not bring it back from its MIDI file,
        nor its MIDI file back from the recording, and 从 MIDI 重新编配 still
        finds the MIDI file."""
        old = time.time() - 10
        source = self.add_midi("录音 0101-1200.mid")
        audio = self.add_audio("录音 0101-1200.wav")
        renamed = self.scores / "冰湖-录音.txt"
        renamed.write_text("# 由 MIDI 自动编配：录音 0101-1200.mid\nspeed=60\na-\n", encoding="utf-8")
        for path in (source, audio):
            os.utime(path, (old, old))
        self.assertEqual(pending_midi(self.midi, self.scores, {}), [])
        source.unlink()  # even with the MIDI file gone, the recording is not transcribed again
        self.assertEqual(pending_audio(self.midi, self.scores, {}), [])
        source = self.add_midi("录音 0101-1200.mid")
        self.assertEqual(midi_for(renamed, self.midi), source)

    def write_scores(self, *names):
        for name in names:
            path = self.scores / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("speed=60\na-\n", encoding="utf-8")
        return [self.scores / name for name in names]

    def test_scores_sort_into_regions_songs_and_unfiled(self):
        self.write_scores("OST/璃月/清润玉响.txt", "OST/蒙德/风息.txt", "OST/蒙德/白日梦.txt", "OST/散落.txt",
                          "OST/新地区/新歌.txt", "歌曲/春日影.txt", "歌曲/分组/Lemon.txt", "未分.txt", "别的/杂项.txt")
        (self.scores / "OST" / "须弥").mkdir()
        library = read_library(self.scores)
        self.assertEqual([name for name, _ in library.regions], [*REGIONS[:-1], "新地区", "其他"])
        regions = {name: [path.stem for path in paths] for name, paths in library.regions}
        self.assertEqual(regions["蒙德"], ["白日梦", "风息"])  # in pinyin order
        self.assertEqual(regions["其他"], ["散落"])  # directly in OST
        self.assertEqual(regions["须弥"], [])
        self.assertEqual({path.stem for path in library.songs}, {"春日影", "Lemon"})
        self.assertEqual({path.stem for path in library.unfiled}, {"未分", "杂项"})
        self.assertEqual([path.stem for path in library.ost], ["白日梦", "风息", "清润玉响", "新歌", "散落"])

    def test_scores_in_folders_keep_their_midi_and_names(self):
        self.write_scores("OST/蒙德/林中晨歌.txt", "歌曲/春日影.txt")
        self.add_midi("林中晨歌.mid")
        self.add_audio("春日影.wav")
        self.assertEqual(pending_midi(self.midi, self.scores, {}), [])
        self.assertEqual(pending_audio(self.midi, self.scores, {}), [])
        self.assertIn("已经有", name_problem("林中晨歌", self.scores, self.midi))

    def test_playlists_stay_apart_and_go_region_by_region(self):
        last, first, song_one, song_two, alone = self.write_scores(
            "OST/璃月/璃月曲.txt", "OST/蒙德/蒙德曲.txt", "歌曲/a.txt", "歌曲/b.txt", "未分.txt")
        library = read_library(self.scores)
        self.assertEqual(library.playlist(first), [first, last])
        self.assertEqual(next_song(library.playlist(first), first, IN_ORDER), last)
        self.assertEqual(next_song(library.playlist(last), last, IN_ORDER), first)  # back to the first region
        self.assertEqual(library.playlist(song_two), [song_one, song_two])
        self.assertEqual(library.playlist(alone), [])
        for _ in range(30):
            self.assertEqual(next_song(library.playlist(first), first, SHUFFLE), last)

    def test_columns_run_down_and_the_first_are_longer(self):
        self.assertEqual([len(column) for column in in_columns(tuple(range(7)), 2)], [4, 3])
        self.assertEqual([len(column) for column in in_columns(tuple(range(13)), 3)], [5, 4, 4])
        self.assertEqual(in_columns(tuple(range(3)), 2), [(0, 1), (2,)])
        self.assertEqual(in_columns((), 3), [(), (), ()])

    def click(self, app, cell, double=False):
        class Click:
            x = (cell.box[0] + cell.box[2]) / 2
            y = (cell.box[1] + cell.box[3]) / 2
        return (app.browser.double_click if double else app.browser.click)(Click)

    def cell(self, app, kind, name):
        return next(cell for cell in app.browser.cells
                    if cell.kind == kind and (cell.name == name or (cell.path and cell.path.stem == name)))

    def test_the_list_opens_with_shelves_open_and_regions_closed(self):
        from lyrebot.app import App
        self.write_scores("未分.txt", "OST/蒙德/甲.txt", "OST/蒙德/乙.txt", "OST/蒙德/丙.txt",
                          "歌曲/一.txt", "歌曲/二.txt", "歌曲/三.txt", "歌曲/四.txt")
        app = App(self.window(), self.scores, self.midi)
        browser = app.browser
        width = int(browser.cget("width"))
        self.assertEqual(app.selected(), self.scores / "未分.txt")
        self.assertEqual({path.stem for path in browser.visible_songs()}, {"未分", "一", "二", "三", "四"})
        self.assertEqual([cell.name for cell in browser.cells if cell.kind == "region"], list(REGIONS))
        unfiled = self.cell(app, "song", "未分")
        self.assertEqual((unfiled.box[0], unfiled.box[2]), (0, width))

        edges = browser.column_edges(width)
        region = self.cell(app, "region", "蒙德")
        below = self.cell(app, "region", "璃月").box[1]
        self.assertEqual(region.box[0], edges[0])
        self.click(app, region)
        songs = [cell for cell in browser.cells if cell.path and cell.path.parent.name == "蒙德"]
        self.assertEqual(sorted({cell.box[0] for cell in songs}), [edges[1], edges[2]])  # two columns beside it
        self.assertEqual(self.cell(app, "region", "璃月").box[1], below + browser.row)  # 2 rows: the next moves down
        shelf = [cell for cell in browser.cells if cell.path and cell.path.parent.name == "歌曲"]
        self.assertEqual(sorted({cell.box[0] for cell in shelf}), edges[:3])  # three, under those columns

        self.click(app, self.cell(app, "region", "蒙德"))
        self.assertNotIn(self.scores / "OST" / "蒙德" / "甲.txt", browser.visible_songs())
        self.click(app, self.cell(app, "shelf", "OST"))
        self.assertEqual([cell for cell in browser.cells if cell.kind == "region"], [])
        app.select(self.scores / "OST" / "蒙德" / "甲.txt")  # playing or choosing a song opens the way to it
        self.assertIn(self.scores / "OST" / "蒙德" / "甲.txt", browser.visible_songs())
        (self.scores / "OST" / "蒙德" / "新.txt").write_text("speed=60\na-\n", encoding="utf-8")
        app.refresh()  # the list is rebuilt; what was open stays open
        self.assertIn(self.scores / "OST" / "蒙德" / "新.txt", browser.visible_songs())

    def test_clicks_choose_and_double_clicks_play(self):
        from unittest import mock
        from lyrebot.app import App
        one, two = self.write_scores("歌曲/一.txt", "歌曲/二.txt")
        app = App(self.window(), self.scores, self.midi)
        self.click(app, self.cell(app, "song", "一"))
        self.assertEqual(app.selected(), one)
        self.assertEqual(app.title_label.cget("text"), "一")
        with mock.patch.object(app.browser, "on_play") as play:
            self.click(app, self.cell(app, "song", "二"), double=True)
        play.assert_called_once_with()
        songs = app.browser.visible_songs()
        app.browser.step(songs.index(two) - songs.index(one))
        self.assertEqual(app.selected(), two)
        app.worker = object()  # while something plays, the list stays as it is
        self.click(app, self.cell(app, "song", "一"))
        self.assertEqual(app.selected(), two)
        app.worker = None

    def test_an_unfiled_song_plays_alone(self):
        from unittest import mock
        from lyrebot.app import App
        alone, first, second = self.write_scores("未分.txt", "OST/蒙德/甲.txt", "OST/璃月/乙.txt")
        app = App(self.window(), self.scores, self.midi)
        app.select(first)
        app.mode.set(IN_ORDER)
        app.select(alone)
        self.assertEqual(app.mode.get(), SINGLE)
        self.assertFalse(app.mode_choice.enabled)
        self.assertFalse(app.next_button.enabled)
        with mock.patch.object(app, "start") as start:
            app.play_next()
        start.assert_not_called()
        app.select(first)
        self.assertEqual(app.mode.get(), IN_ORDER)  # the choice comes back with a playlist
        self.assertTrue(app.mode_choice.enabled)
        self.assertTrue(app.next_button.enabled)
        with mock.patch.object(app, "start") as start:
            app.play_next()
        start.assert_called_once_with(second)

    def test_playing_on_finishes_a_region_before_the_next(self):
        from unittest import mock
        from lyrebot.app import App
        second, first, third, _ = self.write_scores("OST/蒙德/风.txt", "OST/蒙德/白.txt", "OST/璃月/月.txt", "未分.txt")
        app = App(self.window(), self.scores, self.midi)
        results = iter(["done", "done", "stopped"])
        with mock.patch("lyrebot.app.COUNTDOWN", 0), mock.patch("lyrebot.app.GAP", 0), \
                mock.patch("lyrebot.app.Keyboard"), mock.patch("lyrebot.app.play", side_effect=lambda *a, **k: next(results)), \
                mock.patch("lyrebot.app.foreground_title_and_pid", return_value=("游戏", os.getpid() + 1)):
            app.run(first, mock.Mock(duration=1.0), 0.0, 1.0, IN_ORDER, app.playlist(first))
        played = []
        while not app.updates.empty():
            kind, value = app.updates.get()
            if kind == "song":
                played.append(value[0])
        self.assertEqual(played, [first, second, third])

    def test_the_menu_opens_the_folder_clicked(self):
        from unittest import mock
        from lyrebot.app import App
        self.write_scores("OST/蒙德/甲.txt")
        app = App(self.window(), self.scores, self.midi)
        cell = self.cell(app, "region", "须弥")

        class Click:
            x, y = (cell.box[0] + 1, cell.box[1] + 1)
            x_root = y_root = 0
        with mock.patch.object(app.menu, "tk_popup"), mock.patch("lyrebot.app.os.startfile", create=True) as opened:
            app.context_menu(Click)
            self.assertEqual(app.menu.entrycget(0, "label"), "打开「须弥」文件夹")
            app.menu.invoke(0)
        opened.assert_called_once_with(self.scores / "OST" / "须弥")
        self.assertTrue((self.scores / "OST" / "须弥").is_dir())

    def test_the_speed_menu_takes_a_percentage_typed_in(self):
        from unittest import mock
        from lyrebot.app import App, clock
        (self.scores / "小曲.txt").write_text("speed=60\na-s-d-f-\n", encoding="utf-8")
        root = self.window()
        app = App(root, self.scores, self.midi)
        app.select(self.scores / "小曲.txt")
        menu = app.speed_choice.menu
        last = menu.index("end")
        self.assertEqual(menu.entrycget(last, "label"), "自定义…")
        with mock.patch("lyrebot.app.simpledialog.askstring", side_effect=["快一点", "400", "125%"]) as asked:
            menu.invoke(last)
        self.assertEqual(asked.call_count, 3)
        self.assertIn("25 到 300", asked.call_args.args[1])
        self.assertEqual(app.speed.get(), "125%")
        self.assertEqual(app.speed_factor(), 1.25)
        self.assertTrue(app.speed_choice.cget("text").startswith("125%"))
        self.assertEqual(app.time_label.cget("text"), clock(4 / 1.25))
        with mock.patch("lyrebot.app.simpledialog.askstring", return_value=None):
            menu.invoke(last)  # cancelling keeps the speed
        self.assertEqual(app.speed.get(), "125%")
        menu.invoke(0)  # and the usual speeds are still there
        self.assertEqual(app.speed.get(), "80%")

    def test_the_menu_exports_a_score_as_jianpu(self):
        from unittest import mock
        from lyrebot.app import App
        (self.scores / "小曲.txt").write_text("speed=90\na-s-d-f-|z----\n", encoding="utf-8")
        root = self.window()
        app = App(root, self.scores, self.midi, jianpu_dir=self.root / "jianpu_output")
        self.assertEqual(app.menu.entrycget(4, "label"), "导出简谱")
        app.select(self.scores / "小曲.txt")
        with mock.patch.object(app, "open_file") as opened:
            target = app.export_jianpu()
        self.assertEqual(target, self.root / "jianpu_output" / "小曲.html")
        self.assertIn("<title>小曲 · 诗琴简谱</title>", target.read_text(encoding="utf-8"))
        opened.assert_called_once_with(target)
        self.assertIn("简谱已存进 jianpu_output 文件夹", app.status_label.cget("text"))

    def test_a_score_finds_its_midi_by_name(self):
        source = self.add_midi("Literature.mid")
        self.assertEqual(midi_for(self.scores / "literature.txt", self.midi), source)
        self.assertEqual(midi_for(self.scores / "Literature-MIDI.txt", self.midi), source)
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
        target = self.scores / "曲子-MIDI.txt"
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
        self.assertEqual(sorted(path.name for path in self.scores.iterdir()), ["另一首.txt", "曲子-MIDI.txt"])

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

    def test_deleting_a_score_takes_what_it_was_made_from(self):
        """A score goes with the MIDI file and recording it was made from, and
        nothing else: left behind, they would make it again."""
        (self.scores / "ost").mkdir()
        hand = self.scores / "ost" / "林中晨歌.txt"
        played = self.scores / "春日影-演奏.txt"
        midi_score = self.scores / "Literature-MIDI.txt"
        recorded = self.scores / "ost" / "冰湖-录音.txt"
        dropped = self.scores / "别人的歌.txt"  # transcribed from an MP3 put in the midi folder, so no suffix
        renamed = self.scores / "冰湖 新版-录音.txt"
        hand.write_text("speed=60\na-\n", encoding="utf-8")
        played.write_text("# 由键盘演奏记录：春日影-演奏\nspeed=60\na-\n", encoding="utf-8")
        midi_score.write_text("# 由 MIDI 自动编配：Literature.mid\nspeed=60\na-\n", encoding="utf-8")
        recorded.write_text("speed=60\na-\n", encoding="utf-8")  # its first line taken out
        dropped.write_text("# 由 MIDI 自动编配：别人的歌.mid\nspeed=60\na-\n", encoding="utf-8")
        renamed.write_text("# 由 MIDI 自动编配：录音 0101-1200.mid\nspeed=60\na-\n", encoding="utf-8")
        for name in ("林中晨歌.mid", "Literature.mid", "冰湖-录音.mid", "别人的歌.mid"):
            self.add_midi(name)
        for name in ("林中晨歌.wav", "冰湖-录音.wav", "别人的歌.MP3", "录音 0101-1200.wav"):
            self.add_audio(name)
        self.add_audio("冰湖-录音.wav.part")  # still being recorded: not audio yet
        midi = self.midi
        self.assertEqual(song_files(hand, midi), [hand])  # a hand-written score keeps its sources
        self.assertEqual(song_files(played, midi), [played])
        self.assertEqual(song_files(midi_score, midi), [midi_score, midi / "Literature.mid"])
        self.assertEqual(song_files(recorded, midi), [recorded, midi / "冰湖-录音.mid", midi / "冰湖-录音.wav"])
        self.assertEqual(song_files(dropped, midi), [dropped, midi / "别人的歌.mid", midi / "别人的歌.MP3"])
        self.assertEqual(song_files(renamed, midi), [renamed, midi / "录音 0101-1200.wav"])  # its MIDI is gone

        from unittest import mock
        from lyrebot.app import App
        root = self.window()
        pages = self.root / "jianpu_output"
        pages.mkdir()
        (pages / "冰湖-录音.html").write_text("<html></html>", encoding="utf-8")
        (pages / "林中晨歌.html").write_text("<html></html>", encoding="utf-8")
        app = App(root, self.scores, self.midi, jianpu_dir=pages)
        self.assertEqual(app.menu.entrycget(6, "label"), "删除")
        app.select(recorded)
        gone = []

        def recycle(path):
            gone.append(path.name)
            path.unlink()
        with mock.patch("lyrebot.app.messagebox.askyesno", return_value=False) as asked, \
                mock.patch("lyrebot.app.to_recycle_bin", side_effect=recycle):
            app.delete()  # declined: nothing goes
            self.assertEqual(gone, [])
            question = asked.call_args.args[1]
            for shown in (r"txt\ost\冰湖-录音.txt", r"midi\冰湖-录音.mid", r"midi\冰湖-录音.wav",
                          r"jianpu_output\冰湖-录音.html"):
                self.assertIn(shown.replace("\\", os.sep), question)
            self.assertNotIn(".part", question)
            asked.return_value = True
            following = app.songs[app.songs.index(recorded) + 1]
            app.delete()
        self.assertEqual(gone, ["冰湖-录音.html", "冰湖-录音.wav", "冰湖-录音.mid", "冰湖-录音.txt"])  # the score last
        self.assertTrue((pages / "林中晨歌.html").exists())  # another song's page stays
        self.assertNotIn(recorded, app.songs)
        self.assertEqual(app.selected(), following)
        root.update()
        self.assertIn("已把「冰湖-录音」移到回收站", app.status_label.cget("text"))
        app.arrange_next()
        app.transcribe_next()
        self.assertFalse(recorded.exists())  # nothing left to make it again

        app.select(hand)
        with mock.patch("lyrebot.app.messagebox.askyesno", return_value=True), \
                mock.patch("lyrebot.app.messagebox.showerror") as told, \
                mock.patch("lyrebot.app.to_recycle_bin", side_effect=OSError("文件正被占用")):
            app.delete()
        self.assertIn("文件正被占用", told.call_args.args[1])
        self.assertTrue(hand.exists())
        self.assertEqual(app.selected(), hand)


if __name__ == "__main__":
    unittest.main()
