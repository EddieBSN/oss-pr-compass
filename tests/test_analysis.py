from __future__ import annotations

from datetime import datetime, timezone

from oss_pr_compass.analysis import assess_repository
from oss_pr_compass.model import RepositorySnapshot


def test_strong_repository_scores_high() -> None:
    snapshot = RepositorySnapshot(
        full_name="pypa/example",
        html_url="https://github.com/pypa/example",
        description="Example",
        stars=5000,
        forks=300,
        archived=False,
        pushed_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        default_branch="main",
        license_spdx="MIT",
        topics=("python",),
        root_entries=frozenset(
            {
                "LICENSE",
                "CONTRIBUTING.md",
                "CODE_OF_CONDUCT.md",
                ".github/PULL_REQUEST_TEMPLATE.md",
                "tests",
            }
        ),
        workflow_entries=frozenset({"ci.yml"}),
        merged_prs=tuple({"merged_at": "2026-06-01T00:00:00Z"} for _ in range(24)),
        open_pr_count=3,
    )

    assessment = assess_repository(
        snapshot,
        days=90,
        now=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )

    assert assessment.score == 100
    assert assessment.verdict == "strong"
    assert not assessment.recommendations


def test_inactive_repository_gets_actionable_recommendations() -> None:
    snapshot = RepositorySnapshot(
        full_name="example/stale",
        html_url="https://github.com/example/stale",
        description="Stale",
        stars=5,
        forks=1,
        archived=False,
        pushed_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        default_branch="main",
        license_spdx=None,
        topics=(),
        root_entries=frozenset(),
        workflow_entries=frozenset(),
        merged_prs=(),
        open_pr_count=100,
    )

    assessment = assess_repository(
        snapshot,
        days=90,
        now=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )

    assert assessment.verdict == "needs-work"
    assert "Add a standard open source license file." in assessment.recommendations
    assert (
        "Review recent closed PRs before investing in a contribution." in assessment.recommendations
    )
