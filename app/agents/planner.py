from __future__ import annotations

from typing import Any

from app.schemas.issue import ParsedIssue


class Planner:
    def plan(
        self,
        issue: ParsedIssue,
        candidate_files: list[str],
        test_command: str,
        retrieved_reflections: list[dict[str, Any]] | None = None,
        retrieved_skills: list[dict[str, Any]] | None = None,
        retrieved_failure_patterns: list[dict[str, Any]] | None = None,
    ) -> list[str]:
        plan = [
            "Inspect candidate implementation files",
            "Inspect related tests or expected behavior",
            "Generate a minimal unified diff",
            f"Run focused test command: {test_command}",
            "Review final diff and risk flags",
        ]
        retrieved_reflections = retrieved_reflections or []
        retrieved_skills = retrieved_skills or []
        retrieved_failure_patterns = retrieved_failure_patterns or []
        experience_steps: list[str] = []
        if retrieved_reflections:
            experience_steps.append(f"Apply relevant reflection memory: {_reflection_summary(retrieved_reflections[0])}")
        if retrieved_skills:
            experience_steps.append(f"Reuse validated skill: {_skill_summary(retrieved_skills[0])}")
        if retrieved_failure_patterns:
            experience_steps.append(f"Avoid known failure pattern: {_failure_summary(retrieved_failure_patterns[0])}")
        plan = [*experience_steps, *plan]
        if any(file.startswith("tests/") for file in candidate_files):
            plan.insert(2, "Use existing tests as regression oracle")
        if issue.needs_clarification:
            plan.insert(0, "Record assumptions because the issue is underspecified")
        return plan


def _reflection_summary(record: dict[str, Any]) -> str:
    return _shorten(str(record.get("reflection") or record.get("task_id") or "prior similar repair"))


def _skill_summary(record: dict[str, Any]) -> str:
    name = str(record.get("name") or record.get("skill_id") or "prior repair skill")
    procedure = record.get("procedure") or []
    if procedure:
        return _shorten(f"{name}; first steps: {', '.join(str(step) for step in procedure[:2])}")
    return _shorten(name)


def _failure_summary(record: dict[str, Any]) -> str:
    failure_type = str(record.get("failure_type") or "unknown")
    recovery = record.get("recommended_recovery") or []
    if recovery:
        return _shorten(f"{failure_type}; recovery: {', '.join(str(step) for step in recovery[:2])}")
    return _shorten(failure_type)


def _shorten(text: str, limit: int = 220) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 3] + "..."
