from __future__ import annotations

import unittest

from app.agents.error_analyzer import ErrorAnalyzer


class ErrorAnalyzerTests(unittest.TestCase):
    def test_empty_test_selection_stops_retry_loop(self) -> None:
        analysis = ErrorAnalyzer().analyze(
            {
                "failure_type": "test_selection_empty",
                "error_summary": "77 deselected in 0.29s",
            },
            "--- a/example.py\n+++ b/example.py\n",
        )

        self.assertEqual(analysis["next_action"], "adjust_test_command")
        self.assertFalse(analysis["continue_fixing"])
        self.assertIn("did not select any tests", analysis["root_cause"])

    def test_runtime_error_continues_retry_loop(self) -> None:
        analysis = ErrorAnalyzer().analyze(
            {
                "failure_type": "runtime_error",
                "error_summary": 'TypeError: required field "lineno" missing from alias',
            },
            "--- a/src/_pytest/skipping.py\n+++ b/src/_pytest/skipping.py\n",
        )

        self.assertEqual(analysis["next_action"], "revise_patch")
        self.assertTrue(analysis["continue_fixing"])

    def test_logic_error_extracts_preservation_hints_from_failed_output_and_diff(self) -> None:
        raw_output = (
            "FAILED testing/test_collection.py::Test_getinitialnodes::test_pkgfile\n"
            "E       AssertionError: assert False\n"
            "E        +  where False = isinstance(<Session>, <class '_pytest.python.Package'>)\n"
            "FAILED testing/test_skipping.py::test_skip_package\n"
            "E       assert 0 == 2\n"
            "Captured stdout call\n"
            "collected 2 items\n"
        )
        diff = (
            "--- a/src/_pytest/python.py\n"
            "+++ b/src/_pytest/python.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-    patterns = python_files + [\"__init__.py\"]\n"
            "+    patterns = python_files\n"
        )

        analysis = ErrorAnalyzer().analyze(
            {
                "failure_type": "logic_error",
                "error_summary": "2 failed",
                "failed_tests": [
                    "testing/test_collection.py::Test_getinitialnodes::test_pkgfile",
                    "testing/test_skipping.py::test_skip_package",
                ],
                "raw_output": raw_output,
            },
            diff,
        )

        self.assertTrue(analysis["continue_fixing"])
        self.assertIn("__init__.py", analysis["removed_literals"])
        self.assertTrue(any("package" in hint.lower() for hint in analysis["preservation_hints"]))
        self.assertTrue(any("skip" in hint.lower() for hint in analysis["preservation_hints"]))
        self.assertIn("Failure excerpt", analysis["root_cause"])

    def test_package_parent_chain_failure_adds_repair_direction(self) -> None:
        raw_output = (
            "FAILED testing/test_collection.py::Test_getinitialnodes::test_pkgfile\n"
            "E       AssertionError: assert False\n"
            "E        +  where False = isinstance(<Session subdir>, <class '_pytest.python.Package'>)\n"
            "E        +    where <Session subdir> = <Module x.py>.parent\n"
            "collected 0 items\n"
        )

        analysis = ErrorAnalyzer().analyze(
            {
                "failure_type": "logic_error",
                "error_summary": "1 failed",
                "failed_tests": ["testing/test_collection.py::Test_getinitialnodes::test_pkgfile"],
                "raw_output": raw_output,
            },
            "--- a/src/_pytest/python.py\n+++ b/src/_pytest/python.py\n@@ -1,1 +1,1 @@\n-pass\n+pass\n",
        )

        hints = " ".join(analysis["preservation_hints"])
        self.assertIn("Module -> Package -> Session", hints)
        self.assertIn("Package.collect", hints)
        self.assertIn("Package creation", hints)
        self.assertIn("Module -> Package -> Session", analysis["root_cause"])

    def test_session_path_attribute_error_adds_api_hint(self) -> None:
        raw_output = (
            "FAILED testing/test_collection.py::test_collect_sub_with_symlinks\n"
            "E   AttributeError: 'Session' object has no attribute 'path'\n"
        )

        analysis = ErrorAnalyzer().analyze(
            {
                "failure_type": "runtime_error",
                "error_summary": "AttributeError",
                "failed_tests": ["testing/test_collection.py::test_collect_sub_with_symlinks"],
                "raw_output": raw_output,
            },
            "--- a/src/_pytest/python.py\n+++ b/src/_pytest/python.py\n@@ -1,1 +1,1 @@\n-pass\n+pass\n",
        )

        self.assertTrue(any("path attribute" in hint for hint in analysis["preservation_hints"]))

    def test_validation_dependency_error_continues_with_eager_import_hints(self) -> None:
        raw_output = (
            "FAILED testing/test_collection.py::test_does_not_eagerly_collect_packages\n"
            "ERROR collecting foopkg/__init__.py\n"
            "foopkg/__init__.py:1: in <module>\n"
            "    assert False\n"
            "E   AssertionError\n"
            "FAILED testing/test_collection.py::test_does_not_put_src_on_path\n"
            "E   Failed: DID NOT RAISE <class 'ImportError'>\n"
        )

        analysis = ErrorAnalyzer().analyze(
            {
                "failure_type": "dependency_error",
                "error_summary": "2 failed",
                "validation": True,
                "failed_tests": [
                    "testing/test_collection.py::test_does_not_eagerly_collect_packages",
                    "testing/test_collection.py::test_does_not_put_src_on_path",
                ],
                "raw_output": raw_output,
            },
            "--- a/src/_pytest/python.py\n+++ b/src/_pytest/python.py\n@@ -1,1 +1,1 @@\n-pass\n+pass\n",
        )

        hints = " ".join(analysis["preservation_hints"])
        self.assertEqual(analysis["next_action"], "revise_patch")
        self.assertTrue(analysis["continue_fixing"])
        self.assertIn("eager object mounting", hints)
        self.assertIn("sys.path", hints)

    def test_mark_mro_generator_failure_adds_return_shape_hint(self) -> None:
        raw_output = (
            "FAILED testing/test_mark.py::test_mark_mro\n"
            "    all_marks = get_unpacked_marks(C)\n"
            ">   assert all_marks == [xfail(\"c\").mark, xfail(\"a\").mark, xfail(\"b\").mark]\n"
            "E   AssertionError: assert <generator object normalize_mark_list at 0x1> == [Mark(name='xfail')]\n"
        )

        analysis = ErrorAnalyzer().analyze(
            {
                "failure_type": "logic_error",
                "error_summary": "1 failed",
                "failed_tests": ["testing/test_mark.py::test_mark_mro"],
                "raw_output": raw_output,
            },
            (
                "--- a/src/_pytest/mark/structures.py\n"
                "+++ b/src/_pytest/mark/structures.py\n"
                "@@ -1,4 +1,9 @@\n"
                " def get_unpacked_marks(obj):\n"
                "-    mark_list = getattr(obj, \"pytestmark\", [])\n"
                "+    mark_list = []\n"
                "+    for cls in obj.__mro__:\n"
                "+        mark_list.extend(cls.__dict__.get(\"pytestmark\", []))\n"
                "     return normalize_mark_list(mark_list)\n"
            ),
        )

        hints = " ".join(analysis["preservation_hints"])
        self.assertIn("concrete list", hints)
        self.assertIn("list(normalize_mark_list(...))", hints)
        self.assertIn("obj.__mro__", hints)
        self.assertIn("class's own pytestmark", hints)

    def test_mark_mro_consider_mro_error_adds_keyword_flag_hint(self) -> None:
        raw_output = (
            "FAILED testing/test_mark.py::test_mark_mro\n"
            "E   TypeError: get_unpacked_marks() got an unexpected keyword argument 'consider_mro'\n"
        )

        analysis = ErrorAnalyzer().analyze(
            {
                "failure_type": "runtime_error",
                "error_summary": "TypeError",
                "failed_tests": ["testing/test_mark.py::test_mark_mro"],
                "raw_output": raw_output,
            },
            "--- a/src/_pytest/mark/structures.py\n+++ b/src/_pytest/mark/structures.py\n@@ -1,1 +1,1 @@\n-pass\n+pass\n",
        )

        hints = " ".join(analysis["preservation_hints"])
        self.assertIn("consider_mro=False", hints)
        self.assertIn("keyword-only", hints)

    def test_mark_mro_misordered_duplicates_adds_storage_hint(self) -> None:
        raw_output = (
            "FAILED testing/test_mark.py::test_mark_mro\n"
            "E       AssertionError: assert [Mark(name='xfail')] == [Mark(name='xfail')]\n"
            "E         At index 0 diff: Mark(name='xfail', args=('a',), kwargs={}) != "
            "Mark(name='xfail', args=('c',), kwargs={})\n"
            "E         Left contains 2 more items, first extra item: Mark(name='xfail', args=('a',), kwargs={})\n"
        )

        analysis = ErrorAnalyzer().analyze(
            {
                "failure_type": "logic_error",
                "error_summary": "1 failed",
                "failed_tests": ["testing/test_mark.py::test_mark_mro"],
                "raw_output": raw_output,
            },
            "--- a/src/_pytest/mark/structures.py\n+++ b/src/_pytest/mark/structures.py\n@@ -1,1 +1,1 @@\n-pass\n+pass\n",
        )

        hints = " ".join(analysis["preservation_hints"])
        self.assertIn("do not reverse MRO", hints)
        self.assertIn("deduplicate same-named marks", hints)
        self.assertIn("consider_mro=False", hints)


if __name__ == "__main__":
    unittest.main()
