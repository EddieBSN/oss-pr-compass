from __future__ import annotations

from datetime import datetime, timedelta, timezone

from oss_pr_compass.config import ScoreConfig
from oss_pr_compass.model import (
    Assessment,
    IssueSnapshot,
    Recommendation,
    RepositorySnapshot,
    Signal,
)

SIGNAL_WEIGHTS = {
    "OSS license": 12,
    "Recent repository activity": 14,
    "Merged pull request activity": 18,
    "Contribution documentation": 14,
    "Pull request template": 8,
    "CI and test signals": 14,
    "Open pull request queue": 8,
    "Issue triage signals": 12,
}
MAX_SCORE = sum(SIGNAL_WEIGHTS.values())
MAINTAINER_ASSOCIATIONS = {"COLLABORATOR", "MEMBER", "OWNER"}
PULL_REQUEST_TEMPLATE_FILES = frozenset(
    {
        "PULL_REQUEST_TEMPLATE.md",
        "pull_request_template.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/pull_request_template.md",
        "docs/PULL_REQUEST_TEMPLATE.md",
        "docs/pull_request_template.md",
    }
)
PULL_REQUEST_TEMPLATE_DIRECTORIES = (
    "PULL_REQUEST_TEMPLATE/",
    ".github/PULL_REQUEST_TEMPLATE/",
    "docs/PULL_REQUEST_TEMPLATE/",
)


def assess_repository(
    snapshot: RepositorySnapshot,
    *,
    days: int = 90,
    now: datetime | None = None,
    config: ScoreConfig | None = None,
) -> Assessment:
    now = now or datetime.now(timezone.utc)
    config = config or ScoreConfig()
    cutoff = now - timedelta(days=days)
    merged_recently = [
        pr for pr in snapshot.merged_prs if _parse_github_datetime(pr.get("merged_at")) >= cutoff
    ]

    thresholds = config.thresholds
    candidate_signals = (
        _license_signal(snapshot),
        _activity_signal(
            snapshot,
            now,
            thresholds.recent_activity_full_days,
            thresholds.recent_activity_partial_days,
        ),
        _merged_pr_signal(
            merged_recently,
            days,
            thresholds.merged_prs_full,
            thresholds.merged_prs_partial,
            thresholds.merged_prs_minimum,
        ),
        _contribution_docs_signal(snapshot),
        _pr_template_signal(snapshot),
        _ci_and_tests_signal(snapshot),
        _open_pr_queue_signal(
            snapshot,
            thresholds.open_pr_queue_full,
            thresholds.open_pr_queue_partial,
            thresholds.open_pr_queue_minimum,
        ),
        _issue_triage_signal(snapshot, now, config),
    )
    signals = tuple(
        signal for signal in candidate_signals if signal.name not in config.disabled_signals
    )
    score = sum(signal.points for signal in signals)
    max_score = sum(signal.max_points for signal in signals)
    recommendation_details = tuple(_recommendation_details(signals))
    recommendations = tuple(recommendation.next_action for recommendation in recommendation_details)

    return Assessment(
        repository=snapshot.full_name,
        url=snapshot.html_url,
        score=score,
        max_score=max_score,
        verdict=_verdict(score, max_score, archived=snapshot.archived),
        signals=signals,
        recommendations=recommendations,
        recommendation_details=recommendation_details,
    )


def _license_signal(snapshot: RepositorySnapshot) -> Signal:
    max_points = SIGNAL_WEIGHTS["OSS license"]
    if snapshot.license_spdx:
        return Signal("OSS license", max_points, max_points, f"Detected {snapshot.license_spdx}.")
    return Signal("OSS license", 0, max_points, "No license metadata was detected by GitHub.")


def _activity_signal(
    snapshot: RepositorySnapshot,
    now: datetime,
    full_days: int,
    partial_days: int,
) -> Signal:
    max_points = SIGNAL_WEIGHTS["Recent repository activity"]
    if snapshot.archived:
        return Signal("Recent repository activity", 0, max_points, "Repository is archived.")
    if snapshot.pushed_at is None:
        return Signal(
            "Recent repository activity", 0, max_points, "No push timestamp was available."
        )

    age_days = (now - snapshot.pushed_at).days
    if age_days <= full_days:
        return Signal(
            "Recent repository activity",
            max_points,
            max_points,
            f"Last push was {age_days} days ago.",
        )
    if age_days <= partial_days:
        return Signal(
            "Recent repository activity",
            _scaled_points(max_points, 0.55),
            max_points,
            f"Last push was {age_days} days ago.",
        )
    return Signal(
        "Recent repository activity", 0, max_points, f"Last push was {age_days} days ago."
    )


