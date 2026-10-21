from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from patchforge_demo.strings import normalize_name


class StringTests(unittest.TestCase):
    def test_none_name_returns_empty_string(self) -> None:
        self.assertEqual(normalize_name(None), "")

    def test_whitespace_name_is_title_cased(self) -> None:
        self.assertEqual(normalize_name("  ada lovelace "), "Ada Lovelace")


if __name__ == "__main__":
    unittest.main()

