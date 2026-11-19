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

from lyrebot import dialogs, icons
from lyrebot.theme import Theme, fit, geometry, screen_area
from lyrebot.widgets import Choice, PillButton, PopupMenu, ThinScrollbar, Tooltip, WritingLine

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
                     icons.trash, icons.pen_nib, icons.warning, icons.lyre, icons.chevron):
            pixels = icons.rgba(16, 16, icons.paint(draw(16), "#000000"))
            self.assertTrue(any(pixels[3::4]), draw.__name__)
        width = icons.fleuron_width(7)
        self.assertTrue(any(icons.rgba(width, 7, icons.paint(icons.fleuron(width, 7), "#000000"))[3::4]))

    def test_the_gilt_is_laid_in_gold_and_the_lines_in_ink(self):
        ink, gold = "#2B2722", "#B08D57"
        for draw in (icons.next_track, icons.keyboard, icons.magnifier, icons.folder, icons.move_to, icons.quill,
                     icons.score_page, icons.pen_nib):
            colors = {color for color, _ in icons.paint(draw(18), ink, gold)}
            self.assertEqual(colors, {ink, gold}, draw.__name__)  # every one has lines and a touch of gold
        self.assertEqual({color for color, _ in icons.paint(icons.folder(18), "#C9BFAE")}, {"#C9BFAE"})  # off: all faint
        self.assertEqual([color for color, _ in icons.paint(icons.chevron(10), gold)], [gold])  # a lone shape too


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

    def test_the_scrollbar_pages_and_drags(self):
        moves = []
        bar = ThinScrollbar(self.root, self.theme, lambda *args: moves.append(args))
        bar.winfo_height = lambda: 200
        bar.set(0.25, 0.5)
        self.assertEqual(bar.thumb(), (50.0, 100.0))
        bar.press(SimpleNamespace(y=150))
        self.assertEqual(moves[-1], ("scroll", 1, "pages"))
        bar.press(SimpleNamespace(y=60))
        bar.motion(SimpleNamespace(y=80))
        self.assertEqual(moves[-1], ("moveto", 0.35))


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
