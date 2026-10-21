from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from task_manager.models import Task
from task_manager.storage import save_tasks


class StorageTests(unittest.TestCase):
    def test_save_tasks_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "tasks.json"
            save_tasks([Task("ship patch")], target)
            self.assertTrue(target.exists())
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(payload[0]["title"], "ship patch")


if __name__ == "__main__":
    unittest.main()

