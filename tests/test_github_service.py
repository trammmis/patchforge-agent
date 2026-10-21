from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from app.services.github import GitHubIssueError, fetch_issue, parse_issue_url


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class GitHubServiceTests(unittest.TestCase):
    def test_parse_issue_url(self) -> None:
        result = parse_issue_url("https://github.com/octocat/Hello-World/issues/1347")

        self.assertEqual(result, {"owner": "octocat", "repo": "Hello-World", "number": 1347})

    def test_rejects_non_issue_url(self) -> None:
        with self.assertRaises(GitHubIssueError):
            parse_issue_url("https://github.com/octocat/Hello-World/pull/1")

    def test_fetch_issue_maps_github_payload(self) -> None:
        payload = {
            "title": "Bug: settings parser crashes",
            "body": "Broken entries should be skipped.",
            "html_url": "https://github.com/example/project/issues/5",
            "state": "open",
        }

        with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)) as urlopen:
            result = fetch_issue("https://github.com/example/project/issues/5")

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.github.com/repos/example/project/issues/5")
        self.assertEqual(result["issue_title"], "Bug: settings parser crashes")
        self.assertEqual(result["issue_body"], "Broken entries should be skipped.")
        self.assertEqual(result["repo_url"], "https://github.com/example/project")
        self.assertEqual(result["number"], "5")

    def test_rejects_pull_request_payload(self) -> None:
        with patch("urllib.request.urlopen", return_value=_FakeResponse({"title": "PR", "pull_request": {}})):
            with self.assertRaises(GitHubIssueError):
                fetch_issue("https://github.com/example/project/issues/5")


if __name__ == "__main__":
    unittest.main()
