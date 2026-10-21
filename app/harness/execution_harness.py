from __future__ import annotations

import re
import shlex
import time
from typing import Any

from app.agents.orchestrator import PatchForgeAgentOrchestrator
from app.agents.orchestrator import summarize_experience
from app.agents.pr_generator import PRGenerator
from app.agents.reviewer import Reviewer
from app.harness.environment_manager import EnvironmentManager
from app.harness.evolution_harness import EvolutionHarness
from app.harness.reporter import ReportGenerator
from app.harness.sandbox_runner import SandboxRunner
from app.harness.tool_runtime import ToolRuntime
from app.harness.trace_recorder import TraceRecorder
from app.harness.workspace_manager import WorkspaceManager
from app.schemas.task import TaskInstance, TaskRunResult
from app.services.config import settings
from app.services.repository_fetcher import RepositoryFetchError
from app.tools.edit_tools import changed_files_from_diff


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


class ExecutionHarness:
    def __init__(
        self,
        workspace_manager: WorkspaceManager | None = None,
        orchestrator: PatchForgeAgentOrchestrator | None = None,
        evolution: EvolutionHarness | None = None,
        environment_manager: EnvironmentManager | None = None,
    ) -> None:
        settings.ensure_dirs()
        self.workspace_manager = workspace_manager or WorkspaceManager(settings.runs_dir)
        self.reporter = ReportGenerator()
        self.evolution = evolution or EvolutionHarness(settings.data_dir)
        self.orchestrator = orchestrator or PatchForgeAgentOrchestrator(evolution=self.evolution)
        self.environment_manager = environment_manager or EnvironmentManager()
        self.reviewer = Reviewer()
        self.pr_generator = PRGenerator()

    def run_task(self, task: TaskInstance) -> TaskRunResult:
        started = time.perf_counter()
        workspace_started = time.perf_counter()
        # 每次任务都先复制或下载独立 workspace，避免 Agent 直接修改用户的原始仓库。
        try:
            paths = self.workspace_manager.prepare(task)
        except (RepositoryFetchError, FileNotFoundError, OSError, ValueError) as exc:
            return self._setup_failed_result(task, started, exc)

        evaluation_warnings = _evaluation_warnings(task)
        task = _task_with_adjusted_eval_test_command(task, evaluation_warnings)
        trace_path = paths["task_dir"] / "trace.jsonl"
        trace = TraceRecorder(trace_path)
        trace.record("task_started", task.model_dump())
        trace.record(
            "harness_phase",
            {
                "phase": "workspace_prepare",
                "duration_ms": _elapsed_ms(workspace_started),
                "status": "succeeded",
            },
        )
        for warning in evaluation_warnings:
            trace.record("evaluation_warning", warning)
        sandbox = SandboxRunner(
            paths["repo_dir"],
            use_docker=task.use_docker,
            allow_network=task.allow_network,
            docker_image=task.docker_image,
        )
        environment_started = time.perf_counter()
        environment_setup = self.environment_manager.setup(task, paths["repo_dir"], sandbox, trace)
        trace.record(
            "harness_phase",
            {
                "phase": "environment_setup",
                "duration_ms": _elapsed_ms(environment_started),
                "status": environment_setup.status,
                "cache_hit": environment_setup.cache_hit,
            },
        )
        if environment_setup.status == "failed":
            return self._environment_failed_result(task, started, paths, trace, environment_setup.model_dump())
        runtime = ToolRuntime(paths["repo_dir"], paths["baseline_dir"], trace, sandbox)
        agent_started = time.perf_counter()
        state = self.orchestrator.run(task, runtime)
        trace.record(
            "harness_phase",
            {
                "phase": "agent_run",
                "duration_ms": _elapsed_ms(agent_started),
                "status": state.final_status,
                "iterations": state.iteration_count,
            },
        )
        latest_test = state.test_results[-1] if state.test_results else {}
        result = TaskRunResult(
            task_id=task.task_id,
            status=state.final_status,
            patch_apply_success=bool(state.patches and state.current_diff),
            tests_passed=latest_test.get("status") == "passed",
            iterations=state.iteration_count,
            duration_seconds=time.perf_counter() - started,
            modified_files=state.modified_files,
            failure_type=None if state.final_status == "succeeded" else state.latest_failure_type or latest_test.get("failure_type"),
            error_message=None if state.final_status == "succeeded" else state.latest_error,
            final_diff=state.current_diff,
            trace_path=str(trace_path),
            issue_url=task.issue_url,
            pr_title=state.pr_title,
            pr_body=state.pr_body,
            approval_requests=state.approval_requests,
            environment_setup=environment_setup.model_dump(),
            experience_summary=summarize_experience(state),
            evaluation_warnings=evaluation_warnings,
        )
        report_started = time.perf_counter()
        report_path = self.reporter.write_task_report(result, paths["reports_dir"])
        trace.record(
            "harness_phase",
            {
                "phase": "report_write",
                "duration_ms": _elapsed_ms(report_started),
                "status": "succeeded",
            },
        )
        result.report_path = str(report_path)
        self.evolution.ingest_result(result)
        trace.record("task_finished", result.model_dump())
        return result


    def resume_after_approval(
        self,
        task: TaskInstance,
        previous_result: TaskRunResult,
        *,
        approved: bool,
        reviewer: str = "human",
        notes: str = "",
    ) -> TaskRunResult:
        started = time.perf_counter()
        task_dir = self.workspace_manager.runs_dir / task.task_id
        repo_dir = task_dir / "repo"
        baseline_dir = task_dir / "baseline"
        reports_dir = task_dir / "reports"
        trace_path = task_dir / "trace.jsonl"
        trace = TraceRecorder(trace_path)
        approval_requests = _updated_approval_requests(previous_result.approval_requests, approved, reviewer, notes)
        trace.record(
            "approval_decision",
            {"task_id": task.task_id, "approved": approved, "reviewer": reviewer, "notes": notes},
        )

        if not approved:
            result = TaskRunResult(
                task_id=task.task_id,
                status="failed",
                patch_apply_success=False,
                tests_passed=False,
                iterations=previous_result.iterations,
                duration_seconds=time.perf_counter() - started,
                modified_files=previous_result.modified_files,
                failure_type="approval_rejected",
                error_message="人工审批拒绝，PatchForge 未应用待审 patch。",
                final_diff=previous_result.final_diff,
                report_path=previous_result.report_path,
                trace_path=str(trace_path),
                issue_url=task.issue_url,
                pr_title=previous_result.pr_title,
                pr_body=previous_result.pr_body,
                approval_requests=approval_requests,
                environment_setup=previous_result.environment_setup,
                experience_summary=previous_result.experience_summary,
            )
            report_path = self.reporter.write_task_report(result, reports_dir)
            result.report_path = str(report_path)
            self.evolution.ingest_result(result)
            trace.record("task_finished", result.model_dump())
            return result

        if not previous_result.approval_requests:
            return self._approval_resume_failed(
                task,
                previous_result,
                approval_requests,
                trace,
                started,
                "approval_request_missing",
                "没有找到可恢复执行的审批请求。",
            )

        diff = str(previous_result.approval_requests[0].get("diff") or "")
        if not diff.strip():
            return self._approval_resume_failed(
                task,
                previous_result,
                approval_requests,
                trace,
                started,
                "approval_diff_missing",
                "审批请求中没有可应用的 diff。",
            )
        if not repo_dir.exists() or not baseline_dir.exists():
            return self._approval_resume_failed(
                task,
                previous_result,
                approval_requests,
                trace,
                started,
                "approval_workspace_missing",
                "找不到审批恢复所需的 workspace。",
            )

        sandbox = SandboxRunner(
            repo_dir,
            use_docker=task.use_docker,
            allow_network=task.allow_network,
            docker_image=task.docker_image or previous_result.environment_setup.get("docker_image"),
        )
        _configure_sandbox_from_environment(sandbox, previous_result.environment_setup)
        runtime = ToolRuntime(repo_dir, baseline_dir, trace, sandbox)
        trace.record("approval_resume_started", {"task_id": task.task_id, "test_command": task.test_command})
        apply_result = runtime.apply_patch(diff)
        if not apply_result.get("success"):
            return self._approval_resume_failed(
                task,
                previous_result,
                approval_requests,
                trace,
                started,
                "patch_apply_failed",
                str(apply_result.get("message") or "Approved patch failed to apply."),
            )

        latest_test = runtime.run_tests(task.test_command, timeout=min(task.timeout_seconds, 300))
        final_diff = runtime.get_diff()
        modified_files = runtime.modified_files()
        review = self.reviewer.review(final_diff, latest_test, task.expected_modified_files)
        pr = self.pr_generator.generate(task.issue_title, final_diff, latest_test, review, issue_url=task.issue_url)
        succeeded = latest_test.get("status") == "passed" and review.get("approved")
        result = TaskRunResult(
            task_id=task.task_id,
            status="succeeded" if succeeded else "failed",
            patch_apply_success=True,
            tests_passed=latest_test.get("status") == "passed",
            iterations=previous_result.iterations,
            duration_seconds=time.perf_counter() - started,
            modified_files=modified_files,
            failure_type=None if succeeded else latest_test.get("failure_type") or "review_failed",
            error_message=None if succeeded else latest_test.get("error_summary") or "审批后执行未通过。",
            final_diff=final_diff,
            trace_path=str(trace_path),
            issue_url=task.issue_url,
            pr_title=pr["title"],
            pr_body=pr["body"],
            approval_requests=approval_requests,
            environment_setup=previous_result.environment_setup,
            experience_summary=previous_result.experience_summary,
        )
        report_path = self.reporter.write_task_report(result, reports_dir)
        result.report_path = str(report_path)
        self.evolution.ingest_result(result)
        trace.record("task_finished", result.model_dump())
        return result

    def _approval_resume_failed(
        self,
        task: TaskInstance,
        previous_result: TaskRunResult,
        approval_requests: list[dict[str, Any]],
        trace: TraceRecorder,
        started: float,
        failure_type: str,
        error_message: str,
    ) -> TaskRunResult:
        result = TaskRunResult(
            task_id=task.task_id,
            status="failed",
            patch_apply_success=False,
            tests_passed=False,
            iterations=previous_result.iterations,
            duration_seconds=time.perf_counter() - started,
            modified_files=previous_result.modified_files,
            failure_type=failure_type,
            error_message=error_message,
            final_diff=previous_result.final_diff,
            trace_path=str(self.workspace_manager.runs_dir / task.task_id / "trace.jsonl"),
            issue_url=task.issue_url,
            pr_title=previous_result.pr_title,
            pr_body=previous_result.pr_body,
            approval_requests=approval_requests,
            environment_setup=previous_result.environment_setup,
            experience_summary=previous_result.experience_summary,
        )
        report_path = self.reporter.write_task_report(result, self.workspace_manager.runs_dir / task.task_id / "reports")
        result.report_path = str(report_path)
        self.evolution.ingest_result(result)
        trace.record("task_finished", result.model_dump())
        return result

    def _setup_failed_result(self, task: TaskInstance, started: float, exc: Exception) -> TaskRunResult:
        task_dir = self.workspace_manager.runs_dir / task.task_id
        reports_dir = task_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        trace_path = task_dir / "trace.jsonl"
        trace = TraceRecorder(trace_path)
        trace.record(
            "task_setup_failed",
            {"task": task.model_dump(), "error": str(exc), "error_type": exc.__class__.__name__},
        )
        result = TaskRunResult(
            task_id=task.task_id,
            status="failed",
            patch_apply_success=False,
            tests_passed=False,
            iterations=0,
            duration_seconds=time.perf_counter() - started,
            modified_files=[],
            failure_type=_setup_failure_type(exc),
            error_message=str(exc),
            trace_path=str(trace_path),
            issue_url=task.issue_url,
        )
        report_path = self.reporter.write_task_report(result, reports_dir)
        result.report_path = str(report_path)
        self.evolution.ingest_result(result)
        trace.record("task_finished", result.model_dump())
        return result

    def _environment_failed_result(
        self,
        task: TaskInstance,
        started: float,
        paths: dict[str, Any],
        trace: TraceRecorder,
        environment_setup: dict[str, Any],
    ) -> TaskRunResult:
        result = TaskRunResult(
            task_id=task.task_id,
            status="failed",
            patch_apply_success=False,
            tests_passed=False,
            iterations=0,
            duration_seconds=time.perf_counter() - started,
            modified_files=[],
            failure_type=environment_setup.get("failure_type") or "environment_setup_failed",
            error_message=environment_setup.get("message") or "依赖准备失败。",
            trace_path=str(paths["task_dir"] / "trace.jsonl"),
            issue_url=task.issue_url,
            environment_setup=environment_setup,
        )
        report_path = self.reporter.write_task_report(result, paths["reports_dir"])
        result.report_path = str(report_path)
        self.evolution.ingest_result(result)
        trace.record("task_finished", result.model_dump())
        return result


