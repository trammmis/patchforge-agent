from __future__ import annotations

import json
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from app.services.config import settings

REPOSITORY_FETCH_ATTEMPTS = 3
REPOSITORY_RETRY_BACKOFF_SECONDS = 1.0


class RepositoryFetchError(RuntimeError):
    """Raised when PatchForge cannot prepare a remote repository workspace."""


def parse_github_repo_url(repo_url: str) -> dict[str, str]:
    parsed = urllib.parse.urlparse(repo_url.strip())
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        raise RepositoryFetchError("当前无 git 命令可用；自动下载暂只支持 github.com 仓库 URL")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        raise RepositoryFetchError("GitHub 仓库 URL 应类似 https://github.com/owner/repo")
    owner = parts[0]
    repo = parts[1].removesuffix(".git")
    if not owner or not repo:
        raise RepositoryFetchError("GitHub 仓库 URL 缺少 owner 或 repo")
    return {"owner": owner, "repo": repo}


def fetch_repository(repo_url: str, destination: Path | str, ref: str | None = None) -> dict[str, str]:
    target = Path(destination)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    repo_ref = parse_github_repo_url(repo_url)
    owner = repo_ref["owner"]
    repo = repo_ref["repo"]
    resolved_ref = ref or _default_branch(owner, repo)
    archive_url = (
        f"https://api.github.com/repos/{owner}/{repo}/zipball/"
        f"{urllib.parse.quote(resolved_ref, safe='')}"
    )
    try:
        with _urlopen_with_retries(_request(archive_url), timeout=60) as response:
            archive_bytes = response.read()
    except urllib.error.HTTPError as exc:
        raise RepositoryFetchError(f"GitHub 仓库下载失败 {exc.code}: {_read_http_error(exc)}") from exc
    except urllib.error.URLError as exc:
        raise RepositoryFetchError(f"无法连接 GitHub 下载仓库: {exc.reason}") from exc

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        archive_path = tmp_path / "repo.zip"
        extract_dir = tmp_path / "extract"
        archive_path.write_bytes(archive_bytes)
        _safe_extract_zip(archive_path, extract_dir)
        roots = [path for path in extract_dir.iterdir() if path.is_dir()]
        if not roots:
            raise RepositoryFetchError("GitHub archive 中没有仓库目录")
        _copy_contents(roots[0], target)

    return {
        "repo_url": f"https://github.com/{owner}/{repo}",
        "owner": owner,
        "repo": repo,
        "ref": resolved_ref,
        "source": "github_zipball",
    }


def _default_branch(owner: str, repo: str) -> str:
    api_url = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        with _urlopen_with_retries(_request(api_url), timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise RepositoryFetchError(f"GitHub API 限流或无权限: {_read_http_error(exc)}") from exc
        raise RepositoryFetchError(f"GitHub 仓库信息读取失败 {exc.code}: {_read_http_error(exc)}") from exc
    except urllib.error.URLError as exc:
        raise RepositoryFetchError(f"无法连接 GitHub 读取仓库信息: {exc.reason}") from exc
    branch = str(payload.get("default_branch") or "").strip()
    if not branch:
        raise RepositoryFetchError("GitHub 仓库响应中没有 default_branch")
    return branch


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "PatchForgeAgent/0.1",
            **({"Authorization": f"Bearer {settings.github_token}"} if settings.github_token else {}),
        },
    )


def _urlopen_with_retries(request: urllib.request.Request, *, timeout: int):
    last_error: urllib.error.URLError | urllib.error.HTTPError | None = None
    for attempt in range(1, REPOSITORY_FETCH_ATTEMPTS + 1):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in {429, 500, 502, 503, 504} or attempt >= REPOSITORY_FETCH_ATTEMPTS:
                raise
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt >= REPOSITORY_FETCH_ATTEMPTS:
                raise
        time.sleep(REPOSITORY_RETRY_BACKOFF_SECONDS * attempt)
    if last_error is not None:
        raise last_error
    raise urllib.error.URLError("GitHub request failed")


def _safe_extract_zip(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise RepositoryFetchError(f"Archive contains unsafe path: {member.filename}") from exc
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _copy_contents(source: Path, target: Path) -> None:
    ignore = shutil.ignore_patterns(".git", ".patchforge_runs", ".patchforge_data", "__pycache__", ".pytest_cache")
    for item in source.iterdir():
        destination = target / item.name
        if item.is_dir():
            shutil.copytree(item, destination, ignore=ignore)
        else:
            shutil.copy2(item, destination)


def _read_http_error(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8"))
        return str(payload.get("message") or exc.reason)
    except Exception:
        return str(exc.reason)
