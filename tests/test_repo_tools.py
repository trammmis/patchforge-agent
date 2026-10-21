from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.tools.repo_tools import list_files


class RepoToolsTests(unittest.TestCase):
    def test_list_files_does_not_ignore_workspace_named_patchforge_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / ".patchforge_runs" / "task" / "repo"
            (repo / "src").mkdir(parents=True)
            (repo / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
            (repo / ".patchforge_venv").mkdir()
            (repo / ".patchforge_venv" / "noise.py").write_text("noise\n", encoding="utf-8")

            files = list_files(repo)

            self.assertIn("src/app.py", files)
            self.assertNotIn(".patchforge_venv/noise.py", files)


if __name__ == "__main__":
    unittest.main()