def _setup_failure_type(exc: Exception) -> str:
    if isinstance(exc, RepositoryFetchError):
        return "repository_fetch_failed"
    if isinstance(exc, FileNotFoundError):
        return "repo_not_found"
    if isinstance(exc, ValueError):
        return "workspace_config_error"
    return "workspace_setup_failed"


def _updated_approval_requests(
    requests: list[dict[str, Any]],
    approved: bool,
    reviewer: str,
    notes: str,
) -> list[dict[str, Any]]:
    updated = [dict(item) for item in requests]
    if updated:
        updated[0]["approved"] = approved
        updated[0]["reviewer"] = reviewer
        updated[0]["notes"] = notes
        updated[0]["decision"] = "approved" if approved else "rejected"
    return updated


def _configure_sandbox_from_environment(sandbox: SandboxRunner, environment_setup: dict[str, Any]) -> None:
    python_executable = environment_setup.get("python_executable")
    docker_python_executable = environment_setup.get("docker_python_executable")
    if python_executable:
        sandbox.configure_python_env(
            python_executable,
            docker_python_executable=docker_python_executable,
        )


def _evaluation_warnings(task: TaskInstance) -> list[dict[str, Any]]:
    deleted_tests = _deleted_tests_by_file(task.validation_patch or "")
    if not deleted_tests:
        return []
    whole_file_tests = _whole_file_test_command_files(task.test_command)
    conflicted_files = sorted(set(deleted_tests) & whole_file_tests)
    if not conflicted_files:
        return []
    return [
        {
            "type": "validation_patch_deletes_tests_in_primary_command",
            "severity": "warning",
            "message": (
                "validation_patch deletes tests from files that test_command runs as whole files; "
                "the primary command may include stale upstream tests and can make the eval stricter than the target patch."
            ),
            "files": conflicted_files,
            "deleted_tests": {file: deleted_tests[file] for file in conflicted_files},
            "test_command": task.test_command,
            "validation_patch_files": changed_files_from_diff(task.validation_patch or ""),
        }
    ]


