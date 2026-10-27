import sys
import unittest

from lyrebot.collate import pinyin_key


@unittest.skipUnless(sys.platform == "win32", "uses Windows' zh-CN collation")
class Pinyin(unittest.TestCase):
    def test_han_names_follow_pinyin(self):
        names = ["雪抚夜霜", "飞鼠进行曲", "琴弦呀", "向晚的丘甸", "风清月白", "柔月下的暗鸦"]
        self.assertEqual(sorted(names, key=pinyin_key),
                         ["飞鼠进行曲", "风清月白", "琴弦呀", "柔月下的暗鸦", "向晚的丘甸", "雪抚夜霜"])

    def test_numbers_by_value(self):
        self.assertEqual(sorted(["曲10", "曲2"], key=pinyin_key), ["曲2", "曲10"])
