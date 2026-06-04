from __future__ import annotations

import io
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pytest

from oss_pr_compass.github import GitHubClient, GitHubError, GitHubResponse, parse_repository


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("owner/repo", ("owner", "repo")),
        ("owner/repo/", ("owner", "repo")),
        ("https://github.com/owner/repo", ("owner", "repo")),
        ("https://github.com/owner/repo/", ("owner", "repo")),
        ("https://github.com/owner/repo?tab=readme", ("owner", "repo")),
        ("https://github.com/owner/repo#readme", ("owner", "repo")),
        ("https://github.com/owner/repo.git", ("owner", "repo")),
    ),
)
def test_parse_repository_accepts_repository_identifiers(
    value: str, expected: tuple[str, str]
) -> None:
    assert parse_repository(value) == expected


@pytest.mark.parametrize(
    "value",
    (
        "owner",
        "owner/",
        "owner/repo/extra",
        "https://github.com/owner",
        "https://github.com/owner/repo/issues/1",
        "https://github.com/owner/repo/pull/2",
        "https://github.com/owner/repo/tree/main",
        "https://github.com/owner/repo/blob/main/README.md",
        "https://example.com/owner/repo",
        "git@github.com:owner/repo.git",
    ),
)
def test_parse_repository_rejects_non_repository_identifiers(value: str) -> None:
    with pytest.raises(ValueError, match="repository must look like"):
        parse_repository(value)


def test_fetch_snapshot_uses_search_counts_for_open_queues() -> None:
    client = FakeGitHubClient(_base_payloads())

    snapshot = client.fetch_snapshot("owner/repo")

    assert snapshot.open_pr_count == 123
    assert snapshot.open_issue_count == 456


def test_fetch_snapshot_uses_canonical_repository_full_name_after_redirect() -> None:
    payloads = _base_payloads(owner="new", name="repo")
    payloads["/repos/old/repo"] = {
        "full_name": "new/repo",
        "html_url": "https://github.com/new/repo",
        "description": "Example",
        "stargazers_count": 1,
        "forks_count": 2,
        "archived": False,
        "pushed_at": "2026-06-01T00:00:00Z",
        "default_branch": "main",
        "license": {"spdx_id": "MIT"},
        "topics": ["python"],
    }
    client = RecordingGitHubClient(payloads)

    snapshot = client.fetch_snapshot("old/repo")

    assert snapshot.full_name == "new/repo"
    assert client.paths == [
        "/repos/old/repo",
        "/repos/new/repo/contents/",
        "/repos/new/repo/contents/.github",
        "/repos/new/repo/contents/docs",
        "/repos/new/repo/contents/PULL_REQUEST_TEMPLATE",
        "/repos/new/repo/contents/.github/PULL_REQUEST_TEMPLATE",
        "/repos/new/repo/contents/docs/PULL_REQUEST_TEMPLATE",
        "/repos/new/repo/contents/.github/workflows",
        "/repos/new/repo/pulls",
        "/repos/new/repo/labels",
        "/search/issues",
        "/repos/new/repo/issues/comments",
        "/search/issues",
        "/search/issues",
    ]
    assert client.search_queries == [
        "repo:new/repo type:issue state:open",
        "repo:new/repo type:pr state:open draft:false",
        "repo:new/repo type:pr state:open draft:true",
    ]


def test_fetch_snapshot_counts_merged_prs_with_lookback_search_not_closed_pr_cap() -> None:
    payloads = _base_payloads()
    payloads["/repos/owner/repo/pulls"] = [
        {"merged_at": "2024-01-01T00:00:00Z", "updated_at": "2026-06-01T00:00:00Z"}
        for _ in range(500)
    ]
    client = RecordingGitHubClient(payloads)

    snapshot = client.fetch_snapshot(
        "owner/repo",
        merged_since=datetime(2026, 3, 4, tzinfo=timezone.utc),
    )

    assert snapshot.merged_pr_count == 24
    assert snapshot.merged_prs == ()
    assert "/repos/owner/repo/pulls" not in client.paths
    assert "repo:owner/repo is:pr is:merged merged:>=2026-03-04" in client.search_queries


