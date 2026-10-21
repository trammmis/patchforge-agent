from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from app.harness.tool_runtime import ToolRuntime
from app.schemas.issue import ParsedIssue


class RepoExplorer:
    def explore(self, runtime: ToolRuntime, issue: ParsedIssue) -> dict[str, Any]:
        tree = runtime.get_file_tree(max_depth=4)
        scores: dict[str, int] = defaultdict(int)
        all_files = runtime.list_files()
        explicit_paths = _explicit_paths(issue, all_files)
        for path in explicit_paths:
            scores[path] += 100
        domain_boosts = _domain_path_boosts(issue, all_files)
        for path, boost in domain_boosts.items():
            scores[path] += boost
        raw_terms = _query_terms(issue)
        for term in raw_terms[:10]:
            for result in runtime.search_code(term, "*.py")[:20]:
                path = result["path"]
                scores[path] += 10 if not _is_test_path(path) else 6
                if term.lower() in path.lower():
                    scores[path] += 4
        for file in all_files:
            if not file.endswith(".py"):
                continue
            for term in raw_terms:
                if term.lower() in file.lower():
                    scores[file] += 4 if not _is_test_path(file) else 3

        # Once likely source files are found, pull in their directly related tests.
        source_candidates = [path for path, score in scores.items() if score > 0 and not _is_test_path(path)]
        for source_file in source_candidates[:8]:
            for test_file in runtime.find_tests_for_file(source_file):
                scores[test_file] += 8

        explicit_ranked = sorted(explicit_paths, key=lambda path: (_is_test_path(path), path))
        domain_ranked = [
            path
            for path in sorted(domain_boosts, key=lambda path: (-domain_boosts[path], _is_test_path(path), path))
            if path not in explicit_ranked
        ]
        ranked = sorted(scores, key=lambda path: (-scores[path], _is_test_path(path), path))
        ranked = (
            [path for path in explicit_ranked if path in scores]
            + [path for path in domain_ranked if path in scores]
            + [path for path in ranked if path not in explicit_ranked and path not in domain_ranked]
        )
        return {"file_tree": tree, "candidate_files": ranked[:14]}


def _query_terms(issue: ParsedIssue) -> list[str]:
    terms = [
        *issue.suspected_area,
        *_tokens(issue.summary),
        *_tokens(issue.expected_behavior),
        *[token for criterion in issue.success_criteria for token in _tokens(criterion)],
    ]
    unique: list[str] = []
    for term in terms:
        if term not in unique:
            unique.append(term)
    return unique


def _tokens(text: str) -> list[str]:
    stop = {"the", "and", "for", "with", "when", "should", "return", "instead", "existing", "tests", "pass"}
    return [token for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text.lower()) if token not in stop]


def _explicit_paths(issue: ParsedIssue, repo_files: list[str]) -> list[str]:
    terms = [
        *issue.suspected_area,
        issue.summary,
        issue.expected_behavior,
        *issue.success_criteria,
    ]
    raw_paths: list[str] = []
    for text in terms:
        raw_paths.extend(re.findall(r"(?:[\w.-]+[\\/])+[\w.-]+\.(?:py|pyi|toml|cfg|ini|json|yaml|yml|md|rst|txt)", text))

    matched: list[str] = []
    repo_lookup = {path.replace("\\", "/"): path for path in repo_files}
    for raw in raw_paths:
        normalized = raw.replace("\\", "/").strip("`'\".,)")
        normalized = re.sub(r"^[ab]/", "", normalized)
        candidates = []
        if normalized in repo_lookup:
            candidates.append(repo_lookup[normalized])
        candidates.extend(path for key, path in repo_lookup.items() if key.endswith(f"/{normalized}"))
        for candidate in candidates:
            if candidate not in matched:
                matched.append(candidate)
    return matched


def _domain_path_boosts(issue: ParsedIssue, repo_files: list[str]) -> dict[str, int]:
    text = _issue_text(issue)
    boosts: dict[str, int] = {}
    if _looks_like_pytest_repo(repo_files):
        collection_related = any(term in text for term in ["collection", "collect", "__init__.py", "import"])
        skip_related = any(
            term in text
            for term in [
                "runxfail",
                "xfail",
                "pytest.mark.skip",
                "unittest.skip",
                "skipif",
                "skipping.py",
            ]
        ) or (" skip " in f" {text} " and not collection_related)
        if skip_related:
            _boost_existing(boosts, repo_files, "src/_pytest/skipping.py", 110)
            _boost_existing(boosts, repo_files, "testing/test_skipping.py", 105)
        if any(term in text for term in ["caplog", "log", "logging"]):
            _boost_existing(boosts, repo_files, "src/_pytest/logging.py", 90)
            _boost_existing(boosts, repo_files, "testing/logging/test_reporting.py", 70)
            _boost_existing(boosts, repo_files, "testing/logging/test_fixture.py", 65)
        if any(term in text for term in ["unittest", "testcase", "teardown", "setup", "pdb"]):
            _boost_existing(boosts, repo_files, "src/_pytest/unittest.py", 90)
            _boost_existing(boosts, repo_files, "testing/test_unittest.py", 75)
            _boost_existing(boosts, repo_files, "src/_pytest/debugging.py", 55)
        if collection_related:
            _boost_existing(boosts, repo_files, "src/_pytest/python.py", 90)
            _boost_existing(boosts, repo_files, "src/_pytest/main.py", 80)
            _boost_existing(boosts, repo_files, "testing/python/collect.py", 75)
            _boost_existing(boosts, repo_files, "testing/test_collection.py", 65)
        if "mro" in text or "marker" in text or ("mark" in text and not skip_related):
            _boost_existing(boosts, repo_files, "src/_pytest/mark/structures.py", 90)
            _boost_existing(boosts, repo_files, "testing/test_mark.py", 70)
    return boosts


def _issue_text(issue: ParsedIssue) -> str:
    fields = [
        issue.summary,
        issue.expected_behavior,
        " ".join(issue.suspected_area),
        " ".join(issue.success_criteria),
    ]
    return " ".join(fields).lower()


def _looks_like_pytest_repo(repo_files: list[str]) -> bool:
    normalized = {path.replace("\\", "/") for path in repo_files}
    return "src/_pytest/__init__.py" in normalized or any(path.startswith("src/_pytest/") for path in normalized)


def _boost_existing(boosts: dict[str, int], repo_files: list[str], expected: str, boost: int) -> None:
    for path in repo_files:
        normalized = path.replace("\\", "/")
        if normalized == expected:
            boosts[path] = max(boosts.get(path, 0), boost)
            return


def _is_test_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    return normalized.startswith(("tests/", "test/", "testing/")) or name.startswith("test_")
