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
(here / "options.txt").write_text(" ".join(args[i + 1] for i, a in enumerate(args) if a == "--request-option"))
(here / "heard.txt").write_text(str(pathlib.Path(args[args.index("--audio") + 1]).stat().st_size))
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

    def put(self, relative, size=0):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\0" * size)
        return path

    def test_graphics_card_build_first_and_the_full_precision_model(self):
        cpu = self.put("tools/audiocpp-cpu/audiocpp_cli.exe")
        gpu = self.put("tools/audiocpp-vulkan/audiocpp_cli.exe")
        self.put("tools/models/MuScriptor-Small-GGUF/muscriptor-small-f16.gguf", 2)
        f32 = self.put("tools/models/MuScriptor-Small-GGUF/muscriptor-small-f32.gguf", 4)
        self.put("tools/models/other/sheetsage.gguf", 9)
        tools = find_tools([self.root / "tools", self.root / "missing"])
        self.assertEqual(tools.attempts, (((str(gpu),), "vulkan"), ((str(cpu),), "cpu")))
        self.assertEqual(tools.model, f32)

    def test_the_biggest_model_wins_in_any_folder(self):
        self.put("first/audiocpp-vulkan/audiocpp_cli.exe")
        self.put("first/models/MuScriptor-Small-GGUF/muscriptor-small-f32.gguf", 4)
        medium = self.put("second/MuScriptor-Medium-GGUF/muscriptor-medium-f16.gguf", 6)
        self.put("second/MuScriptor-Medium-GGUF/muscriptor-medium-q4_k.gguf", 3)
        tools = find_tools([self.root / "first", self.root / "second"])
        self.assertEqual(tools.model, medium)

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
        self.assertEqual((self.folder / "options.txt").read_text(), "instruments=acoustic_piano")

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

    def test_the_audio_gets_a_lead_in_that_the_midi_gives_back(self):
        """MuScriptor misses a note already sounding when the audio begins, and a recording
        begins with its first note: the model hears half a second of silence first, and the
        notes come back that much earlier."""
        import wave
        from lyrebot.midi import parse_midi
        from lyrebot.transcribe import LEAD_IN
        try:
            from .test_arrange import song
        except ImportError:
            from test_arrange import song
        with wave.open(str(self.audio), "wb") as audio:
            audio.setnchannels(2)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"\x01\x00" * 2 * 800)
        played = song((0, [(1, 2, 60), (2, 3, 64)]), bpm=120)  # a beat is half a second
        (self.folder / "answer.mid").write_bytes(played)
        transcribe(self.audio, self.target, self.tools)
        silence = round(8000 * LEAD_IN) * 4
        self.assertEqual(int((self.folder / "heard.txt").read_text()), self.audio.stat().st_size + silence)
        before, after = parse_midi(played), parse_midi(self.target.read_bytes())
        self.assertEqual([(note.start - after.ticks_per_beat, note.pitch) for note in before.notes],
                         [(note.start, note.pitch) for note in after.notes])
        with wave.open(str(self.folder / "check.wav"), "wb") as check:  # what was heard is a valid WAV
            check.setnchannels(1)
            check.setsampwidth(1)
            check.setframerate(1000)
            check.writeframes(b"\x90" * 10)
        from lyrebot.transcribe import with_lead_in
        self.assertTrue(with_lead_in(self.folder / "check.wav", self.folder / "padded.wav", 0.01))
        with wave.open(str(self.folder / "padded.wav"), "rb") as padded:
            self.assertEqual(padded.readframes(100), b"\x80" * 10 + b"\x90" * 10)  # 8-bit silence is 128
        self.assertFalse(with_lead_in(self.folder / "answer.mid", self.folder / "no.wav", 0.5))

    def test_other_formats_need_ffmpeg(self):
        mp3 = self.folder / "歌.mp3"
        mp3.write_bytes(b"ID3")
        with self.assertRaises(TranscribeError) as caught:
            transcribe(mp3, self.target, self.tools)
        self.assertIn("ffmpeg", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