def test_fetch_snapshot_preserves_merged_pr_author_classification() -> None:
    client = MergedPullRequestSearchGitHubClient(
        _base_payloads(),
        merged_items=[
            {
                "author_association": "CONTRIBUTOR",
                "user": {"login": "external-dev", "type": "User"},
            },
            {
                "author_association": "OWNER",
                "user": {"login": "maintainer", "type": "User"},
            },
            {
                "author_association": "CONTRIBUTOR",
                "user": {"login": "dependabot[bot]", "type": "Bot"},
            },
        ],
    )

    snapshot = client.fetch_snapshot(
        "owner/repo",
        merged_since=datetime(2026, 3, 4, tzinfo=timezone.utc),
    )

    assert snapshot.merged_pr_count == 3
    assert snapshot.external_merged_pr_count == 1
    assert snapshot.maintainer_merged_pr_count == 1
    assert snapshot.bot_merged_pr_count == 1


def test_fetch_snapshot_counts_ready_for_review_prs_separately_from_drafts() -> None:
    client = DraftPullRequestSearchGitHubClient(_base_payloads(), ready_count=4, draft_count=90)

    snapshot = client.fetch_snapshot("owner/repo")

    assert snapshot.open_pr_count == 4
    assert snapshot.draft_open_pr_count == 90
    assert "repo:owner/repo type:pr state:open draft:false" in client.search_queries
    assert "repo:owner/repo type:pr state:open draft:true" in client.search_queries


def test_fetch_snapshot_samples_actual_issues_with_issue_search() -> None:
    payloads = _base_payloads()
    payloads["/repos/owner/repo/issues"] = [
        {"number": 101, "pull_request": {"url": "https://api.example.test/pulls/101"}}
    ]
    client = OpenIssueSearchGitHubClient(
        payloads,
        issue_items=[
            {
                "number": 201,
                "labels": [{"name": "bug"}],
                "created_at": "2026-05-01T00:00:00Z",
                "updated_at": "2026-06-01T00:00:00Z",
                "comments": 1,
                "author_association": "CONTRIBUTOR",
            },
            {
                "number": 202,
                "labels": [{"name": "help wanted"}],
                "created_at": "2026-05-02T00:00:00Z",
                "updated_at": "2026-06-01T00:00:00Z",
                "comments": 0,
                "author_association": "CONTRIBUTOR",
            },
        ],
    )

    snapshot = client.fetch_snapshot("owner/repo")

    assert [issue.number for issue in snapshot.open_issues] == [201, 202]
    assert snapshot.open_issue_count == 2
    assert "/repos/owner/repo/issues" not in client.paths
    assert "repo:owner/repo type:issue state:open" in client.search_queries


def test_fetch_snapshot_rejects_incomplete_search_counts() -> None:
    client = IncompleteSearchGitHubClient(_base_payloads())

    with pytest.raises(GitHubError, match="incomplete results"):
        client.fetch_snapshot("owner/repo")


def test_fetch_snapshot_rejects_incomplete_merged_pr_search_counts() -> None:
    client = IncompleteMergedPullRequestSearchGitHubClient(_base_payloads())

    with pytest.raises(GitHubError, match="merged pull requests"):
        client.fetch_snapshot(
            "owner/repo",
            merged_since=datetime(2026, 3, 4, tzinfo=timezone.utc),
        )


def test_fetch_snapshot_rejects_malformed_open_issue_search_items() -> None:
    client = MalformedOpenIssueSearchGitHubClient(_base_payloads())

    with pytest.raises(GitHubError, match="Expected search items for open issues"):
        client.fetch_snapshot("owner/repo")