def _task_with_adjusted_eval_test_command(
    task: TaskInstance,
    evaluation_warnings: list[dict[str, Any]],
) -> TaskInstance:
    adjustment = _adjusted_pytest_command_for_deleted_tests(task.test_command, evaluation_warnings)
    if not adjustment:
        return task
    for warning in evaluation_warnings:
        if warning.get("type") == "validation_patch_deletes_tests_in_primary_command":
            warning["adjusted_test_command"] = adjustment
            warning["adjustment_reason"] = "Excluded tests deleted by validation_patch from the primary pytest command."
    data = task.model_dump()
    data["test_command"] = adjustment
    return TaskInstance.model_validate(data)


def _adjusted_pytest_command_for_deleted_tests(
    command: str,
    evaluation_warnings: list[dict[str, Any]],
) -> str | None:
    warning = next(
        (
            item
            for item in evaluation_warnings
            if item.get("type") == "validation_patch_deletes_tests_in_primary_command"
        ),
        None,
    )
    if not warning:
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not _is_pytest_command(tokens):
        return None
    deselects = _pytest_deselect_args(warning.get("deleted_tests") or {})
    if not deselects:
        return None
    existing = {token for token in tokens if token.startswith("--deselect=")}
    additions = [arg for arg in deselects if arg not in existing]
    if not additions:
        return None
    return shlex.join([*tokens, *additions])


