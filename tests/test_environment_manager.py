from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.harness.environment_manager import EnvironmentManager
from app.schemas.task import TaskInstance
from app.schemas.test import CommandResult
from app.services.config import settings


class FakeSandbox:
    def __init__(self, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.commands: list[str] = []
        self.configured_python: str | None = None
        self.configured_docker_python: str | None = None
        self.extra_env: dict[str, str] = {}

    def configure_python_env(self, python_executable: Path | str, *, docker_python_executable: str | None = None, path_prefix: Path | str | None = None) -> None:
        self.configured_python = str(python_executable)
        self.configured_docker_python = docker_python_executable

    def configure_environment(self, env: dict[str, str]) -> None:
        self.extra_env.update(env)

    def run(self, command: str, timeout: int = 60) -> CommandResult:
        self.commands.append(command)
        returncode = 1 if self.fail_on and self.fail_on in command else 0
        return CommandResult(
            command=command,
            executed_command=command,
            returncode=returncode,
            message="command failed" if returncode else "ok",
            stderr="boom" if returncode else "",
        )


class EnvironmentManagerTests(unittest.TestCase):
    def test_skips_when_dependency_setup_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements.txt").write_text("pytest\n", encoding="utf-8")
            sandbox = FakeSandbox()
            task = TaskInstance(
                task_id="env_skip",
                repo_path=str(root),
                issue_title="Skip setup",
                install_dependencies=False,
            )

            result = EnvironmentManager().setup(task, root, sandbox)  # type: ignore[arg-type]

            self.assertEqual(result.status, "skipped")
            self.assertEqual(sandbox.commands, [])

    def test_prepares_partial_offline_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements.txt").write_text("pytest\n", encoding="utf-8")
            (root / "pyproject.toml").write_text("[build-system]\nrequires=['setuptools']\n", encoding="utf-8")
            sandbox = FakeSandbox()
            task = TaskInstance(
                task_id="env_partial",
                repo_path=str(root),
                issue_title="Prepare setup",
                install_dependencies=True,
                allow_network=False,
            )

            result = EnvironmentManager().setup(task, root, sandbox)  # type: ignore[arg-type]

            self.assertEqual(result.status, "partial")
            self.assertIn("python -m venv .patchforge_venv", sandbox.commands)
            self.assertIn("python -m pip install -e . --no-deps --no-build-isolation", sandbox.commands)
            self.assertTrue(sandbox.configured_python)
            self.assertEqual(sandbox.extra_env["PIP_DEFAULT_TIMEOUT"], "120")
            self.assertTrue(result.skipped)

    def test_reports_dependency_install_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements.txt").write_text("missing-package\n", encoding="utf-8")
            sandbox = FakeSandbox(fail_on="pip install -r requirements.txt")
            task = TaskInstance(
                task_id="env_fail",
                repo_path=str(root),
                issue_title="Fail setup",
                install_dependencies=True,
                allow_network=True,
            )

            result = EnvironmentManager().setup(task, root, sandbox)  # type: ignore[arg-type]

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.failure_type, "dependency_install_failed")
            self.assertIn("python -m pip install -r requirements.txt", sandbox.commands)

    def test_installs_test_extra_declared_by_tox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "setup.py").write_text(
                "from setuptools import setup\nsetup(extras_require={'testing': ['hypothesis']})\n",
                encoding="utf-8",
            )
            (root / "tox.ini").write_text("[testenv]\nextras = testing\n", encoding="utf-8")
            sandbox = FakeSandbox()
            task = TaskInstance(
                task_id="env_test_extra",
                repo_path=str(root),
                issue_title="Install test dependencies",
                install_dependencies=True,
                allow_network=True,
            )

            result = EnvironmentManager().setup(task, root, sandbox)  # type: ignore[arg-type]

            self.assertEqual(result.status, "succeeded")
            self.assertIn("python -m pip install -e .[testing]", sandbox.commands)

    def test_retries_editable_install_when_setuptools_scm_metadata_is_missing(self) -> None:
        class ScmFallbackSandbox(FakeSandbox):
            def run(self, command: str, timeout: int = 60) -> CommandResult:
                self.commands.append(command)
                if "pip install -e ." in command and "SETUPTOOLS_SCM_PRETEND_VERSION" not in self.extra_env:
                    return CommandResult(
                        command=command,
                        executed_command=command,
                        returncode=1,
                        message="command failed",
                        stderr="LookupError: setuptools-scm was unable to detect version for /workspace/repo.",
                    )
                return CommandResult(command=command, executed_command=command, returncode=0, message="ok")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "setup.py").write_text("from setuptools import setup\nsetup()\n", encoding="utf-8")
            sandbox = ScmFallbackSandbox()
            task = TaskInstance(
                task_id="env_scm_retry",
                repo_path=str(root),
                issue_title="Retry setup",
                install_dependencies=True,
                allow_network=True,
            )

            result = EnvironmentManager().setup(task, root, sandbox)  # type: ignore[arg-type]

            self.assertEqual(result.status, "partial")
            self.assertEqual(sandbox.extra_env["SETUPTOOLS_SCM_PRETEND_VERSION"], "999.0.0")
            self.assertEqual(sandbox.commands.count("python -m pip install -e ."), 2)

    def test_continues_when_pip_upgrade_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "setup.py").write_text("from setuptools import setup\nsetup()\n", encoding="utf-8")
            sandbox = FakeSandbox(fail_on="pip install --upgrade pip")
            task = TaskInstance(
                task_id="env_pip_upgrade_timeout",
                repo_path=str(root),
                issue_title="Continue setup",
                install_dependencies=True,
                allow_network=True,
            )

            with patch.object(settings, "skip_pip_upgrade", False):
                result = EnvironmentManager().setup(task, root, sandbox)  # type: ignore[arg-type]

            self.assertEqual(result.status, "partial")
            self.assertIn("python -m pip install --upgrade pip", sandbox.commands)
            self.assertIn("python -m pip install -e .", sandbox.commands)

    def test_reuses_cached_docker_environment(self) -> None:
        class DockerVenvSandbox(FakeSandbox):
            def __init__(self, workspace: Path) -> None:
                super().__init__()
                self.workspace = workspace
                self.use_docker = True
                self.docker_image = "python:3.8-slim"

            def run(self, command: str, timeout: int = 60) -> CommandResult:
                result = super().run(command, timeout)
                if command == "python -m venv .patchforge_venv":
                    executable = self.workspace / ".patchforge_venv" / "bin" / "python"
                    executable.parent.mkdir(parents=True, exist_ok=True)
                    executable.write_text("fake python\n", encoding="utf-8")
                return result

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first"
            second = root / "second"
            for workspace in (first, second):
                workspace.mkdir()
                (workspace / "pyproject.toml").write_text("[build-system]\nrequires=[]\n", encoding="utf-8")
            cache_dir = root / "cache"
            task = TaskInstance(
                task_id="env_cache",
                repo_path=str(first),
                issue_title="Cache setup",
                install_dependencies=True,
                use_docker=True,
                allow_network=False,
            )

            with patch.object(settings, "environment_cache_enabled", True), patch.object(
                settings, "environment_cache_dir", cache_dir
            ):
                first_result = EnvironmentManager().setup(task, first, DockerVenvSandbox(first))  # type: ignore[arg-type]
                second_result = EnvironmentManager().setup(task, second, DockerVenvSandbox(second))  # type: ignore[arg-type]

            self.assertNotEqual(first_result.status, "cached")
            self.assertEqual(second_result.status, "cached")
            self.assertTrue(second_result.cache_hit)
            self.assertEqual(second_result.commands, [])
            self.assertTrue((second / ".patchforge_venv" / "bin" / "python").exists())


if __name__ == "__main__":
    unittest.main()
