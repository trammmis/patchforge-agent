from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from patchforge_demo.collections import median


class MedianTests(unittest.TestCase):
    def test_even_length_uses_average(self) -> None:
        self.assertEqual(median([1, 9, 3, 5]), 4.0)

    def test_odd_length_still_works(self) -> None:
        self.assertEqual(median([9, 1, 5]), 5.0)


if __name__ == "__main__":
    unittest.main()