def test_fetch_snapshot_tolerates_malformed_issue_labels() -> None:
    client = OpenIssueSearchGitHubClient(
        _base_payloads(),
        issue_items=[
            {
                "number": 1,
                "labels": None,
                "created_at": "2026-06-01T00:00:00Z",
                "updated_at": "2026-06-01T00:00:00Z",
                "comments": 0,
                "author_association": "CONTRIBUTOR",
            }
        ],
    )

    snapshot = client.fetch_snapshot("owner/repo")

    assert snapshot.open_issues[0].labels == ()


def test_fetch_snapshot_collects_pull_request_template_metadata() -> None:
    payloads = _base_payloads()
    payloads["/repos/owner/repo/contents/"] = [
        {"name": "pull_request_template.md", "type": "file"},
        {"name": "PULL_REQUEST_TEMPLATE", "type": "dir"},
    ]
    payloads["/repos/owner/repo/contents/.github"] = [
        {"name": "PULL_REQUEST_TEMPLATE.md", "type": "file"},
        {"name": "PULL_REQUEST_TEMPLATE", "type": "dir"},
    ]
    payloads["/repos/owner/repo/contents/docs"] = [
        {"name": "PULL_REQUEST_TEMPLATE.md", "type": "file"},
        {"name": "PULL_REQUEST_TEMPLATE", "type": "dir"},
    ]
    payloads["/repos/owner/repo/contents/PULL_REQUEST_TEMPLATE"] = [
        {"name": "feature.md", "type": "file"},
    ]
    payloads["/repos/owner/repo/contents/.github/PULL_REQUEST_TEMPLATE"] = [
        {"name": "bugfix.md", "type": "file"},
        {"name": "screenshots.md", "type": "dir"},
    ]
    payloads["/repos/owner/repo/contents/docs/PULL_REQUEST_TEMPLATE"] = [
        {"name": "release.md", "type": "file"},
    ]
    client = FakeGitHubClient(payloads)

    snapshot = client.fetch_snapshot("owner/repo")

    assert {
        "pull_request_template.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
        "docs/PULL_REQUEST_TEMPLATE.md",
        "PULL_REQUEST_TEMPLATE/feature.md",
        ".github/PULL_REQUEST_TEMPLATE/bugfix.md",
        "docs/PULL_REQUEST_TEMPLATE/release.md",
    } <= snapshot.root_entries
    assert ".github/PULL_REQUEST_TEMPLATE/screenshots.md" not in snapshot.root_entries


def test_get_paginated_json_follows_next_links() -> None:
    client = PaginatedGitHubClient()

    items = client.get_paginated_json(
        "/items",
        {"per_page": "100"},
        description="items",
        max_pages=2,
    )

    assert items == [{"id": 1}, {"id": 2}]
    assert client.followed_urls == ["https://api.example.test/items?page=2"]


def test_get_paginated_json_follows_enterprise_same_origin_next_links() -> None:
    client = EnterprisePaginatedGitHubClient()

    items = client.get_paginated_json(
        "/items",
        {"per_page": "100"},
        description="items",
        max_pages=2,
    )

    assert items == [{"id": 1}, {"id": 2}]
    assert client.followed_urls == ["https://github.enterprise.test/api/v3/items?page=2"]


def test_get_paginated_json_rejects_cross_origin_next_links_without_leaking_token() -> None:
    client = CrossOriginPaginatedGitHubClient(token="secret-token")

    with pytest.raises(GitHubError) as exc_info:
        client.get_paginated_json(
            "/items",
            {"per_page": "100"},
            description="items",
            max_pages=2,
        )

    message = str(exc_info.value)
    assert "pagination link" in message
    assert "secret-token" not in message
    assert client.followed_urls == []


@pytest.mark.parametrize(
    ("api_url", "message"),
    (
        ("http://api.example.test", "absolute HTTPS URL"),
        ("https://token@example.test", "must not include credentials"),
        ("https://api.example.test?token=secret", "must not include params"),
    ),
)
def test_github_client_rejects_unsafe_api_urls(api_url: str, message: str) -> None:
    with pytest.raises(GitHubError, match=message):
        GitHubClient(api_url=api_url)


