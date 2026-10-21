from __future__ import annotations

import time
import shutil
from pathlib import Path
from typing import Any, Callable

from app.harness.patch_manager import PatchManager
from app.harness.sandbox_runner import SandboxRunner
from app.harness.test_runner import TestRunner
from app.harness.trace_recorder import TraceRecorder
from app.tools import edit_tools, repo_tools, search_tools
from app.tools.path_guard import safe_join


class ToolRuntime:
    """Audited tool facade. Agents receive this object instead of raw OS access."""

    def __init__(
        self,
        workspace: Path | str,
        baseline: Path | str,
        trace: TraceRecorder,
        sandbox: SandboxRunner,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.baseline = Path(baseline).resolve()
        self.trace = trace
        self.sandbox = sandbox
        self.patch_manager = PatchManager(self.workspace, self.baseline)
        self.test_runner = TestRunner(sandbox)

    def list_files(self, path: str = ".") -> list[str]:
        return self._call("list_files", {"path": path}, lambda: repo_tools.list_files(self.workspace, path))

    def get_file_tree(self, max_depth: int = 3) -> str:
        return self._call("get_file_tree", {"max_depth": max_depth}, lambda: repo_tools.get_file_tree(self.workspace, max_depth))

    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> str:
        return self._call(
            "read_file",
            {"path": path, "start_line": start_line, "end_line": end_line},
            lambda: repo_tools.read_file(self.workspace, path, start_line, end_line),
        )

    def search_code(self, query: str, file_pattern: str | None = None) -> list[dict[str, Any]]:
        results = self._call(
            "search_code",
            {"query": query, "file_pattern": file_pattern},
            lambda: search_tools.search_code(self.workspace, query, file_pattern),
        )
        return [item.model_dump() for item in results]

    def search_symbol(self, symbol_name: str) -> list[dict[str, Any]]:
        results = self._call("search_symbol", {"symbol_name": symbol_name}, lambda: search_tools.search_symbol(self.workspace, symbol_name))
        return [item.model_dump() for item in results]

    def find_tests_for_file(self, source_file: str) -> list[str]:
        return self._call("find_tests_for_file", {"source_file": source_file}, lambda: search_tools.find_tests_for_file(self.workspace, source_file))

    def apply_patch(self, diff: str, check: bool = False) -> dict[str, Any]:
        result = self._call("apply_patch", {"diff": diff, "check": check}, lambda: edit_tools.apply_unified_diff(self.workspace, diff, check))
        return result.model_dump()

    def replace_range(self, file_path: str, start_line: int, end_line: int, new_content: str) -> dict[str, Any]:
        result = self._call(
            "replace_range",
            {"file_path": file_path, "start_line": start_line, "end_line": end_line},
            lambda: edit_tools.replace_range(self.workspace, file_path, start_line, end_line, new_content),
        )
        return result.model_dump()

    def run_tests(self, command: str, timeout: int = 120) -> dict[str, Any]:
        result = self._call("run_tests", {"command": command, "timeout": timeout}, lambda: self.test_runner.run_tests(command, timeout))
        return result.model_dump()

    def get_diff(self) -> str:
        return self._call("get_diff", {}, self.patch_manager.get_diff)

    def modified_files(self) -> list[str]:
        return self._call("modified_files", {}, self.patch_manager.modified_files)

    def revert_files(self, paths: list[str] | None = None) -> dict[str, Any]:
        return self._call("revert_files", {"paths": paths}, lambda: self._revert_files(paths))

    def _revert_files(self, paths: list[str] | None = None) -> dict[str, Any]:
        selected = paths or self.patch_manager.modified_files()
        restored: list[str] = []
        deleted: list[str] = []
        missing: list[str] = []
        cache_deleted: list[str] = []
        for file_path in selected:
            target = safe_join(self.workspace, file_path)
            source = safe_join(self.baseline, file_path)
            if source.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                restored.append(file_path)
            elif target.exists():
                target.unlink()
                deleted.append(file_path)
            else:
                missing.append(file_path)
            cache_deleted.extend(_remove_python_cache_files(self.workspace, file_path))
        return {
            "success": True,
            "message": "Files reverted to baseline",
            "changed_files": selected,
            "restored_files": restored,
            "deleted_files": deleted,
            "missing_files": missing,
            "cache_deleted": cache_deleted,
        }

    def _call(self, name: str, payload: dict[str, Any], fn: Callable[[], Any]) -> Any:
        started = time.perf_counter()
        try:
            result = fn()
            success = True
            error = None
            return result
        except Exception as exc:
            result = None
            success = False
            error = str(exc)
            raise
        finally:
            elapsed = int((time.perf_counter() - started) * 1000)
            serializable = result
            if hasattr(result, "model_dump"):
                serializable = result.model_dump()
            elif isinstance(result, list):
                serializable = [item.model_dump() if hasattr(item, "model_dump") else item for item in result]
            self.trace.record(
                "tool_call",
                {
                    "tool_name": name,
                    "tool_input": payload,
                    "tool_output": serializable,
                    "success": success,
                    "error": error,
                    "duration_ms": elapsed,
                },
            )


def _remove_python_cache_files(root: Path, file_path: str) -> list[str]:
    if not file_path.endswith(".py"):
        return []
    target = safe_join(root, file_path)
    cache_dir = target.parent / "__pycache__"
    if not cache_dir.exists():
        return []
    deleted: list[str] = []
    for cache_file in cache_dir.glob(f"{target.stem}.*.py[co]"):
        try:
            cache_file.unlink()
            deleted.append(str(cache_file.relative_to(root)).replace("\\", "/"))
        except OSError:
            continue
    return deleted
