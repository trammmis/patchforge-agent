from __future__ import annotations

import unittest

from app.harness.test_runner import parse_test_output


class TestRunnerParserTests(unittest.TestCase):
    def test_passed_output(self) -> None:
        parsed = parse_test_output("OK", 0)
        self.assertEqual(parsed.status, "passed")

    def test_assertion_failure(self) -> None:
        parsed = parse_test_output("AssertionError: expected 0", 1)
        self.assertEqual(parsed.status, "failed")
        self.assertEqual(parsed.failure_type, "logic_error")

    def test_all_pytest_items_deselected(self) -> None:
        output = """
        collected 77 items / 77 deselected / 0 selected

        =========================== 77 deselected in 0.29s ============================
        """
        parsed = parse_test_output(output, 5)
        self.assertEqual(parsed.status, "failed")
        self.assertEqual(parsed.failure_type, "test_selection_empty")

    def test_pytest_no_tests_ran(self) -> None:
        parsed = parse_test_output("no tests ran in 0.01s", 5)
        self.assertEqual(parsed.failure_type, "test_selection_empty")

    def test_failed_items_take_precedence_over_nested_empty_selection_text(self) -> None:
        output = """
        FAILED testing/test_skipping.py::TestSkip::test_skip_class - AssertionError
        collected 77 items / 77 deselected / 0 selected
        ======================== 19 failed, 58 passed in 12.02s ========================
        """
        parsed = parse_test_output(output, 1)

        self.assertEqual(parsed.failure_type, "logic_error")
        self.assertIn("testing/test_skipping.py::TestSkip::test_skip_class", parsed.failed_tests)

    def test_python_traceback_runtime_error_is_recoverable_failure(self) -> None:
        output = """
        ERROR testing/test_skipping.py::test_location
        Traceback (most recent call last):
          File "src/_pytest/skipping.py", line 239, in pytest_runtest_setup
            raise skip.Exception(skipped.reason).with_traceback(item._traceback())
        TypeError: required field "lineno" missing from alias
        """
        parsed = parse_test_output(output, 1)

        self.assertEqual(parsed.failure_type, "runtime_error")


if __name__ == "__main__":
    unittest.main()
