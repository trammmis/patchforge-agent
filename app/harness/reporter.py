from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from app.schemas.task import TaskRunResult
from app.services.config import settings


class ReportGenerator:
    def write_task_report(self, result: TaskRunResult, report_dir: Path | str) -> Path:
        report_path = Path(report_dir) / f"{result.task_id}_report.md"
        trace = _trace_summary(result.trace_path)
        archive_path = _published_report_path(trace.get("task") or {}, result, report_path)
        public_report_path = archive_path or report_path
        lines = [
            f"# PatchForge 任务报告：{result.task_id}",
            "",
            "## 测试结论",
            f"- 任务状态：`{result.status}`",
            f"- 测试通过：`{result.tests_passed}`",
            f"- Patch 应用成功：`{result.patch_apply_success}`",
            f"- 迭代轮数：`{result.iterations}`",
            f"- 运行耗时：`{result.duration_seconds:.2f}s`",
            f"- 失败类型：`{result.failure_type or ''}`",
            f"- 错误信息：`{result.error_message or ''}`",
            f"- 关联 Issue：`{result.issue_url or ''}`",
            "",
            "## 测试对象",
            *_task_lines(trace.get("task") or {}, result),
            "",
            "## 执行环境",
            "## 评测警告",
            *_evaluation_warning_lines(result.evaluation_warnings),
            "",
            *_runtime_lines(trace.get("task") or {}, result.environment_setup),
            "",
            "## 执行过程摘要",
            *_phase_lines(trace.get("phases") or []),
            "",
            "## 时间分解",
            *_timing_lines(trace.get("harness_phases") or []),
            "",
            "## Patch 生成记录",
            *_llm_lines(trace.get("llm_attempts") or []),
            "",
            "## Patch 应用记录",
            *_patch_lines(trace.get("patch_attempts") or []),
            "",
            "## 测试执行记录",
            *_test_lines(trace.get("test_runs") or []),
            "",
            "## 产物路径",
            f"- 报告文件：`{report_path}`",
            f"- JSON 结果：`{report_path.with_suffix('.json')}`",
            *([f"- 项目归档报告：`{archive_path}`"] if archive_path else []),
            f"- 轨迹文件：`{result.trace_path or ''}`",
            "",
            "## 环境准备",
            *_environment_lines(result.environment_setup),
            "",
            "## 审批请求",
            *_approval_lines(result.approval_requests),
            "",
            "## 经验复用",
            *_experience_lines(result.experience_summary),
            "",
            "## 修改文件",
            *(f"- `{file}`" for file in result.modified_files),
            "",
            "## PR 草稿",
            f"标题：{result.pr_title or ''}",
            "",
            result.pr_body or "",
            "",
            "## 最终 Diff",
            "```diff",
            result.final_diff or "",
            "```",
        ]
        report_path.write_text("\n".join(lines), encoding="utf-8")
        result.report_path = str(public_report_path)
        json_path = report_path.with_suffix(".json")
        json_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        if archive_path:
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            archive_path.write_text("\n".join(lines), encoding="utf-8")
            archive_path.with_suffix(".json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        return public_report_path

    def write_eval_report(self, metrics: dict, results: list[TaskRunResult], report_dir: Path | str) -> Path:
        report_path = Path(report_dir) / "evaluation_report.md"
        lines = [
            "# PatchForge 评测报告",
            "",
            "## 指标概览",
            "",
            "| 指标 | 数值 |",
            "|---|---:|",
        ]
        for key, value in metrics.items():
            lines.append(f"| {key} | {value} |")
        lines.extend(["", "## 任务明细", ""])
        for result in results:
            lines.append(
                f"- `{result.task_id}`：{result.status}，tests={result.tests_passed}，"
                f"failure={result.failure_type or ''}，iterations={result.iterations}"
            )
        report_path.write_text("\n".join(lines), encoding="utf-8")
        return report_path


def _experience_lines(summary: dict) -> list[str]:
    if not summary:
        return ["- 未检索到可复用经验"]
    lines = [
        f"- 检索策略：`{summary.get('retrieval_strategy', 'unknown')}`",
        f"- Reflection：`{summary.get('reflection_count', 0)}` 条",
        f"- Skill：`{summary.get('skill_count', 0)}` 条",
        f"- Failure Pattern：`{summary.get('failure_pattern_count', 0)}` 条",
    ]
    for label, key in [("相关 Reflection", "reflections"), ("相关 Skill", "skills"), ("相关 Failure Pattern", "failure_patterns")]:
        values = summary.get(key) or []
        if values:
            lines.append(f"- {label}：{', '.join(f'`{value}`' for value in values)}")
    return lines


def _evaluation_warning_lines(warnings: list[dict]) -> list[str]:
    if not warnings:
        return ["- 无"]
    lines: list[str] = []
    for warning in warnings:
        lines.append(f"- `{warning.get('type', 'warning')}`：{warning.get('message', '')}")
        files = warning.get("files") or []
        if files:
            lines.append(f"- 相关文件：{', '.join(f'`{file}`' for file in files)}")
        deleted_tests = warning.get("deleted_tests") or {}
        if deleted_tests:
            lines.append(f"- 删除测试：`{json.dumps(deleted_tests, ensure_ascii=False)}`")
    return lines


def _trace_summary(trace_path: str | None) -> dict:
    summary: dict = {
        "task": {},
        "phases": [],
        "llm_attempts": [],
        "patch_attempts": [],
        "test_runs": [],
        "harness_phases": [],
    }
    if not trace_path:
        return summary
    path = Path(trace_path)
    if not path.exists():
        return summary
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = event.get("payload") or {}
        event_type = event.get("type")
        if event_type == "task_started":
            summary["task"] = payload
        elif event_type == "agent_phase":
            phase = payload.get("phase")
            if phase:
                summary["phases"].append(
                    {
                        "phase": phase,
                        "duration_ms": payload.get("duration_ms"),
                        "iteration": payload.get("iteration"),
                        "status": payload.get("status"),
                        "failure_type": payload.get("failure_type"),
                        "final_status": payload.get("final_status"),
                        "error": payload.get("error"),
                        "candidate_files": payload.get("candidate_files"),
                    }
                )
        elif event_type == "harness_phase":
            phase = payload.get("phase")
            if phase:
                summary["harness_phases"].append(
                    {
                        "phase": phase,
                        "duration_ms": payload.get("duration_ms", 0),
                        "status": payload.get("status"),
                        "cache_hit": payload.get("cache_hit"),
                        "iterations": payload.get("iterations"),
                    }
                )
        elif event_type == "llm_patch_generation":
            summary["llm_attempts"].append(payload)
        elif event_type == "tool_call":
            tool_name = payload.get("tool_name")
            tool_output = payload.get("tool_output")
            if tool_name == "apply_patch" and isinstance(tool_output, dict):
                summary["patch_attempts"].append({**tool_output, "tool_success": payload.get("success")})
            elif tool_name == "run_tests" and isinstance(tool_output, dict):
                summary["test_runs"].append(tool_output)
    return summary


def _published_report_path(task: dict, result: TaskRunResult, internal_path: Path) -> Path | None:
    """Return the stable project-level report path for a GitHub-backed task."""

    if ".patchforge_runs" not in internal_path.parts:
        return None
    repo_url = str(task.get("repo_url") or task.get("repo_path") or "").strip()
    issue_url = str(task.get("issue_url") or result.issue_url or "").strip()
    repo_parts = [part for part in urlparse(repo_url).path.strip("/").split("/") if part]
    project_name = (repo_parts[-1] if repo_parts else Path(repo_url).name).removesuffix(".git")
    if not project_name:
        return None
    issue_match = re.search(r"/(?:issues?|pulls?)/(\d+)(?:/|$)", issue_url)
    issue_name = f"issue{issue_match.group(1)}" if issue_match else result.task_id
    return settings.project_root / "reports" / project_name / f"{issue_name}-测试报告.md"


def _task_lines(task: dict, result: TaskRunResult) -> list[str]:
    if not task:
        return ["- 未能从 trace 中读取任务输入。"]
    return [
        f"- 仓库 URL：`{task.get('repo_url') or ''}`",
        f"- 本地仓库：`{task.get('repo_path') or ''}`",
        f"- Base commit/ref：`{task.get('base_commit') or ''}`",
        f"- Issue 标题：{task.get('issue_title') or ''}",
        f"- Issue URL：`{task.get('issue_url') or result.issue_url or ''}`",
        f"- 测试命令：`{task.get('test_command') or ''}`",
        f"- 最大迭代：`{task.get('max_iterations') or ''}`",
        f"- 期望修改文件：`{', '.join(task.get('expected_modified_files') or []) or ''}`",
    ]


def _runtime_lines(task: dict, environment_setup: dict) -> list[str]:
    return [
        f"- Docker sandbox：`{task.get('use_docker', False)}`",
        f"- Docker 镜像：`{task.get('docker_image') or (environment_setup or {}).get('docker_image') or ''}`",
        f"- 允许网络：`{task.get('allow_network', False)}`",
        f"- 依赖准备：`{task.get('install_dependencies', False)}`",
        f"- 依赖准备状态：`{environment_setup.get('status', 'unknown') if environment_setup else 'skipped'}`",
        f"- Python：`{(environment_setup or {}).get('python_executable') or (environment_setup or {}).get('docker_python_executable') or ''}`",
    ]


def _phase_lines(phases: list[dict]) -> list[str]:
    if not phases:
        return ["- 未记录 agent phase。"]
    lines: list[str] = []
    seen = 0
    for item in phases:
        phase = item.get("phase")
        if not phase:
            continue
        seen += 1
        detail = ""
        if item.get("request_duration_ms") is not None:
            lines.append(
                f"  - LLM request duration: `{item.get('request_duration_ms')}ms`, "
                f"network attempts: `{item.get('request_attempts', 1)}`"
            )
        if item.get("error"):
            detail = f"：{item['error']}"
        elif item.get("final_status"):
            detail = f"：final_status={item['final_status']}"
        elif item.get("candidate_files"):
            detail = "：" + ", ".join(f"`{path}`" for path in item["candidate_files"][:6])
        if item.get("duration_ms") is not None:
            detail = f"{detail}，耗时={item['duration_ms']}ms"
        lines.append(f"{seen}. `{phase}`{detail}")
    return lines or ["- 未记录 agent phase。"]


def _timing_lines(phases: list[dict]) -> list[str]:
    if not phases:
        return ["- 未记录 Harness 阶段耗时。"]
    lines: list[str] = []
    for item in phases:
        detail = f"耗时 `{item.get('duration_ms', 0)}ms`"
        if item.get("cache_hit") is not None:
            detail += f"，cache_hit=`{item['cache_hit']}`"
        if item.get("iterations") is not None:
            detail += f"，iterations=`{item['iterations']}`"
        lines.append(f"- `{item.get('phase', 'unknown')}`：{detail}")
    return lines


def _llm_lines(attempts: list[dict]) -> list[str]:
    if not attempts:
        return ["- 未调用 LLM patch 生成。"]
    lines: list[str] = []
    for index, item in enumerate(attempts, 1):
        context_files = item.get("context_files") or []
        lines.append(
            f"- 第 {index} 次：attempt=`{item.get('attempt', index)}`，prompt=`{item.get('prompt_mode', '')}`，"
            f"provider=`{item.get('provider', '')}`，model=`{item.get('model', '')}`，"
            f"success=`{item.get('success', False)}`，diff_chars=`{item.get('diff_chars', 0)}`"
        )
        if "will_retry" in item:
            lines.append(f"  - 后续重试：`{item.get('will_retry')}`")
        if item.get("error"):
            lines.append(f"  - 错误：{item.get('error')}")
        diagnostics = item.get("diagnostics") or {}
        if diagnostics:
            lines.append(
                "  - 诊断："
                + "；".join(f"{key}={_format_diag(value)}" for key, value in diagnostics.items())
            )
        if context_files:
            lines.append("  - 上下文：" + ", ".join(f"`{path}`" for path in context_files[:8]))
        if item.get("notes"):
            lines.append(f"  - 说明：{item.get('notes')}")
    return lines


def _patch_lines(attempts: list[dict]) -> list[str]:
    if not attempts:
        return ["- 未记录 patch 应用。"]
    lines: list[str] = []
    for index, item in enumerate(attempts, 1):
        lines.append(
            f"- 第 {index} 次：success=`{item.get('success', False)}`，"
            f"requires_approval=`{item.get('requires_approval', False)}`，"
            f"文件=`{', '.join(item.get('changed_files') or [])}`"
        )
        if item.get("message"):
            lines.append(f"  - 信息：{item.get('message')}")
        risk = item.get("risk_reasons") or []
        if risk:
            lines.append("  - 风险：" + "；".join(str(item) for item in risk))
    return lines


def _test_lines(test_runs: list[dict]) -> list[str]:
    if not test_runs:
        return ["- 未执行测试命令。"]
    lines: list[str] = []
    for index, item in enumerate(test_runs, 1):
        lines.extend(
            [
                f"- 第 {index} 次：`{item.get('command', '')}`",
                f"  - 状态：`{item.get('status', '')}`",
                f"  - 失败类型：`{item.get('failure_type') or ''}`",
                f"  - 耗时：`{item.get('duration_ms', 0)}ms`",
            ]
        )
        if item.get("executed_command"):
            lines.append(f"  - 实际命令：`{item.get('executed_command')}`")
        if item.get("failed_tests"):
            lines.append("  - 失败测试：" + ", ".join(f"`{test}`" for test in item["failed_tests"][:10]))
        if item.get("error_summary"):
            lines.append(f"  - 摘要：{item.get('error_summary')}")
    return lines


def _format_diag(value) -> str:
    text = str(value)
    return text[:300] + ("..." if len(text) > 300 else "")


def _environment_lines(summary: dict) -> list[str]:
    if not summary:
        return ["- 未启用依赖准备"]
    lines = [
        f"- 状态：`{summary.get('status', 'unknown')}`",
        f"- 是否启用：`{summary.get('enabled', False)}`",
        f"- 依赖清单：`{', '.join(summary.get('manifests') or []) or '无'}`",
        f"- Python：`{summary.get('python_executable') or summary.get('docker_python_executable') or ''}`",
        f"- 说明：{summary.get('message') or ''}",
    ]
    lines.extend(
        [
            f"- Environment cache hit: `{summary.get('cache_hit', False)}`",
            f"- Environment cache key: `{summary.get('cache_key') or ''}`",
        ]
    )
    skipped = summary.get("skipped") or []
    if skipped:
        lines.append("- 跳过项：" + "；".join(str(item) for item in skipped))
    commands = summary.get("commands") or []
    if commands:
        lines.append("- 命令记录：")
        for item in commands:
            lines.append(
                f"  - `{item.get('command')}` -> returncode={item.get('returncode')}，"
                f"message={item.get('message', '')}"
            )
    return lines


def _approval_lines(requests: list[dict]) -> list[str]:
    if not requests:
        return ["- 无待审批项"]
    lines: list[str] = []
    for index, item in enumerate(requests, 1):
        lines.extend(
            [
                f"- 审批项 {index}：`{item.get('reason', '')}`",
                f"  - 状态：`{'approved' if item.get('approved') else 'pending'}`",
                f"  - 文件：`{', '.join(item.get('changed_files') or [])}`",
                f"  - 风险原因：{'; '.join(item.get('risk_reasons') or [])}",
            ]
        )
    return lines
