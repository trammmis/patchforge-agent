from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from patchforge_demo.calculator import safe_divide


class DivisionTests(unittest.TestCase):
    def test_divide_by_zero_returns_none(self) -> None:
        self.assertIsNone(safe_divide(10, 0))

    def test_regular_division(self) -> None:
        self.assertEqual(safe_divide(10, 2), 5)


if __name__ == "__main__":
    unittest.main()

