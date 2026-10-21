from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from task_manager.models import validate_priority


class PriorityTests(unittest.TestCase):
    def test_accepts_case_insensitive_priority(self) -> None:
        self.assertEqual(validate_priority("HIGH"), "high")

    def test_rejects_unknown_priority(self) -> None:
        with self.assertRaises(ValueError):
            validate_priority("urgent")


if __name__ == "__main__":
    unittest.main()