def _merged_pr_signal(
    merged_recently: list[dict[str, object]],
    days: int,
    full_count: int,
    partial_count: int,
    minimum_count: int,
) -> Signal:
    max_points = SIGNAL_WEIGHTS["Merged pull request activity"]
    count = len(merged_recently)
    if count >= full_count:
        points = max_points
    elif count >= partial_count:
        points = _scaled_points(max_points, 0.75)
    elif count >= minimum_count:
        points = _scaled_points(max_points, 0.40)
    else:
        points = 0
    return Signal(
        "Merged pull request activity",
        points,
        max_points,
        f"{count} merged PRs in {days} days.",
    )


def _contribution_docs_signal(snapshot: RepositorySnapshot) -> Signal:
    max_points = SIGNAL_WEIGHTS["Contribution documentation"]
    entries = snapshot.root_entries
    has_contributing = "CONTRIBUTING.md" in entries or ".github/CONTRIBUTING.md" in entries
    has_code_of_conduct = "CODE_OF_CONDUCT.md" in entries or ".github/CODE_OF_CONDUCT.md" in entries
    points = (9 if has_contributing else 0) + (5 if has_code_of_conduct else 0)

    parts = []
    parts.append("contribution guide" if has_contributing else "no contribution guide")
    parts.append("code of conduct" if has_code_of_conduct else "no code of conduct")
    return Signal("Contribution documentation", points, max_points, ", ".join(parts) + ".")


def _pr_template_signal(snapshot: RepositorySnapshot) -> Signal:
    max_points = SIGNAL_WEIGHTS["Pull request template"]
    has_template = any(_is_pull_request_template_entry(entry) for entry in snapshot.root_entries)
    if has_template:
        return Signal(
            "Pull request template", max_points, max_points, "Pull request template found."
        )
    return Signal("Pull request template", 0, max_points, "No pull request template found.")


def _ci_and_tests_signal(snapshot: RepositorySnapshot) -> Signal:
    max_points = SIGNAL_WEIGHTS["CI and test signals"]
    has_ci = bool(snapshot.workflow_entries)
    has_tests = "tests" in snapshot.root_entries or "test" in snapshot.root_entries
    points = (7 if has_ci else 0) + (7 if has_tests else 0)
    detail = []
    detail.append("CI workflows" if has_ci else "no CI workflows")
    detail.append("tests directory" if has_tests else "no tests directory")
    return Signal("CI and test signals", points, max_points, ", ".join(detail) + ".")


def _open_pr_queue_signal(
    snapshot: RepositorySnapshot,
    full_count: int,
    partial_count: int,
    minimum_count: int,
) -> Signal:
    max_points = SIGNAL_WEIGHTS["Open pull request queue"]
    count = snapshot.open_pr_count
    if count <= full_count:
        points = max_points
    elif count <= partial_count:
        points = _scaled_points(max_points, 0.70)
    elif count <= minimum_count:
        points = _scaled_points(max_points, 0.40)
    else:
        points = 0
    return Signal("Open pull request queue", points, max_points, f"{count} open PRs.")


