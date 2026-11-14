import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "enhanced"))
from studio_controls import RibbonLabelNotFound, find_ribbon_text


def word(text, left, right, top=219):
    return {"text":text,"left":left,"right":right,"top":top,"bottom":top+20}


class RibbonLabelTests(unittest.TestCase):
    def test_ocr_word_split(self):
        self.assertEqual(find_ribbon_text([[word("Data",0,35),word("Delve",39,80)]],"DataDelve"),(20,114.5))

    def test_adjacent_controls_and_duplicate_labels(self):
        with self.assertRaises(RibbonLabelNotFound):
            find_ribbon_text([[word("Frame",0,45),word("Tools",70,110)]],"Frame Tools")
        with self.assertRaisesRegex(ValueError,"found 2"):
            find_ribbon_text([[word("Rojo",0,45)],[word("Rojo",80,125)]],"Rojo")

    def test_top_menu_outside_target_band(self):
        with self.assertRaises(RibbonLabelNotFound):
            find_ribbon_text([[word("Plugins",0,60,top=12)]],"Plugins")