def _pytest_deselect_args(deleted_tests: dict[str, Any]) -> list[str]:
    args: list[str] = []
    for file_path, names in sorted(deleted_tests.items()):
        if not isinstance(names, list):
            continue
        for name in names:
            if isinstance(name, str) and name:
                args.append(f"--deselect={file_path}::{name}")
    return args


def _is_pytest_command(tokens: list[str]) -> bool:
    if not tokens:
        return False
    executable = tokens[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    if executable.endswith(".exe"):
        executable = executable[:-4]
    return executable == "pytest" or (
        len(tokens) >= 3 and executable == "python" and tokens[1] == "-m" and tokens[2] == "pytest"
    )


def _deleted_tests_by_file(diff: str) -> dict[str, list[str]]:
    deleted: dict[str, list[str]] = {}
    current_file = ""
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            continue
        if not current_file or not line.startswith("-") or line.startswith("---"):
            continue
        match = re.match(r"^-[ \t]{0,4}(?:async\s+)?def\s+(test_[A-Za-z0-9_]+)\b", line)
        if not match:
            match = re.match(r"^-[ \t]{0,4}class\s+(Test[A-Za-z0-9_]+)\b", line)
        if match:
            deleted.setdefault(current_file, []).append(match.group(1))
    return deleted


def _whole_file_test_command_files(command: str) -> set[str]:
    files: set[str] = set()
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    for token in tokens:
        cleaned = token.strip("'\"")
        if "::" in cleaned or not cleaned.endswith(".py"):
            continue
        files.add(cleaned.replace("\\", "/"))
    return files