def test_get_paginated_json_rejects_unbounded_pagination() -> None:
    client = PaginatedGitHubClient()

    with pytest.raises(GitHubError, match="exceeded 1 pages"):
        client.get_paginated_json(
            "/items",
            {"per_page": "100"},
            description="items",
            max_pages=1,
        )


def test_get_json_includes_rate_limit_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    error = urllib.error.HTTPError(
        "https://api.example.test/rate",
        429,
        "Too Many Requests",
        {
            "Retry-After": "60",
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": "1780000000",
        },
        io.BytesIO(b'{"message":"rate limited"}'),
    )

    def fake_urlopen(request: urllib.request.Request, timeout: int) -> object:
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = GitHubClient(api_url="https://api.example.test")

    with pytest.raises(GitHubError) as exc_info:
        client.get_json("/rate")

    message = str(exc_info.value)
    assert "GitHub API returned 429 for /rate" in message
    assert "Retry-After: 60" in message
    assert "X-RateLimit-Remaining: 0" in message
    assert "X-RateLimit-Reset: 1780000000" in message


def test_get_json_wraps_connection_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: int) -> object:
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = GitHubClient(api_url="https://api.example.test", sleep=lambda _: None)

    with pytest.raises(GitHubError) as exc_info:
        client.get_json("/timeout")

    message = str(exc_info.value)
    assert "GitHub API timed out for /timeout" in message
    assert "timed out" in message


def test_get_json_wraps_response_read_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: int) -> object:
        return TimeoutResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = GitHubClient(api_url="https://api.example.test", sleep=lambda _: None)

    with pytest.raises(GitHubError) as exc_info:
        client.get_json("/slow-body")

    message = str(exc_info.value)
    assert "GitHub API timed out for /slow-body" in message
    assert "read timed out" in message


def test_get_json_retries_transient_503(monkeypatch: pytest.MonkeyPatch) -> None:
    responses: list[object] = [_http_error(503), JsonResponse(b'{"ok": true}')]
    sleeps: list[float] = []
    calls = 0

    def fake_urlopen(request: urllib.request.Request, timeout: int) -> object:
        nonlocal calls
        calls += 1
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = GitHubClient(api_url="https://api.example.test", sleep=sleeps.append)

    assert client.get_json("/retry") == {"ok": True}
    assert calls == 2
    assert sleeps == [0.25]


def test_get_json_honors_short_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    responses: list[object] = [
        _http_error(429, headers={"Retry-After": "1"}),
        JsonResponse(b'{"ok": true}'),
    ]
    sleeps: list[float] = []
    calls = 0

    def fake_urlopen(request: urllib.request.Request, timeout: int) -> object:
        nonlocal calls
        calls += 1
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = GitHubClient(api_url="https://api.example.test", sleep=sleeps.append)

    assert client.get_json("/retry") == {"ok": True}
    assert calls == 2
    assert sleeps == [1.0]


def test_get_json_does_not_retry_404(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    calls = 0

    def fake_urlopen(request: urllib.request.Request, timeout: int) -> object:
        nonlocal calls
        calls += 1
        raise _http_error(404)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = GitHubClient(api_url="https://api.example.test", sleep=sleeps.append)

    with pytest.raises(GitHubError, match="returned 404"):
        client.get_json("/missing")

    assert calls == 1
    assert sleeps == []


def test_get_json_raises_after_retry_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    calls = 0

    def fake_urlopen(request: urllib.request.Request, timeout: int) -> object:
        nonlocal calls
        calls += 1
        raise _http_error(502)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = GitHubClient(api_url="https://api.example.test", sleep=sleeps.append)

    with pytest.raises(GitHubError, match="returned 502"):
        client.get_json("/retry")

    assert calls == 3
    assert sleeps == [0.25, 0.5]


class TimeoutResponse:
    headers: dict[str, str] = {}

    def __enter__(self) -> TimeoutResponse:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        return None

    def read(self) -> bytes:
        raise TimeoutError("read timed out")


class JsonResponse:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.headers: dict[str, str] = {}

    def __enter__(self) -> JsonResponse:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def _http_error(
    status: int,
    *,
    headers: dict[str, str] | None = None,
    body: bytes = b'{"message":"temporary"}',
) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.example.test/retry",
        status,
        "Error",
        headers or {},
        io.BytesIO(body),
    )


