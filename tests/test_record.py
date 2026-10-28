from array import array
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
import wave

from lyrebot.record import Take, WavWriter

RATE = 1000  # small numbers keep the arithmetic readable


def floats(value, frames, channels=2):
    return array("f", [value] * frames * channels).tobytes()


class Writing(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)

    def test_pcm_file_reads_back(self):
        path = self.folder / "pcm.wav"
        writer = WavWriter(path, 48000, 2, float_samples=False)
        writer.write(array("h", [1, -1, 300, -300]).tobytes())
        writer.close()
        with wave.open(str(path)) as file:
            self.assertEqual((file.getframerate(), file.getnchannels(), file.getsampwidth()), (48000, 2, 2))
            self.assertEqual(file.readframes(10), array("h", [1, -1, 300, -300]).tobytes())

    def test_float_file_header_matches_its_data(self):
        path = self.folder / "float.wav"
        writer = WavWriter(path, 44100, 2, float_samples=True)
        writer.write(floats(0.5, 3))
        writer.close()
        data = path.read_bytes()
        self.assertEqual(data[:4], b"RIFF")
        self.assertEqual(struct.unpack("<I", data[4:8])[0], len(data) - 8)
        tag, channels, rate, _, block, bits = struct.unpack("<HHIIHH", data[20:36])
        self.assertEqual((tag, channels, rate, block, bits), (3, 2, 44100, 8, 32))
        at = data.index(b"fact")
        self.assertEqual(struct.unpack("<I", data[at + 8:at + 12])[0], 3)
        at = data.index(b"data")
        self.assertEqual(struct.unpack("<I", data[at + 4:at + 8])[0], 24)
        self.assertEqual(array("f", data[at + 8:]).tolist(), [0.5] * 6)


class Taking(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.writer = WavWriter(self.folder / "take.wav", RATE, 2, float_samples=True)
        self.addCleanup(self.writer.close)

    def take(self, **limits):
        return Take(self.writer, RATE, 2, True, now=0.0, **{"wait": 10, "silence": 2, "longest": 60, **limits})

    def test_quiet_before_the_music_is_left_out(self):
        take = self.take()
        take.feed(floats(0.0, 100), 0.1)
        take.feed(floats(0.00001, 100), 0.2)  # hiss far below -80 dB
        self.assertIsNone(take.began)
        take.feed(floats(0.3, 100), 0.3)
        self.assertEqual(take.began, 0.3)
        self.assertEqual(self.writer.frames, 100)

    def test_gives_up_when_no_sound_comes(self):
        take = self.take(wait=1)
        take.idle(0.5)
        self.assertFalse(take.done)
        take.idle(1.1)
        self.assertEqual((take.reason, take.seconds), ("no sound", 0))

    def test_ends_after_a_stretch_of_quiet(self):
        take = self.take()
        take.feed(floats(0.3, 100), 1.0)
        take.feed(floats(0.0, 100), 2.0)
        self.assertFalse(take.done)
        take.feed(floats(0.0, 100), 3.1)
        self.assertEqual(take.reason, "quiet")
        take.feed(floats(0.3, 100), 3.2)  # nothing more is written once it ended
        self.assertEqual(self.writer.frames, 300)

    def test_ends_at_the_time_limit(self):
        take = self.take(longest=1)
        for step in range(12):
            take.feed(floats(0.3, 100), step * 0.1)
            if take.done:
                break
        self.assertEqual((take.reason, take.seconds), ("long", 1.0))

    def test_a_gap_in_the_packets_is_kept_as_silence(self):
        take = self.take()
        take.feed(floats(0.3, 100), 0.0)
        take.idle(0.1)  # a short wait for the next packet is normal
        self.assertEqual(self.writer.frames, 100)
        take.idle(1.0)
        self.assertEqual(self.writer.frames, 950)  # padded up to 50 ms behind real time
        take.feed(floats(0.3, 50), 1.0)
        self.assertEqual(self.writer.frames, 1000)

    def test_sixteen_bit_samples(self):
        writer = WavWriter(self.folder / "pcm.wav", RATE, 1, float_samples=False)
        self.addCleanup(writer.close)
        take = Take(writer, RATE, 1, False, now=0.0)
        take.feed(array("h", [2, -3, 1]).tobytes(), 0.1)
        self.assertIsNone(take.began)
        take.feed(array("h", [2, -400, 1]).tobytes(), 0.2)
        self.assertEqual((take.began, writer.frames), (0.2, 3))


if __name__ == "__main__":
    unittest.main()
