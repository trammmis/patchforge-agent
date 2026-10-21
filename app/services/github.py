from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from app.services.config import settings


class GitHubIssueError(RuntimeError):
    """Raised when PatchForge cannot load a GitHub issue."""


def parse_issue_url(issue_url: str) -> dict[str, str | int]:
    parsed = urllib.parse.urlparse(issue_url.strip())
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        raise GitHubIssueError("Only github.com issue URLs are supported")
    match = re.fullmatch(r"/([^/]+)/([^/]+)/issues/(\d+)/?", parsed.path)
    if not match:
        raise GitHubIssueError("Expected a URL like https://github.com/owner/repo/issues/123")
    owner, repo, issue_number = match.groups()
    return {"owner": owner, "repo": repo, "number": int(issue_number)}


def fetch_issue(issue_url: str) -> dict[str, str]:
    issue_ref = parse_issue_url(issue_url)
    owner = str(issue_ref["owner"])
    repo = str(issue_ref["repo"])
    number = int(issue_ref["number"])
    api_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
    request = urllib.request.Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "PatchForgeAgent/0.1",
            **({"Authorization": f"Bearer {settings.github_token}"} if settings.github_token else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        message = _read_http_error(exc)
        raise GitHubIssueError(f"GitHub API returned {exc.code}: {message}") from exc
    except urllib.error.URLError as exc:
        raise GitHubIssueError(f"Could not reach GitHub: {exc.reason}") from exc

    if "pull_request" in payload:
        raise GitHubIssueError("This URL points to a pull request; PatchForge currently expects an issue URL")
    title = str(payload.get("title") or "").strip()
    if not title:
        raise GitHubIssueError("GitHub issue response did not include a title")
    html_url = str(payload.get("html_url") or issue_url)
    body = str(payload.get("body") or "").strip()
    repo_url = f"https://github.com/{owner}/{repo}"
    return {
        "issue_title": title,
        "issue_body": body,
        "issue_url": html_url,
        "repo_url": repo_url,
        "owner": owner,
        "repo": repo,
        "number": str(number),
        "state": str(payload.get("state") or ""),
    }


def _read_http_error(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8"))
        return str(payload.get("message") or exc.reason)
    except Exception:
        return str(exc.reason)
