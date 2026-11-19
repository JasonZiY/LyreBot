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
        for job in root.tk.splitlist(root.tk.call("after", "info")):
            root.after_cancel(job)
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
        self.assertEqual(app.root.pack_slaves()[0], app.title_bar)  # on top of everything
        self.assertEqual(app.crest.winfo_manager(), "")  # the lyre went up into the bar
        self.assertIs(adopt.call_args.args[1], app.title_bar.frame)
        self.assertIn(app.title_bar, adopt.call_args.kwargs["see_through"])
        self.corners.assert_called_once_with(app.root, border=None)  # rounded and shadowed, with no outline

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

        def play(timeline, keyboard, target, cancel, progress, start=0.0):
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
