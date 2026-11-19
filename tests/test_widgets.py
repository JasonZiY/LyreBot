from pathlib import Path
import struct
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest import mock
import zlib

from lyrebot import dialogs, icons
from lyrebot.theme import Theme
from lyrebot.widgets import Choice, PillButton, PopupMenu, ThinScrollbar, WritingLine

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
        layers = [("#2B2722", shape) for shape in icons.restart(size) + icons.keyboard(size)]
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
        for draw in (icons.play, icons.stop, icons.next_track, icons.restart, icons.keyboard, icons.check,
                     icons.magnifier, icons.folder, icons.move_to, icons.sparkle, icons.page_note, icons.trash,
                     icons.pencil, icons.diamond, icons.lyre, icons.chevron):
            shapes = draw(16)
            shapes = list(shapes) if isinstance(shapes, (list, tuple)) else [shapes]
            pixels = icons.rgba(16, 16, [("#000000", shape) for shape in shapes])
            self.assertTrue(any(pixels[3::4]), draw.__name__)


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


if __name__ == "__main__":
    unittest.main()
