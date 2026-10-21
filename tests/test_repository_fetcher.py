from __future__ import annotations

import io
import json
import tempfile
import urllib.error
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.harness.workspace_manager import WorkspaceManager
from app.schemas.task import TaskInstance
from app.services.repository_fetcher import RepositoryFetchError
from app.services.repository_fetcher import fetch_repository
from app.services.repository_fetcher import parse_github_repo_url


class _FakeResponse:
    def __init__(self, payload: bytes | dict) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        if isinstance(self.payload, bytes):
            return self.payload
        return json.dumps(self.payload).encode("utf-8")


class RepositoryFetcherTests(unittest.TestCase):
    def test_parse_github_repo_url(self) -> None:
        self.assertEqual(
            parse_github_repo_url("https://github.com/omnigent-ai/omnigent.git"),
            {"owner": "omnigent-ai", "repo": "omnigent"},
        )

    def test_rejects_non_github_repo_url_without_git(self) -> None:
        with self.assertRaises(RepositoryFetchError):
            parse_github_repo_url("https://example.com/owner/repo")

    def test_fetch_repository_downloads_and_extracts_github_archive(self) -> None:
        archive_bytes = _zip_bytes({"owner-repo-sha/README.md": "hello\n", "owner-repo-sha/pkg/app.py": "x = 1\n"})

        def fake_urlopen(request, timeout=0):
            if request.full_url == "https://api.github.com/repos/owner/repo":
                return _FakeResponse({"default_branch": "main"})
            if request.full_url == "https://api.github.com/repos/owner/repo/zipball/main":
                return _FakeResponse(archive_bytes)
            raise AssertionError(request.full_url)

        with tempfile.TemporaryDirectory() as tmp, patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = fetch_repository("https://github.com/owner/repo", Path(tmp) / "repo")

            self.assertEqual(result["ref"], "main")
            self.assertEqual((Path(tmp) / "repo" / "README.md").read_text(encoding="utf-8"), "hello\n")
            self.assertEqual((Path(tmp) / "repo" / "pkg" / "app.py").read_text(encoding="utf-8"), "x = 1\n")

    def test_fetch_repository_retries_transient_network_error(self) -> None:
        archive_bytes = _zip_bytes({"owner-repo-sha/README.md": "hello\n"})
        responses = [urllib.error.URLError("SSL EOF"), _FakeResponse(archive_bytes)]

        with tempfile.TemporaryDirectory() as tmp, patch(
            "urllib.request.urlopen", side_effect=responses
        ), patch("app.services.repository_fetcher.time.sleep"):
            result = fetch_repository("https://github.com/owner/repo", Path(tmp) / "repo", ref="main")
            self.assertEqual(result["ref"], "main")
            self.assertEqual((Path(tmp) / "repo" / "README.md").read_text(encoding="utf-8"), "hello\n")

    def test_workspace_manager_can_prepare_remote_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_fetch(repo_url: str, destination: Path, ref: str | None = None):
                destination.mkdir(parents=True)
                (destination / "README.md").write_text("remote repo\n", encoding="utf-8")
                return {"repo_url": repo_url, "ref": ref or "main"}

            task = TaskInstance(
                task_id="remote_repo",
                repo_url="https://github.com/owner/repo",
                base_commit="main",
                issue_title="Remote task",
            )
            with patch("app.harness.workspace_manager.fetch_repository", side_effect=fake_fetch):
                paths = WorkspaceManager(root / "runs").prepare(task)

            self.assertTrue((paths["repo_dir"] / "README.md").exists())
            self.assertTrue((paths["baseline_dir"] / "README.md").exists())


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


if __name__ == "__main__":
    unittest.main()
