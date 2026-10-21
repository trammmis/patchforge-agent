from __future__ import annotations

import re

from app.harness.sandbox_runner import SandboxRunner
from app.schemas.test import ParsedTestResult, TestResult


class TestRunner:
    def __init__(self, sandbox: SandboxRunner) -> None:
        self.sandbox = sandbox

    def run_tests(self, command: str, timeout: int = 120) -> TestResult:
        result = self.sandbox.run(command, timeout=timeout)
        parsed = parse_test_output(result.stdout + "\n" + result.stderr, result.returncode, result.timed_out)
        return TestResult(
            status=parsed.status,
            command=command,
            executed_command=result.executed_command,
            failed_tests=parsed.failed_tests,
            error_summary=parsed.error_summary or result.message,
            raw_output=(result.stdout + "\n" + result.stderr).strip(),
            duration_ms=result.duration_ms,
            failure_type=parsed.failure_type,
        )


def parse_test_output(output: str, returncode: int, timed_out: bool = False) -> ParsedTestResult:
    if timed_out:
        return ParsedTestResult(status="failed", error_summary="Test command timed out", failure_type="timeout")
    if returncode == 0:
        return ParsedTestResult(status="passed", error_summary="", failure_type=None)
    failed = []
    for pattern in [r"FAILED\s+([^\s]+)", r"FAIL:\s+([^\s]+)", r"ERROR:\s+([^\s]+)"]:
        failed.extend(re.findall(pattern, output))
    if not failed and _pytest_selected_no_tests(output):
        failure_type = "test_selection_empty"
    elif "SyntaxError" in output:
        failure_type = "syntax_error"
    elif "ModuleNotFoundError" in output or "ImportError" in output:
        failure_type = "dependency_error"
    elif _python_runtime_error(output):
        failure_type = "runtime_error"
    elif "AssertionError" in output or "FAIL:" in output or "FAILED" in output:
        failure_type = "logic_error"
    else:
        failure_type = "environment_error"
    summary_lines = [line.strip() for line in output.splitlines() if line.strip()]
    return ParsedTestResult(
        status="failed",
        failed_tests=failed[:20],
        error_summary=summary_lines[-1] if summary_lines else "Test command failed",
        failure_type=failure_type,
    )


def _pytest_selected_no_tests(output: str) -> bool:
    lowered = output.lower()
    if "no tests ran" in lowered or "no tests collected" in lowered:
        return True
    match = re.search(r"collected\s+(\d+)\s+items?\s*/\s+(\d+)\s+deselected", lowered)
    if match and int(match.group(1)) == int(match.group(2)):
        return True
    return bool(re.search(r"=\s*\d+\s+deselected\s+in\s+[\d.]+s\s*=", lowered))


def _python_runtime_error(output: str) -> bool:
    if "Traceback (most recent call last)" not in output and "ERROR " not in output and "ERROR:" not in output:
        return False
    return bool(
        re.search(
            r"\b(AttributeError|KeyError|IndexError|NameError|RuntimeError|TypeError|ValueError):",
            output,
        )
    )
