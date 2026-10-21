from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.schemas.run import TimelineItem, TimelineResult

PHASE_ORDER = [
    "parse_issue",
    "inspect_repo",
    "retrieve_experience",
    "create_plan",
    "generate_patch",
    "apply_patch",
    "run_tests",
    "analyze_error",
    "approval_required",
    "review_patch",
    "stop_retry",
]

PHASE_LABELS = {
    "parse_issue": "解析 Issue",
    "inspect_repo": "探索仓库",
    "retrieve_experience": "检索历史经验",
    "create_plan": "生成修复计划",
    "generate_patch": "生成补丁",
    "apply_patch": "检查并应用补丁",
    "run_tests": "运行测试",
    "analyze_error": "分析失败原因",
    "review_patch": "审查最终补丁",
    "approval_required": "等待人工审批",
    "stop_retry": "停止无效重试",
}

TOOL_LABELS = {
    "list_files": "列出文件",
    "get_file_tree": "读取文件树",
    "read_file": "读取文件",
    "search_code": "搜索代码",
    "search_symbol": "搜索符号",
    "find_tests_for_file": "查找相关测试",
    "apply_patch": "应用补丁",
    "replace_range": "替换代码片段",
    "run_tests": "执行测试命令",
    "get_diff": "生成 diff",
    "modified_files": "统计修改文件",
}


def read_trace_events(trace_path: Path | str | None) -> list[dict[str, Any]]:
    if not trace_path:
        return []
    path = Path(trace_path)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            events.append({"type": "invalid_json", "payload": {"line": line}})
    return events


def build_timeline(task_id: str, trace_path: Path | str | None) -> TimelineResult:
    events = read_trace_events(trace_path)
    if not events:
        return TimelineResult(task_id=task_id, trace_path=str(trace_path) if trace_path else None)

    first_timestamp = _timestamp(events[0])
    items: list[TimelineItem] = []
    latest_phase = "created"
    terminal = False

    for index, event in enumerate(events, 1):
        item = _timeline_item(index, event, first_timestamp)
        items.append(item)
        if item.phase:
            latest_phase = item.phase
        if item.event_type == "task_finished":
            terminal = True
            latest_phase = "finished"

    return TimelineResult(
        task_id=task_id,
        trace_path=str(trace_path) if trace_path else None,
        latest_phase=latest_phase,
        progress=_progress(latest_phase, terminal),
        terminal=terminal,
        items=items,
    )


def _timeline_item(index: int, event: dict[str, Any], first_timestamp: float | None) -> TimelineItem:
    event_type = str(event.get("type", "unknown"))
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    timestamp = _timestamp(event)
    base = {
        "index": index,
        "timestamp": timestamp,
        "elapsed_seconds": round(timestamp - first_timestamp, 3) if timestamp and first_timestamp else None,
        "event_type": event_type,
        "duration_ms": _duration(payload),
        "raw": event,
    }

    if event_type == "task_started":
        return TimelineItem(
            **base,
            category="lifecycle",
            title="任务启动",
            status="running",
            detail=_task_started_detail(payload),
            phase="created",
        )
    if event_type == "task_setup_failed":
        return TimelineItem(
            **base,
            category="lifecycle",
            title="任务初始化失败",
            status="failed",
            detail=str(payload.get("error", "")),
            phase="setup_failed",
        )
    if event_type == "environment_setup_command":
        return TimelineItem(
            **base,
            category="environment",
            title=f"环境命令：{payload.get('command', '')}",
            status="succeeded" if payload.get("returncode") == 0 else "failed",
            detail=str(payload.get("message") or ""),
            phase="environment_setup",
        )
    if event_type == "environment_setup":
        status = str(payload.get("status", "unknown"))
        return TimelineItem(
            **base,
            category="environment",
            title="环境准备",
            status="succeeded" if status in {"succeeded", "cached", "skipped", "partial"} else "failed",
            detail=str(payload.get("message") or ""),
            phase="environment_setup",
        )
    if event_type == "harness_phase":
        phase = str(payload.get("phase") or "harness_phase")
        status = str(payload.get("status") or "completed")
        detail = f"耗时：{payload.get('duration_ms', 0)}ms"
        if payload.get("cache_hit") is not None:
            detail += f"；环境缓存命中：{payload.get('cache_hit')}"
        if payload.get("iterations") is not None:
            detail += f"；迭代：{payload.get('iterations')}"
        return TimelineItem(
            **base,
            category="performance",
            title=f"耗时统计：{phase}",
            status="failed" if status == "failed" else "completed",
            detail=detail,
            phase=None,
        )
    if event_type == "approval_required":
        return TimelineItem(
            **base,
            category="approval",
            title="等待人工审批",
            status="blocked",
            detail=_approval_detail(payload),
            phase="approval_required",
        )
    if event_type == "approval_decision":
        approved = bool(payload.get("approved"))
        return TimelineItem(
            **base,
            category="approval",
            title="审批通过" if approved else "审批拒绝",
            status="succeeded" if approved else "failed",
            detail=str(payload.get("notes") or ""),
            phase="approval_decision",
        )
    if event_type == "approval_resume_started":
        return TimelineItem(
            **base,
            category="approval",
            title="审批后恢复执行",
            status="running",
            detail=f"测试命令：{payload.get('test_command', '')}",
            phase="approval_resume",
        )
    if event_type == "agent_phase":
        phase = str(payload.get("phase") or "agent_phase")
        status = "failed" if payload.get("error") or payload.get("final_status") == "failed" else "completed"
        if payload.get("final_status") == "succeeded":
            status = "succeeded"
        return TimelineItem(
            **base,
            category="agent",
            title=PHASE_LABELS.get(phase, phase),
            status=status,
            detail=_agent_phase_detail(payload),
            phase=phase,
        )
    if event_type == "tool_call":
        tool_name = str(payload.get("tool_name") or "tool")
        return TimelineItem(
            **base,
            category="tool",
            title=TOOL_LABELS.get(tool_name, tool_name),
            status="succeeded" if payload.get("success") else "failed",
            detail=_tool_detail(payload),
            phase=_phase_for_tool(tool_name),
            tool_name=tool_name,
        )
    if event_type == "task_finished":
        status = str(payload.get("status") or "unknown")
        return TimelineItem(
            **base,
            category="lifecycle",
            title="任务结束",
            status="succeeded" if status == "succeeded" else "failed",
            detail=_task_finished_detail(payload),
            phase="finished",
        )
    return TimelineItem(
        **base,
        category="raw",
        title=event_type,
        status="completed",
        detail=_short_json(payload),
    )


