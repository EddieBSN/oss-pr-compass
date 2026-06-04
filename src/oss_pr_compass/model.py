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
    latest_external_activity_at: datetime | None = None


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
    merged_pr_count: int | None = None
    external_merged_pr_count: int | None = None
    maintainer_merged_pr_count: int | None = None
    bot_merged_pr_count: int | None = None
    draft_open_pr_count: int | None = None
    labels: tuple[str, ...] = ()
    open_issues: tuple[IssueSnapshot, ...] = ()
    oldest_open_issues: tuple[IssueSnapshot, ...] = ()
    open_issue_count: int | None = None
    issue_comment_evidence_incomplete: bool = False


@dataclass(frozen=True)
class Signal:
    name: str
    points: int
    max_points: int
    detail: str
    confidence: str = "high"
    sampled: bool = False
    sample_size: int | None = None
    sample_total: int | None = None

    @property
    def passed(self) -> bool:
        return self.points > 0

    def to_dict(self) -> dict[str, Any]:
        data = {
            "name": self.name,
            "passed": self.passed,
            "points": self.points,
            "max_points": self.max_points,
            "detail": self.detail,
            "confidence": self.confidence,
        }
        if self.sampled:
            data["sampled"] = True
        if self.sample_size is not None:
            data["sample_size"] = self.sample_size
        if self.sample_total is not None:
            data["sample_total"] = self.sample_total
        return data


@dataclass(frozen=True)
class Recommendation:
    id: str
    signal: str
    priority: str
    points_lost: int
    why_it_matters: str
    next_action: str
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "signal": self.signal,
            "priority": self.priority,
            "points_lost": self.points_lost,
            "why_it_matters": self.why_it_matters,
            "next_action": self.next_action,
            "evidence": list(self.evidence),
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
    recommendation_details: tuple[Recommendation, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "url": self.url,
            "score": self.score,
            "max_score": self.max_score,
            "verdict": self.verdict,
            "signals": [signal.to_dict() for signal in self.signals],
            "recommendations": list(self.recommendations),
            "recommendation_details": [
                recommendation.to_dict() for recommendation in self.recommendation_details
            ],
        }
