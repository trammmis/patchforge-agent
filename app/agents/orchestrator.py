from __future__ import annotations

import hashlib
from pathlib import Path
import re
import time

from app.agents.code_editor import CodeEditor
from app.agents.error_analyzer import ErrorAnalyzer
from app.agents.issue_parser import IssueParser
from app.agents.planner import Planner
from app.agents.pr_generator import PRGenerator
from app.agents.repo_explorer import RepoExplorer
from app.agents.reviewer import Reviewer
from app.harness.evolution_harness import EvolutionHarness
from app.harness.tool_runtime import ToolRuntime
from app.schemas.task import AgentState, TaskInstance
from app.services.config import settings
from app.tools.edit_tools import changed_files_from_diff
from app.tools.path_guard import safe_join


class PatchForgeAgentOrchestrator:
    def __init__(self, evolution: EvolutionHarness | None = None) -> None:
        self.issue_parser = IssueParser()
        self.repo_explorer = RepoExplorer()
        self.planner = Planner()
        self.code_editor = CodeEditor()
        self.error_analyzer = ErrorAnalyzer()
        self.reviewer = Reviewer()
        self.pr_generator = PRGenerator()
        self.evolution = evolution or EvolutionHarness()

    def run(self, task: TaskInstance, runtime: ToolRuntime) -> AgentState:
        raw_issue = f"{task.issue_title}\n{task.issue_body}".strip()
        state = AgentState(
            task_id=task.task_id,
            repo_url=task.repo_url,
            workspace_path=str(runtime.workspace),
            raw_issue=raw_issue,
            max_iterations=task.max_iterations,
            test_command=task.test_command,
            current_phase="parse_issue",
        )
        phase_started = time.perf_counter()
        parsed = self.issue_parser.parse(task.issue_title, task.issue_body)
        state.parsed_issue = parsed.model_dump()
        _record_phase(runtime, "parse_issue", phase_started, parsed_issue=state.parsed_issue)

        state.current_phase = "inspect_repo"
        phase_started = time.perf_counter()
        exploration = self.repo_explorer.explore(runtime, parsed)
        state.file_tree = exploration["file_tree"]
        state.candidate_files = _prioritize_candidate_files(
            exploration["candidate_files"],
            task.expected_modified_files,
            task.test_command,
        )
        _record_phase(runtime, "inspect_repo", phase_started, candidate_files=state.candidate_files)

        state.current_phase = "retrieve_experience"
        phase_started = time.perf_counter()
        experience = self.evolution.retrieve(
            _experience_query(raw_issue, state.candidate_files, task.test_command),
            limit=3,
        )
        state.retrieved_reflections = experience["reflections"]
        state.retrieved_skills = experience["skills"]
        state.retrieved_failure_patterns = experience["failure_patterns"]
        _record_phase(
            runtime,
            "retrieve_experience",
            phase_started,
            experience_summary=summarize_experience(state),
        )

        state.current_phase = "create_plan"
        phase_started = time.perf_counter()
        state.plan = self.planner.plan(
            parsed,
            state.candidate_files,
            task.test_command,
            retrieved_reflections=state.retrieved_reflections,
            retrieved_skills=state.retrieved_skills,
            retrieved_failure_patterns=state.retrieved_failure_patterns,
        )
        if task.expected_modified_files:
            state.plan = [
                f"Constrain patch scope to expected modified files: {', '.join(task.expected_modified_files)}",
                *state.plan,
            ]
        _record_phase(runtime, "create_plan", phase_started, plan=state.plan)

        patch_apply_success = False
        latest_test: dict | None = None
        for iteration in range(1, task.max_iterations + 1):
            state.iteration_count = iteration
            state.current_phase = "generate_patch"
            phase_started = time.perf_counter()
            patch = self.code_editor.generate_patch(
                runtime,
                raw_issue,
                candidate_files=state.candidate_files,
                plan=state.plan,
                test_results=state.test_results,
                patch_failures=state.patch_failures,
                expected_modified_files=task.expected_modified_files,
            )
            _record_phase(
                runtime,
                "generate_patch",
                phase_started,
                iteration=iteration,
                success=bool(patch),
            )
            if not patch:
                generation_failure = runtime.trace.latest_payload("patch_generation_failed") or {}
                state.latest_error = generation_failure.get("error") or "No patch generated for this issue"
                state.latest_failure_type = generation_failure.get("failure_type") or (
                    "llm_generation_failed" if settings.llm_provider == "deepseek" else "context_retrieval_failed"
                )
                runtime.trace.record(
                    "agent_phase",
                    {
                        "phase": "generate_patch",
                        "error": state.latest_error,
                        "failure_type": state.latest_failure_type,
                    },
                )
                break
            state.patches.append(patch)

            state.current_phase = "apply_patch"
            check_result = runtime.apply_patch(patch, check=True)
            if not check_result["success"]:
                state.latest_error = check_result["message"]
                state.latest_failure_type = "patch_apply_failed"
                failure = {
                    "iteration": iteration,
                    "failure_type": "patch_apply_failed",
                    "message": check_result["message"],
                    "changed_files": check_result.get("changed_files", []),
                    "diff_excerpt": patch[:2500],
                    "recovery_hint": "Regenerate the unified diff from the latest file content.",
                }
                state.patch_failures.append(failure)
                runtime.trace.record(
                    "agent_phase",
                    {"phase": "apply_patch", "result": check_result, "will_retry_with_patch_failure_feedback": True},
                )
                continue
            if check_result.get("requires_approval"):
                approval_request = {
                    "task_id": task.task_id,
                    "iteration": iteration,
                    "reason": "Patch touches high-risk files or exceeds the configured modification limit.",
                    "risk_reasons": check_result.get("risk_reasons", []),
                    "changed_files": check_result.get("changed_files", []),
                    "diff": patch,
                    "approved": False,
                }
                state.approval_requests.append(approval_request)
                state.latest_error = "Patch requires human approval before applying."
                state.latest_failure_type = "human_approval_required"
                latest_test = {
                    "status": "blocked",
                    "command": task.test_command,
                    "failure_type": "human_approval_required",
                    "error_summary": state.latest_error,
                }
                runtime.trace.record(
                    "approval_required",
                    {
                        **approval_request,
                        "diff": patch[:4000],
                    },
                )
                runtime.trace.record(
                    "agent_phase",
                    {
                        "phase": "approval_required",
                        "risk_reasons": approval_request["risk_reasons"],
                        "changed_files": approval_request["changed_files"],
                    },
                )
                break
            scope_error = _expected_files_violation(check_result.get("changed_files") or [], task.expected_modified_files)
            if scope_error:
                state.latest_error = scope_error["message"]
                state.latest_failure_type = "unexpected_modified_files"
                state.patch_failures.append(
                    {
                        "iteration": iteration,
                        "failure_type": "unexpected_modified_files",
                        "message": scope_error["message"],
                        "changed_files": scope_error["changed_files"],
                        "expected_modified_files": scope_error["expected_modified_files"],
                        "unexpected_files": scope_error["unexpected_files"],
                        "diff_excerpt": patch[:2500],
                        "recovery_hint": "Regenerate the patch using only expected_modified_files.",
                    }
                )
                runtime.trace.record(
                    "agent_phase",
                    {
                        "phase": "apply_patch",
                        "result": check_result,
                        "scope_error": scope_error,
                        "will_retry_with_patch_failure_feedback": True,
                    },
                )
                continue
            apply_result = runtime.apply_patch(patch)
            patch_apply_success = apply_result["success"]
            if not patch_apply_success:
                state.latest_error = apply_result["message"]
                state.latest_failure_type = "patch_apply_failed"
                state.patch_failures.append(
                    {
                        "iteration": iteration,
                        "failure_type": "patch_apply_failed",
                        "message": apply_result["message"],
                        "changed_files": apply_result.get("changed_files", []),
                        "diff_excerpt": patch[:2500],
                        "recovery_hint": "Regenerate the unified diff from the latest file content.",
                    }
                )
                continue

            state.current_diff = runtime.get_diff()
            state.modified_files = runtime.modified_files()
            if not state.current_diff.strip():
                state.latest_error = "Patch applied but produced no reviewable diff."
                state.latest_failure_type = "patch_no_effect"
                state.patch_failures.append(
                    {
                        "iteration": iteration,
                        "failure_type": "patch_no_effect",
                        "message": state.latest_error,
                        "changed_files": apply_result.get("changed_files", []),
                        "diff_excerpt": patch[:2500],
                        "recovery_hint": "Generate a patch that changes repository files, not generated environment artifacts.",
                    }
                )
                runtime.trace.record(
                    "agent_phase",
                    {
                        "phase": "apply_patch",
                        "result": apply_result,
                        "failure_type": state.latest_failure_type,
                        "will_retry_with_patch_failure_feedback": True,
                    },
                )
                continue

            state.current_phase = "run_tests"
            phase_started = time.perf_counter()
            latest_test = runtime.run_tests(task.test_command, timeout=min(task.timeout_seconds, 300))
            _record_phase(
                runtime,
                "run_tests",
                phase_started,
                iteration=iteration,
                status=latest_test.get("status"),
                failure_type=latest_test.get("failure_type"),
            )
            state.test_results.append(latest_test)
            state.current_diff = runtime.get_diff()
            state.modified_files = runtime.modified_files()
            if latest_test["status"] == "passed":
                validation_test = _run_validation_tests(task, runtime)
                if validation_test:
                    latest_test = validation_test
                    state.test_results.append(latest_test)
                    state.current_diff = runtime.get_diff()
                    state.modified_files = runtime.modified_files()
            if latest_test["status"] == "passed":
                break
            repeated_failure = _repeated_failed_patch(state.current_diff or "", state.patch_failures)
            if repeated_failure:
                state.latest_error = repeated_failure
                state.latest_failure_type = "repeated_patch_failure"
                latest_test = {
                    **latest_test,
                    "failure_type": state.latest_failure_type,
                    "error_summary": repeated_failure,
                }
                state.test_results[-1] = latest_test
                runtime.trace.record(
                    "agent_phase",
                    {
                        "phase": "stop_retry",
                        "iteration": iteration,
                        "failure_type": state.latest_failure_type,
                        "reason": repeated_failure,
                    },
                )
                break
            analysis = self.error_analyzer.analyze(latest_test, state.current_diff or "")
            state.latest_error = analysis["root_cause"]
            state.latest_failure_type = analysis["failure_type"]
            runtime.trace.record("agent_phase", {"phase": "analyze_error", "analysis": analysis})
            if not analysis["continue_fixing"]:
                break
            state.patch_failures.append(
                {
                    "iteration": iteration,
                    "failure_type": analysis["failure_type"],
                    "message": analysis["root_cause"],
                    "changed_files": list(state.modified_files),
                    "failed_tests": analysis.get("failed_tests", latest_test.get("failed_tests", [])),
                    "failure_excerpt": analysis.get("failure_excerpt", ""),
                    "removed_literals": analysis.get("removed_literals", []),
                    "preservation_hints": analysis.get("preservation_hints", []),
                    "diff_excerpt": (state.current_diff or "")[:2500],
                    "patch_fingerprint": _patch_fingerprint(state.current_diff or ""),
                    "recovery_hint": (
                        "Previous patch failed tests and was reverted; generate a fresh patch from baseline. "
                        "Do not repeat the same diff or remove behavior protected by failed tests."
                    ),
                }
            )
            if iteration < task.max_iterations and state.modified_files:
                revert_result = runtime.revert_files(state.modified_files)
                state.current_diff = runtime.get_diff()
                state.modified_files = runtime.modified_files()
                patch_apply_success = bool(state.current_diff.strip())
                runtime.trace.record(
                    "agent_phase",
                    {
                        "phase": "revert_patch",
                        "result": revert_result,
                        "reason": "tests_failed_before_retry",
                    },
                )

        state.current_phase = "review_patch"
        latest_test = latest_test or {
            "status": "failed",
            "command": task.test_command,
            "failure_type": state.latest_failure_type or "unknown",
            "error_summary": state.latest_error or "No test run completed",
        }
        final_diff = runtime.get_diff()
        state.current_diff = final_diff
        state.modified_files = runtime.modified_files()
        phase_started = time.perf_counter()
        review = self.reviewer.review(final_diff, latest_test, task.expected_modified_files)
        state.reviewer_result = review
        pr = self.pr_generator.generate(task.issue_title, final_diff, latest_test, review, issue_url=task.issue_url)
        state.pr_title = pr["title"]
        state.pr_body = pr["body"]
        if state.approval_requests:
            state.final_status = "needs_approval"
        else:
            state.final_status = "succeeded" if patch_apply_success and latest_test.get("status") == "passed" and review["approved"] else "failed"
        _record_phase(
            runtime,
            "review_patch",
            phase_started,
            review=review,
            final_status=state.final_status,
        )
        return state