class FakeGitHubClient(GitHubClient):
    def __init__(self, payloads: dict[str, object]):
        super().__init__(api_url="https://api.example.test")
        self.payloads = payloads

    def get_json(self, path: str, params: dict[str, str] | None = None) -> object:
        if path == "/search/issues":
            query = (params or {}).get("q", "")
            if "is:pr is:merged" in query:
                return {
                    "total_count": 24,
                    "incomplete_results": False,
                    "items": [
                        {
                            "author_association": "CONTRIBUTOR",
                            "user": {"login": f"external-{index}", "type": "User"},
                        }
                        for index in range(24)
                    ],
                }
            if "type:pr" in query and "draft:false" in query:
                return {"total_count": 123}
            if "type:pr" in query and "draft:true" in query:
                return {"total_count": 0}
            if "type:pr" in query:
                return {"total_count": 123}
            if "type:issue" in query:
                return {"total_count": 456, "incomplete_results": False, "items": []}
            raise AssertionError(f"unexpected search query: {query}")
        return self.payloads[path]

    def get_json_response(self, path: str, params: dict[str, str] | None = None) -> GitHubResponse:
        return GitHubResponse(self.get_json(path, params), {})


class RecordingGitHubClient(FakeGitHubClient):
    def __init__(self, payloads: dict[str, object]):
        super().__init__(payloads)
        self.paths: list[str] = []
        self.search_queries: list[str] = []

    def get_json(self, path: str, params: dict[str, str] | None = None) -> object:
        self.paths.append(path)
        if path == "/search/issues":
            self.search_queries.append((params or {}).get("q", ""))
        return super().get_json(path, params)


class IncompleteSearchGitHubClient(FakeGitHubClient):
    def get_json(self, path: str, params: dict[str, str] | None = None) -> object:
        if path == "/search/issues":
            return {"total_count": 999, "incomplete_results": True}
        return super().get_json(path, params)


class IncompleteMergedPullRequestSearchGitHubClient(FakeGitHubClient):
    def get_json(self, path: str, params: dict[str, str] | None = None) -> object:
        query = (params or {}).get("q", "")
        if path == "/search/issues" and "is:pr is:merged" in query:
            return {"total_count": 999, "incomplete_results": True}
        return super().get_json(path, params)


class MergedPullRequestSearchGitHubClient(FakeGitHubClient):
    def __init__(self, payloads: dict[str, object], *, merged_items: list[dict[str, object]]):
        super().__init__(payloads)
        self.merged_items = merged_items

    def get_json(self, path: str, params: dict[str, str] | None = None) -> object:
        query = (params or {}).get("q", "")
        if path == "/search/issues" and "is:pr is:merged" in query:
            return {
                "total_count": len(self.merged_items),
                "incomplete_results": False,
                "items": self.merged_items,
            }
        return super().get_json(path, params)


class DraftPullRequestSearchGitHubClient(FakeGitHubClient):
    def __init__(self, payloads: dict[str, object], *, ready_count: int, draft_count: int):
        super().__init__(payloads)
        self.ready_count = ready_count
        self.draft_count = draft_count
        self.search_queries: list[str] = []

    def get_json(self, path: str, params: dict[str, str] | None = None) -> object:
        query = (params or {}).get("q", "")
        if path == "/search/issues":
            self.search_queries.append(query)
            if "type:pr" in query and "draft:false" in query:
                return {"total_count": self.ready_count}
            if "type:pr" in query and "draft:true" in query:
                return {"total_count": self.draft_count}
        return super().get_json(path, params)


