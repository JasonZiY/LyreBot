from pathlib import Path
import re
import struct
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest import mock
import zlib

from lyrebot import chrome, dialogs, icons
from lyrebot.theme import Theme, fit, geometry, screen_area
from lyrebot.widgets import Choice, PageLine, PillButton, PopupMenu, TitleBar, Tooltip, WritingLine

try:  # run as part of the tests package, or by unittest discovery from the tests folder
    from .test_app import hidden_window
except ImportError:
    from test_app import hidden_window


class Drawing(unittest.TestCase):
    def test_edges_are_soft_and_the_rest_solid_or_empty(self):
        size = 20
        pixels = icons.rgba(size, size, [("#D97757", icons.circle(10, 10, 7.6))])
        alpha = lambda x, y: pixels[(y * size + x) * 4 + 3]  # noqa: E731
        self.assertEqual(alpha(10, 10), 255)
        self.assertEqual(alpha(0, 0), 0)
        edge = [alpha(x, 10) for x in range(size)]
        self.assertTrue(any(0 < a < 255 for a in edge))  # the rim fades rather than steps
        self.assertEqual(tuple(pixels[(10 * size + 10) * 4:(10 * size + 10) * 4 + 3]), (0xD9, 0x77, 0x57))

    def test_the_quick_look_matches_full_sampling(self):
        """A pixel is only sampled in full where its corners disagree: thin strokes must not be lost."""
        size = 26
        layers = icons.paint(icons.restart(size) + icons.keyboard(size), "#2B2722")
        fast = icons.rgba(size, size, layers)
        slow = bytearray()
        offsets = [(i + 0.5) / 4 for i in range(4)]
        for y in range(size):
            for x in range(size):
                hits = sum(1 for oy in offsets for ox in offsets if any(s(x + ox, y + oy) for _, s in layers))
                slow.append(round(255 * hits / 16))
        differences = [abs(fast[i * 4 + 3] - slow[i]) for i in range(size * size)]
        self.assertLessEqual(max(differences), 255 * 3 / 16)
        self.assertLess(sum(differences) / len(differences), 2.0)

    def test_a_png_that_decodes(self):
        data = icons.png(3, 2, bytes(range(24)))
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        width, height = struct.unpack(">II", data[16:24])
        self.assertEqual((width, height), (3, 2))
        start = data.index(b"IDAT") + 4
        length = struct.unpack(">I", data[start - 8:start - 4])[0]
        raw = zlib.decompress(data[start:start + length])
        self.assertEqual(raw, b"\x00" + bytes(range(12)) + b"\x00" + bytes(range(12, 24)))

    def test_every_icon_draws(self):
        for draw in (icons.play, icons.stop, icons.next_track, icons.restart, icons.keyboard, icons.record,
                     icons.check, icons.magnifier, icons.folder, icons.move_to, icons.quill, icons.score_page,
                     icons.trash, icons.pen_nib, icons.warning, icons.lyre, icons.chevron, icons.minimise, icons.cross,
                     icons.corners_out, icons.corners_in, icons.undo, icons.seal, icons.question, icons.book,
                     icons.pin, lambda size: icons.pin(size, pressed=False)):
            pixels = icons.rgba(16, 16, icons.paint(draw(16), "#000000"))
            self.assertTrue(any(pixels[3::4]), draw.__name__)
        width = icons.fleuron_width(7)
        self.assertTrue(any(icons.rgba(width, 7, icons.paint(icons.fleuron(width, 7), "#000000"))[3::4]))

    def test_level_and_upright_lines_fill_whole_pixels(self):
        """Every pixel such a line passes over is solid, at any scale; none is left half covered and grey."""
        for size in (14, 18, 21, 28):
            for draw in (icons.trash, icons.minimise):
                alpha = icons.rgba(size, size, icons.paint(draw(size), "#000000"))[3::4]
                self.assertTrue(set(alpha) <= {0, 255}, (draw.__name__, size))
        pixels = icons.rgba(60, 30, icons.pill(60, 30, "#FAF7F0", "#E3D6BC", 1))
        at = lambda x, y: tuple(pixels[(y * 60 + x) * 4:(y * 60 + x) * 4 + 4])  # noqa: E731
        self.assertEqual(at(30, 0), (0xE3, 0xD6, 0xBC, 255))  # a button's edge: one whole row
        self.assertEqual(at(30, 1), (0xFA, 0xF7, 0xF0, 255))

    def test_a_string_is_one_sharp_row_at_rest_and_blurs_at_its_middle_as_it_shivers(self):
        for length in (10, 14, 20):
            straight = icons.rgba(length, 3, [("#000000", icons.string(length, 0.0))])[3::4]
            self.assertEqual(list(straight), [0] * length + [255] * length + [0] * length)  # one solid row
            swollen = icons.rgba(length, 3, [("#000000", icons.string(length, 1.2))])[3::4]
            middle = length // 2
            self.assertEqual(list(swollen[length:2 * length]), [255] * length)  # still solid along its own row ...
            self.assertGreater(swollen[middle], 0)  # ... and blurred into those above and below at its middle
            self.assertEqual(swollen[middle], swollen[2 * length + middle])
            self.assertLess(max(swollen[0], swollen[2 * length]), swollen[middle] / 2)  # much less at its ends

    def test_the_gilt_is_laid_in_gold_and_the_lines_in_ink(self):
        ink, gold = "#2B2722", "#B08D57"
        for draw in (icons.next_track, icons.keyboard, icons.magnifier, icons.folder, icons.move_to, icons.quill,
                     icons.score_page, icons.pen_nib, icons.corners_out, icons.corners_in, icons.undo, icons.seal,
                     icons.question):
            colors = {color for color, _ in icons.paint(draw(18), ink, gold)}
            self.assertEqual(colors, {ink, gold}, draw.__name__)  # every one has lines and a touch of gold
        self.assertEqual({color for color, _ in icons.paint(icons.folder(18), "#C9BFAE")}, {"#C9BFAE"})  # off: all faint
        self.assertEqual([color for color, _ in icons.paint(icons.chevron(10), gold)], [gold])  # a lone shape too

    def test_a_long_curve_sorted_into_cells_covers_what_it_covered_before(self):
        import math
        import random
        points, widths = icons.lyre_arm()
        points = [(x / 8, y / 8) for x, y in points]
        widths = [w / 8 for w in widths]
        line = icons.stroke(points, widths)

        def plainly(x, y):  # every piece of the line tried, as before
            for (ax, ay), (bx, by), wa, wb in zip(points, points[1:], widths, widths[1:]):
                dx, dy = bx - ax, by - ay
                length = dx * dx + dy * dy
                t = 0.0 if length == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length))
                if math.hypot(ax + t * dx - x, ay + t * dy - y) <= (wa + (wb - wa) * t) / 2:
                    return True
            return False
        rng = random.Random(3)
        for _ in range(4000):
            x, y = rng.uniform(-5, 133), rng.uniform(-5, 133)
            self.assertEqual(line(x, y), plainly(x, y), (x, y))

    def test_the_frame_is_a_double_rule_rounding_the_corners(self):
        outer, inner = "#B08D57", "#D2BC93"
        layers, side, depth = icons.frame(1.25, outer, inner)
        corner = icons.rgba(side, side, layers)
        at = lambda pixels, width, x, y: tuple(pixels[(y * width + x) * 4:(y * width + x) * 4 + 4])  # noqa: E731
        self.assertEqual([at(corner, side, side - 1, y) for y in range(depth)],  # straight on at the corner's end:
                         [(0, 0, 0, 0)] * 3 + [(0xB0, 0x8D, 0x57, 255)] * 2 + [(0, 0, 0, 0)] * 3
                         + [(0xD2, 0xBC, 0x93, 255)])  # a heavier line, a gap, a fine line, on whole pixels
        self.assertEqual(at(corner, side, 3, 3)[3], 0)  # the very corner is rounded off
        run = icons.rgba(4, depth, icons.placed(layers, dx=side))  # a length of the top edge
        self.assertEqual([at(run, 4, 2, y) for y in range(depth)], [at(corner, side, side - 1, y) for y in range(depth)])
        foot = icons.rgba(side, side, icons.placed(layers, width=side, height=side))  # the foot's right corner
        for x, y in ((3, 4), (4, 12), (6, 6), (8, 10)):
            self.assertEqual(at(foot, side, side - 1 - x, side - 1 - y), at(corner, side, x, y), (x, y))
        right = icons.rgba(depth, 4, icons.placed(layers, dy=side, width=depth))  # a length of the right side
        self.assertEqual([at(right, depth, x, 1) for x in range(depth)],
                         [at(run, 4, 2, depth - 1 - x) for x in range(depth)])

    def test_the_ribbon_hangs_with_a_swallowtail_and_a_shadow(self):
        band, length, notch, drop = 10, 30, 5, 2
        width, height = band + drop + 1, length + drop + 1
        pixels = icons.rgba(width, height, icons.ribbon(band, length, notch, drop, "#D97757", "#C7684A", "#96513A",
                                                         "#5A322038", "#5A32201A"))
        at = lambda x, y: tuple(pixels[(y * width + x) * 4:(y * width + x) * 4 + 4])  # noqa: E731
        self.assertEqual(at(2, 15), (0xD9, 0x77, 0x57, 255))  # the ribbon
        self.assertEqual(at(8, 15), (0xC7, 0x68, 0x4A, 255))  # its folded half, in shade
        self.assertEqual(at(5, 0)[:3], (0x96, 0x51, 0x3A))  # dark where it comes out from under the cover
        self.assertLess(at(5, length - 2)[3], 80)  # the swallowtail's notch: hardly anything but shadow
        shadow = at(band + 1, 15)[3]
        self.assertTrue(0 < shadow < 80, shadow)  # a faint shadow down the right


