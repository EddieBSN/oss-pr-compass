from __future__ import annotations

from datetime import datetime, timezone

from oss_pr_compass.analysis import assess_repository
from oss_pr_compass.config import ScoreConfig
from oss_pr_compass.model import IssueSnapshot, RepositorySnapshot


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
        labels=("good first issue", "help wanted", "bug"),
        open_issues=(
            IssueSnapshot(
                number=1,
                labels=("good first issue",),
                created_at=datetime(2026, 5, 20, tzinfo=timezone.utc),
                updated_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                comment_count=1,
                author_association="CONTRIBUTOR",
                latest_maintainer_comment_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            ),
            IssueSnapshot(
                number=2,
                labels=("bug",),
                created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                comment_count=0,
                author_association="CONTRIBUTOR",
            ),
        ),
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


def test_disabled_signal_reduces_max_score() -> None:
    snapshot = RepositorySnapshot(
        full_name="example/no-template-needed",
        html_url="https://github.com/example/no-template-needed",
        description="Example",
        stars=5,
        forks=1,
        archived=False,
        pushed_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        default_branch="main",
        license_spdx="MIT",
        topics=(),
        root_entries=frozenset({"LICENSE", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "tests"}),
        workflow_entries=frozenset({"ci.yml"}),
        merged_prs=tuple({"merged_at": "2026-06-01T00:00:00Z"} for _ in range(24)),
        open_pr_count=0,
        labels=("good first issue",),
    )

    assessment = assess_repository(
        snapshot,
        days=90,
        now=datetime(2026, 6, 2, tzinfo=timezone.utc),
        config=ScoreConfig(disabled_signals=frozenset({"Pull request template"})),
    )

    assert assessment.max_score == 92
    assert {signal.name for signal in assessment.signals}.isdisjoint({"Pull request template"})
    assert assessment.verdict == "strong"


def test_issue_signal_flags_stale_unanswered_issues() -> None:
    snapshot = RepositorySnapshot(
        full_name="example/issues",
        html_url="https://github.com/example/issues",
        description="Example",
        stars=5,
        forks=1,
        archived=False,
        pushed_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        default_branch="main",
        license_spdx="MIT",
        topics=(),
        root_entries=frozenset({"LICENSE", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "tests"}),
        workflow_entries=frozenset({"ci.yml"}),
        merged_prs=tuple({"merged_at": "2026-06-01T00:00:00Z"} for _ in range(24)),
        open_pr_count=0,
        labels=(),
        open_issues=(
            IssueSnapshot(
                number=1,
                labels=(),
                created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
                comment_count=3,
                author_association="CONTRIBUTOR",
            ),
        ),
    )

    assessment = assess_repository(
        snapshot,
        days=90,
        now=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )

    issue_signal = next(
        signal for signal in assessment.signals if signal.name == "Issue triage signals"
    )
    assert issue_signal.points < issue_signal.max_points
    assert "1 stale unanswered" in issue_signal.detail


def test_issue_signal_uses_total_open_issue_count_for_queue_pressure() -> None:
    snapshot = RepositorySnapshot(
        full_name="example/many-issues",
        html_url="https://github.com/example/many-issues",
        description="Example",
        stars=5,
        forks=1,
        archived=False,
        pushed_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        default_branch="main",
        license_spdx="MIT",
        topics=(),
        root_entries=frozenset({"LICENSE", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "tests"}),
        workflow_entries=frozenset({"ci.yml"}),
        merged_prs=tuple({"merged_at": "2026-06-01T00:00:00Z"} for _ in range(24)),
        open_pr_count=0,
        labels=("good first issue",),
        open_issues=(
            IssueSnapshot(
                number=1,
                labels=("good first issue",),
                created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                comment_count=1,
                author_association="CONTRIBUTOR",
                latest_maintainer_comment_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            ),
        ),
        open_issue_count=250,
    )

    assessment = assess_repository(
        snapshot,
        days=90,
        now=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )

    issue_signal = next(
        signal for signal in assessment.signals if signal.name == "Issue triage signals"
    )
    assert issue_signal.points == 10
    assert "250 total open issues" in issue_signal.detail


def test_archived_repository_cannot_receive_strong_verdict() -> None:
    snapshot = RepositorySnapshot(
        full_name="example/archived",
        html_url="https://github.com/example/archived",
        description="Archived",
        stars=5000,
        forks=300,
        archived=True,
        pushed_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        default_branch="main",
        license_spdx="MIT",
        topics=(),
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
        open_pr_count=0,
        labels=("good first issue",),
    )

    assessment = assess_repository(
        snapshot,
        days=90,
        now=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )

    assert assessment.score >= 75
    assert assessment.verdict == "needs-work"
