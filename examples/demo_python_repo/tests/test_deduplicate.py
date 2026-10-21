from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from patchforge_demo.collections import deduplicate


class DeduplicateTests(unittest.TestCase):
    def test_preserves_first_seen_order(self) -> None:
        self.assertEqual(deduplicate(["b", "a", "b", "c", "a"]), ["b", "a", "c"])


if __name__ == "__main__":
    unittest.main()

