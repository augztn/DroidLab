import re
import unittest
from pathlib import Path

from src.ui_metrics import column_width, tree_row_height

APP_SOURCE = (Path(__file__).resolve().parent.parent / "src" / "app.py").read_text(encoding="utf-8")


class RowHeightTests(unittest.TestCase):
    def test_row_is_always_taller_than_one_line_of_text(self):
        for linespace in (12, 16, 20, 24, 33, 40, 64, 96):
            with self.subTest(linespace=linespace):
                self.assertGreater(tree_row_height(linespace), linespace)

    def test_row_height_scales_with_the_font(self):
        self.assertLess(tree_row_height(16), tree_row_height(24))
        self.assertLess(tree_row_height(24), tree_row_height(48))

    def test_minimum_row_height_is_respected(self):
        self.assertEqual(tree_row_height(4, padding=2, minimum=24), 24)


class ColumnWidthTests(unittest.TestCase):
    def test_column_fits_text_plus_padding(self):
        self.assertEqual(column_width(90), 118)
        self.assertGreaterEqual(column_width(140), 140 + 28)

    def test_minimum_width_is_respected(self):
        self.assertEqual(column_width(20, minimum=100), 100)


class AppUsesFontDerivedSizesTests(unittest.TestCase):
    def test_no_hard_coded_treeview_row_height(self):
        self.assertEqual(re.findall(r"rowheight\s*=\s*\d+", APP_SOURCE), [])
        self.assertEqual(re.findall(r"rowheight\s*=\s*\d+\s*if\b", APP_SOURCE), [])

    def test_row_height_is_derived_from_font_metrics(self):
        self.assertIn("tree_row_height(", APP_SOURCE)
        self.assertIn('.metrics("linespace")', APP_SOURCE)

    def test_partition_tree_has_a_scrollbar(self):
        self.assertIn("yscrollcommand=tree_scroll.set", APP_SOURCE)


if __name__ == "__main__":
    unittest.main()
