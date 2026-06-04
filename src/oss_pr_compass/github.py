from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from oss_pr_compass.model import IssueSnapshot, RepositorySnapshot

MAINTAINER_ASSOCIATIONS = {"COLLABORATOR", "MEMBER", "OWNER"}
API_REQUEST_ATTEMPTS = 3
BASE_RETRY_DELAY_SECONDS = 0.25
CLOSED_PULL_REQUEST_PAGE_LIMIT = 5
ISSUE_COMMENT_PAGE_LIMIT = 3
MAX_RETRY_AFTER_SECONDS = 5.0
MERGED_PULL_REQUEST_SEARCH_PAGE_LIMIT = 10
OPEN_ISSUE_SAMPLE_PAGE_LIMIT = 3
REPOSITORY_LABEL_PAGE_LIMIT = 10
RETRY_AFTER_HTTP_STATUS_CODES = {403, 429}
TRANSIENT_HTTP_STATUS_CODES = {502, 503, 504}
PULL_REQUEST_TEMPLATE_DIRECTORIES = (
    "PULL_REQUEST_TEMPLATE",
    ".github/PULL_REQUEST_TEMPLATE",
    "docs/PULL_REQUEST_TEMPLATE",
)
REPOSITORY_ERROR_MESSAGE = (
    "repository must look like 'owner/name' or 'https://github.com/owner/name'"
)


class GitHubError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubResponse:
    payload: Any
    headers: dict[str, str]


@dataclass(frozen=True)
class MergedPullRequestCounts:
    total: int
    external: int
    maintainer: int
    bot: int


@dataclass(frozen=True)
class IssueCommentActivity:
    latest_maintainer: dict[int, datetime]
    latest_external: dict[int, datetime]


