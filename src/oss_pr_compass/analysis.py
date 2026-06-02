from __future__ import annotations

from datetime import datetime, timedelta, timezone

from oss_pr_compass.model import Assessment, RepositorySnapshot, Signal

MAX_SCORE = 100


def assess_repository(
    snapshot: RepositorySnapshot,
    *,
    days: int = 90,
    now: datetime | None = None,
) -> Assessment:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    merged_recently = [
        pr for pr in snapshot.merged_prs if _parse_github_datetime(pr.get("merged_at")) >= cutoff
    ]

    signals = (
        _license_signal(snapshot),
        _activity_signal(snapshot, now),
        _merged_pr_signal(merged_recently, days),
        _contribution_docs_signal(snapshot),
        _pr_template_signal(snapshot),
        _ci_and_tests_signal(snapshot),
        _open_pr_queue_signal(snapshot),
    )
    score = sum(signal.points for signal in signals)
    recommendations = _recommendations(signals)

    return Assessment(
        repository=snapshot.full_name,
        url=snapshot.html_url,
        score=score,
        max_score=MAX_SCORE,
        verdict=_verdict(score),
        signals=signals,
        recommendations=tuple(recommendations),
    )


def _license_signal(snapshot: RepositorySnapshot) -> Signal:
    if snapshot.license_spdx:
        return Signal("OSS license", 15, 15, f"Detected {snapshot.license_spdx}.")
    return Signal("OSS license", 0, 15, "No license metadata was detected by GitHub.")


def _activity_signal(snapshot: RepositorySnapshot, now: datetime) -> Signal:
    if snapshot.archived:
        return Signal("Recent repository activity", 0, 15, "Repository is archived.")
    if snapshot.pushed_at is None:
        return Signal("Recent repository activity", 0, 15, "No push timestamp was available.")

    age_days = (now - snapshot.pushed_at).days
    if age_days <= 45:
        return Signal("Recent repository activity", 15, 15, f"Last push was {age_days} days ago.")
    if age_days <= 90:
        return Signal("Recent repository activity", 8, 15, f"Last push was {age_days} days ago.")
    return Signal("Recent repository activity", 0, 15, f"Last push was {age_days} days ago.")


def _merged_pr_signal(merged_recently: list[dict[str, object]], days: int) -> Signal:
    count = len(merged_recently)
    if count >= 20:
        points = 20
    elif count >= 5:
        points = 15
    elif count >= 1:
        points = 8
    else:
        points = 0
    return Signal("Merged pull request activity", points, 20, f"{count} merged PRs in {days} days.")


def _contribution_docs_signal(snapshot: RepositorySnapshot) -> Signal:
    entries = snapshot.root_entries
    has_contributing = "CONTRIBUTING.md" in entries or ".github/CONTRIBUTING.md" in entries
    has_code_of_conduct = "CODE_OF_CONDUCT.md" in entries or ".github/CODE_OF_CONDUCT.md" in entries
    points = (10 if has_contributing else 0) + (5 if has_code_of_conduct else 0)

    parts = []
    parts.append("contribution guide" if has_contributing else "no contribution guide")
    parts.append("code of conduct" if has_code_of_conduct else "no code of conduct")
    return Signal("Contribution documentation", points, 15, ", ".join(parts) + ".")


def _pr_template_signal(snapshot: RepositorySnapshot) -> Signal:
    entries = snapshot.root_entries
    has_template = any(
        entry in entries
        for entry in (
            "PULL_REQUEST_TEMPLATE.md",
            ".github/PULL_REQUEST_TEMPLATE.md",
            ".github/pull_request_template.md",
        )
    )
    if has_template:
        return Signal("Pull request template", 10, 10, "Pull request template found.")
    return Signal("Pull request template", 0, 10, "No pull request template found.")


def _ci_and_tests_signal(snapshot: RepositorySnapshot) -> Signal:
    has_ci = bool(snapshot.workflow_entries)
    has_tests = "tests" in snapshot.root_entries or "test" in snapshot.root_entries
    points = (8 if has_ci else 0) + (7 if has_tests else 0)
    detail = []
    detail.append("CI workflows" if has_ci else "no CI workflows")
    detail.append("tests directory" if has_tests else "no tests directory")
    return Signal("CI and test signals", points, 15, ", ".join(detail) + ".")


def _open_pr_queue_signal(snapshot: RepositorySnapshot) -> Signal:
    count = snapshot.open_pr_count
    if count <= 10:
        points = 10
    elif count <= 50:
        points = 7
    elif count <= 100:
        points = 4
    else:
        points = 0
    return Signal("Open pull request queue", points, 10, f"{count} open PRs sampled.")


def _recommendations(signals: tuple[Signal, ...]) -> list[str]:
    recommendations = []
    for signal in signals:
        if signal.points == signal.max_points:
            continue
        if signal.name == "OSS license":
            recommendations.append("Add a standard open source license file.")
        elif signal.name == "Recent repository activity":
            recommendations.append("Check whether the repository is still actively maintained.")
        elif signal.name == "Merged pull request activity":
            if signal.points == 0:
                recommendations.append(
                    "Review recent closed PRs before investing in a contribution."
                )
        elif signal.name == "Contribution documentation":
            if signal.points == 10:
                recommendations.append(
                    "Add a code of conduct if the project does not inherit one elsewhere."
                )
            elif signal.points == 5:
                recommendations.append("Add a contribution guide for outside contributors.")
            else:
                recommendations.append("Add CONTRIBUTING and CODE_OF_CONDUCT documentation.")
        elif signal.name == "Pull request template":
            recommendations.append(
                "Add a pull request template that asks for summary, tests, and issue links."
            )
        elif signal.name == "CI and test signals":
            recommendations.append("Add visible CI and tests so contributors can validate changes.")
        elif signal.name == "Open pull request queue":
            recommendations.append("Reduce open PR backlog or document review expectations.")
    return recommendations


def _verdict(score: int) -> str:
    if score >= 75:
        return "strong"
    if score >= 55:
        return "promising"
    return "needs-work"


def _parse_github_datetime(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