def _issue_triage_signal(
    snapshot: RepositorySnapshot,
    now: datetime,
    config: ScoreConfig,
) -> Signal:
    max_points = SIGNAL_WEIGHTS["Issue triage signals"]
    thresholds = config.thresholds
    issues = snapshot.open_issues
    sampled_issue_count = len(issues)
    total_issue_count = (
        snapshot.open_issue_count if snapshot.open_issue_count is not None else sampled_issue_count
    )
    contributor_labels = _contributor_labels(snapshot)
    contributor_label_points = 3 if contributor_labels else 0

    labeled_issue_count = sum(1 for issue in issues if issue.labels)
    label_ratio = labeled_issue_count / sampled_issue_count if sampled_issue_count else 1.0
    if label_ratio >= thresholds.issue_label_ratio_full:
        label_points = 3
    elif label_ratio >= thresholds.issue_label_ratio_partial:
        label_points = 2
    elif labeled_issue_count:
        label_points = 1
    else:
        label_points = 0

    if total_issue_count <= thresholds.open_issue_queue_full:
        issue_queue_points = 2
    elif total_issue_count <= thresholds.open_issue_queue_partial:
        issue_queue_points = 1
    else:
        issue_queue_points = 0

    stale_cutoff = now - timedelta(days=thresholds.stale_unanswered_days)
    stale_unanswered = [
        issue for issue in issues if _is_stale_unanswered_external_issue(issue, stale_cutoff)
    ]
    if sampled_issue_count == 0 or not stale_unanswered:
        stale_points = 2
    elif (
        len(stale_unanswered) <= thresholds.stale_unanswered_minimum
        or len(stale_unanswered) / sampled_issue_count <= thresholds.stale_unanswered_partial_ratio
    ):
        stale_points = 1
    else:
        stale_points = 0

    response_cutoff = now - timedelta(days=thresholds.maintainer_response_window_days)
    recent_maintainer_responses = [
        issue
        for issue in issues
        if issue.latest_maintainer_comment_at is not None
        and issue.latest_maintainer_comment_at >= response_cutoff
    ]
    response_ratio = (
        len(recent_maintainer_responses) / sampled_issue_count if sampled_issue_count else 1.0
    )
    if response_ratio >= thresholds.maintainer_response_full_ratio:
        response_points = 2
    elif (
        response_ratio >= thresholds.maintainer_response_partial_ratio
        or recent_maintainer_responses
    ):
        response_points = 1
    else:
        response_points = 0

    points = (
        contributor_label_points
        + label_points
        + issue_queue_points
        + stale_points
        + response_points
    )
    label_detail = (
        f"contributor labels: {', '.join(contributor_labels)}"
        if contributor_labels
        else "no contributor-friendly labels"
    )
    detail = (
        f"{label_detail}; {labeled_issue_count}/{sampled_issue_count} sampled open issues "
        f"labeled; {total_issue_count} total open issues; "
        f"{len(stale_unanswered)} stale unanswered in sample; "
        f"{len(recent_maintainer_responses)} recent maintainer responses."
    )
    sampled = snapshot.open_issue_count is not None and total_issue_count > sampled_issue_count
    return Signal(
        "Issue triage signals",
        points,
        max_points,
        detail,
        confidence="sampled" if sampled else "high",
        sampled=sampled,
        sample_size=sampled_issue_count if sampled else None,
        sample_total=total_issue_count if sampled else None,
    )


def _recommendations(signals: tuple[Signal, ...]) -> list[str]:
    return [recommendation.next_action for recommendation in _recommendation_details(signals)]


def _recommendation_details(signals: tuple[Signal, ...]) -> list[Recommendation]:
    recommendations: list[Recommendation] = []
    for signal in signals:
        if signal.points == signal.max_points:
            continue
        points_lost = signal.max_points - signal.points
        priority = _recommendation_priority(points_lost)
        evidence = _recommendation_evidence(signal)
        if signal.name == "OSS license":
            recommendations.append(
                Recommendation(
                    id="add-license",
                    signal=signal.name,
                    priority=priority,
                    points_lost=points_lost,
                    why_it_matters=(
                        "A clear license tells outside contributors whether the code can be "
                        "used, changed, and redistributed."
                    ),
                    next_action="Add a standard open source license file.",
                    evidence=evidence,
                )
            )
        elif signal.name == "Recent repository activity":
            recommendations.append(
                Recommendation(
                    id="check-maintenance",
                    signal=signal.name,
                    priority=priority,
                    points_lost=points_lost,
                    why_it_matters=(
                        "Recent repository activity is a basic signal that maintainers may still "
                        "review incoming work."
                    ),
                    next_action="Check whether the repository is still actively maintained.",
                    evidence=evidence,
                )
            )
        elif signal.name == "Merged pull request activity":
            if signal.points == 0:
                next_action = "Review recent closed PRs before investing in a contribution."
            else:
                next_action = (
                    "Check whether recent PRs show timely review and merges before investing "
                    "in a larger contribution."
                )
            recommendations.append(
                Recommendation(
                    id="review-pr-activity",
                    signal=signal.name,
                    priority=priority,
                    points_lost=points_lost,
                    why_it_matters=(
                        "Merged pull requests show whether outside contributions are making it "
                        "through review."
                    ),
                    next_action=next_action,
                    evidence=evidence,
                )
            )
        elif signal.name == "Contribution documentation":
            if signal.points == 9:
                next_action = "Add a code of conduct if the project does not inherit one elsewhere."
                recommendation_id = "add-code-of-conduct"
            elif signal.points == 5:
                next_action = "Add a contribution guide for outside contributors."
                recommendation_id = "add-contributing"
            else:
                next_action = "Add CONTRIBUTING and CODE_OF_CONDUCT documentation."
                recommendation_id = "add-contributor-docs"
            recommendations.append(
                Recommendation(
                    id=recommendation_id,
                    signal=signal.name,
                    priority=priority,
                    points_lost=points_lost,
                    why_it_matters=(
                        "Contributor documentation reduces guesswork for first-time pull "
                        "requests and moderation expectations."
                    ),
                    next_action=next_action,
                    evidence=evidence,
                )
            )
        elif signal.name == "Pull request template":
            recommendations.append(
                Recommendation(
                    id="add-pr-template",
                    signal=signal.name,
                    priority=priority,
                    points_lost=points_lost,
                    why_it_matters=(
                        "A pull request template nudges contributors to include review-ready "
                        "context."
                    ),
                    next_action=(
                        "Add a pull request template that asks for summary, tests, and issue links."
                    ),
                    evidence=evidence,
                )
            )
        elif signal.name == "CI and test signals":
            recommendations.append(
                Recommendation(
                    id="add-ci-tests",
                    signal=signal.name,
                    priority=priority,
                    points_lost=points_lost,
                    why_it_matters=(
                        "Visible validation gives contributors fast feedback before maintainers "
                        "spend review time."
                    ),
                    next_action="Add visible CI and tests so contributors can validate changes.",
                    evidence=evidence,
                )
            )
        elif signal.name == "Open pull request queue":
            recommendations.append(
                Recommendation(
                    id="reduce-pr-backlog",
                    signal=signal.name,
                    priority=priority,
                    points_lost=points_lost,
                    why_it_matters=(
                        "A large open PR queue can indicate slow review, unclear ownership, or "
                        "stalled contribution flow."
                    ),
                    next_action="Reduce open PR backlog or document review expectations.",
                    evidence=evidence,
                )
            )
        elif signal.name == "Issue triage signals":
            recommendations.append(
                Recommendation(
                    id="improve-issue-triage",
                    signal=signal.name,
                    priority=priority,
                    points_lost=points_lost,
                    why_it_matters=(
                        "Issue labels and maintainer responses help contributors find scoped "
                        "work and avoid stale threads."
                    ),
                    next_action=(
                        "Review issue triage gaps in labels, stale unanswered issues, and "
                        "recent maintainer responses."
                    ),
                    evidence=evidence,
                )
            )
    return recommendations


