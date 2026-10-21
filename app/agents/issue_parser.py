from __future__ import annotations

import re

from app.schemas.issue import ParsedIssue


class IssueParser:
    def parse(self, title: str, body: str = "") -> ParsedIssue:
        raw = f"{title}\n{body}".strip()
        lowered = raw.lower()
        suspected = []
        suspected.extend(_extract_paths(raw))
        for keyword in [
            "discount",
            "divide",
            "division",
            "normalize",
            "name",
            "median",
            "deduplicate",
            "empty",
            "none",
            "zero",
            "priority",
            "filter",
            "config",
            "storage",
            "parent",
            "directory",
            "pytest",
            "runxfail",
            "xfail",
            "skip",
            "caplog",
            "unittest",
            "pdb",
            "teardown",
            "collection",
            "marker",
            "mro",
        ]:
            if keyword in lowered:
                suspected.append(keyword)
        needs_clarification = len(raw.split()) < 3
        criteria = ["Relevant focused tests pass", "Patch is minimal", "Final diff is reviewable"]
        if any(word in lowered for word in ["test", "regression", "bug"]):
            criteria.append("Regression behavior is covered")
        expected = _expected_behavior(lowered)
        return ParsedIssue(
            task_type="bugfix",
            summary=title.strip() or "Local bugfix task",
            expected_behavior=expected,
            success_criteria=criteria,
            risk_level="low",
            suspected_area=sorted(set(suspected)),
            needs_clarification=needs_clarification,
            assumptions=["Proceeding with local demo assumptions"] if needs_clarification else [],
        )


def _expected_behavior(text: str) -> str:
    if "discount" in text:
        return "Discount calculation should handle zero-price inputs without returning None."
    if "divide" in text or "division" in text:
        return "Division helper should handle denominator zero without raising."
    if "normalize" in text or "name" in text:
        return "Name normalization should handle None and whitespace safely."
    if "median" in text:
        return "Median should return the average of the two middle values for even-length inputs."
    if "deduplicate" in text:
        return "Deduplication should preserve first-seen order."
    if "priority" in text:
        return "Priority validation should accept valid values and normalize them safely."
    if "filter" in text or "completed" in text or "done" in text:
        return "Task filtering should return the requested completion state without dropping valid tasks."
    if "save_tasks" in text or "parent directories" in text or "parent directory" in text:
        return "Task persistence should create required parent directories before writing output files."
    if "load_config" in text or "malformed" in text or "config" in text or "configuration" in text or "settings" in text:
        return "Config parsing should skip malformed lines while preserving valid key-value pairs."
    match = re.search(r"should\s+(.+)", text)
    return match.group(0) if match else "The described bug should be fixed while preserving existing behavior."


def _extract_paths(text: str) -> list[str]:
    pattern = r"(?:[\w.-]+[\\/])+[\w.-]+\.(?:py|pyi|toml|cfg|ini|json|yaml|yml|md|rst|txt)"
    paths = []
    for match in re.findall(pattern, text):
        normalized = match.replace("\\", "/").strip("`'\".,)")
        if normalized not in paths:
            paths.append(normalized)
    return paths
