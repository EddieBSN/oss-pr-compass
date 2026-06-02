from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class IssueSnapshot:
    number: int
    labels: tuple[str, ...]
    created_at: datetime | None
    updated_at: datetime | None
    comment_count: int
    author_association: str
    latest_maintainer_comment_at: datetime | None = None


@dataclass(frozen=True)
class RepositorySnapshot:
    full_name: str
    html_url: str
    description: str
    stars: int
    forks: int
    archived: bool
    pushed_at: datetime | None
    default_branch: str
    license_spdx: str | None
    topics: tuple[str, ...]
    root_entries: frozenset[str]
    workflow_entries: frozenset[str]
    merged_prs: tuple[dict[str, Any], ...]
    open_pr_count: int
    labels: tuple[str, ...] = ()
    open_issues: tuple[IssueSnapshot, ...] = ()


@dataclass(frozen=True)
class Signal:
    name: str
    points: int
    max_points: int
    detail: str

    @property
    def passed(self) -> bool:
        return self.points > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "points": self.points,
            "max_points": self.max_points,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Assessment:
    repository: str
    url: str
    score: int
    max_score: int
    verdict: str
    signals: tuple[Signal, ...]
    recommendations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "url": self.url,
            "score": self.score,
            "max_score": self.max_score,
            "verdict": self.verdict,
            "signals": [signal.to_dict() for signal in self.signals],
            "recommendations": list(self.recommendations),
        }
