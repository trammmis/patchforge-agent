from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.tools.edit_tools import apply_unified_diff, build_unified_diff, check_python_syntax_after_patch


class EditToolsTests(unittest.TestCase):
    def test_apply_unified_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "sample.py"
            old = "def f():\n    return None\n"
            new = "def f():\n    return 1\n"
            target.write_text(old, encoding="utf-8")
            diff = build_unified_diff("sample.py", old, new)
            checked = apply_unified_diff(root, diff, check=True)
            self.assertTrue(checked.success)
            self.assertEqual(target.read_text(encoding="utf-8"), old)
            applied = apply_unified_diff(root, diff)
            self.assertTrue(applied.success)
            self.assertEqual(target.read_text(encoding="utf-8"), new)

    def test_apply_diff_with_offset_hunk_line_number(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "sample.py"
            target.write_text(
                "def f():\n"
                "    if value is None:\n"
                "        return None\n"
                "    return value\n",
                encoding="utf-8",
            )
            diff = (
                "--- a/sample.py\n"
                "+++ b/sample.py\n"
                "@@ -99,4 +99,4 @@ def f():\n"
                " def f():\n"
                "     if value is None:\n"
                "-        return None\n"
                "+        return 0\n"
                "     return value\n"
            )
            result = apply_unified_diff(root, diff)
            self.assertTrue(result.success, result.message)
            self.assertIn("return 0", target.read_text(encoding="utf-8"))

    def test_apply_git_style_multi_file_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.py"
            second = root / "second.py"
            first.write_text("def first():\n    return 1\n", encoding="utf-8")
            second.write_text("def second():\n    return 2\n", encoding="utf-8")
            diff = (
                "diff --git a/first.py b/first.py\n"
                "--- a/first.py\n"
                "+++ b/first.py\n"
                "@@ -1,2 +1,2 @@\n"
                " def first():\n"
                "-    return 1\n"
                "+    return 10\n"
                "diff --git a/second.py b/second.py\n"
                "--- a/second.py\n"
                "+++ b/second.py\n"
                "@@ -1,2 +1,2 @@\n"
                " def second():\n"
                "-    return 2\n"
                "+    return 20\n"
            )

            checked = apply_unified_diff(root, diff, check=True)
            self.assertTrue(checked.success, checked.message)
            self.assertEqual(first.read_text(encoding="utf-8"), "def first():\n    return 1\n")

            applied = apply_unified_diff(root, diff)

            self.assertTrue(applied.success, applied.message)
            self.assertEqual(first.read_text(encoding="utf-8"), "def first():\n    return 10\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "def second():\n    return 20\n")

    def test_rejects_ambiguous_fuzzy_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "sample.py"
            target.write_text(
                "if flag:\n"
                "    return None\n"
                "if flag:\n"
                "    return None\n",
                encoding="utf-8",
            )
            diff = (
                "--- a/sample.py\n"
                "+++ b/sample.py\n"
                "@@ -50,2 +50,2 @@\n"
                " if flag:\n"
                "-    return None\n"
                "+    return 0\n"
            )
            result = apply_unified_diff(root, diff)
            self.assertFalse(result.success)
            self.assertIn("ambiguous", result.message)

    def test_rejects_malformed_hunk_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "sample.py"
            target.write_text("def f():\n    return None\n", encoding="utf-8")
            diff = (
                "--- a/sample.py\n"
                "+++ b/sample.py\n"
                "@@ -X,1 +X,1 @@\n"
                "-    return None\n"
                "+    return 1\n"
            )

            result = apply_unified_diff(root, diff)

            self.assertFalse(result.success)
            self.assertIn("Malformed diff hunk header", result.message)

    def test_rejects_file_section_without_hunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "sample.py"
            target.write_text("def f():\n    return None\n", encoding="utf-8")
            diff = "--- a/sample.py\n+++ b/sample.py\n"

            result = apply_unified_diff(root, diff)

            self.assertFalse(result.success)
            self.assertIn("No valid hunks found", result.message)

    def test_check_python_syntax_after_patch_rejects_invalid_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "sample.py"
            old = "def f():\n    return None\n"
            new = "def f():\n    return None\n    if\n"
            target.write_text(old, encoding="utf-8")
            diff = build_unified_diff("sample.py", old, new)

            result = check_python_syntax_after_patch(root, diff)

            self.assertIsNotNone(result)
            self.assertIn("SyntaxError", str(result["message"]))
            self.assertEqual(target.read_text(encoding="utf-8"), old)


if __name__ == "__main__":
    unittest.main()
