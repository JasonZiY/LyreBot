import ctypes
import unittest
from unittest.mock import patch

from lyrebot import windows_input as win


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.responses = []
        self.states = {}
        self.cursor = (100, 100)

    def send_inputs(self, inputs):
        self.calls.append([(item.type, item.ki.wVk, item.ki.wScan, item.ki.dwFlags)
                           for item in inputs])
        response = self.responses.pop(0) if self.responses else len(inputs)
        if isinstance(response, Exception):
            raise response
        return response

    def last_error(self):
        return 5

    def async_key_state(self, key):
        return self.states.get(key, 0)

    def cursor_position(self):
        return self.cursor

    def foreground_window(self):
        return 123


class WindowsInputTests(unittest.TestCase):
    def test_input_structure_matches_windows_abi(self):
        self.assertEqual(ctypes.sizeof(win.INPUT), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        self.assertEqual(win.INPUT.data.offset, 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 4)

    def test_scan_code_presses_and_releases(self):
        api = FakeAPI()
        backend = win.WindowsInput(_api=api)
        backend.send_keys(("q", "a"), True)
        self.assertEqual(api.calls, [[(1, 0, 0x10, 0x08), (1, 0, 0x1E, 0x08)]])
        self.assertEqual(backend.owned_keys, ("q", "a"))
        backend.release_all()
        self.assertEqual(api.calls[1:], [[(1, 0, 0x10, 0x0A)], [(1, 0, 0x1E, 0x0A)]])
        self.assertEqual(backend.owned_keys, ())

    def test_partial_press_retains_every_key_for_cleanup(self):
        api = FakeAPI()
        api.responses = [1]
        backend = win.WindowsInput(_api=api)
        with self.assertRaisesRegex(win.WindowsInputError, "1/2"):
            backend.send_keys(("q", "a"), True)
        self.assertEqual(backend.owned_keys, ("q", "a"))
        backend.release_all()
        self.assertEqual(len(api.calls), 3)
        self.assertEqual(backend.owned_keys, ())

    def test_keydown_exception_also_retains_cleanup_keys(self):
        api = FakeAPI()
        api.responses = [OSError("connection failed")]
        backend = win.WindowsInput(_api=api)
        with self.assertRaises(win.WindowsInputError):
            backend.send_keys(("q",), True)
        backend.release_all()
        self.assertEqual(api.calls[-1], [(1, 0, 0x10, 0x0A)])
        self.assertEqual(backend.owned_keys, ())

    def test_failed_release_keeps_ownership_and_attempts_other_keys(self):
        api = FakeAPI()
        backend = win.WindowsInput(_api=api)
        backend.send_keys(("q", "a"), True)
        api.responses = [0, 1]
        with self.assertRaisesRegex(win.WindowsInputError, "Q"):
            backend.release_all()
        self.assertEqual(backend.owned_keys, ("q",))
        self.assertEqual(len(api.calls), 3)
        backend.release_all()
        self.assertEqual(backend.owned_keys, ())

    def test_release_does_not_emit_unowned_keys(self):
        api = FakeAPI()
        backend = win.WindowsInput(_api=api)
        backend.send_keys(("q",), False)
        self.assertEqual(api.calls, [])

    def test_unsupported_key_never_sent(self):
        api = FakeAPI()
        backend = win.WindowsInput(_api=api)
        with self.assertRaises(win.WindowsInputError):
            backend.send_keys(("enter",), True)
        self.assertEqual(api.calls, [])
        self.assertEqual(backend.owned_keys, ())

    def test_f8_uses_high_bit_not_unreliable_transition_bit(self):
        api = FakeAPI()
        backend = win.WindowsInput(_api=api)
        api.states[0x77] = 1
        self.assertFalse(backend.stop_requested())
        api.states[0x77] = 0x8000
        self.assertTrue(backend.stop_requested())

    def test_emergency_corner_and_disable_option(self):
        api = FakeAPI()
        api.cursor = (0, 0)
        self.assertTrue(win.WindowsInput(_api=api).stop_requested())
        self.assertFalse(win.WindowsInput(_api=api, emergency_corner=False).stop_requested())
        api.cursor = (-100, -100)
        self.assertFalse(win.WindowsInput(_api=api).stop_requested())

    def test_all_dangerous_modifiers_detected(self):
        api = FakeAPI()
        for vk in (0x11, 0x10, 0x12, 0x5B, 0x5C):
            api.states[vk] = 0x8000
        self.assertEqual(win.WindowsInput(_api=api).held_modifiers(), ("Ctrl", "Shift", "Alt", "Windows"))

    def test_instantiation_off_windows_fails_readably(self):
        with patch.object(win.sys, "platform", "linux"):
            with self.assertRaisesRegex(win.WindowsInputError, "仅支持 Windows"):
                win.WindowsInput()


if __name__ == "__main__":
    unittest.main()
