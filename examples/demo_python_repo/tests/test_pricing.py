from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from patchforge_demo.calculator import calculate_discount


class PricingTests(unittest.TestCase):
    def test_zero_price_returns_zero(self) -> None:
        self.assertEqual(calculate_discount(0, 0.25), 0.0)

    def test_regular_price_still_works(self) -> None:
        self.assertEqual(calculate_discount(100, 0.25), 25.0)


if __name__ == "__main__":
    unittest.main()

