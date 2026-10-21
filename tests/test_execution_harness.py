from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents.orchestrator import PatchForgeAgentOrchestrator
from app.harness.evolution_harness import EvolutionHarness
from app.harness.execution_harness import ExecutionHarness
from app.harness.workspace_manager import WorkspaceManager
from app.schemas.task import TaskInstance
from app.schemas.task import TaskRunResult
from app.schemas.test import EnvironmentSetupResult
from app.tools.edit_tools import build_unified_diff


class FailingEnvironmentManager:
    def setup(self, task, workspace, sandbox, trace=None) -> EnvironmentSetupResult:  # noqa: ANN001
        return EnvironmentSetupResult(
            status="failed",
            enabled=True,
            failure_type="dependency_install_failed",
            message="依赖准备命令失败：python -m pip install -r requirements.txt",
        )


class ObservingEnvironmentManager:
    def __init__(self) -> None:
        self.docker_image: str | None = None

    def setup(self, task, workspace, sandbox, trace=None) -> EnvironmentSetupResult:  # noqa: ANN001
        self.docker_image = getattr(sandbox, "docker_image", None)
        return EnvironmentSetupResult(
            status="skipped",
            enabled=False,
            docker_image=self.docker_image if task.use_docker else None,
        )


class ExecutionHarnessTests(unittest.TestCase):
    def test_repairs_demo_discount_bug(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = TaskInstance(
                task_id="unit_discount",
                repo_path="examples/demo_python_repo",
                issue_title="Fix zero price discount bug",
                issue_body="calculate_discount returns None for price 0.",
                test_command="python -m unittest tests.test_pricing",
                expected_modified_files=["src/patchforge_demo/calculator.py"],
            )
            with patch("app.agents.code_editor.settings.llm_provider", "offline"):
                harness = ExecutionHarness(workspace_manager=WorkspaceManager(Path(tmp) / "runs"))
                result = harness.run_task(task)
            self.assertEqual(result.status, "succeeded", result.final_diff)
            self.assertTrue(result.tests_passed)
            self.assertIn("return 0.0", result.final_diff or "")
            self.assertIn("src/patchforge_demo/calculator.py", result.modified_files)

    def test_includes_retrieved_experience_in_result_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evolution = EvolutionHarness(root / "data")
            evolution.ingest_result(
                TaskRunResult(
                    task_id="prior_discount_fix",
                    status="succeeded",
                    patch_apply_success=True,
                    tests_passed=True,
                    iterations=1,
                    duration_seconds=0.1,
                    modified_files=["src/patchforge_demo/calculator.py"],
                )
            )
            task = TaskInstance(
                task_id="unit_discount_with_memory",
                repo_path="examples/demo_python_repo",
                issue_title="Fix zero price discount bug",
                issue_body="calculate_discount returns None for price 0.",
                test_command="python -m unittest tests.test_pricing",
                expected_modified_files=["src/patchforge_demo/calculator.py"],
            )

            with patch("app.agents.code_editor.settings.llm_provider", "offline"):
                harness = ExecutionHarness(workspace_manager=WorkspaceManager(root / "runs"), evolution=evolution)
                result = harness.run_task(task)

            self.assertEqual(result.status, "succeeded", result.final_diff)
            self.assertGreaterEqual(result.experience_summary["skill_count"], 1)
            self.assertIn("skill_prior_discount_fix", result.experience_summary["skills"])
            report = Path(result.report_path or "").read_text(encoding="utf-8")
            self.assertIn("## 经验复用", report)
            self.assertIn("skill_prior_discount_fix", report)

    def test_workspace_setup_failure_returns_structured_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = TaskInstance(
                task_id="missing_repo",
                repo_path=str(root / "does_not_exist"),
                issue_title="Missing repo should not crash",
            )
            harness = ExecutionHarness(workspace_manager=WorkspaceManager(root / "runs"))
            result = harness.run_task(task)

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.failure_type, "repo_not_found")
            self.assertIn("does_not_exist", result.error_message or "")
            self.assertEqual(result.iterations, 0)
            self.assertFalse(result.tests_passed)
            self.assertTrue(Path(result.trace_path or "").exists())
            report = Path(result.report_path or "").read_text(encoding="utf-8")
            self.assertIn("失败类型：`repo_not_found`", report)
            self.assertIn("错误信息：", report)

    def test_environment_setup_failure_returns_structured_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = TaskInstance(
                task_id="env_setup_failed",
                repo_path="examples/demo_python_repo",
                issue_title="Environment setup should fail cleanly",
                install_dependencies=True,
            )
            harness = ExecutionHarness(
                workspace_manager=WorkspaceManager(root / "runs"),
                environment_manager=FailingEnvironmentManager(),  # type: ignore[arg-type]
            )
            result = harness.run_task(task)

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.failure_type, "dependency_install_failed")
            self.assertEqual(result.iterations, 0)
            self.assertFalse(result.tests_passed)
            self.assertEqual(result.environment_setup["status"], "failed")
            report = Path(result.report_path or "").read_text(encoding="utf-8")
            self.assertIn("## 环境准备", report)
            self.assertIn("dependency_install_failed", result.model_dump_json())

    def test_task_docker_image_is_passed_to_sandbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            observer = ObservingEnvironmentManager()
            task = TaskInstance(
                task_id="custom_docker_image",
                repo_path="examples/demo_python_repo",
                issue_title="Image wiring only",
                issue_body="Do not run Docker in this unit test.",
                test_command="python -m unittest tests.test_pricing",
                use_docker=True,
                docker_image="python:3.8-slim",
            )
            orchestrator = PatchForgeAgentOrchestrator()
            orchestrator.code_editor.generate_patch = lambda *_, **__: ""  # type: ignore[method-assign]

            with patch("app.agents.code_editor.settings.llm_provider", "offline"):
                harness = ExecutionHarness(
                    workspace_manager=WorkspaceManager(root / "runs"),
                    orchestrator=orchestrator,
                    environment_manager=observer,  # type: ignore[arg-type]
                )
                result = harness.run_task(task)

            self.assertEqual(observer.docker_image, "python:3.8-slim")
            self.assertEqual(result.environment_setup["docker_image"], "python:3.8-slim")

    def test_patch_generation_failure_uses_recorded_llm_failure_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            orchestrator = PatchForgeAgentOrchestrator()

            def fail_generation(runtime, *_, **__):  # noqa: ANN001
                runtime.trace.record(
                    "patch_generation_failed",
                    {
                        "failure_type": "llm_empty_response",
                        "error": "DeepSeek API returned empty content",
                    },
                )
                return ""

            orchestrator.code_editor.generate_patch = fail_generation  # type: ignore[method-assign]
            task = TaskInstance(
                task_id="llm_empty_response",
                repo_path="examples/demo_python_repo",
                issue_title="LLM should fail cleanly",
                test_command="python -m unittest tests.test_pricing",
            )

            with patch("app.agents.orchestrator.settings.llm_provider", "deepseek"):
                harness = ExecutionHarness(workspace_manager=WorkspaceManager(root / "runs"), orchestrator=orchestrator)
                result = harness.run_task(task)

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.failure_type, "llm_empty_response")
            self.assertIn("DeepSeek API returned empty content", result.error_message or "")

    def test_no_effect_patch_is_retried_and_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            no_effect_diff = (
                "--- a/src/patchforge_demo/calculator.py\n"
                "+++ b/src/patchforge_demo/calculator.py\n"
                "@@ -7,3 +7,3 @@\n"
                "     if price == 0:\n"
                "-        return None\n"
                "+        return None\n"
                "     return price * rate\n"
            )
            orchestrator = PatchForgeAgentOrchestrator()
            orchestrator.code_editor.generate_patch = lambda *_, **__: no_effect_diff  # type: ignore[method-assign]
            task = TaskInstance(
                task_id="patch_no_effect",
                repo_path="examples/demo_python_repo",
                issue_title="No-op patch should not pass",
                test_command="python -m unittest tests.test_pricing",
                max_iterations=1,
            )

            harness = ExecutionHarness(workspace_manager=WorkspaceManager(root / "runs"), orchestrator=orchestrator)
            result = harness.run_task(task)

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.failure_type, "patch_no_effect")
            self.assertFalse(result.final_diff)

    def test_stops_repeated_failed_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "target_repo"
            (repo / "tests").mkdir(parents=True)
            old_code = "def value():\n    return 0\n"
            bad_code = "def value():\n    return 2\n"
            (repo / "app.py").write_text(old_code, encoding="utf-8")
            (repo / "tests" / "test_app.py").write_text(
                "import unittest\n"
                "import app\n\n"
                "class AppTests(unittest.TestCase):\n"
                "    def test_value(self):\n"
                "        self.assertEqual(app.value(), 1)\n",
                encoding="utf-8",
            )
            bad_patch = build_unified_diff("app.py", old_code, bad_code)
            orchestrator = PatchForgeAgentOrchestrator()
            orchestrator.code_editor.generate_patch = lambda *_, **__: bad_patch  # type: ignore[method-assign]
            task = TaskInstance(
                task_id="repeated_failed_patch",
                repo_path=str(repo),
                issue_title="Fix value",
                test_command="python -m unittest tests.test_app",
                expected_modified_files=["app.py"],
                max_iterations=3,
            )

            result = ExecutionHarness(
                workspace_manager=WorkspaceManager(root / "runs"),
                orchestrator=orchestrator,
            ).run_task(task)

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.failure_type, "repeated_patch_failure")
            self.assertEqual(result.iterations, 2)
            trace_text = Path(result.trace_path or "").read_text(encoding="utf-8")
            self.assertIn("stop_retry", trace_text)

    def test_unexpected_modified_file_is_rejected_before_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "target_repo"
            repo.mkdir()
            app_old = "def value():\n    return 0\n"
            other_old = "def helper():\n    return 0\n"
            other_new = "def helper():\n    return 1\n"
            (repo / "app.py").write_text(app_old, encoding="utf-8")
            (repo / "other.py").write_text(other_old, encoding="utf-8")
            patch_diff = build_unified_diff("other.py", other_old, other_new)
            orchestrator = PatchForgeAgentOrchestrator()
            orchestrator.code_editor.generate_patch = lambda *_, **__: patch_diff  # type: ignore[method-assign]
            task = TaskInstance(
                task_id="unexpected_file",
                repo_path=str(repo),
                issue_title="Fix value",
                test_command="python -m unittest discover",
                expected_modified_files=["app.py"],
                max_iterations=1,
            )

            result = ExecutionHarness(
                workspace_manager=WorkspaceManager(root / "runs"),
                orchestrator=orchestrator,
            ).run_task(task)

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.failure_type, "unexpected_modified_files")
            self.assertFalse(result.final_diff)
            workspace_other = root / "runs" / "unexpected_file" / "repo" / "other.py"
            self.assertEqual(workspace_other.read_text(encoding="utf-8"), other_old)

    def test_failed_patch_is_reverted_before_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "target_repo"
            (repo / "tests").mkdir(parents=True)
            old_code = "def value():\n    return 0\n"
            bad_code = "def value():\n    return 2\n"
            good_code = "def value():\n    return 1\n"
            test_code = (
                "import unittest\n"
                "import app\n\n"
                "class AppTests(unittest.TestCase):\n"
                "    def test_value(self):\n"
                "        self.assertEqual(app.value(), 1)\n"
            )
            (repo / "app.py").write_text(old_code, encoding="utf-8")
            (repo / "tests" / "test_app.py").write_text(test_code, encoding="utf-8")
            bad_patch = build_unified_diff("app.py", old_code, bad_code)
            good_patch = build_unified_diff("app.py", old_code, good_code)
            patches = [bad_patch, good_patch]

            orchestrator = PatchForgeAgentOrchestrator()

            def generate_patch(*_, **__):  # noqa: ANN002, ANN003
                return patches.pop(0)

            orchestrator.code_editor.generate_patch = generate_patch  # type: ignore[method-assign]
            task = TaskInstance(
                task_id="revert_before_retry",
                repo_path=str(repo),
                issue_title="Fix value",
                test_command="python -m unittest tests.test_app",
                expected_modified_files=["app.py"],
                max_iterations=2,
            )

            result = ExecutionHarness(
                workspace_manager=WorkspaceManager(root / "runs"),
                orchestrator=orchestrator,
            ).run_task(task)

            self.assertEqual(result.status, "succeeded", result.error_message)
            self.assertTrue(result.tests_passed)
            self.assertIn("return 1", result.final_diff or "")
            self.assertNotIn("return 2", result.final_diff or "")
            trace_text = Path(result.trace_path or "").read_text(encoding="utf-8")
            self.assertIn("revert_files", trace_text)
            self.assertIn("revert_patch", trace_text)

    def test_high_risk_patch_requires_approval_before_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "target_repo"
            repo.mkdir()
            old_requirements = "requests==1.0.0\n"
            new_requirements = "requests==2.0.0\n"
            (repo / "requirements.txt").write_text(old_requirements, encoding="utf-8")
            (repo / "tests").mkdir()
            (repo / "tests" / "test_placeholder.py").write_text(
                "import unittest\n\n"
                "class PlaceholderTests(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            patch_diff = build_unified_diff("requirements.txt", old_requirements, new_requirements)
            orchestrator = PatchForgeAgentOrchestrator()
            orchestrator.code_editor.generate_patch = lambda *_, **__: patch_diff  # type: ignore[method-assign]
            task = TaskInstance(
                task_id="approval_required",
                repo_path=str(repo),
                issue_title="Update dependency",
                issue_body="Change dependency version.",
                test_command="python -m unittest discover -s tests",
            )

            harness = ExecutionHarness(
                workspace_manager=WorkspaceManager(root / "runs"),
                orchestrator=orchestrator,
            )
            result = harness.run_task(task)

            self.assertEqual(result.status, "needs_approval")
            self.assertEqual(result.failure_type, "human_approval_required")
            self.assertFalse(result.patch_apply_success)
            self.assertFalse(result.tests_passed)
            self.assertEqual(result.approval_requests[0]["changed_files"], ["requirements.txt"])
            self.assertIn("high-risk file modified", result.approval_requests[0]["risk_reasons"][0])
            workspace_requirements = root / "runs" / "approval_required" / "repo" / "requirements.txt"
            self.assertEqual(workspace_requirements.read_text(encoding="utf-8"), old_requirements)
            report = Path(result.report_path or "").read_text(encoding="utf-8")
            self.assertIn("## 审批请求", report)
            self.assertIn("requirements.txt", report)

    def test_approved_high_risk_patch_resumes_and_runs_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "target_repo"
            repo.mkdir()
            old_requirements = "requests==1.0.0\n"
            new_requirements = "requests==2.0.0\n"
            (repo / "requirements.txt").write_text(old_requirements, encoding="utf-8")
            (repo / "tests").mkdir()
            (repo / "tests" / "test_placeholder.py").write_text(
                "import unittest\n\n"
                "class PlaceholderTests(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            patch_diff = build_unified_diff("requirements.txt", old_requirements, new_requirements)
            orchestrator = PatchForgeAgentOrchestrator()
            orchestrator.code_editor.generate_patch = lambda *_, **__: patch_diff  # type: ignore[method-assign]
            task = TaskInstance(
                task_id="approval_resume",
                repo_path=str(repo),
                issue_title="Update dependency",
                issue_body="Change dependency version.",
                test_command="python -m unittest discover -s tests",
            )
            harness = ExecutionHarness(
                workspace_manager=WorkspaceManager(root / "runs"),
                orchestrator=orchestrator,
            )
            blocked = harness.run_task(task)

            resumed = harness.resume_after_approval(
                task,
                blocked,
                approved=True,
                reviewer="unit-test",
                notes="Looks intentional.",
            )

            self.assertEqual(resumed.status, "succeeded", resumed.error_message)
            self.assertTrue(resumed.patch_apply_success)
            self.assertTrue(resumed.tests_passed)
            self.assertTrue(resumed.approval_requests[0]["approved"])
            workspace_requirements = root / "runs" / "approval_resume" / "repo" / "requirements.txt"
            self.assertEqual(workspace_requirements.read_text(encoding="utf-8"), new_requirements)
            trace_text = Path(resumed.trace_path or "").read_text(encoding="utf-8")
            self.assertIn("approval_decision", trace_text)
            self.assertIn("approval_resume_started", trace_text)

    def test_validation_patch_runs_temporarily_without_polluting_final_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "target_repo"
            (repo / "src").mkdir(parents=True)
            (repo / "tests").mkdir()
            old_code = "def value():\n    return 0\n"
            new_code = "def value():\n    return 1\n"
            old_test = (
                "import unittest\n"
                "from src.app import value\n\n"
                "class AppTests(unittest.TestCase):\n"
                "    def test_smoke(self):\n"
                "        self.assertIn(value(), [0, 1])\n"
            )
            new_test = old_test + (
                "\n"
                "class RegressionTests(unittest.TestCase):\n"
                "    def test_regression(self):\n"
                "        self.assertEqual(value(), 1)\n"
            )
            (repo / "src" / "app.py").write_text(old_code, encoding="utf-8")
            (repo / "tests" / "test_app.py").write_text(old_test, encoding="utf-8")
            patch_diff = build_unified_diff("src/app.py", old_code, new_code)
            validation_patch = build_unified_diff("tests/test_app.py", old_test, new_test)
            orchestrator = PatchForgeAgentOrchestrator()
            orchestrator.code_editor.generate_patch = lambda *_, **__: patch_diff  # type: ignore[method-assign]
            task = TaskInstance(
                task_id="validation_patch",
                repo_path=str(repo),
                issue_title="Fix value",
                test_command="python -m unittest tests.test_app",
                validation_patch=validation_patch,
                validation_test_command="python -m unittest tests.test_app.RegressionTests.test_regression",
                expected_modified_files=["src/app.py"],
            )

            result = ExecutionHarness(
                workspace_manager=WorkspaceManager(root / "runs"),
                orchestrator=orchestrator,
            ).run_task(task)

            self.assertEqual(result.status, "succeeded", result.error_message)
            self.assertTrue(result.tests_passed)
            self.assertIn("src/app.py", result.final_diff or "")
            self.assertNotIn("RegressionTests", result.final_diff or "")
            workspace_test = root / "runs" / "validation_patch" / "repo" / "tests" / "test_app.py"
            self.assertNotIn("RegressionTests", workspace_test.read_text(encoding="utf-8"))
            trace_text = Path(result.trace_path or "").read_text(encoding="utf-8")
            self.assertIn("run_validation_tests", trace_text)

    def test_warns_when_validation_patch_deletes_tests_from_primary_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "target_repo"
            (repo / "tests").mkdir(parents=True)
            old_test = (
                "def test_old_behavior():\n"
                "    assert True\n\n"
                "def helper():\n"
                "    return '''\n"
                "        def test_inside_string():\n"
                "            pass\n"
                "    '''\n\n"
                "def test_remaining():\n"
                "    assert True\n"
            )
            new_test = "def test_remaining():\n    assert True\n"
            (repo / "tests" / "test_app.py").write_text(old_test, encoding="utf-8")
            validation_patch = build_unified_diff("tests/test_app.py", old_test, new_test)
            orchestrator = PatchForgeAgentOrchestrator()
            orchestrator.code_editor.generate_patch = lambda *_, **__: ""  # type: ignore[method-assign]
            task = TaskInstance(
                task_id="validation_warning",
                repo_path=str(repo),
                issue_title="Expose stale primary test command",
                test_command="python -m pytest tests/test_app.py",
                validation_patch=validation_patch,
            )

            result = ExecutionHarness(
                workspace_manager=WorkspaceManager(root / "runs"),
                orchestrator=orchestrator,
            ).run_task(task)

            self.assertEqual(result.evaluation_warnings[0]["type"], "validation_patch_deletes_tests_in_primary_command")
            self.assertEqual(result.evaluation_warnings[0]["files"], ["tests/test_app.py"])
            self.assertIn("adjusted_test_command", result.evaluation_warnings[0])
            self.assertIn("--deselect=tests/test_app.py::test_old_behavior", result.evaluation_warnings[0]["adjusted_test_command"])
            self.assertNotIn("test_inside_string", result.evaluation_warnings[0]["adjusted_test_command"])
            trace_text = Path(result.trace_path or "").read_text(encoding="utf-8")
            self.assertIn("evaluation_warning", trace_text)
            self.assertIn("--deselect=tests/test_app.py::test_old_behavior", trace_text)
            report = Path(result.report_path or "").read_text(encoding="utf-8")
            self.assertIn("validation_patch_deletes_tests_in_primary_command", report)

    def test_rejected_high_risk_patch_does_not_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "target_repo"
            repo.mkdir()
            old_requirements = "requests==1.0.0\n"
            new_requirements = "requests==2.0.0\n"
            (repo / "requirements.txt").write_text(old_requirements, encoding="utf-8")
            patch_diff = build_unified_diff("requirements.txt", old_requirements, new_requirements)
            orchestrator = PatchForgeAgentOrchestrator()
            orchestrator.code_editor.generate_patch = lambda *_, **__: patch_diff  # type: ignore[method-assign]
            task = TaskInstance(
                task_id="approval_reject",
                repo_path=str(repo),
                issue_title="Update dependency",
                issue_body="Change dependency version.",
            )
            harness = ExecutionHarness(
                workspace_manager=WorkspaceManager(root / "runs"),
                orchestrator=orchestrator,
            )
            blocked = harness.run_task(task)

            rejected = harness.resume_after_approval(task, blocked, approved=False, notes="Too risky.")

            self.assertEqual(rejected.status, "failed")
            self.assertEqual(rejected.failure_type, "approval_rejected")
            workspace_requirements = root / "runs" / "approval_reject" / "repo" / "requirements.txt"
            self.assertEqual(workspace_requirements.read_text(encoding="utf-8"), old_requirements)


if __name__ == "__main__":
    unittest.main()