def _timestamp(event: dict[str, Any]) -> float | None:
    value = event.get("timestamp")
    return float(value) if isinstance(value, (int, float)) else None


def _duration(payload: dict[str, Any]) -> int | None:
    value = payload.get("duration_ms")
    return int(value) if isinstance(value, (int, float)) else None


def _progress(latest_phase: str, terminal: bool) -> float:
    if terminal:
        return 1.0
    if latest_phase in {"created", "environment_setup"}:
        return 0.08
    if latest_phase not in PHASE_ORDER:
        return 0.0
    return round((PHASE_ORDER.index(latest_phase) + 1) / (len(PHASE_ORDER) + 1), 3)


def _phase_for_tool(tool_name: str) -> str:
    if tool_name in {"get_file_tree", "list_files", "read_file", "search_code", "search_symbol", "find_tests_for_file"}:
        return "inspect_repo"
    if tool_name in {"apply_patch", "replace_range"}:
        return "apply_patch"
    if tool_name == "run_tests":
        return "run_tests"
    if tool_name in {"get_diff", "modified_files"}:
        return "review_patch"
    return "tool_call"


def _task_started_detail(payload: dict[str, Any]) -> str:
    title = payload.get("issue_title") or payload.get("task_id") or ""
    command = payload.get("test_command") or ""
    return "；".join(part for part in [str(title), f"测试：{command}" if command else ""] if part)


def _task_finished_detail(payload: dict[str, Any]) -> str:
    tests = payload.get("tests_passed")
    failure = payload.get("failure_type") or ""
    return f"测试通过：{tests}；失败类型：{failure}" if failure else f"测试通过：{tests}"


def _agent_phase_detail(payload: dict[str, Any]) -> str:
    if payload.get("candidate_files"):
        return "候选文件：" + ", ".join(str(item) for item in payload["candidate_files"][:5])
    if payload.get("plan"):
        return "计划：" + " / ".join(str(item) for item in payload["plan"][:4])
    if payload.get("analysis"):
        analysis = payload["analysis"]
        return str(analysis.get("root_cause") or analysis.get("failure_type") or analysis)
    if payload.get("review"):
        review = payload["review"]
        return f"review approved={review.get('approved')} risk={review.get('risk_level', '')}"
    if payload.get("error"):
        return str(payload["error"])
    if payload.get("risk_reasons"):
        return "；".join(str(item) for item in payload["risk_reasons"])
    if payload.get("experience_summary"):
        summary = payload["experience_summary"]
        return (
            f"Reflection={summary.get('reflection_count', 0)}, "
            f"Skill={summary.get('skill_count', 0)}, "
            f"Failure={summary.get('failure_pattern_count', 0)}"
        )
    return ""


def _approval_detail(payload: dict[str, Any]) -> str:
    reasons = payload.get("risk_reasons") or []
    changed_files = payload.get("changed_files") or []
    parts = []
    if changed_files:
        parts.append("文件：" + ", ".join(str(item) for item in changed_files[:8]))
    if reasons:
        parts.append("原因：" + "；".join(str(item) for item in reasons))
    return "；".join(parts) or str(payload.get("reason") or "Patch requires approval.")


def _tool_detail(payload: dict[str, Any]) -> str:
    if payload.get("error"):
        return str(payload["error"])
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    if "path" in tool_input:
        return f"path={tool_input['path']}"
    if "query" in tool_input:
        return f"query={tool_input['query']}"
    if "command" in tool_input:
        return f"command={tool_input['command']}"
    if "check" in tool_input:
        return f"check={tool_input['check']}"
    return _short_json(tool_input)


def _short_json(value: Any, limit: int = 240) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 3] + "..."
