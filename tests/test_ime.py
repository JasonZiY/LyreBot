import unittest

from lyrebot.ime import (GCS_COMPSTR, GCS_RESULTSTR, ISC_SHOWUICOMPOSITIONWINDOW, WM_IME_COMPOSITION,
                         WM_IME_ENDCOMPOSITION, WM_IME_SETCONTEXT, WM_IME_STARTCOMPOSITION, Composition)


class Context:
    """The input method, as the window procedure sees it: what is being composed, and where the
    candidates were put."""

    def __init__(self):
        self.composing_now = ""
        self.placed = []

    def composing(self):
        return self.composing_now

    def place(self, caret, area):
        self.placed.append((caret, area))


class Composing(unittest.TestCase):
    def setUp(self):
        self.composition, self.context = Composition(), Context()
        self.composition.caret, self.composition.area = (120, 4, 30), (90, 4, 220, 30)

    def handle(self, message, lparam=0):
        return self.composition.handle(message, 1, lparam, self.context)

    def test_the_window_draws_compositions_itself(self):
        shown = ISC_SHOWUICOMPOSITIONWINDOW | 0x0F  # Windows' own composition window, and the candidates
        self.assertEqual(self.handle(WM_IME_SETCONTEXT, shown), ("pass", 1, 0x0F))
        self.composition.active = True
        self.assertEqual(self.handle(WM_IME_SETCONTEXT, shown), ("pass", 1, 0x0F))

    def test_composing_shows_in_the_field_and_the_candidates_go_under_it(self):
        self.composition.active = True
        self.assertEqual(self.handle(WM_IME_STARTCOMPOSITION), 0)  # Windows' own window never opens
        self.context.composing_now = "feng"
        self.assertEqual(self.handle(WM_IME_COMPOSITION, GCS_COMPSTR), 0)
        self.assertEqual((self.composition.text, self.composition.version), ("feng", 1))
        self.assertEqual(self.context.placed[-1], ((120, 4, 30), (90, 4, 220, 30)))
        self.context.composing_now = ""  # 风 chosen: nothing left being composed
        self.assertIsNone(self.handle(WM_IME_COMPOSITION, GCS_RESULTSTR | GCS_COMPSTR))  # the choice goes on to Tk
        self.assertEqual(self.composition.text, "")
        self.assertEqual(self.handle(WM_IME_ENDCOMPOSITION), 0)
        self.assertEqual(self.composition.version, 2)

    def test_elsewhere_in_the_window_the_input_method_is_left_alone(self):
        self.context.composing_now = "feng"
        for message in (WM_IME_STARTCOMPOSITION, WM_IME_COMPOSITION, WM_IME_ENDCOMPOSITION):
            self.assertIsNone(self.handle(message, GCS_COMPSTR))
        self.assertEqual((self.composition.text, self.context.placed), ("", []))


if __name__ == "__main__":
    unittest.main()
