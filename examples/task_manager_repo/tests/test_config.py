from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from task_manager.config import load_config


class ConfigTests(unittest.TestCase):
    def test_ignores_malformed_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.env"
            path.write_text("owner=patchforge\ninvalid-line\npriority=high\n", encoding="utf-8")
            self.assertEqual(load_config(path), {"owner": "patchforge", "priority": "high"})


if __name__ == "__main__":
    unittest.main()

