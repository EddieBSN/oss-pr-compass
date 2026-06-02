from __future__ import annotations

import pytest

from oss_pr_compass.github import parse_repository


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
