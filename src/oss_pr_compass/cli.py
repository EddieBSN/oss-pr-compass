from __future__ import annotations

import argparse
import json
import os
import sys

from oss_pr_compass.analysis import assess_repository
from oss_pr_compass.github import GitHubClient, GitHubError
from oss_pr_compass.model import Assessment


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oss-pr-compass",
        description="Score a public GitHub repository for contribution readiness.",
    )
    parser.add_argument("repository", help="GitHub repository, for example 'pypa/pipx'.")
    parser.add_argument("--days", type=int, default=90, help="Lookback window for merged PRs.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument(
        "--token", default=os.getenv("GITHUB_TOKEN"), help="GitHub token for higher API limits."
    )
    parser.add_argument("--api-url", default="https://api.github.com", help="GitHub API base URL.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.days <= 0:
        parser.error("--days must be greater than zero")

    client = GitHubClient(token=args.token, api_url=args.api_url)
    try:
        snapshot = client.fetch_snapshot(args.repository)
    except (GitHubError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    assessment = assess_repository(snapshot, days=args.days)
    if args.json:
        print(json.dumps(assessment.to_dict(), indent=2, sort_keys=True))
    else:
        print(format_assessment(assessment))
    return 0


def format_assessment(assessment: Assessment) -> str:
    lines = [
        f"Repository: {assessment.repository}",
        f"URL: {assessment.url}",
        f"Score: {assessment.score}/{assessment.max_score} ({assessment.verdict})",
        "",
        "Signals:",
    ]
    for signal in assessment.signals:
        if signal.points == signal.max_points:
            marker = "PASS"
        elif signal.passed:
            marker = "PART"
        else:
            marker = "MISS"
        lines.append(
            f"- {marker} {signal.name}: {signal.points}/{signal.max_points} - {signal.detail}"
        )

    if assessment.recommendations:
        lines.extend(["", "Recommendations:"])
        lines.extend(f"- {recommendation}" for recommendation in assessment.recommendations)

    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
