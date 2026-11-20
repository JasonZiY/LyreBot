import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest

from lyrebot.app import (IN_ORDER, REGIONS, REPEAT, SETTLE, SHUFFLE, SINGLE, in_columns, midi_for, name_problem, next_match,
                         next_song,
                         pending_audio, pending_midi, read_library, recording_name, score_name, song_files)

try:  # run as part of the tests package, or by unittest discovery from the tests folder
    from .test_arrange import song
    from .test_transcribe import fake_tools
except ImportError:
    from test_arrange import song
    from test_transcribe import fake_tools

SONGS = [Path("a.txt"), Path("b.txt"), Path("c.txt")]
_TK = None  # the one Tk interpreter the window tests share


def hidden_window(test):
    """A hidden window for a test, closed after it with its timers cancelled.

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
            test.skipTest(f"no display: {error}")
        _TK.withdraw()
    root = tk.Toplevel(_TK)

    def close():
        # Every timer is cancelled, but its callback is left to the window that made it: after_cancel would
        # delete it here, and a window of the same test made after this one (its cleanup runs first) would
        # then find its own gone when it is destroyed ("can't delete Tcl command").
        for job in root.tk.splitlist(root.tk.call("after", "info")):
            root.tk.call("after", "cancel", job)
        root.destroy()
        gc.collect()
    test.addCleanup(close)
    root.withdraw()
    return root


def press(widget, sequence):
    """Do what ``widget`` is bound to do for ``sequence``.  A hidden window never has the focus, so a key
    event generated for it goes nowhere: run the command its binding calls instead."""
    import re
    command = re.search(r"\[(\S+)", widget.bind(sequence)).group(1)
    # The event's fields (%# %b %f %h %k %s %t %w %x %y %A %E %K %N %W %T %X %Y %D), as a key press of nothing.
    widget.tk.call(command, *["0"] * 10, "", "0", "", "0", str(widget), "2", "0", "0", "0")


class Place(unittest.TestCase):
    """Where the window was left, read back for the screen it is on now."""

    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "window.json"
        self.addCleanup(shutil.rmtree, self.path.parent, ignore_errors=True)

    def read(self, area, dpi=120):
        from unittest import mock
        from lyrebot.app import read_place
        with mock.patch("lyrebot.app.screen_holding", return_value=area) as holding:
            found = read_place(dpi, self.path)
        return found, holding

    def test_the_window_opens_where_it_was_left_at_the_size_it_first_opens_at(self):
        import json
        from lyrebot.app import write_place
        write_place((-1500, 80), 120, self.path)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"left": -1500, "top": 80, "dpi": 120})
        found, holding = self.read((-2400, 0, 0, 1290))
        self.assertEqual(found, (-1500, 80, 438, 800))  # 350 x 640 at 125%
        holding.assert_called_once_with(-1500, 80, -1062, 880)
        found, _ = self.read((0, 0, 1920, 1040), dpi=96)  # the main screen at 100% now: that size at 100%
        self.assertEqual(found[2:], (350, 640))

    def test_a_size_kept_before_is_passed_over(self):
        self.path.write_text('{"left": 100, "top": 100, "width": 900, "height": 1000, "dpi": 120}', encoding="utf-8")
        found, _ = self.read((0, 0, 1920, 1040))
        self.assertEqual(found, (100, 100, 438, 800))

    def test_it_is_all_on_the_screen_it_is_on(self):
        from lyrebot.app import OPENING_HEIGHT, WINDOW_WIDTH, write_place
        write_place((1800, 1000), 96, self.path)
        found, _ = self.read((0, 0, 1920, 1040), dpi=96)
        self.assertEqual(found, (1920 - WINDOW_WIDTH, 1040 - OPENING_HEIGHT, WINDOW_WIDTH, OPENING_HEIGHT))
        found, _ = self.read((0, 0, 1280, 600), dpi=96)  # a screen too short for it: as tall as the screen
        self.assertEqual(found, (1280 - WINDOW_WIDTH, 0, WINDOW_WIDTH, 600))

    def test_nothing_kept_or_a_screen_gone_or_a_spoilt_note_opens_it_afresh(self):
        from lyrebot.app import write_place
        self.assertEqual(self.read((0, 0, 1920, 1040))[0], None)  # nothing kept
        write_place((-1500, 80), 120, self.path)
        self.assertEqual(self.read(None)[0], None)  # the screen it was on is gone
        self.path.write_text("{", encoding="utf-8")
        self.assertEqual(self.read((0, 0, 1920, 1040))[0], None)


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
        # No test sounds: the real synthesizer is never opened, and a test that tries fails.
        from unittest import mock
        from lyrebot.audition import AuditionError
        guard = mock.patch("lyrebot.audition._WinMM", side_effect=AuditionError("测试里不出声"))
        winmm = guard.start()
        self.addCleanup(guard.stop)  # (cleanups run last first: the count is checked before the guard goes)
        self.addCleanup(lambda: self.assertEqual(winmm.call_count, 0, "a test opened the real synthesizer"))
        self.midi, self.scores = self.root / "midi", self.root / "txt"
        self.midi.mkdir()
        self.scores.mkdir()

    def window(self):
        return hidden_window(self)

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
        self.assertEqual(app.status_label.cget("text"), "刚从录音编配好")
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
        with mock.patch("lyrebot.app.dialogs.info") as told:
            app.record()
        told.assert_called_once()
        self.assertIsNone(app.worker)
        self.add_midi("旧歌-录音.mid")
        answers = ["旧歌", "a:b", None]
        with mock.patch("lyrebot.app.find_tools", return_value=fake_tools(self.root)), \
                mock.patch("lyrebot.app.dialogs.ask_text", side_effect=answers) as asked:
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
                mock.patch("lyrebot.app.dialogs.ask_text", return_value="小调"):
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
                mock.patch("lyrebot.app.dialogs.ask_text", return_value=""):
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

    def framed_app(self, adopted):
        """An App asked to draw its own title bar, on a system where Windows does (or refuses) its part.
        One per test: the windows' cleanup cancels every timer the interpreter has."""
        from unittest import mock
        from lyrebot.app import App
        self.write_scores("甲.txt")
        with mock.patch("lyrebot.app.chrome.available", return_value=True), \
                mock.patch("lyrebot.app.chrome.adopt", return_value=adopted) as adopt, \
                mock.patch("lyrebot.app.round_corners") as corners:
            app = App(self.window(), self.scores, self.midi, own_frame=True)
        self.corners = corners
        return app, adopt

    def test_the_window_may_draw_its_own_title_bar_and_lend_it_the_lyre(self):
        app, adopt = self.framed_app(True)
        self.assertIsNotNone(app.title_bar)
        self.assertEqual(app.title_bar.commands["start"], app.toggle_quickstart)  # the open book: 快速开始
        self.assertEqual(app.root.pack_slaves()[0], app.title_bar)  # on top of everything
        self.assertEqual(app.crest.winfo_manager(), "")  # the lyre went up into the bar
        self.assertIs(adopt.call_args.args[1], app.title_bar.frame)
        self.assertIn(app.title_bar, adopt.call_args.kwargs["see_through"])
        self.corners.assert_called_once_with(app.root, border=None)  # rounded and shadowed, with no outline

    def test_the_page_carries_a_gilt_frame_a_watermark_and_a_ribbon_over_the_list(self):
        from lyrebot.app import RIBBON_FROM_RIGHT
        app, _ = self.framed_app(True)
        page, px = app.page, app.theme.px
        width, height = page.window()
        head = app.title_bar.winfo_reqheight()  # (the window is never shown: the bar's height as it asks)
        self.assertEqual(app.head(), head)
        side, depth, _ = app.theme.frame()
        places = [place(width, height) for _, place in page.marks]
        self.assertEqual(sorted(places[:8]), sorted([
            (0, head), (width - side, head), (0, height - side), (width - side, height - side),  # its corners
            (side, head, width - 2 * side, depth), (side, height - depth, width - 2 * side, depth),  # its edges
            (0, head + side, depth, height - head - 2 * side), (width - depth, head + side, depth, height - head - 2 * side)]))
        size = app.theme.watermark(px(136)).width()
        self.assertEqual(places[8], ((width - size) // 2, (height - size) // 2))  # the lyre: the window's middle
        [(ribbon, place)] = page.over
        self.assertEqual(place(width, height), (width - px(RIBBON_FROM_RIGHT), head))  # from under the leather
        browser = app.browser
        browser.winfo_width, browser.winfo_height = (lambda: width), (lambda: px(200))
        browser.lay_out()
        page.refresh()
        hung = browser.find_withtag("over")
        self.assertEqual(len(hung), 1)
        self.assertEqual(browser.find_all()[-1], hung[0])  # over the songs, and over their fades too
        self.assertIn(browser.fade_top, browser.find_all())

    def test_where_windows_will_not_lend_its_frame_the_window_stays_as_it_was(self):
        app, _ = self.framed_app(False)
        self.assertIsNone(app.title_bar)  # Windows keeps its bar, and the lyre its place
        self.assertEqual(app.crest.winfo_manager(), "grid")
        self.assertEqual(int(app.crest.grid_info()["column"]), 0)
        self.assertNotIn("TitleBar", [type(child).__name__ for child in app.root.winfo_children()])

    def test_the_list_scrolls_over_a_page_that_stays_still(self):
        from lyrebot.app import App
        self.write_scores(*[f"OST/蒙德/曲{index:02d}.txt" for index in range(40)])
        app = App(self.window(), self.scores, self.midi)
        browser = app.browser
        browser.toggle(self.cell(app, "region", "蒙德"))
        app.page.refresh()
        browser.yview_scroll(5, "units")
        top = browser.canvasy(0)
        self.assertGreater(top, 0)
        x, y = app.page.offset(browser)
        self.assertEqual(browser.coords(browser.find_withtag("paper")[0]), [-x, top - y])
        [laid] = app.search_entry.find_withtag("paper")  # the search field's words lie on the page too
        self.assertEqual(app.search_entry.coords(laid), [-v for v in app.page.offset(app.search_entry)])

    def test_the_list_fades_only_at_an_edge_with_more_beyond_it(self):
        from lyrebot.app import App
        self.write_scores("甲.txt")
        app = App(self.window(), self.scores, self.midi)
        app.browser.extent = 900
        self.assertEqual(app.browser.edges(0, 400), (False, True))  # at the top: more below
        self.assertEqual(app.browser.edges(250, 400), (True, True))  # in the middle
        self.assertEqual(app.browser.edges(500, 400), (True, False))  # at the bottom: more above
        app.browser.extent = 300
        self.assertEqual(app.browser.edges(0, 400), (False, False))  # all of it in sight

    def test_unless_asked_the_window_keeps_the_system_title_bar(self):
        from lyrebot.app import App
        self.write_scores("甲.txt")
        self.assertIsNone(App(self.window(), self.scores, self.midi).title_bar)

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

    def test_a_group_runs_english_names_first_then_pinyin_down_its_columns(self):
        from lyrebot.app import App
        self.write_scores("歌曲/春日影-演奏.txt", "歌曲/Lemon-演奏.txt", "歌曲/东南苦行山.txt", "歌曲/despacito.txt",
                          "歌曲/Seeuagain.txt", "歌曲/欢乐斗地主-演奏.txt")
        app = App(self.window(), self.scores, self.midi)
        app.browser.closed_shelves.discard("歌曲")
        app.browser.lay_out()
        songs = [cell for cell in app.browser.cells if cell.path is not None]
        lefts = sorted({cell.box[0] for cell in songs})
        read = [cell.path.stem for left in lefts for cell in sorted((c for c in songs if c.box[0] == left),
                                                                   key=lambda c: c.box[1])]
        english = ["despacito", "Lemon-演奏", "Seeuagain"]
        self.assertEqual(read[:3], english)  # down the first column, then on down the second
        self.assertEqual(set(read[3:]), {"春日影-演奏", "东南苦行山", "欢乐斗地主-演奏"})
        if sys.platform == "win32":  # Han by pinyin needs Windows' Chinese collation
            self.assertEqual(read[3:], ["春日影-演奏", "东南苦行山", "欢乐斗地主-演奏"])

    def test_columns_run_down_and_the_first_are_longer(self):
        self.assertEqual([len(column) for column in in_columns(tuple(range(7)), 2)], [4, 3])
        self.assertEqual([len(column) for column in in_columns(tuple(range(13)), 3)], [5, 4, 4])
        self.assertEqual(in_columns(tuple(range(3)), 2), [(0, 1), (2,)])
        self.assertEqual(in_columns((), 3), [(), (), ()])

    def click(self, app, cell, double=False):
        browser = app.browser

        class Click:  # in the window's own coordinates, which scrolling moves the list under
            x = (cell.box[0] + cell.box[2]) / 2 - browser.canvasx(0)
            y = (cell.box[1] + cell.box[3]) / 2 - browser.canvasy(0)
        return (app.browser.double_click if double else app.browser.click)(Click)

    def cell(self, app, kind, name):
        return next(cell for cell in app.browser.cells
                    if cell.kind == kind and (cell.name == name or (cell.path and cell.path.stem == name)))

    def test_the_list_opens_with_ost_open_and_the_rest_closed(self):
        from lyrebot.app import App
        self.write_scores("未分.txt", "OST/蒙德/甲.txt", "OST/蒙德/乙.txt", "OST/蒙德/丙.txt",
                          "歌曲/一.txt", "歌曲/二.txt", "歌曲/三.txt", "歌曲/四.txt")
        app = App(self.window(), self.scores, self.midi)
        browser = app.browser
        width = int(browser.cget("width"))
        self.assertIsNone(app.selected())  # no song is made ready: the player asks for one
        self.assertEqual(app.title_label.cget("text"), "选择乐曲")
        self.assertEqual(app.time_label.cget("text"), "")
        self.assertFalse(app.button.enabled)
        self.assertEqual({path.stem for path in browser.visible_songs()}, {"未分"})  # 歌曲 is closed
        self.assertEqual([cell.name for cell in browser.cells if cell.kind == "region"], list(REGIONS))
        self.click(app, self.cell(app, "shelf", "歌曲"))
        self.assertEqual({path.stem for path in browser.visible_songs()}, {"未分", "一", "二", "三", "四"})
        left, right = browser.margin, width - browser.margin  # a little room from either side
        self.assertGreater(browser.indent, 0)
        unfiled = self.cell(app, "song", "未分")
        self.assertEqual((unfiled.box[0], unfiled.box[2]), (left + browser.indent, right))  # in line with names
        ost, region = self.cell(app, "shelf", "OST"), self.cell(app, "region", "蒙德")
        self.assertEqual((ost.box[0], region.box[0]), (left, left))  # one left edge for every level
        self.assertEqual(self.cell(app, "region", list(REGIONS)[0]).box[1], ost.box[3])  # regions under OST

        below = self.cell(app, "region", "璃月").box[1]
        self.click(app, region)
        across = browser.column_edges(left + browser.indent, right)  # songs start after the ▸, at the name
        songs = [cell for cell in browser.cells if cell.path and cell.path.parent.name == "蒙德"]
        self.assertEqual(sorted({cell.box[0] for cell in songs}), across[:2])  # two columns ...
        self.assertEqual(min(cell.box[1] for cell in songs), region.box[3])  # ... on the lines under its name
        self.assertEqual(self.cell(app, "region", "璃月").box[1], below + 2 * browser.row)  # 3 songs take 2 lines
        shelf = self.cell(app, "shelf", "歌曲")
        songs = [cell for cell in browser.cells if cell.path and cell.path.parent.name == "歌曲"]
        self.assertEqual(sorted({cell.box[0] for cell in songs}), across[:2])  # the same two columns
        self.assertEqual(min(cell.box[1] for cell in songs), shelf.box[3])  # under 歌曲
        self.assertEqual(max(cell.box[2] for cell in songs), right)

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
        self.click(app, self.cell(app, "shelf", "歌曲"))
        self.click(app, self.cell(app, "song", "一"))
        self.assertEqual(app.selected(), one)
        self.assertEqual(app.title_label.cget("text"), "一")
        self.assertTrue(app.button.enabled)
        self.assertEqual(app.status_label.cget("text"), "请切换到游戏窗口，按 F7 立刻开始")  # no BPM or tempo changes
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
        menu = app.mode_choice.menu
        states = {menu.entrycget(index, "label"): menu.entrycget(index, "state") for index in range(4)}
        self.assertEqual(states, {SINGLE: "normal", REPEAT: "normal", SHUFFLE: "disabled", IN_ORDER: "disabled"})
        self.assertTrue(app.mode_choice.enabled)  # once or over again, but no playlist to go on in
        self.assertFalse(app.next_button.enabled)
        with mock.patch.object(app, "start") as start:
            app.play_next()
        start.assert_not_called()
        app.select(first)
        self.assertEqual(app.mode.get(), IN_ORDER)  # the choice comes back with a playlist
        self.assertEqual(menu.entrycget(3, "state"), "normal")
        self.assertTrue(app.next_button.enabled)
        with mock.patch.object(app, "start") as start:
            app.play_next()
        start.assert_called_once_with(second, from_top=True)

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

    def three_bars(self):
        """A song of three bars of four seconds each at 100%, with another beside it."""
        path, other = self.write_scores("歌曲/三小节.txt", "歌曲/别的.txt")
        path.write_text("speed=60\na-s-d-f-\ng-h-j-q-\nw-e-r-t-\n", encoding="utf-8")
        return path, other

    def stopped_at(self, app, path, seconds, reason="stopped"):
        """Play ``path`` in ``app`` until ``seconds`` in, stopped there by F8 (or a window switch)."""
        from unittest import mock

        def play(timeline, keyboard, target, cancel, progress, start=0.0, on_strike=None):
            progress(seconds)
            return reason
        with mock.patch("lyrebot.app.Keyboard"), mock.patch("lyrebot.app.play", side_effect=play), \
                mock.patch("lyrebot.app.foreground_title_and_pid", return_value=("游戏", os.getpid() + 1)):
            app.run(path, mock.Mock(duration=12.0), 0.0, 1.0, SINGLE, [], 0.0)
        app.poll()

    def started(self, app, **how):
        """What ``app.start`` hands to the player: (song, start in seconds, countdown)."""
        from unittest import mock
        app.quiet_until = 0  # past the half second after a stop in which clicks start nothing
        with mock.patch.object(app, "run") as run:
            app.start(**how)
            app.worker.join(5)
        app.worker = None
        app.set_playing(False)
        args = run.call_args.args
        return args[0], args[2], args[6]

    def test_a_song_stopped_part_way_goes_on_from_the_start_of_its_bar(self):
        from lyrebot.app import App
        path, other = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        app.select(path)
        self.stopped_at(app, path, 5.5)  # in the second bar, which starts at 4 s
        self.assertEqual(app.resume, (path, 1))
        self.assertEqual(app.status_label.cget("text"), "已停止 · 点 ▶ 或按 F7 从该小节继续")
        self.assertEqual(app.bar_label.cget("text"), "2|3")
        self.assertAlmostEqual(app.fraction, 4 / 12)
        app.show_selected()  # the list redrawn: still remembered
        self.assertEqual(app.status_label.cget("text"), "点 ▶ 或按 F7 从该小节继续")
        self.assertEqual(self.started(app), (path, 4.0, None))  # ▶ goes on, after the countdown
        self.assertIsNone(app.resume)  # and then it is used up

        self.stopped_at(app, path, 9.0, reason="focus")
        app.speed.set("200%")
        self.assertEqual(self.started(app), (path, 4.0, None))  # bar 3, at twice the speed
        self.stopped_at(app, path, 5.0)
        self.assertEqual(self.started(app, from_top=True), (path, 0.0, None))  # ↺
        self.stopped_at(app, path, 5.0)
        self.assertEqual(self.started(app, bar=2)[1], 4.0)  # a bar picked on the progress bar: from its start
        self.stopped_at(app, path, 5.0)
        app.select(other)  # another song chosen: no going on with the first
        self.assertIsNone(app.resume)
        app.select(path)
        self.assertEqual(self.started(app)[1], 0.0)

    def test_dragging_the_knob_plays_from_the_bar_it_is_let_go_in(self):
        from types import SimpleNamespace
        from unittest import mock
        from lyrebot.app import App, LET_GO
        path, _ = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        app.select(path)
        with mock.patch.object(app, "track", return_value=(0.0, 120.0)):  # 40 px a bar
            app.pick_up(SimpleNamespace(x=10))
            self.assertEqual(app.bar_label.cget("text"), "1|3")
            app.slide(SimpleNamespace(x=70))  # into the second bar, which runs from 4 s to 8 s
            self.assertEqual(app.bar_label.cget("text"), "2|3")
            self.assertAlmostEqual(app.fraction, 4 / 12)  # the knob at the start of that bar
            self.assertEqual(app.time_label.cget("text"), "0:04 / 0:12")
            self.assertEqual(app.status_label.cget("text"), LET_GO)
            app.slide(SimpleNamespace(x=900))  # past the end: the last bar
            self.assertEqual(app.bar_label.cget("text"), "3|3")
            app.slide(SimpleNamespace(x=-50))  # before the start: the first
            self.assertEqual(app.bar_label.cget("text"), "1|3")
            app.slide(SimpleNamespace(x=95))
            with mock.patch.object(app, "start") as start:
                app.let_go(SimpleNamespace(x=95))
            start.assert_called_once_with(path, bar=2)  # with the usual countdown
            self.assertIsNone(app.dragging)
            app.let_go(SimpleNamespace(x=95))  # a second release holds nothing: nothing more happens
            app.worker = object()  # while something plays, the knob cannot be taken hold of
            app.pick_up(SimpleNamespace(x=70))
            self.assertIsNone(app.dragging)
            app.worker = None
        self.assertEqual(self.started(app, bar=1), (path, 4.0, None))  # None: the 2 s countdown

    def leaf(self, app, path):
        """Choose ``path`` and turn to its 简谱 page, as a click on the ribbon does."""
        app.select(path)
        app.turn_page()
        self.assertTrue(app.leaf)
        return app.score_view

    def note_at(self, app, seconds):
        """The note of the 简谱 page that sounds ``seconds`` into its song."""
        return next(index for index, note in enumerate(app.score_view.notes) if abs(note.at - seconds) < 1e-9)

    def heard(self, app, *plays, begin=0.0):
        """Hear the 简谱 page's song as ``audition`` would, the player doing each of ``plays`` in turn (each
        given the backend, its start and the progress callback, and answering how it ended), with no sound:
        the synthesizer is a stand-in.  Returns the stand-ins made."""
        from unittest import mock
        made = []
        turns = iter(plays)

        def synthesizer(program, interrupt):
            synth = mock.Mock(program=program, interrupt=interrupt)
            synth.foreground.return_value = 0
            made.append(synth)
            return synth

        def play(timeline, synth, target, cancel, progress, start=0.0):
            self.assertEqual(target, 0)
            return next(turns)(synth, start, progress)
        path, _, _, timeline, speed = app.leaf_song
        app.auditioning = True
        with mock.patch("lyrebot.app.Audition", side_effect=synthesizer), mock.patch("lyrebot.app.play", side_effect=play):
            app.run_audition(path, timeline, begin, speed)
        app.poll()
        return made

    def begun(self, app, act):
        """Where hearing begins when ``act`` is done (in seconds), or None if it does not begin."""
        from unittest import mock
        app.quiet_until = 0
        with mock.patch.object(app, "run_audition") as run:
            act()
            if app.worker is not None:
                app.worker.join(5)
        if not run.called:
            return None
        app.worker = None
        app.auditioning = False
        app.set_playing(False)
        return run.call_args.args[2]

    def test_the_ribbon_turns_to_the_chosen_songs_score_and_back(self):
        from types import SimpleNamespace
        from unittest import mock
        from lyrebot.app import App, TO_HEAR
        path, other = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        with mock.patch.object(app.callout, "say") as said:
            app.turn_page()  # nothing chosen
        self.assertFalse(app.leaf)
        self.assertEqual(said.call_args.args[0], "先选一首曲子")

        view = self.leaf(app, path)
        self.assertEqual(app.leaf_panel.winfo_manager(), "pack")  # the 简谱 page in the songs' place
        self.assertEqual(app.panel.winfo_manager(), "")
        self.assertEqual(app.leaf_title.cget("text"), "三小节")
        self.assertTrue(app.leaf_about.cget("text").startswith("1=C　4/4　♩=60"))
        self.assertEqual(len(view.notes), 12)
        self.assertEqual(sorted(note.at for note in view.notes), [float(n) for n in range(12)])
        self.assertTrue(all(note.box[0] < note.box[2] and note.box[1] < note.box[3] for note in view.notes))
        self.assertEqual((app.leaf_bar.cget("text"), app.leaf_time.cget("text")), ("1|3", "0:00 / 0:12"))
        self.assertEqual(app.leaf_status.cget("text"), TO_HEAR)
        self.assertEqual((app.hear_button.images, app.hear_button.enabled), (app.play_images, True))
        self.assertEqual(app.root.title(), "三小节 · 简谱 · LyreBot")

        app.turn_page()  # and back
        self.assertFalse(app.leaf)
        self.assertEqual((app.panel.winfo_manager(), app.leaf_panel.winfo_manager()), ("pack", ""))

        app.worker = object()  # playing in the game (or recording): it stays with the songs
        with mock.patch.object(app.callout, "say") as said:
            app.turn_page()
        self.assertFalse(app.leaf)
        self.assertIn("结束后再看简谱", said.call_args.args[0])
        app.worker = None
        (self.scores / "坏.txt").write_text("speed=60\nk-\n", encoding="utf-8")
        app.refresh()
        app.select(self.scores / "坏.txt")
        with mock.patch.object(app.callout, "say") as said:
            app.turn_page()
        self.assertFalse(app.leaf)
        self.assertEqual(said.call_args.args[0], "曲谱有误，没有简谱可看")

        # A press on the ribbon (its band, give or take a few pixels) turns the page, and nothing under it
        # hears of the press; anywhere else, the press is the page's.
        app.select(path)
        root, px = app.root, app.theme.px
        on = SimpleNamespace(x_root=root.winfo_rootx() + root.winfo_width() - px(59) + px(4),
                             y_root=root.winfo_rooty() + app.head() + px(10), widget=app.browser)
        off = SimpleNamespace(x_root=on.x_root - px(30), y_root=on.y_root, widget=app.browser)
        self.assertEqual((app.on_ribbon(on), app.on_ribbon(off)), ("orange", None))
        self.assertIsNone(app.ribbon_press(off))
        self.assertFalse(app.leaf)
        from lyrebot.app import TUG, TUG_TURN
        rest = app.ribbon_image.height()
        self.assertEqual(app.ribbon_press(on), "break")  # a tug: pulled down a little, the page not turned yet
        self.assertFalse(app.leaf)
        self.assertEqual(app.tugging, 0)
        self.assertGreater(app.hung_ribbon.height(), rest)
        for frame in range(1, TUG_TURN + 1):
            app.tug(frame)
        self.assertTrue(app.leaf)  # turned as it is pulled furthest
        self.assertEqual(app.hung_ribbon.height(), max(app.theme.ribbon(round(n * app.theme.scale)).height()
                                                       for n in TUG))
        self.assertEqual(app.ribbon_press(on), "break")  # while it springs back, a click counts for nothing
        app.finish_tug()
        self.assertTrue(app.leaf)
        self.assertIsNone(app.tugging)
        self.assertIs(app.hung_ribbon, app.ribbon_image)  # at rest again
        self.assertEqual([image for image, _ in app.page.over], [app.ribbon_image, app.green_image])
        app.ribbon_press(on)  # and now it may be tugged again
        app.finish_tug()
        self.assertFalse(app.leaf)
        self.assertEqual(app.browser.bindtags()[0], app.ribbon_tag)  # the pieces it lies over ask it first
        self.assertEqual(app.score_view.bindtags()[0], app.ribbon_tag)

    def test_a_song_is_heard_only_from_its_play_button_or_a_note_clicked(self):
        from types import SimpleNamespace
        from unittest import mock
        from lyrebot.app import HALT_TIP, App
        path, other = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        self.assertEqual(self.labels(app.song_menu), ["移到…", "—", "从 MIDI 重新编配", "导出简谱", "—", "删除"])
        self.assertEqual(self.begun(app, app.press_play if hasattr(app, "press_play") else app.start), None)
        view = self.leaf(app, path)

        app.quiet_until = 0
        with mock.patch.object(app, "run_audition") as run:
            app.press_hear()
            app.worker.join(5)
        heard_path, timeline, begin, speed = run.call_args.args
        self.assertEqual((heard_path, begin, speed), (path, 0.0, 1.0))
        self.assertEqual(next(event.at for event in timeline.events if not event.down), 2.0)  # notes ring
        # LyreBot stays in front while it is heard: the page's ■ is live, and nothing else can start.
        self.assertTrue(app.auditioning)
        self.assertEqual((app.hear_button.images, app.hear_button.enabled), (app.halt_images, True))
        self.assertEqual(app.hear_button.tooltip.text, HALT_TIP)
        self.assertFalse(any((app.button.enabled, app.record_button.enabled, app.keys_button.enabled)))
        with mock.patch.object(app, "run") as play, mock.patch("lyrebot.app.dialogs.ask_text") as asked:
            app.start(path)
            app.record()
            app.listen()
            app.audition(3.0)
        play.assert_not_called()
        asked.assert_not_called()
        self.assertTrue(app.leaf)
        app.press_hear()  # ■ stops it
        self.assertTrue(app.cancel.is_set())
        app.worker, app.auditioning = None, False
        app.set_playing(False)

        index = self.note_at(app, 5.0)  # a note clicked: heard from it
        left, top, right, bottom = view.notes[index].box
        click = SimpleNamespace(x=(left + right) / 2 - view.canvasx(0), y=(top + bottom) / 2 - view.canvasy(0))
        self.assertEqual(self.begun(app, lambda: view.click(click)), 5.0)
        self.assertIsNone(self.begun(app, lambda: view.click(SimpleNamespace(x=-50, y=-50))))  # beside the notes

    def test_hearing_lights_the_notes_and_goes_on_from_where_it_stopped(self):
        from lyrebot.app import HEARING, PIANO, App
        from lyrebot.theme import INK, ORANGE
        path, _ = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        view = self.leaf(app, path)
        view.finish()  # (drawn a few systems at a time, otherwise)
        index = self.note_at(app, 5.0)
        digit = next(item for item in view.find_withtag(f"n{index}") if view.type(item) == "text")
        self.assertEqual(view.itemcget(digit, "fill"), INK)
        app.auditioning = True
        app.updates.put(("heard", (app.hearing, 5.5)))
        app.updates.put(("heard", (app.hearing - 1, 9.5)))  # from a stretch cut short: not shown
        app.poll()
        self.assertEqual(view.lit, [index])
        self.assertEqual(view.itemcget(digit, "fill"), ORANGE)
        self.assertEqual((app.leaf_bar.cget("text"), app.leaf_time.cget("text")), ("2|3", "0:06 / 0:12"))
        self.assertEqual(app.leaf_status.cget("text"), HEARING)

        made = self.heard(app, lambda synth, start, progress: progress(5.5) or "stopped")
        self.assertEqual(made[0].program, PIANO)  # unless another was chosen
        self.assertEqual(app.leaf_from, 5.0)  # from the note lit when it stopped
        self.assertEqual(app.leaf_status.cget("text"), "已停止 · 点 ▶ 从亮着的音接着听")
        self.assertFalse(app.auditioning)
        self.assertEqual((app.hear_button.images, app.hear_button.enabled), (app.play_images, True))
        self.assertIsNone(app.resume)  # the game's place is its own
        self.assertEqual(self.begun(app, app.press_hear), 5.0)

        app.leaf_from = 5.0
        self.heard(app, lambda synth, start, progress: progress(12.0) or "done")
        self.assertIsNone(app.leaf_from)
        self.assertEqual(view.lit, [])
        self.assertEqual(view.itemcget(digit, "fill"), INK)
        self.assertTrue(app.leaf_status.cget("text").startswith("试听完毕"))
        self.assertEqual(self.begun(app, app.press_hear), 0.0)  # once it was heard to the end: from the top

    def test_clicking_another_note_while_heard_goes_on_from_it(self):
        from lyrebot.app import App
        path, _ = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        view = self.leaf(app, path)
        app.auditioning, app.worker = True, object()
        before = app.hearing
        index = self.note_at(app, 8.0)
        app.hear_note(index)
        self.assertTrue(app.leaping.is_set())
        self.assertEqual((app.leap_to, app.hearing), (8.0, before + 1))
        self.assertEqual(view.lit, [index])  # lit at once
        app.leaping.clear()
        app.worker = None
        starts = []

        def leap(synth, start, progress):  # another note clicked while the first is heard
            starts.append(start)
            self.assertFalse(synth.interrupt())
            app.leap_to = 8.0
            app.leaping.set()
            self.assertTrue(synth.interrupt())  # which stops the player as F8 would
            return "stopped"

        def end(synth, start, progress):
            starts.append(start)
            return "done"
        made = self.heard(app, leap, end)
        self.assertEqual(starts, [0.0, 8.0])
        self.assertEqual(len(made), 2)  # the synthesizer was closed and opened again
        self.assertTrue(app.leaf_status.cget("text").startswith("试听完毕"))

    def test_turning_pages_puts_the_other_page_away_and_reads_a_song_only_once(self):
        from unittest import mock
        from lyrebot import jianpu
        from lyrebot.app import App
        path, other = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        self.assertTrue(app.score_view.away and not app.browser.away)  # the songs come first
        real = jianpu.sheet_of
        with mock.patch("lyrebot.app.jianpu.sheet_of", side_effect=real) as read:
            self.leaf(app, path)
            self.assertTrue(app.browser.away and not app.score_view.away)
            for _ in range(3):
                app.turn_page()
                app.turn_page()
            self.assertEqual(read.call_count, 1)  # turned to again and again: read once
            app.turn_page()
            self.leaf(app, other)
            app.turn_page()
            self.leaf(app, path)  # read lately, unchanged: not read again
            self.assertEqual(read.call_count, 2)
        self.assertEqual(len(app.score_view.notes), 12)
        self.assertFalse(hasattr(app, "score_bar") or hasattr(app, "scrollbar"))  # no bars: the wheel scrolls

    def test_the_size_button_goes_between_four_bars_a_line_and_the_narrow_window(self):
        from unittest import mock
        from lyrebot.app import GROW_TIP, SHRINK_TIP, WINDOW_WIDTH, App
        path, _ = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        view = self.leaf(app, path)
        px = app.theme.px
        self.assertEqual((app.size_button.images, app.size_button.tooltip.text), (app.grow_images, GROW_TIP))
        sizes = []
        with mock.patch.object(app, "size_window", side_effect=lambda w, h: sizes.append((w, h))), \
                mock.patch.object(app.root, "winfo_width", return_value=px(400)), \
                mock.patch.object(app.root, "winfo_height", return_value=px(700)), \
                mock.patch.object(view, "winfo_width", return_value=px(370)), \
                mock.patch.object(view, "winfo_height", return_value=px(500)):
            app.toggle_size()
            wanted = view.four_bars()
            self.assertEqual(sizes[-1], (wanted[0] + px(400) - px(370), wanted[1] + px(700) - px(500)))  # and round it
            self.assertTrue(app.grown)
            self.assertEqual(view.most, 4)
            self.assertEqual((app.size_button.images, app.size_button.tooltip.text), (app.shrink_images, SHRINK_TIP))
            app.toggle_size()
            self.assertEqual(sizes[-1], (px(WINDOW_WIDTH), px(640)))  # the narrowest, as tall as the songs open
            self.assertFalse(app.grown)
            self.assertIsNone(view.most)
            app.toggle_size()
            app.turn_page()  # back to the songs: as if the button were pressed first
            self.assertEqual(sizes[-1], (px(WINDOW_WIDTH), px(640)))
            self.assertFalse(app.grown or app.leaf)
            self.assertEqual(len(sizes), 4)
            self.leaf(app, path)
            app.turn_page()  # not made larger: nothing to undo
            self.assertEqual(len(sizes), 4)

    def test_the_window_keeps_its_corner_and_stays_on_its_screen(self):
        from unittest import mock
        from lyrebot.app import App
        app = App(self.window(), self.scores, self.midi)
        px = app.theme.px
        placed = []
        with mock.patch("lyrebot.app.screen_area", return_value=(-2400, 0, 0, 1290)), \
                mock.patch.object(app.root, "winfo_x", return_value=-500), \
                mock.patch.object(app.root, "winfo_y", return_value=900), \
                mock.patch.object(app.root, "geometry", side_effect=placed.append):
            app.size_window(300, 300)  # where it is, if it fits
            app.size_window(900, 800)  # moved back on to its screen
            app.size_window(5000, 5000)  # no larger than the screen
        self.assertEqual(placed, [f"{px(350)}x{px(360)}+-500+900", "900x800+-900+490", "2400x1290+-2400+0"])

    def text_page(self, app, path):
        """Turn to ``path``'s text page, as the green ribbon does."""
        if app.where != "leaf":
            self.leaf(app, path)
        app.ribbon_turns("green")
        self.assertEqual(app.where, "text")
        return app.text_box

    def write(self, text, content):
        text.delete("1.0", "end")
        text.insert("1.0", content)

    def test_the_green_ribbon_hangs_behind_the_orange_on_the_score_and_text_pages_only(self):
        from types import SimpleNamespace
        from lyrebot.app import App
        path, _ = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        self.assertIsNone(app.hung["green"])
        root, px = app.root, app.theme.px
        green = SimpleNamespace(x_root=root.winfo_rootx() + root.winfo_width() - px(74) + px(4),
                                y_root=root.winfo_rooty() + app.head() + px(10), widget=app.browser)
        self.assertIsNone(app.on_ribbon(green))  # not on the songs' page
        self.leaf(app, path)
        self.assertIs(app.hung["green"], app.green_image)
        self.assertEqual(app.on_ribbon(green), "green")
        self.assertEqual([image for image, _ in app.page.over], [app.ribbon_image, app.green_image])
        self.assertEqual(app.ribbon_press(green), "break")
        app.finish_tug()
        self.assertEqual(app.where, "text")  # green: the 简谱 to its text
        self.assertIs(app.hung["green"], app.green_image)
        app.ribbon_press(green)
        app.finish_tug()
        self.assertEqual(app.where, "leaf")  # and back
        app.ribbon_turns("green")
        app.ribbon_turns("orange")  # orange, from either: the songs
        self.assertEqual(app.where, "songs")
        self.assertIsNone(app.hung["green"])
        self.assertEqual([image for image, _ in app.page.over], [app.ribbon_image])

    def test_the_text_opens_as_saved_at_the_top_then_where_it_was_left(self):
        from unittest import mock
        from lyrebot.app import TEXT_HINT, App
        path, other = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        text = self.text_page(app, path)
        self.assertEqual(text.get("1.0", "end-1c"), path.read_text(encoding="utf-8"))
        self.assertEqual(text.index("insert"), "1.0")  # the first time: the top
        self.assertEqual(app.text_title.cget("text"), "三小节")
        self.assertEqual(app.text_about.cget("text"), "txt/歌曲/三小节.txt")  # where it is, and nothing more
        self.assertEqual(app.text_status.cget("text"), TEXT_HINT)
        self.assertFalse(app.undo_button.enabled)
        self.assertEqual(app.root.title(), "三小节 · 原文 · LyreBot")
        text.mark_set("insert", "3.4")
        app.ribbon_turns("green")  # to the 简谱, and back: the cursor and the view where they were left
        with mock.patch.object(text, "yview", return_value=(0.4, 0.6)), \
                mock.patch.object(text, "yview_moveto") as moved:
            self.text_page(app, path)
        self.assertEqual(text.index("insert"), "3.4")
        moved.assert_called_with(0.4)
        text.insert("insert", "# 改 ")
        app.ribbon_turns("green")  # (saved on the way)
        self.text_page(app, path)
        self.assertEqual(text.index("insert"), "3.8")  # read again from the file: still there
        app.ribbon_turns("orange")
        self.leaf(app, other)  # another song: from the top
        self.assertEqual(self.text_page(app, other).index("insert"), "1.0")

    def test_a_mistake_is_marked_and_neither_saved_nor_left(self):
        from unittest import mock
        from types import SimpleNamespace
        from lyrebot.app import App
        from lyrebot.theme import WARN
        path, _ = self.three_bars()
        before = path.read_text(encoding="utf-8")
        app = App(self.window(), self.scores, self.midi)
        text = self.text_page(app, path)
        self.write(text, "speed=60\na-s-d-f-\nk-\n")
        app.check_text()
        self.assertIn("第 2 小节", app.text_error)  # (line 3: the slip numbers bars, so it is said by bar)
        self.assertEqual(app.manuscript.wrong, 3)
        self.assertEqual(text.tag_ranges("wrong")[0].string, "3.0")
        self.assertEqual(app.text_status.cget("fg"), WARN)  # where in the line is left to the line marked:
        self.assertEqual(app.text_status.cget("text"), "第 2 小节第 1 声部：无法识别的字符 'k'")
        self.assertIn("（位置：", app.text_error)  # (all of it on save, or on a ribbon)
        with mock.patch.object(app.callout, "say") as said:
            self.assertFalse(app.save_text())
        self.assertIn("第 2 小节", said.call_args.args[0])
        self.assertEqual(path.read_text(encoding="utf-8"), before)  # not written
        root, px = app.root, app.theme.px
        for from_right in (59, 74):  # either ribbon: tugged, the page staying, the mistake said
            on = SimpleNamespace(x_root=root.winfo_rootx() + root.winfo_width() - px(from_right) + px(4),
                                 y_root=root.winfo_rooty() + app.head() + px(10), widget=app.manuscript)
            with mock.patch.object(app.callout, "say") as said:
                self.assertEqual(app.ribbon_press(on), "break")
                app.finish_tug()
            self.assertEqual(app.where, "text")
            self.assertIn("第 2 小节", said.call_args.args[0])
        self.write(text, "speed=60\na-s-d-f-\ng---\n")
        app.check_text()
        self.assertIsNone(app.text_error)
        self.assertIsNone(app.manuscript.wrong)
        self.assertEqual(text.tag_ranges("wrong"), ())

    def test_a_mistake_off_the_bars_is_still_said_by_line(self):
        from lyrebot.app import App
        path, _ = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        text = self.text_page(app, path)
        self.write(text, "speed=60\na-s-d-f-\nspeed=fast\ng-\n")
        app.check_text()
        self.assertIn("第 3 行", app.text_error)  # a directive: no number on the slip to say it by
        self.assertEqual(app.manuscript.wrong, 3)

    def numbers(self, app, draw=True):
        """The numbers down the slip's left, each with the line it stands beside: drawn afresh, or (``draw``
        false) as the slip has drawn them by itself."""
        app.root.update()
        if draw:
            app.manuscript.number()
        gutter, text = app.manuscript.gutter, app.text_box
        found = []
        for item in gutter.find_withtag("all"):
            if gutter.type(item) != "text":
                continue
            y = gutter.coords(item)[1]
            line = next(line for line in range(1, int(text.index("end").split(".")[0]))
                        if (row := text.dlineinfo(f"{line}.0")) and row[1] <= y <= row[1] + row[3] + 2)
            found.append((gutter.itemcget(item, "text"), line))
        return sorted(found, key=lambda pair: pair[1])

    def test_the_slip_numbers_the_bars_not_the_lines(self):
        from lyrebot.app import App
        from lyrebot.theme import GOLD, WARN
        path, _ = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        text = self.text_page(app, path)
        self.assertEqual(self.numbers(app), [("1", 2), ("2", 3), ("3", 4)])  # speed=60 has none
        self.write(text, "speed=60\n# 前奏\na-s-d-f-\n\ng-h-j-q-  # 二\nspeed=90\nw-e-r-t-\n")
        self.assertEqual(self.numbers(app), [("1", 3), ("2", 5), ("3", 7)])
        text.insert("4.0", "z---")  # the blank line written on: a bar, and the ones after it renumbered,
        self.assertEqual(self.numbers(app, draw=False), [("1", 3), ("2", 4), ("3", 5), ("4", 7)])  # unasked
        text.insert("5.0", "k")  # a mistake: its bar keeps its number, in red
        app.check_text()
        app.manuscript.number()
        gutter = app.manuscript.gutter
        colours = {gutter.itemcget(item, "text"): gutter.itemcget(item, "fill")
                   for item in gutter.find_withtag("all") if gutter.type(item) == "text"}
        self.assertEqual(colours, {"1": GOLD, "2": GOLD, "3": WARN, "4": GOLD})
        self.assertIn("第 3 小节", app.text_error)

    def test_undo_goes_back_no_further_than_the_text_was_when_the_page_opened(self):
        from lyrebot.app import App
        path, _ = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        text = self.text_page(app, path)
        opened = text.get("1.0", "end-1c")
        for words in ("# 一", "# 二"):
            text.insert("end", words)
            text.edit_separator()
        app.text_changed()
        self.assertTrue(app.undo_button.enabled)
        for _ in range(5):
            app.undo_text()
        self.assertEqual(text.get("1.0", "end-1c"), opened)
        self.assertFalse(app.undo_button.enabled)

    def test_save_writes_the_file_and_leaving_saves_too(self):
        from unittest import mock
        from lyrebot.app import SAVED, App
        path, _ = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        text = self.text_page(app, path)
        self.write(text, "speed=60\na-s-d-f-\ng-h-j-q-\nw-e-r-t-\nq---\n")
        app.text_changed()
        self.assertTrue(app.text_status.cget("text").startswith("有改动"))
        self.assertTrue(app.save_text())
        self.assertEqual(path.read_text(encoding="utf-8").count("\n"), 5)
        self.assertEqual(app.text_status.cget("text"), SAVED)
        text.insert("end", "# 改完\n")
        sizes = []
        app.grown = True  # (made larger on the 简谱 page)
        with mock.patch.object(app, "size_window", side_effect=lambda w, h: sizes.append((w, h))):
            app.ribbon_turns("orange")  # back to the songs: saved, and the window narrow again
        self.assertTrue(path.read_text(encoding="utf-8").endswith("# 改完\n"))
        self.assertEqual(app.where, "songs")
        self.assertEqual(len(sizes), 1)
        self.assertFalse(app.grown)

    def test_the_score_page_keeps_its_own_place_whatever_the_text_page_does(self):
        from lyrebot.app import App
        path, other = self.three_bars()
        path.write_text("speed=60\n" + "a-s-d-f-\ng-h-j-q-\n" * 20, encoding="utf-8")  # long enough to scroll
        other.write_text("speed=60\n" + "w-e-r-t-\n" * 20, encoding="utf-8")
        app = App(self.window(), self.scores, self.midi)
        view = self.leaf(app, path)
        view.yview_moveto(0.5)
        top = view.canvasy(0)
        self.assertGreater(top, 0)
        text = self.text_page(app, path)
        text.mark_set("insert", "end")  # (where the text's cursor is has nothing to do with it)
        app.ribbon_turns("green")
        self.assertEqual(app.where, "leaf")
        self.assertEqual(view.canvasy(0), top)
        text = self.text_page(app, path)
        text.insert("2.0", "# 改过\n")
        notes = len(view.notes)
        app.ribbon_turns("green")  # saved, so drawn again from the file: still where it was
        self.assertEqual(len(view.notes), notes)
        self.assertNotEqual(view.sheet, None)
        self.assertAlmostEqual(view.canvasy(0), top, delta=app.theme.px(16))
        app.ribbon_turns("orange")
        self.leaf(app, other)  # another song: from the top
        self.assertEqual(view.canvasy(0), 0)

    def test_while_writing_f7_plays_nothing_and_the_mark_at_the_start_is_kept(self):
        from unittest import mock
        from lyrebot.app import App
        path, _ = self.three_bars()
        path.write_text("\ufeffspeed=60\na-s-d-f-\n", encoding="utf-8")
        app = App(self.window(), self.scores, self.midi)
        text = self.text_page(app, path)
        self.assertEqual(text.get("1.0", "end-1c"), "speed=60\na-s-d-f-\n")
        app.quiet_until = 0
        with mock.patch.object(app, "run") as run:
            app.start(countdown=0)
        run.assert_not_called()
        self.assertEqual(app.where, "text")
        text.insert("end", "g---\n")
        self.assertTrue(app.save_text())
        self.assertEqual(path.read_bytes()[:3], b"\xef\xbb\xbf")

    def test_the_text_pages_foot_is_the_score_pages_so_the_rule_stays_put(self):
        from lyrebot.app import App
        path, _ = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        self.text_page(app, path)
        app.root.update_idletasks()
        self.assertEqual(app.undo_button.master.winfo_reqheight(), app.hear_button.master.winfo_reqheight())
        self.assertEqual(app.text_panel.winfo_reqheight(), app.leaf_panel.winfo_reqheight())
        px = app.theme.px
        how = dict(app.text_page)[app.manuscript]
        self.assertEqual((how["padx"], how["pady"]), (px(22), (px(2), px(8))))  # wider each side, its foot lifted

    def test_the_status_lines_keep_to_one_line(self):
        from unittest import mock
        from lyrebot.app import App
        path, _ = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        self.text_page(app, path)
        status = app.text_status
        long = "第 3 行：" + "各声部长度不一致，" * 12
        with mock.patch.object(status, "winfo_width", return_value=app.theme.px(200)):
            status.configure(text=long)
            shown = status.words["text"]
            self.assertTrue(shown.endswith("…"))
            self.assertLessEqual(app.theme.measure(status.words["font"], shown), app.theme.px(200))
            self.assertEqual(status.cget("text"), long)  # all of it, for whoever asks
        with mock.patch.object(status, "winfo_width", return_value=app.theme.px(2000)):
            status.refit()  # wider: all of it shows
        self.assertEqual(status.words["text"], long)
        self.assertIsInstance(app.leaf_status, type(status))

    def test_the_format_card_opens_beside_the_text_and_goes_with_its_page(self):
        import tkinter as tk
        from lyrebot.app import App
        from lyrebot.guide import SECTIONS
        path, _ = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        self.text_page(app, path)
        self.assertEqual(app.guide_button.pack_info()["side"], "right")  # at the far right of undo and save's row
        app.toggle_guide()
        card = app.guide
        self.assertTrue(card.winfo_exists())
        shown = []

        def words(widget):
            for child in widget.winfo_children():
                if isinstance(child, tk.Label):
                    shown.append(child.cget("text"))
                words(child)
        words(card)
        for _, rows in SECTIONS:
            for code, meaning in rows:
                self.assertIn(code, shown)
                self.assertIn(meaning, shown)
        self.assertIn("qwertyu"[0], shown)
        app.toggle_guide()  # pressed again: closed
        self.assertIsNone(app.guide)
        self.assertFalse(card.winfo_exists())
        app.toggle_guide()
        card = app.guide
        card.event_generate("<Escape>")
        card.update()
        self.assertIsNone(app.guide)
        app.toggle_guide()
        card = app.guide
        app.ribbon_turns("green")  # the page turned: it goes too
        self.assertIsNone(app.guide)
        self.assertFalse(card.winfo_exists())

    def test_quick_start_opens_beside_the_window_and_closes_again(self):
        import tkinter as tk
        from lyrebot.app import App
        from lyrebot.quickstart import README, STEPS, TIPS
        from lyrebot.widgets import PillButton
        app = App(self.window(), self.scores, self.midi)
        app.toggle_quickstart()
        card = app.quickstart
        self.assertTrue(card.winfo_exists())
        shown, labels = [], {}

        def words(widget):
            for child in widget.winfo_children():
                if isinstance(child, tk.Label):
                    shown.append(child.cget("text"))
                    labels[child.cget("text")] = child
                words(child)
        words(card)
        for title, text in STEPS:
            self.assertIn(title, shown)
            self.assertIn(text, shown)
        self.assertIn(TIPS, shown)
        self.assertNotIn("输入法", TIPS)  # (it makes no difference)
        self.assertIn("README.md", README)  # at the foot, in small words beside 收起
        foot = labels[README].master
        self.assertTrue(any(isinstance(child, PillButton) for child in foot.winfo_children()))
        self.assertEqual(labels[README].cget("font"), labels[TIPS].cget("font"))
        app.toggle_quickstart()  # pressed again: closed
        self.assertIsNone(app.quickstart)
        self.assertFalse(card.winfo_exists())
        app.toggle_quickstart()
        card = app.quickstart
        card.event_generate("<Escape>")
        card.update()
        self.assertIsNone(app.quickstart)

    def test_closing_with_a_mistake_asks_first(self):
        from unittest import mock
        from lyrebot.app import App
        path, _ = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        text = self.text_page(app, path)
        self.write(text, "speed=60\nk-\n")
        with mock.patch("lyrebot.app.dialogs.confirm", return_value=False) as asked, \
                mock.patch.object(app.callout, "say"):
            app.close()
        self.assertIn("第 1 小节", asked.call_args.args[1])
        self.assertFalse(app.closing)

    def test_turning_back_stops_hearing_and_the_game_turns_back_to_the_songs(self):
        from unittest import mock
        from lyrebot.app import App
        path, _ = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        self.leaf(app, path)
        app.auditioning, app.worker = True, object()
        app.turn_page()
        self.assertTrue(app.cancel.is_set())
        self.assertFalse(app.leaf)
        app.auditioning, app.worker = False, None
        self.leaf(app, path)
        app.quiet_until = 0
        with mock.patch.object(app, "run") as run:  # F7 in the game, say
            app.start(countdown=0)
            app.worker.join(5)
        app.worker = None
        run.assert_called_once()
        self.assertFalse(app.leaf)
        self.assertEqual(app.panel.winfo_manager(), "pack")

    def test_a_score_edited_while_open_is_drawn_again(self):
        import os
        from lyrebot.app import App
        path, _ = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        view = self.leaf(app, path)
        self.assertTrue(app.read_leaf())  # unchanged: left as it is
        self.assertEqual(len(view.notes), 12)
        path.write_text("speed=60\na-s-d-f-\ng-h-j-q-\nw-e-r-t-\nq---\n", encoding="utf-8")
        os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
        self.assertTrue(app.read_leaf())
        self.assertEqual(len(view.notes), 13)
        self.assertEqual(app.leaf_bar.cget("text"), "1|4")
        path.unlink()  # gone: back to the songs
        self.assertFalse(app.read_leaf())
        self.assertFalse(app.leaf)

    def test_the_instrument_is_chosen_on_the_score_page_kept_and_heard_at_once(self):
        import json
        from unittest import mock
        from lyrebot.app import App, INSTRUMENTS, PIANO, read_timbre, write_timbre
        path, _ = self.three_bars()
        settings = self.root / "settings.json"
        app = App(self.window(), self.scores, self.midi, settings=settings)
        self.assertEqual((app.timbre, app.timbre_name.get()), (PIANO, "钢琴"))  # the piano unless chosen
        self.leaf(app, path)
        self.assertTrue(app.timbre_choice.text.startswith("钢琴"))
        menu = app.timbre_choice.menu
        names = self.labels(menu)
        self.assertEqual(names, [name for _, name in INSTRUMENTS])
        menu.invoke(names.index("竖琴"))
        self.assertEqual(app.timbre, 47)
        self.assertEqual(json.loads(settings.read_text(encoding="utf-8")), {"timbre": 47})
        self.assertEqual(read_timbre(settings), 47)  # chosen again next time

        made = self.heard(app, lambda synth, start, progress: "done")
        self.assertEqual(made[0].program, 47)
        app.synth = mock.Mock()  # while a song is heard it may change, and is heard on the new one at once
        app.auditioning = True
        self.assertTrue(app.timbre_choice.enabled)
        menu.invoke(names.index("筝"))
        app.synth.instrument.assert_called_once_with(108)
        app.synth, app.auditioning = None, False

        app.timbre = 13
        made = []

        def factory(program, interrupt):
            synth = mock.Mock(program=program)
            synth.foreground.return_value = 0
            app.timbre = 16  # chosen while the synthesizer was opening
            made.append(synth)
            return synth

        def play(timeline, synth, *args, **options):
            self.assertIs(app.synth, synth)  # the one open while it plays
            return "done"
        with mock.patch("lyrebot.app.Audition", side_effect=factory), mock.patch("lyrebot.app.play", side_effect=play):
            app.auditioning = True
            app.run_audition(path, app.leaf_song[3], 0.0, 1.0)
        self.assertEqual(made[0].program, 13)
        made[0].instrument.assert_called_once_with(16)  # so it plays on the one chosen
        self.assertIsNone(app.synth)  # closed with the song
        app.poll()

        settings.write_text("{not json", encoding="utf-8")
        self.assertEqual(read_timbre(settings), PIANO)
        settings.write_text(json.dumps({"timbre": 200, "other": 1}), encoding="utf-8")
        self.assertEqual(read_timbre(settings), PIANO)  # not one offered
        write_timbre(108, settings)
        self.assertEqual(json.loads(settings.read_text(encoding="utf-8")), {"timbre": 108, "other": 1})
        self.assertEqual(read_timbre(self.root / "nothing.json"), PIANO)

    def test_without_a_settings_file_the_choice_lasts_only_while_open(self):
        from lyrebot.app import App
        app = App(self.window(), self.scores, self.midi)
        app.timbre_name.set("筝")
        app.pick_timbre()
        self.assertEqual(app.timbre, 108)
        self.assertEqual(list(self.root.glob("*.json")), [])

    def test_a_synthesizer_that_will_not_open_says_why(self):
        from unittest import mock
        from lyrebot.app import App, AuditionError
        from lyrebot.theme import WARN
        path, _ = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        self.leaf(app, path)
        app.auditioning = True
        with mock.patch("lyrebot.app.Audition", side_effect=AuditionError("这台电脑没有可以发声的 MIDI 设备")):
            app.run_audition(path, app.leaf_song[3], 0.0, 1.0)
        app.poll()
        self.assertEqual(app.leaf_status.cget("text"), "不能试听：这台电脑没有可以发声的 MIDI 设备")
        self.assertEqual(app.leaf_status.cget("fg"), WARN)
        self.assertFalse(app.auditioning)

    def test_an_empty_library_says_how_to_add_songs(self):
        from lyrebot.app import App, EMPTY
        app = App(self.window(), self.scores, self.midi)
        self.assertEqual(app.title_label.cget("text"), "还没有曲谱")
        self.assertEqual(app.status_label.cget("text"), EMPTY)
        self.assertNotIn("右键", EMPTY)  # a right click below the list opens nothing

    def test_the_bar_being_played_shows_beside_the_time(self):
        from lyrebot.app import App
        path, other = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        self.assertEqual(app.bar_label.cget("text"), "")  # nothing chosen
        app.select(path)
        self.assertEqual(app.bar_label.cget("text"), "1|3")  # where it would start
        app.updates.put(("song", (path, 12.0)))
        app.updates.put(("progress", 5.5))
        app.poll()
        self.assertEqual(app.bar_label.cget("text"), "2|3")
        app.updates.put(("progress", 12.0))
        app.poll()
        self.assertEqual(app.bar_label.cget("text"), "3|3")
        self.stopped_at(app, path, 5.5)
        self.assertEqual(app.bar_label.cget("text"), "2|3")  # where it will go on from
        app.show_selected()
        self.assertEqual(app.bar_label.cget("text"), "2|3")
        app.speed.set("200%")
        app.updates.put(("song", (path, 6.0)))
        app.updates.put(("progress", 4.5))  # twice as fast: 4.5 s in is bar 3
        app.poll()
        self.assertEqual(app.bar_label.cget("text"), "3|3")

    def test_the_song_being_played_has_a_lyre_whose_strings_light_as_their_rows_are_struck(self):
        from lyrebot.app import App
        path, _ = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        browser = app.browser
        self.assertEqual(browser.find_withtag("string"), ())  # nothing plays
        app.updates.put(("song", (path, 12.0)))
        app.poll()

        def pictures():
            return [browser.itemcget(browser.find_withtag(f"string{row}")[0], "image") for row in range(3)]
        rest = pictures()
        self.assertEqual(len(set(rest)), 1)  # three strings, all gold
        app.updates.put(("struck", ("q", "z")))  # a high key and a low one
        app.poll()
        lit = pictures()
        self.assertNotEqual(lit[0], rest[0])
        self.assertEqual(lit[1], rest[1])  # no middle key: its string stays at rest
        self.assertNotEqual(lit[2], rest[2])
        self.assertIsNotNone(browser.ring_job)  # and they fade
        browser.struck[0] = browser.struck[2] = time.monotonic() - 0.22  # past shivering, half faded
        browser.ring()
        fading = pictures()
        self.assertNotIn(fading[0], (rest[0], lit[0]))
        browser.lay_out()  # the list laid again (scrolled, a region opened) shows them as they ring
        self.assertEqual(pictures(), fading)
        browser.struck = [time.monotonic() - 1.0] * 3  # struck a second ago: gold again, and nothing to look at
        browser.ring()
        self.assertEqual(pictures(), rest)
        self.assertIsNone(browser.ring_job)
        app.updates.put(("struck", ("a",)))
        app.updates.put(("done", None))
        app.poll()
        self.assertEqual(browser.find_withtag("string"), ())
        self.assertIsNone(browser.ring_job)

    def test_restart_is_live_when_play_is(self):
        from lyrebot.app import App
        path, _ = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        self.assertEqual((app.button.enabled, app.restart_button.enabled), (False, False))  # nothing chosen
        app.select(path)
        self.assertEqual((app.button.enabled, app.restart_button.enabled), (True, True))
        app.set_playing(True)
        self.assertEqual((app.button.enabled, app.restart_button.enabled), (False, False))
        app.set_playing(False)
        self.assertTrue(app.restart_button.enabled)

    def test_f7_plays_at_once_as_play_would(self):
        from unittest import mock
        from lyrebot.app import App
        path, _ = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        with mock.patch.object(app, "start") as start, \
                mock.patch("lyrebot.app.start_pressed", side_effect=[True, False]):
            app.watch_start_key()  # nothing chosen: nothing to play
            app.watch_start_key()
        start.assert_not_called()
        app.select(path)
        with mock.patch.object(app, "start") as start, \
                mock.patch("lyrebot.app.start_pressed", side_effect=[True, True, False, True]):
            for _ in range(4):
                app.watch_start_key()
        self.assertEqual(start.call_args_list, [mock.call(countdown=0.0)] * 2)  # once per press, held or not
        app.worker = object()
        with mock.patch.object(app, "start") as start, mock.patch("lyrebot.app.start_pressed", side_effect=[False, True]):
            app.watch_start_key()
            app.watch_start_key()
        start.assert_not_called()  # not while something plays
        app.worker = None

    def test_f7_in_lyrebot_itself_plays_nothing(self):
        from unittest import mock
        from lyrebot.app import App
        path, _ = self.three_bars()
        app = App(self.window(), self.scores, self.midi)
        with mock.patch("lyrebot.app.Keyboard"), mock.patch("lyrebot.app.play") as play, \
                mock.patch("lyrebot.app.foreground_title_and_pid", return_value=("LyreBot", os.getpid())):
            app.run(path, mock.Mock(duration=12.0), 0.0, 1.0, SINGLE, [], 0.0)
        play.assert_not_called()
        app.poll()
        self.assertEqual(app.status_label.cget("text"), "按 F7 时前台是 LyreBot，没有演奏")

    def test_repeat_plays_the_same_song_again(self):
        from unittest import mock
        from lyrebot.app import App
        alone, = self.write_scores("未分.txt")
        app = App(self.window(), self.scores, self.midi)
        app.select(alone)
        menu = app.mode_choice.menu
        menu.invoke(1)
        self.assertEqual(app.mode.get(), REPEAT)
        results = iter(["done", "done", "stopped"])
        with mock.patch("lyrebot.app.COUNTDOWN", 0), mock.patch("lyrebot.app.GAP", 0), \
                mock.patch("lyrebot.app.Keyboard"), mock.patch("lyrebot.app.play", side_effect=lambda *a, **k: next(results)), \
                mock.patch("lyrebot.app.foreground_title_and_pid", return_value=("游戏", os.getpid() + 1)):
            app.run(alone, mock.Mock(duration=1.0), 0.0, 1.0, REPEAT, app.playlist(alone))
        played = []
        while not app.updates.empty():
            kind, value = app.updates.get()
            if kind == "song":
                played.append(value[0])
        self.assertEqual(played, [alone, alone, alone])

    def test_shelves_and_regions_show_how_many_songs_they_hold(self):
        from lyrebot.app import App
        self.write_scores("OST/蒙德/甲.txt", "OST/蒙德/乙.txt", "OST/璃月/丙.txt", "歌曲/一.txt", "未分.txt")
        app = App(self.window(), self.scores, self.midi)
        self.assertEqual(self.cell(app, "shelf", "OST").count, 3)
        self.assertEqual(self.cell(app, "region", "蒙德").count, 2)
        self.assertEqual(self.cell(app, "region", "须弥").count, 0)
        self.assertEqual(self.cell(app, "shelf", "歌曲").count, 1)
        self.assertIsNone(self.cell(app, "song", "未分").count)
        shown = {app.browser.itemcget(item, "text") for item in app.browser.find_withtag("content")
                 if app.browser.type(item) == "text"}
        self.assertTrue({"OST", "3", "蒙德", "2"} <= shown)

    def test_searching_goes_through_the_matches_in_list_order(self):
        songs = [Path("寂夜月如锋-录音.txt"), Path("静夜的轻语.txt"), Path("Lemon.txt"), Path("夜阑安暇时.txt")]
        self.assertEqual(next_match(songs, "夜"), songs[0])
        self.assertEqual(next_match(songs, "夜", songs[0]), songs[1])
        self.assertEqual(next_match(songs, "夜", songs[2]), songs[3])  # from a song that does not match
        self.assertEqual(next_match(songs, "夜", songs[3]), songs[0])  # round to the top
        self.assertEqual(next_match(songs, " lemon "), songs[2])
        self.assertIsNone(next_match(songs, "雪"))
        self.assertIsNone(next_match(songs, "  "))

    def test_the_search_field_has_the_right_third_of_the_row_over_the_rule_and_no_button(self):
        from lyrebot.app import App
        from lyrebot.widgets import PillButton
        self.write_scores("甲.txt")
        app = App(self.window(), self.scores, self.midi)
        row = app.search_field.master
        self.assertEqual(int(app.search_field.grid_info()["column"]), 1)
        self.assertEqual(app.search_field.grid_info()["sticky"], "ew")
        parts = [row.grid_columnconfigure(column) for column in (0, 1)]
        self.assertEqual([(part["weight"], part["uniform"]) for part in parts], [(2, "thirds"), (1, "thirds")])
        self.assertFalse([child for child in row.winfo_children() if isinstance(child, PillButton)])
        self.assertEqual(app.search_entry.get(), "")  # and nothing written in it to start with
        packed = app.root.pack_slaves()  # from the foot up: the player, its rule, the search row; the list fills
        rule = next(widget for widget in packed if type(widget).__name__ == "Ornament")
        self.assertEqual(packed.index(row), packed.index(rule) + 1)
        self.assertEqual(row.pack_info()["side"], "bottom")
        self.assertIs(packed[-1], app.browser.master)

    def test_a_press_anywhere_but_the_search_line_leaves_it(self):
        from types import SimpleNamespace
        from unittest import mock
        from lyrebot.app import App
        self.write_scores("甲.txt")
        app = App(self.window(), self.scores, self.midi)
        mark = app.search_field.grid_slaves(row=0, column=0)[0]
        with mock.patch.object(app.root, "focus_get", return_value=app.search_entry), \
                mock.patch.object(app.root, "focus_set") as leave:
            for inside in (app.search_entry, mark, app.search_field):
                app.click_away(SimpleNamespace(widget=inside))
            leave.assert_not_called()
            for outside in (app.browser, app.button, app.backdrop, app.search_field.master):
                app.click_away(SimpleNamespace(widget=outside))
            self.assertEqual(leave.call_count, 4)
        with mock.patch.object(app.root, "focus_get", return_value=app.browser), \
                mock.patch.object(app.root, "focus_set") as leave:
            app.click_away(SimpleNamespace(widget=app.backdrop))  # not in it: nothing to leave
        leave.assert_not_called()

    def test_closing_keeps_the_place(self):
        from unittest import mock
        app, _ = self.framed_app(True)
        with mock.patch.object(app, "keep_place") as keep:
            app.close()
        keep.assert_called_once_with()

    def test_a_press_on_the_title_bar_leaves_the_search_line_too(self):
        from unittest import mock
        app, _ = self.framed_app(True)
        with mock.patch.object(app.root, "focus_get", return_value=app.search_entry), \
                mock.patch.object(app.root, "focus_set") as leave:
            app.poll()
            leave.assert_not_called()
            app.title_bar.frame.presses += 1  # what the frame counts for a press Windows has
            app.poll()
            leave.assert_called_once_with()

    def test_the_search_box_jumps_to_each_match(self):
        from lyrebot.app import App
        first, second, third = self.write_scores("OST/蒙德/风息的离别诗.txt", "OST/纳塔/风清月白.txt", "歌曲/春风.txt")
        app = App(self.window(), self.scores, self.midi)
        app.search_entry.insert(0, "风")
        press(app.search_entry, "<Return>")  # Enter searches
        self.assertEqual(app.selected(), first)
        self.assertIn(first, app.browser.visible_songs())  # its region opened
        self.assertIn("第 1 首，共 3 首，再按回车看下一首", app.status_label.cget("text"))
        press(app.search_entry, "<Return>")
        self.assertEqual(app.selected(), second)
        app.search()
        self.assertEqual(app.selected(), third)  # 歌曲 opened too
        self.assertIn(third, app.browser.visible_songs())
        app.search()
        self.assertEqual(app.selected(), first)
        app.search_entry.delete(0, "end")
        app.search_entry.insert(0, "清")
        app.search()  # a new search starts from the top
        self.assertEqual(app.selected(), second)
        self.assertIn("只有这一首", app.status_label.cget("text"))
        app.search_entry.delete(0, "end")
        app.search_entry.insert(0, "雪")
        app.search()
        self.assertEqual(app.selected(), second)
        self.assertIn("没有名字里有「雪」的曲子", app.status_label.cget("text"))
        app.worker = object()  # nothing moves while a song plays
        app.search_entry.delete(0, "end")
        app.search_entry.insert(0, "春")
        app.search()
        self.assertEqual(app.selected(), second)
        app.worker = None

    def right_click(self, app, cell=None):
        from unittest import mock

        browser = app.browser

        class Click:
            x, y = (cell.box[0] + 1 - browser.canvasx(0), cell.box[1] + 1 - browser.canvasy(0)) if cell else (5, 10 ** 5)
            x_root = y_root = 0
        with mock.patch.object(app.song_menu, "tk_popup") as song, mock.patch.object(app.folder_menu, "tk_popup") as folder:
            app.context_menu(Click)
        return app.song_menu if song.called else app.folder_menu if folder.called else None

    def labels(self, menu):
        return [menu.entrycget(index, "label") if menu.type(index) != "separator" else "—"
                for index in range(menu.index("end") + 1)]

    def test_folders_and_songs_have_their_own_menus(self):
        from unittest import mock
        from lyrebot.app import App
        self.write_scores("OST/蒙德/甲.txt", "歌曲/一.txt")
        app = App(self.window(), self.scores, self.midi)
        menu = self.right_click(app, self.cell(app, "region", "须弥"))
        self.assertEqual(self.labels(menu), ["打开「须弥」文件夹"])  # a folder: only its own folder
        with mock.patch("lyrebot.app.os.startfile", create=True) as opened:
            menu.invoke(0)
        opened.assert_called_once_with(self.scores / "OST" / "须弥")
        self.assertTrue((self.scores / "OST" / "须弥").is_dir())
        self.assertEqual(self.labels(self.right_click(app, self.cell(app, "shelf", "歌曲"))), ["打开「歌曲」文件夹"])
        self.assertIsNone(self.right_click(app))  # below the list: no menu

        self.click(app, self.cell(app, "region", "蒙德"))
        menu = self.right_click(app, self.cell(app, "song", "甲"))
        self.assertEqual(self.labels(menu), ["移到…", "—", "从 MIDI 重新编配", "导出简谱", "—", "删除"])
        self.assertEqual(app.selected(), self.scores / "OST" / "蒙德" / "甲.txt")
        places = self.labels(app.move_menu)
        self.assertEqual(places, ["未归类", "—", *REGIONS, "—", "歌曲"])
        self.assertEqual(app.move_menu.entrycget(places.index("蒙德"), "state"), "disabled")  # where it is now
        self.assertEqual(app.move_menu.entrycget(places.index("璃月"), "state"), "normal")

    def test_a_song_moves_to_another_region_from_its_menu(self):
        from unittest import mock
        from lyrebot.app import App
        song, = self.write_scores("OST/蒙德/甲.txt")
        self.write_scores("歌曲/甲.txt")
        app = App(self.window(), self.scores, self.midi)
        app.select(song)
        self.right_click(app, app.browser.cell_of(song))
        places = self.labels(app.move_menu)
        app.move_menu.invoke(places.index("璃月"))
        moved = self.scores / "OST" / "璃月" / "甲.txt"
        self.assertTrue(moved.exists())
        self.assertFalse(song.exists())
        self.assertEqual(app.selected(), moved)
        self.assertIn(moved, app.browser.visible_songs())  # the region it went to is open
        self.assertEqual(app.status_label.cget("text"), "已移到「璃月」")
        with mock.patch("lyrebot.app.dialogs.error") as told:
            self.assertIsNone(app.move(moved, self.scores / "歌曲", "歌曲"))  # one of that name is there
        self.assertIn("已经有", told.call_args.args[1])
        self.assertTrue(moved.exists())
        self.assertEqual(app.move(moved, self.scores, "未归类"), self.scores / "甲.txt")

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
        with mock.patch("lyrebot.app.dialogs.ask_text", side_effect=["快一点", "400", "125%"]) as asked:
            menu.invoke(last)
        self.assertEqual(asked.call_count, 3)
        self.assertIn("25 到 300", asked.call_args.args[1])
        self.assertEqual(app.speed.get(), "125%")
        self.assertEqual(app.speed_factor(), 1.25)
        self.assertTrue(app.speed_choice.text.startswith("125%"))
        self.assertEqual(app.time_label.cget("text"), clock(4 / 1.25))
        with mock.patch("lyrebot.app.dialogs.ask_text", return_value=None):
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
        self.assertEqual(app.song_menu.entrycget(3, "label"), "导出简谱")
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
        self.assertEqual(app.status_label.cget("text"), "刚从 MIDI 编配好")
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
        with mock.patch("lyrebot.app.dialogs.confirm", return_value=False):
            app.rearrange()  # declined: the edit stays
        self.assertIn("手改过", target.read_text(encoding="utf-8"))
        with mock.patch("lyrebot.app.dialogs.confirm", return_value=True):
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
        self.assertEqual(app.song_menu.entrycget(5, "label"), "删除")
        app.select(recorded)
        gone = []

        def recycle(path):
            gone.append(path.name)
            path.unlink()
        with mock.patch("lyrebot.app.dialogs.confirm", return_value=False) as asked, \
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
        with mock.patch("lyrebot.app.dialogs.confirm", return_value=True), \
                mock.patch("lyrebot.app.dialogs.error") as told, \
                mock.patch("lyrebot.app.to_recycle_bin", side_effect=OSError("文件正被占用")):
            app.delete()
        self.assertIn("文件正被占用", told.call_args.args[1])
        self.assertTrue(hand.exists())
        self.assertEqual(app.selected(), hand)


if __name__ == "__main__":
    unittest.main()
