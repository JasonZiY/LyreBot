from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest

from lyrebot.transcribe import Tools, TranscribeError, find_tools, transcribe

# Stands in for audiocpp_cli.exe: copies answer.mid to --out, or fails the way
# mode.txt next to it says.
FAKE_CLI = """
import pathlib, shutil, sys, time
here = pathlib.Path(__file__).parent
args = sys.argv[1:]
out, backend = args[args.index("--out") + 1], args[args.index("--backend") + 1]
mode = (here / "mode.txt").read_text().strip() if (here / "mode.txt").exists() else "ok"
(here / "calls.txt").open("a").write(backend + "\\n")
if mode == "slow":
    time.sleep(30)
if mode == "broken" or (mode == "no gpu" and backend == "vulkan"):
    print("ggml_vulkan: Found 0 Vulkan devices", file=sys.stderr)
    print("audiocpp_cli failed: no usable device for " + backend, file=sys.stderr)
    sys.exit(1)
shutil.copyfile(here / "answer.mid", out)
"""


def fake_tools(folder: Path) -> Tools:
    script = folder / "fake_cli.py"
    script.write_text(FAKE_CLI, encoding="utf-8")
    (folder / "answer.mid").write_bytes(b"MThd fake midi")
    command = (sys.executable, str(script))
    return Tools(((command, "vulkan"), (command, "cpu")), folder / "muscriptor.gguf", ffmpeg=None)


class Finding(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def put(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        return path

    def test_graphics_card_build_first_and_the_full_precision_model(self):
        cpu = self.put("tools/audiocpp-cpu/audiocpp_cli.exe")
        gpu = self.put("tools/audiocpp-vulkan/audiocpp_cli.exe")
        self.put("tools/models/MuScriptor-Small-GGUF/muscriptor-small-f16.gguf")
        f32 = self.put("tools/models/MuScriptor-Small-GGUF/muscriptor-small-f32.gguf")
        self.put("tools/models/other/sheetsage.gguf")
        tools = find_tools([self.root / "tools", self.root / "missing"])
        self.assertEqual(tools.attempts, (((str(gpu),), "vulkan"), ((str(cpu),), "cpu")))
        self.assertEqual(tools.model, f32)

    def test_an_unnamed_build_tries_both_and_folders_combine(self):
        cli = self.put("first/audiocpp/audiocpp_cli.exe")
        model = self.put("second/muscriptor-small-f32.gguf")
        tools = find_tools([self.root / "first", self.root / "second"])
        self.assertEqual(tools.attempts, (((str(cli),), "vulkan"), ((str(cli),), "cpu")))
        self.assertEqual(tools.model, model)

    def test_nothing_without_both_pieces(self):
        self.put("tools/audiocpp-cpu/audiocpp_cli.exe")
        self.assertIsNone(find_tools([self.root / "tools"]))
        self.assertIsNone(find_tools([self.root / "nowhere"]))


class Transcribing(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.tools = fake_tools(self.folder)
        self.audio = self.folder / "歌.wav"
        self.audio.write_bytes(b"RIFF fake")
        self.target = self.folder / "out" / "歌.mid"
        self.target.parent.mkdir()

    def mode(self, text):
        (self.folder / "mode.txt").write_text(text)

    def calls(self):
        return (self.folder / "calls.txt").read_text().split()

    def test_writes_the_midi(self):
        transcribe(self.audio, self.target, self.tools)
        self.assertEqual(self.target.read_bytes(), b"MThd fake midi")
        self.assertEqual(self.calls(), ["vulkan"])
        self.assertEqual(list(self.target.parent.iterdir()), [self.target])

    def test_falls_back_to_the_processor(self):
        self.mode("no gpu")
        transcribe(self.audio, self.target, self.tools)
        self.assertEqual(self.calls(), ["vulkan", "cpu"])
        self.assertTrue(self.target.exists())

    def test_reports_the_tool_s_own_error(self):
        self.mode("broken")
        with self.assertRaises(TranscribeError) as caught:
            transcribe(self.audio, self.target, self.tools)
        self.assertIn("no usable device for cpu", str(caught.exception))
        self.assertFalse(self.target.exists())

    def test_cancel_stops_it_quickly(self):
        self.mode("slow")
        cancel = threading.Event()
        threading.Timer(0.5, cancel.set).start()
        started = time.monotonic()
        with self.assertRaises(TranscribeError) as caught:
            transcribe(self.audio, self.target, self.tools, cancel)
        self.assertEqual(str(caught.exception), "已取消")
        self.assertLess(time.monotonic() - started, 5)
        self.assertFalse(self.target.exists())

    def test_other_formats_need_ffmpeg(self):
        mp3 = self.folder / "歌.mp3"
        mp3.write_bytes(b"ID3")
        with self.assertRaises(TranscribeError) as caught:
            transcribe(mp3, self.target, self.tools)
        self.assertIn("ffmpeg", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
