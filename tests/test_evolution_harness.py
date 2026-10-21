from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.harness.evolution_harness import EvolutionHarness
from app.schemas.task import TaskRunResult


class EvolutionHarnessTests(unittest.TestCase):
    def test_retrieves_relevant_skill_and_reflection_by_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = EvolutionHarness(Path(tmp))
            harness.ingest_result(
                TaskRunResult(
                    task_id="taskmgr_priority_uppercase",
                    status="succeeded",
                    patch_apply_success=True,
                    tests_passed=True,
                    iterations=1,
                    duration_seconds=0.1,
                    modified_files=["src/task_manager/models.py"],
                )
            )
            harness.ingest_result(
                TaskRunResult(
                    task_id="taskmgr_config_invalid",
                    status="succeeded",
                    patch_apply_success=True,
                    tests_passed=True,
                    iterations=1,
                    duration_seconds=0.1,
                    modified_files=["src/task_manager/config.py"],
                )
            )

            result = harness.retrieve("priority uppercase src/task_manager/models.py", limit=2)

            self.assertEqual(result["skills"][0]["skill_id"], "skill_taskmgr_priority_uppercase")
            self.assertEqual(result["reflections"][0]["task_id"], "taskmgr_priority_uppercase")

    def test_retrieves_failure_patterns_for_similar_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = EvolutionHarness(Path(tmp))
            harness.ingest_result(
                TaskRunResult(
                    task_id="bad_patch_context",
                    status="failed",
                    patch_apply_success=False,
                    tests_passed=False,
                    iterations=1,
                    duration_seconds=0.1,
                    modified_files=[],
                    failure_type="patch_apply_failed",
                )
            )

            result = harness.retrieve("patch context failed", limit=1)

            self.assertEqual(result["failure_patterns"][0]["failure_type"], "patch_apply_failed")

    def test_semantic_retrieval_matches_synonym_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = EvolutionHarness(Path(tmp))
            harness.ingest_result(
                TaskRunResult(
                    task_id="taskmgr_config_malformed_line",
                    status="succeeded",
                    patch_apply_success=True,
                    tests_passed=True,
                    iterations=1,
                    duration_seconds=0.1,
                    modified_files=["src/task_manager/config.py"],
                )
            )
            harness.ingest_result(
                TaskRunResult(
                    task_id="taskmgr_priority_uppercase",
                    status="succeeded",
                    patch_apply_success=True,
                    tests_passed=True,
                    iterations=1,
                    duration_seconds=0.1,
                    modified_files=["src/task_manager/models.py"],
                )
            )

            result = harness.retrieve("settings parser skips bad rows", limit=1)

            self.assertEqual(result["skills"][0]["skill_id"], "skill_taskmgr_config_malformed_line")
            self.assertEqual(result["reflections"][0]["task_id"], "taskmgr_config_malformed_line")

    def test_semantic_retrieval_deduplicates_repeated_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = EvolutionHarness(Path(tmp))
            result = TaskRunResult(
                task_id="taskmgr_config_malformed_line",
                status="succeeded",
                patch_apply_success=True,
                tests_passed=True,
                iterations=1,
                duration_seconds=0.1,
                modified_files=["src/task_manager/config.py"],
            )
            harness.ingest_result(result)
            harness.ingest_result(result)

            retrieved = harness.retrieve("settings parser skips bad rows", limit=3)

            self.assertEqual(
                [item["skill_id"] for item in retrieved["skills"]],
                ["skill_taskmgr_config_malformed_line"],
            )


if __name__ == "__main__":
    unittest.main()
