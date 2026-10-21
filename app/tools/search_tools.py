from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from app.schemas.base import BaseSchema
from app.tools.path_guard import display_path, safe_join

DEFAULT_IGNORES = {".git", ".patchforge_runs", ".patchforge_data", ".patchforge_venv", "__pycache__", ".pytest_cache"}


class SearchResult(BaseSchema):
    path: str
    line_number: int
    line: str


def search_code(root: Path | str, query: str, file_pattern: str | None = None) -> list[SearchResult]:
    root_path = Path(root).resolve()
    if not query.strip():
        return []
    rg = shutil.which("rg")
    if rg:
        command = [rg, "--line-number", "--no-heading", query]
        for ignored in DEFAULT_IGNORES:
            command.extend(["--glob", f"!{ignored}/**"])
        if file_pattern:
            command.extend(["--glob", file_pattern])
        proc = subprocess.run(
            command,
            cwd=root_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        results: list[SearchResult] = []
        for line in proc.stdout.splitlines():
            parts = line.split(":", 2)
            if len(parts) == 3 and parts[1].isdigit():
                results.append(SearchResult(path=parts[0].replace("\\", "/"), line_number=int(parts[1]), line=parts[2]))
        return results

    results = []
    for file_path in root_path.rglob("*"):
        if any(part in DEFAULT_IGNORES for part in file_path.parts):
            continue
        if not file_path.is_file():
            continue
        if file_pattern and file_path.match(file_pattern) is False:
            continue
        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for idx, line in enumerate(lines, 1):
            if query.lower() in line.lower():
                results.append(SearchResult(path=display_path(root_path, file_path), line_number=idx, line=line))
    return results


def search_symbol(root: Path | str, symbol_name: str) -> list[SearchResult]:
    return search_code(root, f"def {symbol_name}") + search_code(root, f"class {symbol_name}")


def find_tests_for_file(root: Path | str, source_file: str) -> list[str]:
    root_path = Path(root).resolve()
    safe_join(root_path, source_file)
    stem = Path(source_file).stem
    candidates = []
    for path in root_path.rglob("test*.py"):
        if any(part in DEFAULT_IGNORES for part in path.parts):
            continue
        if stem in path.name or stem in path.read_text(encoding="utf-8", errors="ignore"):
            candidates.append(display_path(root_path, path))
    return sorted(set(candidates))
