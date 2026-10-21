from __future__ import annotations

import os
import shutil
import stat
import time
from pathlib import Path

from app.schemas.task import TaskInstance
from app.services.config import settings
from app.services.repository_fetcher import fetch_repository


class WorkspaceManager:
    def __init__(self, runs_dir: Path | str | None = None) -> None:
        self.runs_dir = Path(runs_dir) if runs_dir is not None else settings.runs_dir

    def prepare(self, task: TaskInstance) -> dict[str, Path]:
        task_dir = self.runs_dir / task.task_id
        repo_dir = task_dir / "repo"
        baseline_dir = task_dir / "baseline"
        reports_dir = task_dir / "reports"
        _clear_task_dir(task_dir)
        reports_dir.mkdir(parents=True, exist_ok=True)
        if task.repo_path:
            self._copy_local_repo(task.repo_path, repo_dir)
        elif task.repo_url:
            fetch_repository(task.repo_url, repo_dir, ref=task.base_commit)
        else:
            raise ValueError("需要提供 repo_path 或 repo_url")
        shutil.copytree(repo_dir, baseline_dir, ignore=_repo_ignore())
        return {"task_dir": task_dir, "repo_dir": repo_dir, "baseline_dir": baseline_dir, "reports_dir": reports_dir}

    def _copy_local_repo(self, repo_path: str, repo_dir: Path) -> None:
        source = Path(repo_path).resolve()
        if not source.exists():
            raise FileNotFoundError(source)
        shutil.copytree(source, repo_dir, ignore=_repo_ignore())


def _repo_ignore():
    return shutil.ignore_patterns(".git", ".patchforge_runs", ".patchforge_data", ".patchforge_venv", "__pycache__", ".pytest_cache")


def _clear_task_dir(task_dir: Path) -> None:
    if not task_dir.exists():
        return
    try:
        shutil.rmtree(task_dir, onerror=_make_writable_and_retry)
        return
    except OSError:
        stale_dir = task_dir.with_name(f"{task_dir.name}.stale-{time.time_ns()}")
        try:
            task_dir.rename(stale_dir)
        except OSError:
            shutil.rmtree(task_dir, ignore_errors=True)
            if task_dir.exists():
                raise
        else:
            shutil.rmtree(stale_dir, ignore_errors=True)


def _make_writable_and_retry(function, path, exc_info):  # noqa: ANN001
    try:
        os.chmod(path, stat.S_IWRITE)
        function(path)
    except OSError:
        raise exc_info[1]
