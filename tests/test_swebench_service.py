from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.swebench import eval_task_from_swebench_row
from app.services.swebench import import_swebench_eval_dataset
from app.services.swebench import parse_instance_ids


class SweBenchServiceTests(unittest.TestCase):
    def test_eval_task_from_swebench_row(self) -> None:
        task = eval_task_from_swebench_row(
            {
                "instance_id": "pytest-dev__pytest-7432",
                "repo": "pytest-dev/pytest",
                "base_commit": "e6e300e729dd33956e5448d8be9a0b1540b4e53a",
                "problem_statement": "skipping: --runxfail breaks location\n\nDetails here.",
                "patch": "--- a/src/_pytest/skipping.py\n+++ b/src/_pytest/skipping.py\n@@ -1 +1 @@\n",
                "test_patch": "--- a/testing/test_skipping.py\n+++ b/testing/test_skipping.py\n@@ -1 +1 @@\n",
                "FAIL_TO_PASS": ["testing/test_skipping.py::test_issue"],
            },
            test_command="python -m pytest testing/test_skipping.py",
            use_docker=True,
            docker_image="python:3.8-slim",
            install_dependencies=True,
            allow_network=True,
        )

        self.assertEqual(task.task_id, "pytest-dev__pytest-7432")
        self.assertEqual(task.repo_url, "https://github.com/pytest-dev/pytest")
        self.assertIsNone(task.issue_url)
        self.assertEqual(task.issue_title, "skipping: --runxfail breaks location")
        self.assertEqual(task.issue_body, "Details here.")
        self.assertEqual(task.expected_modified_files, ["src/_pytest/skipping.py"])
        self.assertIn("testing/test_skipping.py", task.validation_patch or "")
        self.assertEqual(task.validation_test_command, "python -m pytest testing/test_skipping.py::test_issue")
        self.assertEqual(task.docker_image, "python:3.8-slim")
        self.assertTrue(task.install_dependencies)
        self.assertTrue(task.allow_network)

    def test_import_swebench_eval_dataset_writes_jsonl(self) -> None:
        row = {
            "instance_id": "pytest-dev__pytest-7432",
            "repo": "pytest-dev/pytest",
            "base_commit": "abc123",
            "problem_statement": "Title\n\nBody",
            "patch": "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n",
            "test_patch": "--- a/tests/test_app.py\n+++ b/tests/test_app.py\n@@ -1 +1 @@\n",
            "FAIL_TO_PASS": ["tests/test_app.py::test_bug"],
        }
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.swebench.load_swebench_rows",
            return_value=[row],
        ):
            output = Path(tmp) / "real.jsonl"
            result = import_swebench_eval_dataset(
                ["pytest-dev__pytest-7432"],
                output,
                test_command="pytest tests/test_demo.py",
                overwrite=True,
            )

            line = output.read_text(encoding="utf-8").strip()
            payload = json.loads(line)

        self.assertEqual(result["imported"], 1)
        self.assertEqual(payload["task_id"], "pytest-dev__pytest-7432")
        self.assertEqual(payload["test_command"], "pytest tests/test_demo.py")
        self.assertEqual(payload["expected_modified_files"], ["src/app.py"])
        self.assertEqual(payload["validation_test_command"], "python -m pytest tests/test_app.py::test_bug")
        self.assertIn("tests/test_app.py", payload["validation_patch"])

    def test_bare_pytest_uses_validation_files_as_primary_command(self) -> None:
        task = eval_task_from_swebench_row(
            {
                "instance_id": "pytest-dev__pytest-6197",
                "repo": "pytest-dev/pytest",
                "base_commit": "abc123",
                "problem_statement": "Collect packages\n\nBody",
                "patch": "--- a/src/_pytest/python.py\n+++ b/src/_pytest/python.py\n",
                "test_patch": "--- a/testing/test_collection.py\n+++ b/testing/test_collection.py\n",
                "FAIL_TO_PASS": [
                    "testing/test_collection.py::test_does_not_eagerly_collect_packages",
                    "testing/test_collection.py::test_does_not_put_src_on_path",
                ],
            },
            test_command="pytest",
        )

        self.assertEqual(task.test_command, "python -m pytest testing/test_collection.py")
        self.assertEqual(
            task.validation_test_command,
            "python -m pytest testing/test_collection.py::test_does_not_eagerly_collect_packages "
            "testing/test_collection.py::test_does_not_put_src_on_path",
        )

    def test_parse_instance_ids_accepts_repeated_and_comma_separated_values(self) -> None:
        self.assertEqual(
            parse_instance_ids(["a,b", "a", " c "]),
            ["a", "b", "c"],
        )


if __name__ == "__main__":
    unittest.main()
