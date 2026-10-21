from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.tools.search_tools import search_code


class SearchToolsTests(unittest.TestCase):
    def test_search_ignores_task_virtualenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / ".patchforge_venv" / "Lib").mkdir(parents=True)
            (root / "src" / "app.py").write_text("needle = True\n", encoding="utf-8")
            (root / ".patchforge_venv" / "Lib" / "site.py").write_text("needle = False\n", encoding="utf-8")

            results = search_code(root, "needle")

            self.assertEqual([item.path for item in results], ["src/app.py"])


if __name__ == "__main__":
    unittest.main()