def _record_phase(runtime: ToolRuntime, phase: str, started: float, **payload: object) -> None:
    runtime.trace.record(
        "agent_phase",
        {
            **payload,
            "phase": phase,
            "duration_ms": max(0, round((time.perf_counter() - started) * 1000)),
        },
    )


def _patch_fingerprint(diff: str) -> str:
    normalized = "\n".join(line.rstrip() for line in diff.strip().splitlines())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16] if normalized else ""


def _repeated_failed_patch(diff: str, patch_failures: list[dict]) -> str | None:
    fingerprint = _patch_fingerprint(diff)
    if not fingerprint:
        return None
    if not any(item.get("patch_fingerprint") == fingerprint for item in patch_failures):
        return None
    return (
        "The same patch was applied again and produced another test failure; "
        "stopping to avoid an unproductive retry loop."
    )


def _prioritize_candidate_files(
    candidate_files: list[str],
    expected_modified_files: list[str] | None,
    test_command: str,
) -> list[str]:
    selected: list[str] = []
    expected = [path for path in expected_modified_files or [] if path]
    test_text = test_command.replace("\\", "/").lower()

    for path in expected:
        if path not in selected:
            selected.append(path)

    for path in candidate_files:
        normalized = path.replace("\\", "/").lower()
        name = Path(path).name.lower()
        if (normalized in test_text or name in test_text) and path not in selected:
            selected.append(path)

    for path in candidate_files:
        if path not in selected:
            selected.append(path)
    return selected


