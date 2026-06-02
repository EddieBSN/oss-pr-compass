from __future__ import annotations

import pytest

from oss_pr_compass.github import GitHubClient, GitHubError, parse_repository


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("owner/repo", ("owner", "repo")),
        ("owner/repo/", ("owner", "repo")),
        ("https://github.com/owner/repo", ("owner", "repo")),
        ("https://github.com/owner/repo/", ("owner", "repo")),
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


def test_fetch_snapshot_rejects_malformed_list_payloads() -> None:
    payloads = _base_payloads()
    payloads["/repos/owner/repo/issues"] = {"message": "not a list"}
    client = FakeGitHubClient(payloads)

    with pytest.raises(GitHubError, match="Expected a list for open issues"):
        client.fetch_snapshot("owner/repo")


def test_fetch_snapshot_tolerates_malformed_issue_labels() -> None:
    payloads = _base_payloads()
    payloads["/repos/owner/repo/issues"] = [
        {
            "number": 1,
            "labels": None,
            "created_at": "2026-06-01T00:00:00Z",
            "updated_at": "2026-06-01T00:00:00Z",
            "comments": 0,
            "author_association": "CONTRIBUTOR",
        }
    ]
    client = FakeGitHubClient(payloads)

    snapshot = client.fetch_snapshot("owner/repo")

    assert snapshot.open_issues[0].labels == ()


class FakeGitHubClient(GitHubClient):
    def __init__(self, payloads: dict[str, object]):
        super().__init__(api_url="https://api.example.test")
        self.payloads = payloads

    def get_json(self, path: str, params: dict[str, str] | None = None) -> object:
        if path == "/search/issues":
            query = (params or {}).get("q", "")
            if "type:pr" in query:
                return {"total_count": 123}
            if "type:issue" in query:
                return {"total_count": 456}
            raise AssertionError(f"unexpected search query: {query}")
        return self.payloads[path]


def _base_payloads() -> dict[str, object]:
    return {
        "/repos/owner/repo": {
            "full_name": "owner/repo",
            "html_url": "https://github.com/owner/repo",
            "description": "Example",
            "stargazers_count": 1,
            "forks_count": 2,
            "archived": False,
            "pushed_at": "2026-06-01T00:00:00Z",
            "default_branch": "main",
            "license": {"spdx_id": "MIT"},
            "topics": ["python"],
        },
        "/repos/owner/repo/contents/": [],
        "/repos/owner/repo/contents/.github": [],
        "/repos/owner/repo/contents/.github/workflows": [],
        "/repos/owner/repo/pulls": [],
        "/repos/owner/repo/labels": [],
        "/repos/owner/repo/issues": [],
        "/repos/owner/repo/issues/comments": [],
    }
