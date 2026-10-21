from __future__ import annotations

import unittest
from unittest.mock import patch

from app import cli
from app.schemas.task import TaskRunResult


class CLITests(unittest.TestCase):
    def test_run_uses_github_issue_url_when_provided(self) -> None:
        fake_result = TaskRunResult(
            task_id="gh_issue",
            status="succeeded",
            patch_apply_success=True,
            tests_passed=True,
            iterations=1,
            duration_seconds=0.1,
            issue_url="https://github.com/example/project/issues/5",
        )
        with patch(
            "app.cli.fetch_issue",
            return_value={
                "issue_title": "Settings parser should skip bad rows",
                "issue_body": "Broken entries should be skipped.",
                "issue_url": "https://github.com/example/project/issues/5",
                "repo_url": "https://github.com/example/project",
            },
        ) as fetch_issue, patch("app.cli.ExecutionHarness") as harness_cls:
            harness_cls.return_value.run_task.return_value = fake_result
            exit_code = cli.main(
                [
                    "run",
                    "--task-id",
                    "gh_issue",
                    "--repo",
                    "examples/task_manager_repo",
                    "--issue-url",
                    "https://github.com/example/project/issues/5",
                    "--test-command",
                    "python -m unittest tests.test_config",
                ]
            )

        self.assertEqual(exit_code, 0)
        fetch_issue.assert_called_once()
        task = harness_cls.return_value.run_task.call_args.args[0]
        self.assertEqual(task.issue_title, "Settings parser should skip bad rows")
        self.assertEqual(task.issue_url, "https://github.com/example/project/issues/5")
        self.assertEqual(task.repo_url, "https://github.com/example/project")

    def test_run_can_use_issue_repo_url_without_local_repo(self) -> None:
        fake_result = TaskRunResult(
            task_id="gh_issue_remote",
            status="failed",
            patch_apply_success=False,
            tests_passed=False,
            iterations=1,
            duration_seconds=0.1,
        )
        with patch(
            "app.cli.fetch_issue",
            return_value={
                "issue_title": "Remote issue",
                "issue_body": "Body",
                "issue_url": "https://github.com/example/project/issues/5",
                "repo_url": "https://github.com/example/project",
            },
        ), patch("app.cli.ExecutionHarness") as harness_cls:
            harness_cls.return_value.run_task.return_value = fake_result
            exit_code = cli.main(
                [
                    "run",
                    "--task-id",
                    "gh_issue_remote",
                    "--issue-url",
                    "https://github.com/example/project/issues/5",
                    "--test-command",
                    "python -m unittest",
                ]
            )

        self.assertEqual(exit_code, 1)
        task = harness_cls.return_value.run_task.call_args.args[0]
        self.assertIsNone(task.repo_path)
        self.assertEqual(task.repo_url, "https://github.com/example/project")

    def test_swebench_import_passes_options_to_importer(self) -> None:
        with patch(
            "app.cli.import_swebench_eval_dataset",
            return_value={
                "output_path": "evals/real_python_tasks.jsonl",
                "imported": 1,
                "instance_ids": ["pytest-dev__pytest-7432"],
            },
        ) as importer:
            exit_code = cli.main(
                [
                    "swebench-import",
                    "--instance-id",
                    "pytest-dev__pytest-7432",
                    "--output",
                    "evals/real_python_tasks.jsonl",
                    "--test-command",
                    "python -m pytest testing/test_skipping.py",
                    "--docker",
                    "--docker-image",
                    "python:3.8-slim",
                    "--install-dependencies",
                    "--allow-network",
                    "--append",
                ]
            )

        self.assertEqual(exit_code, 0)
        args, kwargs = importer.call_args
        self.assertEqual(args[0], ["pytest-dev__pytest-7432"])
        self.assertEqual(args[1], "evals/real_python_tasks.jsonl")
        self.assertEqual(kwargs["test_command"], "python -m pytest testing/test_skipping.py")
        self.assertTrue(kwargs["use_docker"])
        self.assertEqual(kwargs["docker_image"], "python:3.8-slim")
        self.assertTrue(kwargs["install_dependencies"])
        self.assertTrue(kwargs["allow_network"])
        self.assertTrue(kwargs["append"])


if __name__ == "__main__":
    unittest.main()