def _expected_files_violation(
    changed_files: list[str],
    expected_modified_files: list[str] | None,
) -> dict[str, list[str] | str] | None:
    expected = {path for path in expected_modified_files or [] if path}
    if not expected:
        return None
    unexpected = sorted(set(changed_files) - expected)
    if not unexpected:
        return None
    return {
        "changed_files": sorted(changed_files),
        "expected_modified_files": sorted(expected),
        "unexpected_files": unexpected,
        "message": (
            "Patch modifies files outside expected_modified_files: "
            f"{', '.join(unexpected)}; expected only: {', '.join(sorted(expected))}"
        ),
    }


def _experience_query(raw_issue: str, candidate_files: list[str], test_command: str) -> str:
    focused_files = _focused_experience_files(candidate_files, test_command)
    return "\n".join([raw_issue, test_command, *focused_files])


def _focused_experience_files(candidate_files: list[str], test_command: str) -> list[str]:
    source_files = [path for path in candidate_files if path.endswith(".py") and not path.startswith("tests/")]
    test_text = test_command.lower()
    focused = []
    for path in source_files:
        stem = Path(path).stem.lower()
        singular = stem[:-1] if stem.endswith("s") else stem
        if stem in test_text or singular in test_text:
            focused.append(path)
    if focused:
        return focused[:4]
    tokenized_tests = set(re.findall(r"test[_./-]([a-zA-Z0-9_]+)", test_text))
    for path in source_files:
        stem = Path(path).stem.lower()
        if any(stem in token or token in stem for token in tokenized_tests):
            focused.append(path)
    return (focused or source_files)[:4]


