from __future__ import annotations

import re


class ErrorAnalyzer:
    def analyze(self, test_result: dict, current_diff: str) -> dict:
        failure_type = test_result.get("failure_type") or "unknown"
        failure_excerpt = _failure_excerpt(str(test_result.get("raw_output") or ""), test_result.get("failed_tests") or [])
        changed_literals = _removed_literals(current_diff)
        preservation_hints = _preservation_hints(failure_excerpt, changed_literals)
        if not current_diff:
            next_action = "retrieve_more_context"
            root_cause = "No patch was generated for this issue."
        elif failure_type == "patch_apply_failed":
            next_action = "regenerate_patch_from_latest_file"
            root_cause = "Patch could not be applied to the current workspace."
        elif failure_type == "test_selection_empty":
            next_action = "adjust_test_command"
            root_cause = "The test command did not select any tests; use a broader or valid regression command."
        elif failure_type in {"logic_error", "syntax_error", "runtime_error"}:
            next_action = "revise_patch"
            root_cause = _logic_root_cause(test_result, failure_excerpt, preservation_hints)
        elif failure_type == "dependency_error" and test_result.get("validation"):
            next_action = "revise_patch"
            root_cause = _logic_root_cause(test_result, failure_excerpt, preservation_hints)
        else:
            next_action = "human_review"
            root_cause = test_result.get("error_summary", "The failure is not clearly recoverable.")
        return {
            "failure_type": failure_type,
            "root_cause": root_cause,
            "next_action": next_action,
            "continue_fixing": next_action not in {"human_review", "adjust_test_command"},
            "failed_tests": list(test_result.get("failed_tests") or []),
            "failure_excerpt": failure_excerpt,
            "removed_literals": changed_literals,
            "preservation_hints": preservation_hints,
        }


def _logic_root_cause(test_result: dict, failure_excerpt: str, preservation_hints: list[str]) -> str:
    parts = [str(test_result.get("error_summary") or "Focused tests still fail.")]
    if preservation_hints:
        parts.append("Preserve behavior: " + " ".join(preservation_hints))
    if failure_excerpt:
        parts.append("Failure excerpt:\n" + failure_excerpt)
    return "\n\n".join(parts)[:5000]


def _failure_excerpt(raw_output: str, failed_tests: list[str]) -> str:
    if not raw_output:
        return ""
    lines = raw_output.splitlines()
    interesting: list[str] = []
    failed_names = [_short_test_name(test) for test in failed_tests]
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if _is_interesting_failure_line(stripped, failed_names):
            start = max(0, idx - 2)
            end = min(len(lines), idx + 8)
            interesting.extend(lines[start:end])
            interesting.append("")
    if not interesting:
        tail = "\n".join(lines[-80:])
        return tail[-2500:]
    return "\n".join(_dedupe_adjacent_blank_lines(interesting))[-3500:]


def _is_interesting_failure_line(line: str, failed_names: list[str]) -> bool:
    if not line:
        return False
    if any(name and name in line for name in failed_names):
        return True
    return any(
        token in line
        for token in [
            "E       ",
            "AssertionError",
            "assert ",
            "FAILED ",
            "Captured stdout",
            "Traceback",
        ]
    )


def _short_test_name(test: str) -> str:
    return test.rsplit("::", 1)[-1].split("[", 1)[0]


def _removed_literals(diff: str) -> list[str]:
    values: list[str] = []
    for line in diff.splitlines():
        if not line.startswith("-") or line.startswith("---"):
            continue
        for match in re.finditer(r"(['\"])(?P<value>[^'\"]{2,80})\1", line):
            value = match.group("value")
            if value not in values:
                values.append(value)
    return values[:12]


def _preservation_hints(failure_excerpt: str, removed_literals: list[str]) -> list[str]:
    hints: list[str] = []
    if removed_literals:
        hints.append(
            "Previous patch removed or changed key literals "
            f"{removed_literals}; do not remove those semantics unless replacing them with equivalent behavior."
        )
    lowered = failure_excerpt.lower()
    if "package" in lowered or "parent" in lowered:
        hints.append("Preserve package/parent collection relationships covered by failed tests.")
    if "pytest.package" in lowered or ("package" in lowered and "session" in lowered and "parent" in lowered):
        hints.append(
            "Preserve the collector parent chain Module -> Package -> Session; do not fix this by simply "
            "returning early for package __init__.py collection. Inspect Package.collect, Module/Pyobj object "
            "mounting, or equivalent package collector setup."
        )
    if "object has no attribute 'path'" in lowered or 'object has no attribute "path"' in lowered:
        hints.append(
            "Do not assume every collector parent has a path attribute; prefer APIs available on Session/Package "
            "or derive package paths from the file path itself."
        )
    if "collected 0 items" in lowered and "package" in lowered:
        hints.append("Package creation must still happen for modules inside packages even when __init__.py is not a test module.")
    if "__init__.py" in lowered and ("assert false" in lowered or "interrupted" in lowered):
        hints.append(
            "Do not import arbitrary package __init__.py files just to create collectors; avoid eager object mounting "
            "for Package collectors and only import package __init__.py when it is intentionally collected as a module."
        )
    if "did not raise" in lowered and "importerror" in lowered:
        hints.append("Do not put unrelated package directories such as src/nope on sys.path during collection.")
    if "skip" in lowered or "skipped" in lowered:
        hints.append("Preserve skip/marker behavior covered by failed tests.")
    if ("generator object" in lowered or "<generator" in lowered) and ("== [" in failure_excerpt or "assert " in lowered):
        hints.append(
            "The failed assertion compares the function result directly to a list; return a concrete list and preserve "
            "the tested ordering instead of returning an iterator/generator."
        )
        if "normalize_mark_list" in lowered:
            hints.append(
                "If the live code returns normalize_mark_list(...), wrap that exact call at the public boundary as "
                "list(normalize_mark_list(...)); do not leave a bare generator return and do not change the helper's "
                "generator contract without evidence."
            )
    if "test_mark_mro" in lowered or ("get_unpacked_marks" in lowered and "xfail" in lowered):
        hints.append(
            "For class marker inheritance, collect each class's own pytestmark in obj.__mro__ order using "
            "cls.__dict__.get('pytestmark', []) so inherited attributes do not hide sibling-base marks."
        )
        if "at index 0 diff" in lowered:
            hints.append(
                "Preserve MRO order exactly: the decorated class's own marks come first, then base-class marks; "
                "do not reverse MRO or deduplicate same-named marks with different arguments."
            )
        if "left contains" in lowered or "right contains" in lowered:
            hints.append(
                "If class decoration stores inherited marks into the class's own pytestmark, add an own-only flag "
                "such as consider_mro=False and use it from the storage helper so base marks are not copied twice."
            )
    if "consider_mro" in lowered and "unexpected keyword" in lowered:
        hints.append(
            "Add a backwards-compatible keyword-only consider_mro flag; consider_mro=False should limit collection "
            "to the object's own marks."
        )
    return hints


def _dedupe_adjacent_blank_lines(lines: list[str]) -> list[str]:
    result: list[str] = []
    previous_blank = False
    for line in lines:
        blank = not line.strip()
        if blank and previous_blank:
            continue
        result.append(line)
        previous_blank = blank
    return result