class Pieces(unittest.TestCase):
    def setUp(self):
        self.root = hidden_window(self)
        self.theme = Theme(self.root)
        self.cache = Path(tempfile.mkdtemp())
        patcher = mock.patch("lyrebot.theme.CACHE", self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_images_are_drawn_once_and_kept(self):
        with mock.patch("lyrebot.icons.rgba", wraps=icons.rgba) as drawn:
            first = self.theme.icon("check", 14, "#D97757", icons.check)
            again = self.theme.icon("check", 14, "#D97757", icons.check)
        self.assertIs(first, again)
        self.assertEqual(drawn.call_count, 1)
        self.assertEqual(len(list(self.cache.glob("*.png"))), 1)
        fresh = Theme(hidden_window(self))  # another window reads it from disk instead of drawing it
        with mock.patch("lyrebot.icons.rgba", wraps=icons.rgba) as drawn:
            image = fresh.icon("check", 14, "#D97757", icons.check)
        self.assertEqual(drawn.call_count, 0)
        self.assertEqual((image.width(), image.height()), (14, 14))

    def test_a_pill_button_fits_its_words_and_does_nothing_while_off(self):
        pressed = []
        button = PillButton(self.root, self.theme, "搜索", lambda: pressed.append(1))
        narrow = button.wide
        button.set_text("一段长一些的字")
        self.assertGreater(button.wide, narrow)
        self.assertEqual(int(button.cget("width")), button.wide)
        inside = SimpleNamespace(x=2, y=2)
        button.winfo_width = lambda: button.wide  # the window is never drawn
        button.winfo_height = lambda: button.tall
        button.press(inside)
        button.release(inside)
        self.assertEqual(pressed, [1])
        button.set_enabled(False)
        button.press(inside)
        button.release(inside)
        self.assertEqual(pressed, [1])

    def test_a_choice_shows_its_value_and_greys_out_what_is_not_allowed(self):
        value = tk.StringVar(master=self.root, value="单曲")
        picked = []
        choice = Choice(self.root, self.theme, value, ("单曲", "单曲循环", "随机演奏"), lambda: picked.append(value.get()))
        self.assertEqual(choice.text, "单曲")
        width = choice.wide
        choice.menu.invoke(1)
        self.assertEqual((value.get(), choice.text, picked), ("单曲循环", "单曲循环", ["单曲循环"]))
        self.assertGreater(choice.wide, width)  # it fits what it says
        choice.allow(("单曲",))
        self.assertEqual([choice.menu.entrycget(i, "state") for i in range(3)], ["normal", "disabled", "disabled"])
        choice.menu.invoke(2)  # a disabled option is not taken
        self.assertEqual(value.get(), "单曲循环")

    def test_a_writing_line_hints_only_while_empty(self):
        line = WritingLine(self.root, self.theme, hint="搜索曲名")
        line.pack()
        self.root.update_idletasks()
        self.assertTrue(line.hint.winfo_manager())
        line.entry.insert(0, "风")
        self.assertFalse(line.hint.winfo_manager())
        line.entry.delete(0, "end")
        self.assertTrue(line.hint.winfo_manager())

    def type_into(self, line, *keys):
        """Press ``keys`` (a character, or a key's name with "^" before it for Control) in ``line``."""
        for key in keys:
            if len(key) == 1:
                line.key(SimpleNamespace(keysym="??", state=0, char=key))
            else:
                held = 0x4 if key.startswith("^") else 0
                line.key(SimpleNamespace(keysym=key.lstrip("^"), state=held, char=""))

    def test_a_page_line_takes_what_an_entry_takes_and_ends_as_an_entry_would(self):
        line = PageLine(self.root, self.theme, icon=icons.magnifier).entry
        entry = tk.Entry(self.root)
        for target in (line, entry):
            target.insert(0, "风息")
            target.insert("end", "的离别诗")
            target.insert(2, "·")
            target.delete(0)  # one character
            target.delete(1, 3)  # up to the third, not including it
            target.delete(0, "end")
            target.insert(0, "清润玉响")
        self.assertEqual(line.get(), entry.get())
        self.assertEqual(line.get(), "清润玉响")

    def test_typing_on_a_page_line(self):
        line = PageLine(self.root, self.theme, icon=icons.magnifier).entry
        self.type_into(line, *"春日影")
        self.assertEqual(line.get(), "春日影")
        self.type_into(line, "Left", "BackSpace", "Home", "Delete", "End", "Right", "x")
        self.assertEqual(line.get(), "影x")  # 日 then 春 gone; the cursor never runs past either end
        self.type_into(line, "Home", "^a", "风")  # a selection is written over
        self.assertEqual(line.get(), "风")
        self.assertEqual(line.key(SimpleNamespace(keysym="Tab", state=0, char="\t")), None)  # left to others

    def test_a_page_line_cuts_copies_and_pastes_on_one_line(self):
        line = PageLine(self.root, self.theme).entry
        board = []
        with mock.patch.object(line, "clipboard_clear", side_effect=board.clear), \
                mock.patch.object(line, "clipboard_append", side_effect=board.append), \
                mock.patch.object(line, "clipboard_get", side_effect=lambda: "".join(board)):
            self.type_into(line, *"晨歌", "^a", "^c")
            self.assertEqual((line.get(), board), ("晨歌", ["晨歌"]))
            self.type_into(line, "^x")
            self.assertEqual((line.get(), board), ("", ["晨歌"]))
            board[:] = ["林中\r\n晨歌  "]
            self.type_into(line, "^v")
        self.assertEqual(line.get(), "林中 晨歌")  # lines and runs of space pasted as one space

    def test_what_an_input_method_composes_shows_in_the_line(self):
        line = PageLine(self.root, self.theme, icon=icons.magnifier).entry
        if line.composition is None:
            self.skipTest("input methods are Windows' here")
        line.insert(0, "风息")
        line.icursor(line.words, 1)
        line.composition.active = True
        line.composition.show("qing")
        line.watch()
        [shown] = [item for item in line.find_withtag("composing") if line.type(item) == "text"]
        self.assertEqual(line.itemcget(shown, "text"), "风qing息")  # at the cursor, the rest moved on
        self.assertEqual(line.itemcget(line.words, "state"), "hidden")
        self.assertEqual(line.get(), "风息")  # nothing is written until the input method chooses
        line.composition.show("")
        line.watch()
        self.assertEqual(line.find_withtag("composing"), ())
        self.assertEqual(line.itemcget(line.words, "state"), "normal")
        line.focused(False)  # leaving the line stops watching and forgets any composition
        self.assertFalse(line.composition.active)
        self.assertIsNone(line.watching)

    def test_the_magnifier_is_part_of_the_line(self):
        line = PageLine(self.root, self.theme, icon=icons.magnifier)
        mark = line.grid_slaves(row=0, column=0)[0]
        self.assertEqual(mark.cget("cursor"), "xterm")
        self.assertEqual(line.entry.cget("cursor"), "xterm")

    def test_a_long_search_slides_to_keep_the_cursor_in_sight(self):
        line = PageLine(self.root, self.theme).entry
        line.configure(width=60)
        line.insert(0, "一宵岁起明日新-录音" * 2)
        self.assertGreater(line.shift, 0)
        x = line.coords(line.words)[0]
        width = self.theme.measure(line.font, line.get())
        self.assertLessEqual(x + width, 60)  # its end, where the cursor is, inside the line
        self.type_into(line, "Home")
        self.assertEqual(line.shift, 0)  # and back to its start

    def test_a_dialogs_title_bar_has_its_close_button_alone(self):
        frame = chrome.Frame(edge=0, caption=0)
        closed = []
        bar = TitleBar(self.root, self.theme, frame, "命名", "", None, lambda: closed.append(True))
        with mock.patch.object(bar, "winfo_width", return_value=300), mock.patch.object(bar, "winfo_x", return_value=0), \
                mock.patch.object(bar, "winfo_y", return_value=0):
            bar.redraw()
        [close] = frame.buttons
        self.assertEqual(close[2], 300 - self.theme.px(8))
        middle = SimpleNamespace(x=(close[0] + close[2]) / 2, y=(close[1] + close[3]) / 2)
        bar.press(middle)
        bar.release(middle)
        self.assertEqual(closed, [True])

    def test_a_window_may_have_its_corners_rounded_without_an_edge(self):
        from lyrebot import theme
        with mock.patch.object(theme, "_windows_11", return_value=True), \
                mock.patch.object(theme, "_set", return_value=True) as setting:
            self.assertTrue(theme.round_corners(self.root, border=None))
            theme.round_corners(self.root, border="#B08D57")
        edges = [call.args[2] for call in setting.call_args_list if call.args[1] == theme.DWMWA_BORDER_COLOR]
        self.assertEqual(edges, [theme.NO_COLOR, 0x578DB0])  # none at all; then old gold, as Windows reads it

    def test_the_main_windows_title_bar_has_an_open_book_left_of_minimise(self):
        pressed = []
        frame = chrome.Frame(edge=5, caption=0)
        bar = TitleBar(self.root, self.theme, frame, "LyreBot", "", lambda: pressed.append("minimise"),
                       lambda: pressed.append("close"), start=lambda: pressed.append("start"))
        with mock.patch.object(bar, "winfo_width", return_value=400), mock.patch.object(bar, "winfo_x", return_value=0), \
                mock.patch.object(bar, "winfo_y", return_value=0):
            bar.redraw()
        close, minimise, start = frame.buttons  # (all three told to the frame: presses there reach the bar)
        self.assertLess(start[2], minimise[0])
        self.assertEqual(minimise[0] - start[2], close[0] - minimise[2])  # spaced alike

        def at(box):
            return SimpleNamespace(x=(box[0] + box[2]) / 2, y=(box[1] + box[3]) / 2)
        bar.point(bar.button_at(at(start).x, at(start).y))
        self.assertEqual(bar.inside, "start")
        self.assertIsNotNone(bar.tip_job)  # its words, once the pointer rests on it
        bar.press(at(start))
        self.assertIsNone(bar.tip_job)  # (not once it is pressed)
        bar.release(at(start))
        self.assertEqual(pressed, ["start"])
        self.assertNotIn("start", TitleBar(self.root, self.theme, chrome.Frame(edge=0, caption=0), "对话框", "", None,
                                           lambda: None).commands)  # a dialog's bar has × alone

    def test_the_pin_sits_between_the_book_and_minimise_and_shows_whether_the_window_is_on_top(self):
        pressed = []
        frame = chrome.Frame(edge=5, caption=0)
        bar = TitleBar(self.root, self.theme, frame, "LyreBot", "", lambda: None, lambda: None,
                       start=lambda: None, pin=lambda: pressed.append("pin"), pinned=True)
        with mock.patch.object(bar, "winfo_width", return_value=400), mock.patch.object(bar, "winfo_x", return_value=0), \
                mock.patch.object(bar, "winfo_y", return_value=0):
            bar.redraw()
        close, minimise, pin, start = frame.buttons
        self.assertLess(start[2], pin[0])
        self.assertLess(pin[2], minimise[0])
        self.assertEqual(pin[0] - start[2], minimise[0] - pin[2])  # spaced alike
        self.assertEqual(bar.itemcget("pin", "image"), str(bar.pin_faces[True][False]))  # pressed in: kept on top
        self.assertEqual(bar.tips["pin"], "取消置顶")
        middle = SimpleNamespace(x=(pin[0] + pin[2]) / 2, y=(pin[1] + pin[3]) / 2)
        bar.press(middle)
        bar.release(middle)
        self.assertEqual(pressed, ["pin"])  # the window's to change, and then to say so:
        bar.show_pinned(False)
        self.assertEqual(bar.itemcget("pin", "image"), str(bar.pin_faces[False][False]))  # leaning
        self.assertEqual(bar.tips["pin"], "窗口置顶")
        bar.point("pin")
        self.assertEqual(bar.itemcget("pin", "image"), str(bar.pin_faces[False][True]))  # lit under the pointer
        bar.show_pinned(True)
        self.assertEqual(bar.itemcget("pin", "image"), str(bar.pin_faces[True][True]))  # and still lit

    def test_the_title_bar_shows_the_frame_its_buttons_and_presses_them(self):
        pressed = []
        frame = chrome.Frame(edge=5, caption=0)
        bar = TitleBar(self.root, self.theme, frame, "LyreBot", "未以管理员身份运行",
                       lambda: pressed.append("minimise"), lambda: pressed.append("close"))
        px = self.theme.px
        with mock.patch.object(bar, "winfo_width", return_value=400), mock.patch.object(bar, "winfo_x", return_value=0), \
                mock.patch.object(bar, "winfo_y", return_value=0):
            bar.redraw()
        self.assertEqual(frame.caption, bar.bar_height)
        close, minimise = frame.buttons
        self.assertEqual(close[2], 400 - px(8))  # × sits at the right, — just left of it
        self.assertLess(minimise[2], close[0])
        self.assertGreater(close[1], 0)
        self.assertLessEqual(close[3], bar.bar_height - px(3))  # above the gilt fillet
        texts = [bar.itemcget(item, "text") for item in bar.find_all() if bar.type(item) == "text"]
        self.assertEqual(texts, ["LyreBot", "未以管理员身份运行"])

        def at(box):
            return SimpleNamespace(x=(box[0] + box[2]) / 2, y=(box[1] + box[3]) / 2)
        bar.point(bar.button_at(at(close).x, at(close).y))
        self.assertEqual(bar.inside, "close")
        self.assertEqual(bar.itemcget("close", "image"), str(bar.faces["close"][True]))  # lit under the pointer
        bar.press(at(close))
        bar.release(SimpleNamespace(x=200, y=10))  # let go off the button: nothing
        self.assertEqual(pressed, [])
        for box in (minimise, close):
            bar.press(at(box))
            bar.release(at(box))
        self.assertEqual(pressed, ["minimise", "close"])
        self.assertIsNone(bar.inside)


class Menus(unittest.TestCase):
    def setUp(self):
        self.root = hidden_window(self)
        self.theme = Theme(self.root)
        self.value = tk.StringVar(master=self.root, value="a")
        self.done = []
        self.menu = PopupMenu(self.root, self.theme)
        self.menu.add_radiobutton(label="甲", value="a", variable=self.value)
        self.menu.add_radiobutton(label="乙", value="b", variable=self.value)
        self.menu.add_separator()
        self.sub = PopupMenu(self.root, self.theme)
        self.sub.add_command(label="里面", command=lambda: self.done.append("里面"))
        self.menu.add_cascade(label="更多", menu=self.sub, icon=icons.folder)
        self.menu.add_command(label="不可用", command=lambda: self.done.append("不可用"), state="disabled")
        self.menu.add_command(label="删除", command=lambda: self.done.append("删除"), accelerator="Delete", danger=True)
        self.addCleanup(self.menu.close)

    def middle(self, menu, index):
        top, bottom = menu.rows[index]
        return (top + bottom) // 2

    def pointer(self, menu, kind, index=None, x=20, y=None):
        y = self.middle(menu, index) if index is not None else y
        menu.window.event_generate(kind, x=x, y=y, when="now")

    def test_it_answers_like_a_tk_menu(self):
        menu = self.menu
        self.assertEqual(menu.index("end"), 5)
        self.assertEqual([menu.type(i) for i in range(6)], ["radiobutton", "radiobutton", "separator", "cascade",
                                                          "command", "command"])
        self.assertEqual(menu.entrycget(5, "accelerator"), "Delete")
        menu.invoke(1)
        self.assertEqual(self.value.get(), "b")
        menu.invoke(4)
        self.assertEqual(self.done, [])
        menu.entryconfigure(4, state="normal")
        menu.invoke(4)
        self.assertEqual(self.done, ["不可用"])
        menu.delete(4, "end")
        self.assertEqual(menu.index("end"), 3)

    def test_pointing_and_releasing_chooses_an_item_and_closes(self):
        menu = self.menu
        menu.post(200, 200)
        menu.window.update()
        self.pointer(menu, "<ButtonRelease-1>", 1)  # the release of the click that opened it
        self.assertEqual(self.value.get(), "a")
        time.sleep(0.2)
        self.pointer(menu, "<Motion>", 1)
        self.assertEqual(menu.active, 1)
        self.pointer(menu, "<ButtonRelease-1>", 1)
        self.assertEqual(self.value.get(), "b")
        self.assertIsNone(menu.window)

    def test_a_click_outside_closes_it(self):
        menu = self.menu
        menu.post(200, 200)
        menu.window.update()
        self.pointer(menu, "<ButtonPress-1>", x=-30, y=-30)
        self.assertIsNone(menu.window)

    def test_the_keys_move_skip_what_cannot_be_chosen_and_open_a_submenu(self):
        menu = self.menu
        menu.post(200, 200)
        menu.window.update()
        key = lambda name: menu.key(SimpleNamespace(keysym=name))  # noqa: E731
        key("Down")
        key("Down")
        key("Down")
        self.assertEqual(menu.active, 3)  # past the separator, onto 更多
        key("Right")
        self.assertIsNotNone(self.sub.window)
        self.assertEqual(self.sub.active, 0)
        key("Return")
        self.assertEqual(self.done, ["里面"])
        self.assertIsNone(menu.window)
        self.assertIsNone(self.sub.window)

    def test_escape_closes_a_submenu_first(self):
        menu = self.menu
        menu.post(200, 200)
        menu.window.update()
        menu.hover(3)
        menu.open_child(3)
        self.assertIsNotNone(self.sub.window)
        menu.key(SimpleNamespace(keysym="Escape"))
        self.assertIsNone(self.sub.window)
        self.assertIsNotNone(menu.window)
        menu.key(SimpleNamespace(keysym="Escape"))
        self.assertIsNone(menu.window)


class Dialogs(unittest.TestCase):
    def setUp(self):
        self.root = hidden_window(self)
        Theme(self.root)

    def answer(self, title, *steps):
        """Once the dialog called ``title`` is up, run ``steps`` on it."""
        def find(window):
            return [w for w in window.winfo_children() if isinstance(w, tk.Toplevel) and w.title() == title]

        def act(tries=100):
            found = find(self.root)
            if not found:
                if tries:
                    self.root.after(30, act, tries - 1)
                return
            dialog = found[0]
            dialog.update()
            for step in steps:
                step(dialog)
        self.root.after(30, act)

    @staticmethod
    def entry(dialog):
        stack = [dialog]
        while stack:
            widget = stack.pop()
            if isinstance(widget, tk.Entry):
                return widget
            stack.extend(widget.winfo_children())

    def test_ask_text_gives_what_was_written_or_none(self):
        def write(dialog):
            entry = self.entry(dialog)
            self.assertEqual(entry.get(), "旧名")
            entry.delete(0, "end")
            entry.insert(0, "新名")
        self.answer("命名", write, lambda d: d.event_generate("<Return>"))
        self.assertEqual(dialogs.ask_text("命名", "叫什么？", initialvalue="旧名", parent=self.root), "新名")
        self.answer("命名", lambda d: d.event_generate("<Escape>"))
        self.assertIsNone(dialogs.ask_text("命名", "叫什么？", parent=self.root))

    def test_a_dialog_wears_the_leather_bar_its_window_wears(self):
        self.root.lyrebot_frame = chrome.Frame(edge=5, caption=40)  # as App.take_frame leaves it
        seen = {}

        def look(dialog):
            [bar] = [child for child in dialog.winfo_children() if isinstance(child, TitleBar)]
            seen["commands"], seen["title"] = list(bar.commands), bar.title
            bar.commands["close"]()  # × answers as "no" would
        with mock.patch.object(chrome, "available", return_value=True), \
                mock.patch.object(chrome, "adopt", return_value=True) as adopt, \
                mock.patch.object(dialogs, "round_corners") as corners:
            self.answer("删除", look)
            self.assertFalse(dialogs.confirm("删除", "删除「歌」？", parent=self.root, yes="删除", danger=True))
        self.assertEqual(seen, {"commands": ["close"], "title": "删除"})  # × alone: no minimising a dialog
        self.assertIs(adopt.call_args.kwargs["owner"], self.root)  # kept over the window, out of the taskbar
        corners.assert_called_once()

    def test_a_dangerous_question_says_no_to_enter(self):
        self.answer("删除", lambda d: d.event_generate("<Return>"))
        self.assertFalse(dialogs.confirm("删除", "删除「歌」？\n\n会移到回收站", parent=self.root, yes="删除", danger=True))
        self.answer("编配", lambda d: d.event_generate("<Return>"))
        self.assertTrue(dialogs.confirm("编配", "重新生成？\n\n会覆盖", parent=self.root, yes="重新编配"))

    def test_the_question_leads_and_the_details_follow(self):
        texts = []

        def read(dialog):
            stack = [dialog]
            while stack:
                widget = stack.pop()
                if isinstance(widget, tk.Label) and widget.cget("text"):
                    texts.append(widget.cget("text"))
                stack.extend(widget.winfo_children())
            dialog.event_generate("<Escape>")
        self.answer("删除", read)
        dialogs.confirm("删除", "删除「歌」？\n\n这些文件会移到回收站：\n\nmidi\\歌.mid", parent=self.root, danger=True)
        self.assertIn("删除「歌」？", texts)
        self.assertIn("这些文件会移到回收站：\n\nmidi\\歌.mid", texts)

    def test_errors_and_notes_wait_to_be_read(self):
        self.answer("无法移动", lambda d: d.event_generate("<Return>"))
        self.assertIsNone(dialogs.error("无法移动", "那里已经有同名的曲谱", parent=self.root))
        self.answer("还不能录音", lambda d: d.event_generate("<Escape>"))
        self.assertIsNone(dialogs.info("还不能录音", "缺少转录工具", parent=self.root))


LEFT_SCREEN = (-2400, 0, 0, 1290)  # a second screen left of the main one, less its taskbar


def placed(window):
    """Where a window was put and how big it is, as its geometry says: (x, y, width, height)."""
    window.update_idletasks()
    width, height, x, y = map(int, re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", window.wm_geometry()).groups())
    return x, y, width, height


class Screens(unittest.TestCase):
    """Menus, tips and dialogs open on the screen LyreBot is on, a second one at negative coordinates included."""

    def setUp(self):
        self.root = hidden_window(self)
        self.theme = Theme(self.root)
        self.margin = self.theme.px(4)

    def test_fit_keeps_a_popup_whole_on_its_own_screen(self):
        self.assertEqual(fit(200, 150, -900, 500, LEFT_SCREEN), (-900, 500))  # room enough: where it was asked
        self.assertEqual(fit(200, 150, -100, 500, LEFT_SCREEN), (-200, 500))  # in from that screen's right edge
        self.assertEqual(fit(200, 150, -900, 1200, LEFT_SCREEN, bottom_instead=1190), (-900, 1040))  # upwards
        self.assertEqual(fit(200, 150, -150, 500, LEFT_SCREEN, left_instead=-600), (-600, 500))  # to the left
        self.assertEqual(fit(200, 150, -2390, 1200, LEFT_SCREEN, bottom_instead=100), (-2390, 1140))  # no room up
        above = (0, -1080, 1920, -40)  # a screen above the main one
        self.assertEqual(fit(200, 150, 100, -100, above, bottom_instead=-110), (100, -260))

    def test_tk_is_given_negative_positions_as_it_reads_them(self):
        self.assertEqual(geometry(-300, -20, 100, 50), "100x50+-300+-20")
        self.assertEqual(geometry(10.7, 20.2), "+10+20")

    def test_the_area_is_that_of_the_screen_at_the_point(self):
        middle = (self.root.winfo_screenwidth() // 2, self.root.winfo_screenheight() // 2)
        left, top, right, bottom = screen_area(self.root, *middle)
        self.assertTrue(left <= middle[0] < right and top <= middle[1] < bottom)
        with mock.patch("lyrebot.theme.sys.platform", "linux"):  # elsewhere: the main screen
            self.assertEqual(screen_area(self.root, -500, 300),
                             (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()))

    def test_a_menu_stays_on_the_screen_it_opens_on(self):
        menu = PopupMenu(self.root, self.theme)
        for name in ("甲", "乙", "丙"):
            menu.add_command(label=name, command=lambda: None)
        self.addCleanup(menu.close)
        with mock.patch("lyrebot.widgets.screen_area", return_value=LEFT_SCREEN):
            menu.post(-900, 500)
            self.assertEqual(placed(menu.window)[:2], (-900, 500))
            menu.post(-30, 500)  # by the edge it shares with the main screen: kept on its own
            x, y, width, _ = placed(menu.window)
            self.assertEqual((x + width, y), (-self.margin, 500))
            menu.post(-900, 1280)  # at the bottom: upwards, ending where it was opened
            x, y, _, height = placed(menu.window)
            self.assertEqual((x, y + height), (-900, 1280))

    def test_a_submenu_short_of_room_opens_to_the_left_and_upwards(self):
        menu, sub = PopupMenu(self.root, self.theme), PopupMenu(self.root, self.theme)
        for n in range(12):
            sub.add_command(label=f"第{n + 1}处", command=lambda: None)
        menu.add_cascade(label="移到…", menu=sub)
        self.addCleanup(menu.close)
        width, height = menu.measure()
        with mock.patch("lyrebot.widgets.screen_area", return_value=LEFT_SCREEN):
            menu.post(-self.margin - width, LEFT_SCREEN[3] - self.margin - height)  # the bottom right corner
            menu.window.update()
            menu.open_child(0)
            x, y, _, _ = placed(menu.window)
            sub_x, sub_y, sub_width, sub_height = placed(sub.window)
        self.assertEqual(sub_x + sub_width, x + self.theme.px(4))  # over its menu's edge by as much as on the right
        self.assertEqual(sub_y + sub_height, y + menu.rows[0][1] + menu.metrics()["edge"])  # level with its row

    def test_a_choice_opens_below_or_else_above(self):
        value = tk.StringVar(master=self.root, value="100%")
        choice = Choice(self.root, self.theme, value, ("80%", "100%", "120%", "150%", "200%"), lambda: None)
        self.addCleanup(choice.menu.close)
        gap = self.theme.px(4)
        with mock.patch("lyrebot.widgets.screen_area", return_value=LEFT_SCREEN):
            for top in (300, 1250):
                with mock.patch.multiple(choice, winfo_rootx=lambda: -900, winfo_rooty=lambda top=top: top,
                                         winfo_height=lambda: 30):
                    choice.open()
                    x, y, _, height = placed(choice.menu.window)
                    if top == 300:
                        self.assertEqual((x, y), (-900, 300 + 30 + gap))
                    else:
                        self.assertEqual((x, y + height), (-900, 1250 - gap))

    def test_a_tip_floats_above_its_button_and_stays_on_that_screen(self):
        tip = Tooltip(tk.Label(self.root), "下一首", self.theme)
        gap = self.theme.px(6)

        def at(x, y):
            tip.widget = SimpleNamespace(winfo_rootx=lambda: x, winfo_rooty=lambda: y, winfo_width=lambda: 30,
                                         winfo_height=lambda: 30)
            return tip.position(60, 20)
        with mock.patch("lyrebot.widgets.screen_area", return_value=LEFT_SCREEN):
            self.assertEqual(at(-500, 600), (-515, 580 - gap))  # centred over the button
            self.assertEqual(at(-500, 5), (-515, 35 + gap))  # at the top of the screen: under it
            self.assertEqual(at(-20, 600), (-60, 580 - gap))  # at the edge: kept on the button's screen

    def test_a_dialog_opens_over_the_window_or_where_the_pointer_is(self):
        def owner(x, shown=True):
            return SimpleNamespace(winfo_viewable=lambda: shown, winfo_rootx=lambda: x, winfo_rooty=lambda: 300,
                                   winfo_width=lambda: 438, winfo_height=lambda: 800,
                                   winfo_pointerxy=lambda: (-1200, 600))
        with mock.patch("lyrebot.dialogs.screen_area", return_value=LEFT_SCREEN) as area:
            window = owner(-900)
            self.assertEqual(dialogs._place(window, 400, 230), (-881, 490))  # centred, a third of the way down
            area.assert_called_with(window, -681, 700)  # the screen of the window's middle
            self.assertEqual(dialogs._place(owner(-300), 400, 230), (-400, 490))  # kept on that screen
            hidden = owner(-900, shown=False)
            self.assertEqual(dialogs._place(hidden, 400, 230), (-1400, 353))  # in the middle of the pointer's screen
            area.assert_called_with(hidden, -1200, 600)


if __name__ == "__main__":
    unittest.main()
