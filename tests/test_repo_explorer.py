from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.agents.issue_parser import IssueParser
from app.agents.repo_explorer import RepoExplorer
from app.harness.sandbox_runner import SandboxRunner
from app.harness.tool_runtime import ToolRuntime
from app.harness.trace_recorder import TraceRecorder


class RepoExplorerTests(unittest.TestCase):
    def test_ranks_relevant_source_before_unrelated_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            baseline = root / "baseline"
            (repo / "src").mkdir(parents=True)
            (repo / "tests").mkdir()
            (baseline / "src").mkdir(parents=True)
            (baseline / "tests").mkdir()
            for base in [repo, baseline]:
                (base / "src" / "pricing.py").write_text("def calculate_discount(price):\n    return None\n", encoding="utf-8")
                (base / "tests" / "test_pricing.py").write_text("from src.pricing import calculate_discount\n", encoding="utf-8")
                (base / "tests" / "test_unrelated.py").write_text("def test_other():\n    assert True\n", encoding="utf-8")
            runtime = ToolRuntime(repo, baseline, TraceRecorder(root / "trace.jsonl"), SandboxRunner(repo))
            issue = IssueParser().parse("Fix calculate_discount zero price", "discount returns None")

            result = RepoExplorer().explore(runtime, issue)

            self.assertIn("src/pricing.py", result["candidate_files"][:2])
            self.assertIn("tests/test_pricing.py", result["candidate_files"])
            self.assertNotIn("tests/test_unrelated.py", result["candidate_files"][:3])

    def test_prioritizes_file_paths_mentioned_in_issue_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            baseline = root / "baseline"
            for base in [repo, baseline]:
                (base / "src" / "_pytest").mkdir(parents=True)
                (base / "testing").mkdir()
                (base / "src" / "_pytest" / "skipping.py").write_text("def f():\n    pass\n", encoding="utf-8")
                (base / "testing" / "test_skipping.py").write_text("def test_f():\n    pass\n", encoding="utf-8")
                (base / "src" / "_pytest" / "cacheprovider.py").write_text("def cache():\n    pass\n", encoding="utf-8")
            runtime = ToolRuntime(repo, baseline, TraceRecorder(root / "trace.jsonl"), SandboxRunner(repo))
            issue = IssueParser().parse(
                "--runxfail reports wrong location",
                "The problem is in src/_pytest/skipping.py. Update testing/test_skipping.py too.",
            )

            result = RepoExplorer().explore(runtime, issue)

            self.assertEqual(result["candidate_files"][:2], ["src/_pytest/skipping.py", "testing/test_skipping.py"])

    def test_pytest_skip_runxfail_issue_prioritizes_skipping_files_without_explicit_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            baseline = root / "baseline"
            for base in [repo, baseline]:
                (base / "src" / "_pytest").mkdir(parents=True)
                (base / "testing").mkdir()
                (base / "src" / "_pytest" / "__init__.py").write_text("", encoding="utf-8")
                (base / "src" / "_pytest" / "skipping.py").write_text("def evaluate_skip_marks(item):\n    pass\n", encoding="utf-8")
                (base / "src" / "_pytest" / "fixtures.py").write_text("def fixture():\n    pass\n", encoding="utf-8")
                (base / "testing" / "acceptance_test.py").write_text(
                    "def test_runxfail_cli():\n"
                    "    # runxfail skip pytest mark skip location runxfail skip\n"
                    "    pass\n",
                    encoding="utf-8",
                )
                (base / "testing" / "test_skipping.py").write_text("def test_skip():\n    pass\n", encoding="utf-8")
            runtime = ToolRuntime(repo, baseline, TraceRecorder(root / "trace.jsonl"), SandboxRunner(repo))
            issue = IssueParser().parse(
                "--runxfail reports wrong location for tests skipped with pytest.mark.skip",
                "The report points to an internal decorator location instead of the user's test.",
            )

            result = RepoExplorer().explore(runtime, issue)

            self.assertEqual(result["candidate_files"][:2], ["src/_pytest/skipping.py", "testing/test_skipping.py"])

    def test_pytest_collection_issue_includes_session_collection_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            baseline = root / "baseline"
            for base in [repo, baseline]:
                (base / "src" / "_pytest").mkdir(parents=True)
                (base / "testing").mkdir(parents=True)
                (base / "src" / "_pytest" / "__init__.py").write_text("", encoding="utf-8")
                (base / "src" / "_pytest" / "python.py").write_text("class Package:\n    pass\n", encoding="utf-8")
                (base / "src" / "_pytest" / "main.py").write_text("class Session:\n    def _collect(self): pass\n", encoding="utf-8")
                (base / "testing" / "test_collection.py").write_text("def test_pkgfile():\n    pass\n", encoding="utf-8")
                (base / "testing" / "python").mkdir()
                (base / "testing" / "python" / "collect.py").write_text("def test_collect():\n    pass\n", encoding="utf-8")
            runtime = ToolRuntime(repo, baseline, TraceRecorder(root / "trace.jsonl"), SandboxRunner(repo))
            issue = IssueParser().parse(
                "pytest collects random __init__.py files",
                "Collection should not import package __init__.py, but package parent relationships must be preserved.",
            )

            result = RepoExplorer().explore(runtime, issue)

            self.assertIn("src/_pytest/python.py", result["candidate_files"][:4])
            self.assertIn("src/_pytest/main.py", result["candidate_files"][:4])
            self.assertIn("testing/test_collection.py", result["candidate_files"])

    def test_pytest_collection_issue_ignores_skipsdist_noise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            baseline = root / "baseline"
            for base in [repo, baseline]:
                (base / "src" / "_pytest").mkdir(parents=True)
                (base / "testing" / "python").mkdir(parents=True)
                (base / "src" / "_pytest" / "__init__.py").write_text("", encoding="utf-8")
                (base / "src" / "_pytest" / "python.py").write_text("def pytest_collect_file(): pass\n", encoding="utf-8")
                (base / "src" / "_pytest" / "main.py").write_text("class Session: pass\n", encoding="utf-8")
                (base / "src" / "_pytest" / "skipping.py").write_text("def skip(): pass\n", encoding="utf-8")
                (base / "testing" / "test_collection.py").write_text("def test_collection(): pass\n", encoding="utf-8")
                (base / "testing" / "test_skipping.py").write_text("def test_skip(): pass\n", encoding="utf-8")
                (base / "testing" / "python" / "collect.py").write_text("def test_collect(): pass\n", encoding="utf-8")
            runtime = ToolRuntime(repo, baseline, TraceRecorder(root / "trace.jsonl"), SandboxRunner(repo))
            issue = IssueParser().parse(
                "pytest tries to collect random __init__.py files",
                "tox has skipsdist = true, but the bug is package collection importing __init__.py.",
            )

            result = RepoExplorer().explore(runtime, issue)

            self.assertEqual(result["candidate_files"][:2], ["src/_pytest/python.py", "src/_pytest/main.py"])
            self.assertNotIn("src/_pytest/skipping.py", result["candidate_files"][:3])


if __name__ == "__main__":
    unittest.main()
