from __future__ import annotations


def build_context(file_tree: str, candidate_files: list[str]) -> str:
    return "\n".join(["# File tree", file_tree, "", "# Candidate files", *candidate_files])