class OpenIssueSearchGitHubClient(RecordingGitHubClient):
    def __init__(self, payloads: dict[str, object], *, issue_items: list[dict[str, object]]):
        super().__init__(payloads)
        self.issue_items = issue_items

    def get_json(self, path: str, params: dict[str, str] | None = None) -> object:
        query = (params or {}).get("q", "")
        if path == "/search/issues" and "type:issue" in query:
            self.paths.append(path)
            self.search_queries.append(query)
            return {
                "total_count": len(self.issue_items),
                "incomplete_results": False,
                "items": self.issue_items,
            }
        return super().get_json(path, params)


class MalformedOpenIssueSearchGitHubClient(FakeGitHubClient):
    def get_json(self, path: str, params: dict[str, str] | None = None) -> object:
        query = (params or {}).get("q", "")
        if path == "/search/issues" and "type:issue" in query:
            return {
                "total_count": 1,
                "incomplete_results": False,
                "items": {"message": "not a list"},
            }
        return super().get_json(path, params)


class PaginatedGitHubClient(GitHubClient):
    def __init__(self) -> None:
        super().__init__(api_url="https://api.example.test")
        self.followed_urls: list[str] = []

    def get_json_response(self, path: str, params: dict[str, str] | None = None) -> GitHubResponse:
        return GitHubResponse(
            [{"id": 1}],
            {"link": '<https://api.example.test/items?page=2>; rel="next"'},
        )

    def _request_json_url(self, url: str, display_path: str) -> GitHubResponse:
        self.followed_urls.append(url)
        return GitHubResponse([{"id": 2}], {})


class EnterprisePaginatedGitHubClient(GitHubClient):
    def __init__(self) -> None:
        super().__init__(api_url="https://github.enterprise.test/api/v3")
        self.followed_urls: list[str] = []

    def get_json_response(self, path: str, params: dict[str, str] | None = None) -> GitHubResponse:
        return GitHubResponse(
            [{"id": 1}],
            {"link": '<https://github.enterprise.test/api/v3/items?page=2>; rel="next"'},
        )

    def _request_json_url(self, url: str, display_path: str) -> GitHubResponse:
        self.followed_urls.append(url)
        return GitHubResponse([{"id": 2}], {})


class CrossOriginPaginatedGitHubClient(GitHubClient):
    def __init__(self, *, token: str) -> None:
        super().__init__(token=token, api_url="https://api.example.test")
        self.followed_urls: list[str] = []

    def get_json_response(self, path: str, params: dict[str, str] | None = None) -> GitHubResponse:
        return GitHubResponse(
            [{"id": 1}],
            {"link": '<https://evil.example.test/items?page=2>; rel="next"'},
        )

    def _request_json_url(self, url: str, display_path: str) -> GitHubResponse:
        self.followed_urls.append(url)
        raise AssertionError(f"off-origin URL should not be followed: {url}")


def _base_payloads(*, owner: str = "owner", name: str = "repo") -> dict[str, object]:
    repository = f"{owner}/{name}"
    return {
        f"/repos/{repository}": {
            "full_name": repository,
            "html_url": f"https://github.com/{repository}",
            "description": "Example",
            "stargazers_count": 1,
            "forks_count": 2,
            "archived": False,
            "pushed_at": "2026-06-01T00:00:00Z",
            "default_branch": "main",
            "license": {"spdx_id": "MIT"},
            "topics": ["python"],
        },
        f"/repos/{repository}/contents/": [],
        f"/repos/{repository}/contents/.github": [],
        f"/repos/{repository}/contents/docs": [],
        f"/repos/{repository}/contents/PULL_REQUEST_TEMPLATE": [],
        f"/repos/{repository}/contents/.github/PULL_REQUEST_TEMPLATE": [],
        f"/repos/{repository}/contents/docs/PULL_REQUEST_TEMPLATE": [],
        f"/repos/{repository}/contents/.github/workflows": [],
        f"/repos/{repository}/pulls": [],
        f"/repos/{repository}/labels": [],
        f"/repos/{repository}/issues": [],
        f"/repos/{repository}/issues/comments": [],
    }
