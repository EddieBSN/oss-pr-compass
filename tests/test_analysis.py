from __future__ import annotations

from datetime import datetime, timezone

import pytest

from oss_pr_compass.analysis import assess_repository
from oss_pr_compass.config import ScoreConfig
from oss_pr_compass.model import Assessment, IssueSnapshot, RepositorySnapshot, Signal


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


@pytest.mark.parametrize(
    "entry",
    (
        "PULL_REQUEST_TEMPLATE.md",
        "pull_request_template.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/pull_request_template.md",
        "docs/PULL_REQUEST_TEMPLATE.md",
        "docs/pull_request_template.md",
        "PULL_REQUEST_TEMPLATE/feature.md",
        ".github/PULL_REQUEST_TEMPLATE/bugfix.md",
        "docs/PULL_REQUEST_TEMPLATE/release.md",
    ),
)
def test_pull_request_template_signal_accepts_supported_locations(entry: str) -> None:
    assessment = assess_repository(
        _snapshot_with_root_entries(frozenset({entry})),
        days=90,
        now=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )

    signal = _signal(assessment, "Pull request template")
    assert signal.points == signal.max_points
    assert signal.detail == "Pull request template found."


@pytest.mark.parametrize(
    "entries",
    (
        frozenset({"PULL_REQUEST_TEMPLATE"}),
        frozenset({".github/PULL_REQUEST_TEMPLATE"}),
        frozenset({"docs/PULL_REQUEST_TEMPLATE"}),
        frozenset({"PULL_REQUEST_TEMPLATE/README.txt"}),
        frozenset({".github/PULL_REQUEST_TEMPLATE/assets.png"}),
        frozenset({"docs/not_a_template.md"}),
    ),
)
def test_pull_request_template_signal_rejects_empty_or_unrelated_entries(
    entries: frozenset[str],
) -> None:
    assessment = assess_repository(
        _snapshot_with_root_entries(entries),
        days=90,
        now=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )

    signal = _signal(assessment, "Pull request template")
    assert signal.points == 0
    assert signal.detail == "No pull request template found."


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
    assert any(
        recommendation.id == "add-license" and recommendation.evidence
        for recommendation in assessment.recommendation_details
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


def test_merged_pr_signal_uses_exact_lookback_count_when_available() -> None:
    snapshot = RepositorySnapshot(
        full_name="example/search-count",
        html_url="https://github.com/example/search-count",
        description="Example",
        stars=5,
        forks=1,
        archived=False,
        pushed_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        default_branch="main",
        license_spdx=None,
        topics=(),
        root_entries=frozenset(),
        workflow_entries=frozenset(),
        merged_prs=(),
        merged_pr_count=20,
        open_pr_count=0,
    )

    assessment = assess_repository(
        snapshot,
        days=90,
        now=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )

    signal = _signal(assessment, "Merged pull request activity")
    assert signal.points == signal.max_points
    assert signal.detail == "20 merged PRs in 90 days."


def test_merged_pr_signal_does_not_count_maintainer_or_bot_prs_as_external() -> None:
    snapshot = _snapshot_with_merged_pr_counts(
        total=20,
        external=0,
        maintainer=12,
        bot=8,
    )

    assessment = assess_repository(
        snapshot,
        days=90,
        now=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )

    signal = _signal(assessment, "Merged pull request activity")
    assert signal.points == 0
    assert "0 external human merged PRs" in signal.detail
    assert "12 maintainer PRs" in signal.detail
    assert "8 bot PRs" in signal.detail
    assert "20 total merged PRs" in signal.detail


def test_merged_pr_signal_scores_mixed_external_maintainer_and_bot_counts() -> None:
    snapshot = _snapshot_with_merged_pr_counts(
        total=20,
        external=5,
        maintainer=10,
        bot=5,
    )

    assessment = assess_repository(
        snapshot,
        days=90,
        now=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )

    signal = _signal(assessment, "Merged pull request activity")
    assert signal.points == 14
    assert signal.max_points == 18
    assert "5 external human merged PRs" in signal.detail
    assert "10 maintainer PRs" in signal.detail
    assert "5 bot PRs" in signal.detail


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
    assert issue_signal.sampled is True
    assert issue_signal.sample_size == 1
    assert issue_signal.sample_total == 250
    assert issue_signal.confidence == "sampled"


def test_issue_signal_does_not_treat_missing_sample_as_healthy_triage() -> None:
    snapshot = RepositorySnapshot(
        full_name="example/missing-issue-sample",
        html_url="https://github.com/example/missing-issue-sample",
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
        open_issues=(),
        open_issue_count=25,
    )

    assessment = assess_repository(
        snapshot,
        days=90,
        now=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )

    issue_signal = _signal(assessment, "Issue triage signals")
    assert issue_signal.points < issue_signal.max_points
    assert issue_signal.confidence == "sampled"
    assert issue_signal.sample_size == 0
    assert issue_signal.sample_total == 25
    assert "0 sampled open issues available from 25 total open issues" in issue_signal.detail


def test_issue_signal_uses_oldest_issue_sample_for_stale_unanswered_detection() -> None:
    snapshot = RepositorySnapshot(
        full_name="example/old-stale",
        html_url="https://github.com/example/old-stale",
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
                labels=("bug",),
                created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                comment_count=0,
                author_association="CONTRIBUTOR",
            ),
        ),
        oldest_open_issues=(
            IssueSnapshot(
                number=2,
                labels=("help wanted",),
                created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
                comment_count=0,
                author_association="CONTRIBUTOR",
            ),
        ),
        open_issue_count=250,
    )

    assessment = assess_repository(
        snapshot,
        days=90,
        now=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )

    issue_signal = _signal(assessment, "Issue triage signals")
    assert issue_signal.points < issue_signal.max_points
    assert "1 stale unanswered in oldest open issue sample" in issue_signal.detail
    assert "sampled 1/250" in issue_signal.detail
    assert issue_signal.sampled is True