def _run_validation_tests(task: TaskInstance, runtime: ToolRuntime) -> dict | None:
    validation_patch = (task.validation_patch or "").strip()
    if not validation_patch:
        return None
    validation_command = task.validation_test_command or task.test_command
    backup = _backup_validation_files(runtime.workspace, validation_patch)
    check_result = runtime.apply_patch(validation_patch, check=True)
    if not check_result.get("success"):
        runtime.trace.record(
            "agent_phase",
            {
                "phase": "run_validation_tests",
                "status": "failed",
                "failure_type": "validation_patch_apply_failed",
                "error": check_result.get("message"),
            },
        )
        return {
            "status": "failed",
            "command": validation_command,
            "executed_command": validation_command,
            "failed_tests": [],
            "failure_type": "validation_patch_apply_failed",
            "error_summary": str(check_result.get("message") or "Validation patch failed to apply."),
            "raw_output": "",
            "duration_ms": 0,
            "validation": True,
        }

    apply_result = runtime.apply_patch(validation_patch)
    if not apply_result.get("success"):
        _restore_validation_files(runtime.workspace, backup)
        return {
            "status": "failed",
            "command": validation_command,
            "executed_command": validation_command,
            "failed_tests": [],
            "failure_type": "validation_patch_apply_failed",
            "error_summary": str(apply_result.get("message") or "Validation patch failed to apply."),
            "raw_output": "",
            "duration_ms": 0,
            "validation": True,
        }

    try:
        runtime.trace.record(
            "agent_phase",
            {
                "phase": "run_validation_tests",
                "command": validation_command,
                "validation_patch_files": changed_files_from_diff(validation_patch),
            },
        )
        result = runtime.run_tests(validation_command, timeout=min(task.timeout_seconds, 300))
        result["validation"] = True
        return result
    finally:
        _restore_validation_files(runtime.workspace, backup)