class GitHubClient:
    def __init__(
        self,
        *,
        token: str | None = None,
        api_url: str = "https://api.github.com",
        sleep: Callable[[float], None] | None = None,
    ):
        self.token = token
        self.api_url, self._api_origin = _normalize_api_url(api_url)
        self._sleep = time.sleep if sleep is None else sleep

    def fetch_snapshot(
        self,
        repository: str,
        *,
        merged_since: datetime | None = None,
    ) -> RepositorySnapshot:
        owner, name = parse_repository(repository)
        repo = self.get_json(f"/repos/{owner}/{name}")
        if not isinstance(repo, dict):
            raise GitHubError(f"Expected repository object for /repos/{owner}/{name}.")
        owner, name = _canonical_repository(repo, requested_owner=owner, requested_name=name)

        root_entries = set(self._content_names(owner, name, ""))
        github_entries = {
            f".github/{entry}" for entry in self._content_names(owner, name, ".github")
        }
        docs_entries = {f"docs/{entry}" for entry in self._content_names(owner, name, "docs")}
        pull_request_template_entries = self._pull_request_template_entries(owner, name)
        workflow_entries = set(self._content_names(owner, name, ".github/workflows"))
        if merged_since is None:
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
            merged_pr_count = None
            external_merged_pr_count = None
            maintainer_merged_pr_count = None
            bot_merged_pr_count = None
        else:
            closed_prs = []
            merged_counts = self._merged_pull_request_counts(owner, name, merged_since)
            merged_pr_count = merged_counts.total
            external_merged_pr_count = merged_counts.external
            maintainer_merged_pr_count = merged_counts.maintainer
            bot_merged_pr_count = merged_counts.bot
        labels = self._get_list(
            f"/repos/{owner}/{name}/labels",
            {"per_page": "100"},
            "repository labels",
            max_pages=REPOSITORY_LABEL_PAGE_LIMIT,
            allow_truncated=True,
        )
        open_issue_count, open_issue_items = self._open_issue_items(owner, name, order="desc")
        _, oldest_open_issue_items = self._open_issue_items(owner, name, order="asc")
        issue_comment_activity = self._issue_comment_activity(owner, name)
        open_pr_count = self._search_issue_count(
            owner,
            name,
            "pr",
            extra_qualifiers=("draft:false",),
            description="ready-for-review open PRs",
        )
        draft_open_pr_count = self._search_issue_count(
            owner,
            name,
            "pr",
            extra_qualifiers=("draft:true",),
            description="draft open PRs",
        )

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
            root_entries=frozenset(
                root_entries | github_entries | docs_entries | pull_request_template_entries
            ),
            workflow_entries=frozenset(workflow_entries),
            merged_prs=merged_prs,
            open_pr_count=open_pr_count,
            merged_pr_count=merged_pr_count,
            external_merged_pr_count=external_merged_pr_count,
            maintainer_merged_pr_count=maintainer_merged_pr_count,
            bot_merged_pr_count=bot_merged_pr_count,
            draft_open_pr_count=draft_open_pr_count,
            labels=tuple(
                label["name"] for label in labels if isinstance(label, dict) and "name" in label
            ),
            open_issues=_issue_snapshots(open_issue_items, issue_comment_activity),
            oldest_open_issues=_issue_snapshots(
                oldest_open_issue_items,
                issue_comment_activity,
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
            if _url_origin(next_url) != self._api_origin:
                raise GitHubError(
                    f"Refusing to follow off-origin GitHub pagination link for {path}."
                )
            response = self._request_json_url(next_url, path)

    def _request_json_url(self, url: str, display_path: str) -> GitHubResponse:
        for attempt in range(API_REQUEST_ATTEMPTS):
            request = urllib.request.Request(url, headers=self._headers())
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    body = response.read().decode("utf-8")
                    payload = json.loads(body) if body else None
                    return GitHubResponse(
                        payload=payload,
                        headers=_normalize_headers(response.headers),
                    )
            except urllib.error.HTTPError as exc:
                retry_after = _retry_after_seconds(exc.headers)
                error = _github_http_error(exc, display_path)
                if (
                    _should_retry_http_error(exc.code, retry_after)
                    and attempt < API_REQUEST_ATTEMPTS - 1
                ):
                    self._sleep(_retry_delay_seconds(attempt, retry_after))
                    continue
                raise error from exc
            except urllib.error.URLError as exc:
                error = GitHubError(f"Could not reach GitHub API: {exc.reason}")
                if attempt < API_REQUEST_ATTEMPTS - 1:
                    self._sleep(_retry_delay_seconds(attempt, None))
                    continue
                raise error from exc
            except TimeoutError as exc:
                error = GitHubError(f"GitHub API timed out for {display_path}: {exc}")
                if attempt < API_REQUEST_ATTEMPTS - 1:
                    self._sleep(_retry_delay_seconds(attempt, None))
                    continue
                raise error from exc
            except json.JSONDecodeError as exc:
                raise GitHubError(
                    f"GitHub API returned invalid JSON for {display_path}: {exc.msg}"
                ) from exc
        raise GitHubError(f"GitHub API request failed for {display_path}.")

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
        content = self._content_items(owner, name, path)
        return [entry["name"] for entry in content if isinstance(entry.get("name"), str)]

    def _content_file_names(self, owner: str, name: str, path: str) -> list[str]:
        content = self._content_items(owner, name, path)
        return [
            entry["name"]
            for entry in content
            if isinstance(entry.get("name"), str) and entry.get("type") == "file"
        ]

    def _content_items(self, owner: str, name: str, path: str) -> list[dict[str, Any]]:
        try:
            content = self.get_json(f"/repos/{owner}/{name}/contents/{path}")
        except GitHubError as exc:
            if "returned 404" in str(exc):
                return []
            raise
        if not isinstance(content, list):
            return []
        return [entry for entry in content if isinstance(entry, dict)]

    def _pull_request_template_entries(self, owner: str, name: str) -> set[str]:
        entries: set[str] = set()
        for directory in PULL_REQUEST_TEMPLATE_DIRECTORIES:
            entries.update(
                f"{directory}/{entry}" for entry in self._content_file_names(owner, name, directory)
            )
        return entries

    def _issue_comment_activity(self, owner: str, name: str) -> IssueCommentActivity:
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
        latest_maintainer: dict[int, datetime] = {}
        latest_external: dict[int, datetime] = {}
        for comment in comments:
            association = str(comment.get("author_association") or "").upper()
            issue_number = _issue_number_from_url(comment.get("issue_url"))
            if issue_number is None:
                continue

            updated_at = parse_datetime(comment.get("updated_at") or comment.get("created_at"))
            if updated_at is None:
                continue
            latest = (
                latest_maintainer if association in MAINTAINER_ASSOCIATIONS else latest_external
            )
            current = latest.get(issue_number)
            if current is None or updated_at > current:
                latest[issue_number] = updated_at
        return IssueCommentActivity(
            latest_maintainer=latest_maintainer,
            latest_external=latest_external,
        )

    def _search_issue_count(
        self,
        owner: str,
        name: str,
        item_type: str,
        *,
        extra_qualifiers: tuple[str, ...] = (),
        description: str | None = None,
    ) -> int:
        query = f"repo:{owner}/{name} type:{item_type} state:open"
        if extra_qualifiers:
            query = f"{query} {' '.join(extra_qualifiers)}"
        count_description = description or f"open {item_type}s"
        payload = self.get_json(
            "/search/issues",
            {
                "q": query,
                "per_page": "1",
            },
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("total_count"), int):
            raise GitHubError(f"Expected search count for {count_description} from /search/issues.")
        if payload.get("incomplete_results") is True:
            raise GitHubError(
                "GitHub Search returned incomplete results "
                f"for {count_description} from /search/issues."
            )
        return int(payload["total_count"])

    def _open_issue_items(
        self,
        owner: str,
        name: str,
        *,
        order: str,
    ) -> tuple[int, list[dict[str, Any]]]:
        total_count, items = self._search_issue_items(
            f"repo:{owner}/{name} type:issue state:open",
            "open issues",
            max_pages=OPEN_ISSUE_SAMPLE_PAGE_LIMIT,
            allow_truncated=True,
            extra_params={"sort": "updated", "order": order},
        )
        return total_count, [item for item in items if "pull_request" not in item]

    def _merged_pull_request_counts(
        self, owner: str, name: str, merged_since: datetime
    ) -> MergedPullRequestCounts:
        merged_since_date = merged_since.date().isoformat()
        query = f"repo:{owner}/{name} is:pr is:merged merged:>={merged_since_date}"
        total_count, items = self._search_issue_items(
            query,
            "merged pull requests",
            max_pages=MERGED_PULL_REQUEST_SEARCH_PAGE_LIMIT,
        )
        if len(items) < total_count:
            raise GitHubError(
                "GitHub Search did not return every merged pull request needed "
                "to classify external contribution activity."
            )

        external = 0
        maintainer = 0
        bot = 0
        for item in items:
            if _is_bot_user(item.get("user")):
                bot += 1
            elif str(item.get("author_association") or "").upper() in MAINTAINER_ASSOCIATIONS:
                maintainer += 1
            else:
                external += 1
        return MergedPullRequestCounts(
            total=total_count,
            external=external,
            maintainer=maintainer,
            bot=bot,
        )

    def _search_issue_items(
        self,
        query: str,
        description: str,
        *,
        max_pages: int,
        allow_truncated: bool = False,
        extra_params: dict[str, str] | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        if max_pages <= 0:
            raise ValueError("max_pages must be greater than zero")

        items: list[dict[str, Any]] = []
        params = {
            "q": query,
            "per_page": "100",
        }
        if extra_params:
            params.update(extra_params)
        response = self.get_json_response(
            "/search/issues",
            params,
        )
        page = 1
        total_count: int | None = None

        while True:
            payload = response.payload
            if not isinstance(payload, dict) or not isinstance(payload.get("total_count"), int):
                raise GitHubError(f"Expected search count for {description} from /search/issues.")
            if payload.get("incomplete_results") is True:
                raise GitHubError(
                    f"GitHub Search returned incomplete results for {description} "
                    "from /search/issues."
                )
            if not isinstance(payload.get("items"), list):
                raise GitHubError(f"Expected search items for {description} from /search/issues.")

            if total_count is None:
                total_count = int(payload["total_count"])
            items.extend(_validate_list_payload(payload["items"], "/search/issues", description))

            next_url = _next_link(response.headers)
            if next_url is None:
                return total_count, items
            if len(items) >= total_count:
                return total_count, items[:total_count]
            if page >= max_pages:
                if allow_truncated:
                    return total_count, items
                raise GitHubError(
                    f"GitHub Search pagination for {description} exceeded {max_pages} pages."
                )

            page += 1
            if _url_origin(next_url) != self._api_origin:
                raise GitHubError(
                    "Refusing to follow off-origin GitHub search pagination "
                    f"link for {description}."
                )
            response = self._request_json_url(next_url, "/search/issues")

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
    parsed = urllib.parse.urlparse(cleaned)
    if parsed.scheme or parsed.netloc:
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or parsed.username
            or parsed.password
        ):
            raise ValueError(REPOSITORY_ERROR_MESSAGE)
        return _parse_repository_path(parsed.path)
    if "://" in cleaned or cleaned.startswith("git@") or "?" in cleaned or "#" in cleaned:
        raise ValueError(REPOSITORY_ERROR_MESSAGE)
    return _parse_repository_path(cleaned)


def parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_repository_path(value: str) -> tuple[str, str]:
    parts = value.strip("/").split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(REPOSITORY_ERROR_MESSAGE)

    repo_name = parts[1].removesuffix(".git")
    if not repo_name:
        raise ValueError(REPOSITORY_ERROR_MESSAGE)
    return parts[0], repo_name


def _canonical_repository(
    repo: dict[str, Any], *, requested_owner: str, requested_name: str
) -> tuple[str, str]:
    full_name = repo.get("full_name")
    if not isinstance(full_name, str) or not full_name:
        raise GitHubError(
            f"Expected repository full_name for /repos/{requested_owner}/{requested_name}."
        )
    try:
        return parse_repository(full_name)
    except ValueError as exc:
        raise GitHubError(
            "GitHub API returned an invalid repository full_name "
            f"for /repos/{requested_owner}/{requested_name}."
        ) from exc


def _issue_number_from_url(value: object) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return int(value.rstrip("/").rsplit("/", 1)[-1])
    except ValueError:
        return None


def _issue_snapshots(
    items: list[dict[str, Any]],
    issue_comment_activity: IssueCommentActivity,
) -> tuple[IssueSnapshot, ...]:
    snapshots: list[IssueSnapshot] = []
    for issue in items:
        number = int(issue.get("number") or 0)
        created_at = parse_datetime(issue.get("created_at"))
        author_association = str(issue.get("author_association") or "")
        latest_external_comment_at = issue_comment_activity.latest_external.get(number)
        snapshots.append(
            IssueSnapshot(
                number=number,
                labels=_issue_label_names(issue),
                created_at=created_at,
                updated_at=parse_datetime(issue.get("updated_at")),
                comment_count=int(issue.get("comments") or 0),
                author_association=author_association,
                latest_maintainer_comment_at=issue_comment_activity.latest_maintainer.get(number),
                latest_external_activity_at=_latest_external_activity_at(
                    created_at,
                    author_association,
                    latest_external_comment_at,
                ),
            )
        )
    return tuple(snapshots)


def _latest_external_activity_at(
    created_at: datetime | None,
    author_association: str,
    latest_external_comment_at: datetime | None,
) -> datetime | None:
    candidates = []
    if author_association.upper() not in MAINTAINER_ASSOCIATIONS and created_at is not None:
        candidates.append(created_at)
    if latest_external_comment_at is not None:
        candidates.append(latest_external_comment_at)
    return max(candidates) if candidates else None


def _issue_label_names(issue: dict[str, Any]) -> tuple[str, ...]:
    labels = issue.get("labels", [])
    if not isinstance(labels, list):
        return ()
    return tuple(label["name"] for label in labels if isinstance(label, dict) and "name" in label)


def _is_bot_user(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    user_type = str(value.get("type") or "").casefold()
    login = str(value.get("login") or "").casefold()
    return user_type == "bot" or login.endswith("[bot]")


def _normalize_api_url(value: str) -> tuple[str, tuple[str, str, int]]:
    cleaned = value.strip().rstrip("/")
    parsed = urllib.parse.urlparse(cleaned)
    if parsed.scheme != "https" or not parsed.hostname:
        raise GitHubError("--api-url must be an absolute HTTPS URL.")
    if parsed.username or parsed.password:
        raise GitHubError("--api-url must not include credentials.")
    if parsed.params or parsed.query or parsed.fragment:
        raise GitHubError("--api-url must not include params, query, or fragment components.")
    return cleaned, _origin_from_parsed_url(parsed)


def _url_origin(value: str) -> tuple[str, str, int]:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise GitHubError("GitHub pagination link must be an absolute HTTPS URL.")
    if parsed.username or parsed.password:
        raise GitHubError("GitHub pagination link must not include credentials.")
    return _origin_from_parsed_url(parsed)


def _origin_from_parsed_url(parsed: urllib.parse.ParseResult) -> tuple[str, str, int]:
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise GitHubError("GitHub API URL must include a valid port.") from exc
    return parsed.scheme, parsed.hostname.lower(), port


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


def _should_retry_http_error(code: int, retry_after: float | None) -> bool:
    if code in TRANSIENT_HTTP_STATUS_CODES:
        return True
    return code in RETRY_AFTER_HTTP_STATUS_CODES and retry_after is not None


def _retry_after_seconds(headers: Mapping[str, str] | None) -> float | None:
    value = _normalize_headers(headers).get("retry-after")
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    if not 0 <= seconds <= MAX_RETRY_AFTER_SECONDS:
        return None
    return seconds


def _retry_delay_seconds(attempt: int, retry_after: float | None) -> float:
    if retry_after is not None:
        return retry_after
    return min(BASE_RETRY_DELAY_SECONDS * (2**attempt), MAX_RETRY_AFTER_SECONDS)


def _github_http_error(exc: urllib.error.HTTPError, display_path: str) -> GitHubError:
    message = exc.read().decode("utf-8", errors="replace")
    context = _rate_limit_context(exc.headers)
    if context:
        message = f"{message} {context}"
    return GitHubError(f"GitHub API returned {exc.code} for {display_path}: {message}")


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
