from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from oss_pr_compass.analysis import assess_repository
from oss_pr_compass.config import (
    ScoreConfig,
    ScoreConfigError,
    load_score_config,
    parse_score_config,
)
from oss_pr_compass.github import GitHubClient, GitHubError
from oss_pr_compass.model import Assessment, Signal


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oss-pr-compass",
        description="Score a public GitHub repository for contribution readiness.",
    )
    parser.add_argument("repository", help="GitHub repository, for example 'pypa/pipx'.")
    parser.add_argument("--days", type=int, default=90, help="Lookback window for merged PRs.")
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    output_group.add_argument(
        "--markdown",
        action="store_true",
        help="Print Markdown suitable for PR comments or GitHub Actions summaries.",
    )
    parser.add_argument(
        "--github-step-summary",
        nargs="?",
        const="",
        metavar="PATH",
        help=("Write a Markdown summary to PATH, or to GITHUB_STEP_SUMMARY when PATH is omitted."),
    )
    parser.add_argument(
        "--config",
        help="Read local scoring configuration from a JSON file.",
    )
    parser.add_argument(
        "--no-remote-config",
        action="store_true",
        help="Ignore .oss-pr-compass.json from the target repository.",
    )
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
        config = _load_score_config(client, args.repository, args.config, args.no_remote_config)
    except (GitHubError, ScoreConfigError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    assessment = assess_repository(snapshot, days=args.days, config=config)

    if args.github_step_summary is not None:
        try:
            summary_path = _github_step_summary_path(args.github_step_summary)
            summary_path.write_text(format_markdown(assessment) + "\n", encoding="utf-8")
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    if args.json:
        print(json.dumps(assessment.to_dict(), indent=2, sort_keys=True))
    elif args.markdown:
        print(format_markdown(assessment))
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


def format_markdown(assessment: Assessment) -> str:
    lines = [
        f"## oss-pr-compass: `{assessment.repository}`",
        "",
        f"[Repository]({assessment.url})",
        "",
        f"**Score:** {assessment.score}/{assessment.max_score} (`{assessment.verdict}`)",
        "",
        "| Signal | Result | Score | Detail |",
        "| --- | --- | ---: | --- |",
    ]
    for signal in assessment.signals:
        lines.append(
            "| "
            f"{_escape_table_cell(signal.name)} | "
            f"{_signal_marker(signal)} | "
            f"{signal.points}/{signal.max_points} | "
            f"{_escape_table_cell(signal.detail)} |"
        )

    if assessment.recommendations:
        lines.extend(["", "### Recommendations"])
        lines.extend(
            f"- {_escape_markdown_text(recommendation)}"
            for recommendation in assessment.recommendations
        )

    return "\n".join(lines)


def _load_score_config(
    client: GitHubClient,
    repository: str,
    local_config_path: str | None,
    no_remote_config: bool,
) -> ScoreConfig:
    config = ScoreConfig()
    if not no_remote_config:
        remote_config = client.fetch_file_text(repository, ".oss-pr-compass.json")
        if remote_config is not None:
            config = parse_score_config(
                remote_config,
                source=f"{repository}:.oss-pr-compass.json",
                base=config,
            )

    if local_config_path:
        config = load_score_config(local_config_path, base=config)

    return config


def _github_step_summary_path(value: str) -> Path:
    if value:
        return Path(value)

    env_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not env_path:
        raise ValueError("--github-step-summary requires PATH when GITHUB_STEP_SUMMARY is not set")
    return Path(env_path)


def _signal_marker(signal: Signal) -> str:
    if signal.points == signal.max_points:
        return "PASS"
    if signal.passed:
        return "PART"
    return "MISS"


def _escape_table_cell(value: str) -> str:
    return _escape_markdown_text(value).replace("\n", "<br>").replace("|", "\\|")


def _escape_markdown_text(value: str) -> str:
    return value.replace("\\", "\\\\")


if __name__ == "__main__":
    raise SystemExit(main())
