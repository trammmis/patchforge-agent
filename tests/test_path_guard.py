from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.tools.path_guard import PathPolicyError, safe_join


class PathGuardTests(unittest.TestCase):
    def test_blocks_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(PathPolicyError):
                safe_join(root, "../outside.txt")

    def test_allows_inside_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(safe_join(root, "a/b.txt"), (root / "a" / "b.txt").resolve())


if __name__ == "__main__":
    unittest.main()