def _recommendation_priority(points_lost: int) -> str:
    if points_lost >= 12:
        return "high"
    if points_lost >= 5:
        return "medium"
    return "low"


def _recommendation_evidence(signal: Signal) -> tuple[str, ...]:
    evidence = [signal.detail]
    if signal.sampled:
        evidence.append(f"Sampled {signal.sample_size}/{signal.sample_total} open issues.")
    if signal.confidence != "high":
        evidence.append(f"Confidence: {signal.confidence}.")
    return tuple(evidence)


def _verdict(score: int, max_score: int, *, archived: bool = False) -> str:
    if archived:
        return "needs-work"

    percent = (score / max_score * 100) if max_score else 0
    if percent >= 75:
        return "strong"
    if percent >= 55:
        return "promising"
    return "needs-work"


def _parse_github_datetime(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _scaled_points(max_points: int, ratio: float) -> int:
    return max(1, round(max_points * ratio))


def _contributor_labels(snapshot: RepositorySnapshot) -> tuple[str, ...]:
    labels = {label for label in snapshot.labels if _is_contributor_friendly_label(label)}
    for issue in snapshot.open_issues:
        labels.update(label for label in issue.labels if _is_contributor_friendly_label(label))
    return tuple(sorted(labels, key=str.casefold))


def _is_contributor_friendly_label(label: str) -> bool:
    normalized = _normalize_label(label)
    exact_matches = {
        "beginner",
        "documentation",
        "docs",
        "easy",
        "first timers only",
        "good first bug",
        "good first issue",
        "help wanted",
        "up for grabs",
    }
    prefixes = ("good first", "help wanted", "first timer")
    return normalized in exact_matches or any(normalized.startswith(prefix) for prefix in prefixes)


def _is_pull_request_template_entry(entry: str) -> bool:
    if entry in PULL_REQUEST_TEMPLATE_FILES:
        return True
    for directory in PULL_REQUEST_TEMPLATE_DIRECTORIES:
        if not entry.startswith(directory):
            continue
        filename = entry.removeprefix(directory)
        if filename and "/" not in filename and filename.lower().endswith(".md"):
            return True
    return False


def _is_stale_unanswered_external_issue(issue: IssueSnapshot, cutoff: datetime) -> bool:
    if issue.created_at is None:
        return False
    if issue.author_association.upper() in MAINTAINER_ASSOCIATIONS:
        return False
    if issue.latest_maintainer_comment_at is not None:
        return False
    return issue.created_at <= cutoff


def _normalize_label(label: str) -> str:
    return " ".join(label.strip().lower().replace("_", " ").replace("-", " ").split())
