from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

from oss_pr_compass.model import IssueSnapshot, RepositorySnapshot

MAINTAINER_ASSOCIATIONS = {"COLLABORATOR", "MEMBER", "OWNER"}


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, *, token: str | None = None, api_url: str = "https://api.github.com"):
        self.token = token
        self.api_url = api_url.rstrip("/")

    def fetch_snapshot(self, repository: str) -> RepositorySnapshot:
        owner, name = parse_repository(repository)
        repo = self.get_json(f"/repos/{owner}/{name}")
        root_entries = set(self._content_names(owner, name, ""))
        github_entries = {
            f".github/{entry}" for entry in self._content_names(owner, name, ".github")
        }
        workflow_entries = set(self._content_names(owner, name, ".github/workflows"))
        closed_prs = self.get_json(
            f"/repos/{owner}/{name}/pulls",
            {
                "state": "closed",
                "sort": "updated",
                "direction": "desc",
                "per_page": "100",
            },
        )
        open_prs = self.get_json(
            f"/repos/{owner}/{name}/pulls",
            {
                "state": "open",
                "per_page": "100",
            },
        )
        labels = self.get_json(f"/repos/{owner}/{name}/labels", {"per_page": "100"})
        open_issue_items = [
            issue
            for issue in self.get_json(
                f"/repos/{owner}/{name}/issues",
                {
                    "state": "open",
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": "100",
                },
            )
            if "pull_request" not in issue
        ]
        latest_maintainer_comments = self._latest_maintainer_issue_comments(owner, name)

        merged_prs = tuple(pr for pr in closed_prs if pr.get("merged_at"))

        return RepositorySnapshot(
            full_name=repo["full_name"],
            html_url=repo["html_url"],
            description=repo.get("description") or "",
            stars=int(repo.get("stargazers_count") or 0),
            forks=int(repo.get("forks_count") or 0),
            archived=bool(repo.get("archived")),
            pushed_at=parse_datetime(repo.get("pushed_at")),
            default_branch=repo.get("default_branch") or "main",
            license_spdx=(repo.get("license") or {}).get("spdx_id"),
            topics=tuple(repo.get("topics") or ()),
            root_entries=frozenset(root_entries | github_entries),
            workflow_entries=frozenset(workflow_entries),
            merged_prs=merged_prs,
            open_pr_count=len(open_prs),
            labels=tuple(
                label["name"] for label in labels if isinstance(label, dict) and "name" in label
            ),
            open_issues=tuple(
                IssueSnapshot(
                    number=int(issue.get("number") or 0),
                    labels=tuple(
                        label["name"]
                        for label in issue.get("labels", [])
                        if isinstance(label, dict) and "name" in label
                    ),
                    created_at=parse_datetime(issue.get("created_at")),
                    updated_at=parse_datetime(issue.get("updated_at")),
                    comment_count=int(issue.get("comments") or 0),
                    author_association=str(issue.get("author_association") or ""),
                    latest_maintainer_comment_at=latest_maintainer_comments.get(
                        int(issue.get("number") or 0)
                    ),
                )
                for issue in open_issue_items
            ),
        )

    def fetch_file_text(self, repository: str, path: str) -> str | None:
        owner, name = parse_repository(repository)
        try:
            content = self.get_json(f"/repos/{owner}/{name}/contents/{path}")
        except GitHubError as exc:
            if "returned 404" in str(exc):
                return None
            raise

        if not isinstance(content, dict) or content.get("encoding") != "base64":
            return None
        encoded = str(content.get("content") or "")
        try:
            return base64.b64decode(encoded).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise GitHubError(f"Could not decode {path} from {repository}: {exc}") from exc

    def get_json(self, path: str, params: dict[str, str] | None = None) -> Any:
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        request = urllib.request.Request(
            f"{self.api_url}{path}{query}",
            headers=self._headers(),
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            raise GitHubError(f"GitHub API returned {exc.code} for {path}: {message}") from exc
        except urllib.error.URLError as exc:
            raise GitHubError(f"Could not reach GitHub API: {exc.reason}") from exc

    def _content_names(self, owner: str, name: str, path: str) -> list[str]:
        try:
            content = self.get_json(f"/repos/{owner}/{name}/contents/{path}")
        except GitHubError as exc:
            if "returned 404" in str(exc):
                return []
            raise
        if not isinstance(content, list):
            return []
        return [entry["name"] for entry in content if "name" in entry]

    def _latest_maintainer_issue_comments(self, owner: str, name: str) -> dict[int, datetime]:
        comments = self.get_json(
            f"/repos/{owner}/{name}/issues/comments",
            {
                "sort": "updated",
                "direction": "desc",
                "per_page": "100",
            },
        )
        latest: dict[int, datetime] = {}
        for comment in comments:
            association = str(comment.get("author_association") or "").upper()
            if association not in MAINTAINER_ASSOCIATIONS:
                continue

            issue_number = _issue_number_from_url(comment.get("issue_url"))
            if issue_number is None:
                continue

            updated_at = parse_datetime(comment.get("updated_at") or comment.get("created_at"))
            if updated_at is None:
                continue
            current = latest.get(issue_number)
            if current is None or updated_at > current:
                latest[issue_number] = updated_at
        return latest

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "oss-pr-compass",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers


def parse_repository(value: str) -> tuple[str, str]:
    cleaned = value.removeprefix("https://github.com/").strip("/")
    parts = cleaned.split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise ValueError(
            "repository must look like 'owner/name' or 'https://github.com/owner/name'"
        )
    return parts[0], parts[1]


def parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _issue_number_from_url(value: object) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return int(value.rstrip("/").rsplit("/", 1)[-1])
    except ValueError:
        return None