def test_open_pr_queue_scores_ready_for_review_prs_and_reports_drafts() -> None:
    snapshot = RepositorySnapshot(
        full_name="example/drafts",
        html_url="https://github.com/example/drafts",
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
        open_pr_count=5,
        draft_open_pr_count=75,
        labels=("good first issue",),
    )

    assessment = assess_repository(
        snapshot,
        days=90,
        now=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )

    signal = _signal(assessment, "Open pull request queue")
    assert signal.points == signal.max_points
    assert signal.detail == "5 ready-for-review open PRs; 75 draft PRs excluded."


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


def _snapshot_with_root_entries(entries: frozenset[str]) -> RepositorySnapshot:
    return RepositorySnapshot(
        full_name="example/templates",
        html_url="https://github.com/example/templates",
        description="Example",
        stars=5,
        forks=1,
        archived=False,
        pushed_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        default_branch="main",
        license_spdx=None,
        topics=(),
        root_entries=entries,
        workflow_entries=frozenset(),
        merged_prs=(),
        open_pr_count=0,
    )


def _snapshot_with_merged_pr_counts(
    *,
    total: int,
    external: int,
    maintainer: int,
    bot: int,
) -> RepositorySnapshot:
    return RepositorySnapshot(
        full_name="example/merged-prs",
        html_url="https://github.com/example/merged-prs",
        description="Example",
        stars=5,
        forks=1,
        archived=False,
        pushed_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        default_branch="main",
        license_spdx=None,
        topics=(),
        root_entries=frozenset(),
        workflow_entries=frozenset(),
        merged_prs=(),
        open_pr_count=0,
        merged_pr_count=total,
        external_merged_pr_count=external,
        maintainer_merged_pr_count=maintainer,
        bot_merged_pr_count=bot,
    )


def _signal(assessment: Assessment, name: str) -> Signal:
    return next(signal for signal in assessment.signals if signal.name == name)
