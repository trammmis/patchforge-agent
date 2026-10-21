from __future__ import annotations

from pathlib import Path

from app.tools.path_guard import display_path, safe_join

DEFAULT_IGNORES = {".git", ".patchforge_runs", ".patchforge_data", ".patchforge_venv", "__pycache__", ".pytest_cache"}


def list_files(root: Path | str, path: str = ".") -> list[str]:
    root_path = Path(root).resolve()
    base = safe_join(root, path)
    files: list[str] = []
    for item in base.rglob("*"):
        rel = item.relative_to(root_path)
        if any(part in DEFAULT_IGNORES for part in rel.parts):
            continue
        try:
            is_file = item.is_file()
        except OSError:
            continue
        if is_file:
            files.append(display_path(root, item))
    return sorted(files)


def get_file_tree(root: Path | str, max_depth: int = 3) -> str:
    root_path = Path(root).resolve()
    lines: list[str] = []

    def walk(path: Path, depth: int) -> None:
        if depth > max_depth:
            return
        entries = sorted(
            [entry for entry in path.iterdir() if entry.name not in DEFAULT_IGNORES],
            key=lambda item: (not item.is_dir(), item.name.lower()),
        )
        for entry in entries:
            rel = display_path(root_path, entry)
            prefix = "  " * depth
            suffix = "/" if entry.is_dir() else ""
            lines.append(f"{prefix}{rel}{suffix}")
            if entry.is_dir():
                walk(entry, depth + 1)

    walk(root_path, 0)
    return "\n".join(lines)


def read_file(root: Path | str, path: str, start_line: int = 1, end_line: int | None = None) -> str:
    file_path = safe_join(root, path)
    lines = file_path.read_text(encoding="utf-8").splitlines()
    start = max(start_line, 1) - 1
    end = end_line if end_line is not None else len(lines)
    selected = lines[start:end]
    return "\n".join(selected) + ("\n" if selected else "")
