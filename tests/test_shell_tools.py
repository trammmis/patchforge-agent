from __future__ import annotations

import unittest

from app.tools.shell_tools import is_command_allowed, normalize_command_parts


class ShellToolsTests(unittest.TestCase):
    def test_blocks_forbidden_command(self) -> None:
        allowed, reason = is_command_allowed("sudo rm -rf /")
        self.assertFalse(allowed)
        self.assertIn("Forbidden", reason)

    def test_allows_python_test_command(self) -> None:
        allowed, reason = is_command_allowed("python -m unittest tests.test_pricing")
        self.assertTrue(allowed, reason)

    def test_routes_pytest_through_task_python(self) -> None:
        parts = normalize_command_parts("pytest tests/test_sample.py", python_executable="C:/repo/.patchforge_venv/Scripts/python.exe")
        self.assertEqual(parts[:3], ["C:/repo/.patchforge_venv/Scripts/python.exe", "-m", "pytest"])
        self.assertEqual(parts[-1], "tests/test_sample.py")

    def test_routes_python_command_through_task_python(self) -> None:
        parts = normalize_command_parts("python -m unittest discover", python_executable="C:/repo/.patchforge_venv/Scripts/python.exe")
        self.assertEqual(parts[0], "C:/repo/.patchforge_venv/Scripts/python.exe")
        self.assertEqual(parts[1:], ["-m", "unittest", "discover"])


if __name__ == "__main__":
    unittest.main()