def _backup_validation_files(root: Path, diff: str) -> dict[str, tuple[bool, str]]:
    backup: dict[str, tuple[bool, str]] = {}
    for file_path in changed_files_from_diff(diff):
        target = safe_join(root, file_path)
        backup[file_path] = (
            target.exists(),
            target.read_text(encoding="utf-8") if target.exists() else "",
        )
    return backup


def _restore_validation_files(root: Path, backup: dict[str, tuple[bool, str]]) -> None:
    for file_path, (existed, content) in backup.items():
        target = safe_join(root, file_path)
        if existed:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        elif target.exists():
            target.unlink()


def summarize_experience(state: AgentState) -> dict[str, list[str] | int]:
    return {
        "retrieval_strategy": settings.evolution_retrieval,
        "reflection_count": len(state.retrieved_reflections),
        "skill_count": len(state.retrieved_skills),
        "failure_pattern_count": len(state.retrieved_failure_patterns),
        "reflections": [
            str(record.get("task_id") or record.get("reflection", "")) for record in state.retrieved_reflections[:3]
        ],
        "skills": [str(record.get("skill_id") or record.get("name", "")) for record in state.retrieved_skills[:3]],
        "failure_patterns": [
            str(record.get("failure_type") or record.get("symptoms", "")) for record in state.retrieved_failure_patterns[:3]
        ],
    }
