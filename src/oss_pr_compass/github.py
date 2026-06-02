from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from oss_pr_compass.model import IssueSnapshot, RepositorySnapshot

MAINTAINER_ASSOCIATIONS = {"COLLABORATOR", "MEMBER", "OWNER"}
CLOSED_PULL_REQUEST_PAGE_LIMIT = 5
ISSUE_COMMENT_PAGE_LIMIT = 3
OPEN_ISSUE_SAMPLE_PAGE_LIMIT = 3
REPOSITORY_LABEL_PAGE_LIMIT = 10


class GitHubError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubResponse:
    payload: Any
    headers: dict[str, str]


class GitHubClient:
    def __init__(self, *, token: str | None = None, api_url: str = "https://api.github.com"):
        self.token = token
        self.api_url = api_url.rstrip("/")

    def fetch_snapshot(self, repository: str) -> RepositorySnapshot:
        owner, name = parse_repository(repository)
        repo = self.get_json(f"/repos/{owner}/{name}")
        if not isinstance(repo, dict):
            raise GitHubError(f"Expected repository object for /repos/{owner}/{name}.")

        root_entries = set(self._content_names(owner, name, ""))
        github_entries = {
            f".github/{entry}" for entry in self._content_names(owner, name, ".github")
        }
        workflow_entries = set(self._content_names(owner, name, ".github/workflows"))
        closed_prs = self._get_list(
            f"/repos/{owner}/{name}/pulls",
            {
                "state": "closed",
                "sort": "updated",
                "direction": "desc",
                "per_page": "100",
            },
            "closed pull requests",
            max_pages=CLOSED_PULL_REQUEST_PAGE_LIMIT,
            allow_truncated=True,
        )
        labels = self._get_list(
            f"/repos/{owner}/{name}/labels",
            {"per_page": "100"},
            "repository labels",
            max_pages=REPOSITORY_LABEL_PAGE_LIMIT,
            allow_truncated=True,
        )
        open_issue_items = [
            issue
            for issue in self._get_list(
                f"/repos/{owner}/{name}/issues",
                {
                    "state": "open",
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": "100",
                },
                "open issues",
                max_pages=OPEN_ISSUE_SAMPLE_PAGE_LIMIT,
                allow_truncated=True,
            )
            if "pull_request" not in issue
        ]
        latest_maintainer_comments = self._latest_maintainer_issue_comments(owner, name)
        open_pr_count = self._search_issue_count(owner, name, "pr")
        open_issue_count = self._search_issue_count(owner, name, "issue")

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
            open_pr_count=open_pr_count,
            labels=tuple(
                label["name"] for label in labels if isinstance(label, dict) and "name" in label
            ),
            open_issues=tuple(
                IssueSnapshot(
                    number=int(issue.get("number") or 0),
                    labels=_issue_label_names(issue),
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
            open_issue_count=open_issue_count,
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
        return self.get_json_response(path, params).payload

    def get_json_response(self, path: str, params: dict[str, str] | None = None) -> GitHubResponse:
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        return self._request_json_url(f"{self.api_url}{path}{query}", path)

    def get_paginated_json(
        self,
        path: str,
        params: dict[str, str] | None = None,
        *,
        description: str,
        max_pages: int,
        allow_truncated: bool = False,
    ) -> list[dict[str, Any]]:
        if max_pages <= 0:
            raise ValueError("max_pages must be greater than zero")

        items: list[dict[str, Any]] = []
        response = self.get_json_response(path, params)
        page = 1

        while True:
            items.extend(_validate_list_payload(response.payload, path, description))

            next_url = _next_link(response.headers)
            if next_url is None:
                return items
            if page >= max_pages:
                if allow_truncated:
                    return items
                raise GitHubError(
                    f"GitHub pagination for {description} from {path} exceeded {max_pages} pages."
                )

            page += 1
            response = self._request_json_url(next_url, path)

    def _request_json_url(self, url: str, display_path: str) -> GitHubResponse:
        request = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8")
                payload = json.loads(body) if body else None
                return GitHubResponse(payload=payload, headers=_normalize_headers(response.headers))
        except urllib.error.HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            context = _rate_limit_context(exc.headers)
            if context:
                message = f"{message} {context}"
            raise GitHubError(
                f"GitHub API returned {exc.code} for {display_path}: {message}"
            ) from exc
        except urllib.error.URLError as exc:
            raise GitHubError(f"Could not reach GitHub API: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise GitHubError(
                f"GitHub API returned invalid JSON for {display_path}: {exc.msg}"
            ) from exc

    def _get_list(
        self,
        path: str,
        params: dict[str, str] | None,
        description: str,
        *,
        max_pages: int = 1,
        allow_truncated: bool = False,
    ) -> list[dict[str, Any]]:
        return self.get_paginated_json(
            path,
            params,
            description=description,
            max_pages=max_pages,
            allow_truncated=allow_truncated,
        )

    def _content_names(self, owner: str, name: str, path: str) -> list[str]:
        try:
            content = self.get_json(f"/repos/{owner}/{name}/contents/{path}")
        except GitHubError as exc:
            if "returned 404" in str(exc):
                return []
            raise
        if not isinstance(content, list):
            return []
        return [entry["name"] for entry in content if isinstance(entry, dict) and "name" in entry]

    def _latest_maintainer_issue_comments(self, owner: str, name: str) -> dict[int, datetime]:
        comments = self._get_list(
            f"/repos/{owner}/{name}/issues/comments",
            {
                "sort": "updated",
                "direction": "desc",
                "per_page": "100",
            },
            "issue comments",
            max_pages=ISSUE_COMMENT_PAGE_LIMIT,
            allow_truncated=True,
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

    def _search_issue_count(self, owner: str, name: str, item_type: str) -> int:
        query = f"repo:{owner}/{name} type:{item_type} state:open"
        payload = self.get_json(
            "/search/issues",
            {
                "q": query,
                "per_page": "1",
            },
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("total_count"), int):
            raise GitHubError(f"Expected search count for open {item_type}s from /search/issues.")
        return int(payload["total_count"])

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
    cleaned = value.strip()
    if cleaned.startswith("https://github.com/"):
        cleaned = cleaned.removeprefix("https://github.com/")
    elif "://" in cleaned or cleaned.startswith("git@"):
        raise ValueError(
            "repository must look like 'owner/name' or 'https://github.com/owner/name'"
        )

    cleaned = cleaned.strip("/")
    parts = cleaned.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
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


def _issue_label_names(issue: dict[str, Any]) -> tuple[str, ...]:
    labels = issue.get("labels", [])
    if not isinstance(labels, list):
        return ()
    return tuple(label["name"] for label in labels if isinstance(label, dict) and "name" in label)


def _validate_list_payload(
    payload: object,
    path: str,
    description: str,
) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise GitHubError(f"Expected a list for {description} from {path}.")

    bad_item = next((item for item in payload if not isinstance(item, dict)), None)
    if bad_item is not None:
        raise GitHubError(f"Expected {description} items from {path} to be objects.")
    return payload


def _normalize_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    if headers is None:
        return {}
    return {key.lower(): value for key, value in headers.items()}


def _next_link(headers: Mapping[str, str]) -> str | None:
    link_header = headers.get("link", "")
    for part in link_header.split(","):
        section = part.strip()
        if 'rel="next"' not in section:
            continue
        start = section.find("<")
        end = section.find(">", start + 1)
        if start != -1 and end != -1:
            return section[start + 1 : end]
    return None


def _rate_limit_context(headers: Mapping[str, str] | None) -> str:
    normalized = _normalize_headers(headers)
    details = []
    for header, label in (
        ("retry-after", "Retry-After"),
        ("x-ratelimit-remaining", "X-RateLimit-Remaining"),
        ("x-ratelimit-reset", "X-RateLimit-Reset"),
    ):
        value = normalized.get(header)
        if value:
            details.append(f"{label}: {value}")

    if not details:
        return ""
    return f"Rate limit details: {'; '.join(details)}."
