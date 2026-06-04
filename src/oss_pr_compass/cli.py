from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from oss_pr_compass.analysis import assess_repository
from oss_pr_compass.config import (
    ScoreConfig,
    ScoreConfigError,
    load_score_config,
    parse_score_config,
)
from oss_pr_compass.github import GitHubClient, GitHubError, parse_repository
from oss_pr_compass.model import Assessment, Signal

VERDICT_RANK = {"needs-work": 0, "promising": 1, "strong": 2}


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
        "--fail-under",
        type=float,
        metavar="SCORE",
        help="Exit with status 1 when the normalized score is below SCORE from 0 to 100.",
    )
    parser.add_argument(
        "--fail-on-verdict",
        choices=("strong", "promising", "needs-work"),
        help="Exit with status 1 when the verdict is this value or lower.",
    )
    parser.add_argument(
        "--warn-only",
        action="store_true",
        help="Print policy failures as warnings without changing the exit status.",
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
    if args.fail_under is not None and not 0 <= args.fail_under <= 100:
        parser.error("--fail-under must be between 0 and 100")

    try:
        owner, name = parse_repository(args.repository)
        requested_repository = f"{owner}/{name}"
        now = datetime.now(timezone.utc)
        client = GitHubClient(token=args.token, api_url=args.api_url)
        snapshot = client.fetch_snapshot(
            requested_repository,
            merged_since=now - timedelta(days=args.days),
        )
        config = _load_score_config(client, snapshot.full_name, args.config, args.no_remote_config)
    except (GitHubError, ScoreConfigError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    assessment = assess_repository(snapshot, days=args.days, now=now, config=config)

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

    policy_failure = _policy_failure_reason(
        assessment,
        fail_under=args.fail_under,
        fail_on_verdict=args.fail_on_verdict,
    )
    if policy_failure:
        prefix = "warning" if args.warn_only else "error"
        print(f"{prefix}: oss-pr-compass policy failed: {policy_failure}", file=sys.stderr)
        if not args.warn_only:
            return 1
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
            f"- {marker} {signal.name}: {signal.points}/{signal.max_points} - "
            f"{_signal_detail_for_output(signal)}"
        )

    if assessment.recommendations:
        lines.extend(["", "Recommendations:"])
        lines.extend(f"- {recommendation}" for recommendation in assessment.recommendations)

    if assessment.recommendation_details:
        lines.extend(["", "Recommendation details:"])
        for recommendation in assessment.recommendation_details:
            evidence = "; ".join(recommendation.evidence)
            lines.append(
                f"- [{recommendation.priority.upper()}] {recommendation.signal}: "
                f"{recommendation.points_lost} points lost. {recommendation.why_it_matters} "
                f"Next: {recommendation.next_action}"
                + (f" Evidence: {evidence}" if evidence else "")
            )

    return "\n".join(lines)


def format_markdown(assessment: Assessment) -> str:
    repository = _escape_markdown_inline(assessment.repository)
    repository_url = _safe_markdown_link_target(assessment.url)
    lines = [
        f"## oss-pr-compass: {repository}",
        "",
        f"[Repository]({repository_url})"
        if repository_url
        else f"Repository: {_escape_markdown_inline(assessment.url)}",
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
            f"{_escape_table_cell(_signal_detail_for_output(signal))} |"
        )

    if assessment.recommendations:
        lines.extend(["", "### Recommendations"])
        lines.extend(
            f"- {_escape_markdown_inline(recommendation)}"
            for recommendation in assessment.recommendations
        )

    if assessment.recommendation_details:
        lines.extend(
            [
                "",
                "### Recommendation Details",
                "| Priority | Signal | Points Lost | Next Action | Evidence |",
                "| --- | --- | ---: | --- | --- |",
            ]
        )
        for recommendation in assessment.recommendation_details:
            evidence = "; ".join(recommendation.evidence)
            lines.append(
                "| "
                f"{_escape_table_cell(recommendation.priority)} | "
                f"{_escape_table_cell(recommendation.signal)} | "
                f"{recommendation.points_lost} | "
                f"{_escape_table_cell(recommendation.next_action)} | "
                f"{_escape_table_cell(evidence)} |"
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


def _signal_detail_for_output(signal: Signal) -> str:
    if not signal.sampled and signal.confidence == "high":
        return signal.detail

    details = [signal.detail]
    if signal.sampled:
        details.append(f"sampled {signal.sample_size}/{signal.sample_total}")
    if signal.confidence != "high":
        details.append(f"confidence: {signal.confidence}")
    return f"{signal.detail} ({'; '.join(details[1:])})"


def _normalized_score(assessment: Assessment) -> float:
    if assessment.max_score <= 0:
        return 0.0
    return assessment.score / assessment.max_score * 100


def _policy_failure_reason(
    assessment: Assessment,
    *,
    fail_under: float | None,
    fail_on_verdict: str | None,
) -> str | None:
    reasons = []
    score = _normalized_score(assessment)
    if fail_under is not None and score < fail_under:
        reasons.append(f"score {score:.1f} is below {fail_under:g}")

    if fail_on_verdict is not None:
        actual_rank = VERDICT_RANK.get(assessment.verdict, -1)
        threshold_rank = VERDICT_RANK[fail_on_verdict]
        if actual_rank <= threshold_rank:
            reasons.append(f"verdict is {assessment.verdict!r}, at or below {fail_on_verdict!r}")

    if not reasons:
        return None
    return "; ".join(reasons)


def _escape_table_cell(value: str) -> str:
    return _escape_markdown_text(value).replace("\n", "<br>").replace("|", "\\|")


def _escape_markdown_text(value: str) -> str:
    escaped = (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\\", "\\\\")
    )
    for char in ("`", "*", "_", "{", "}", "[", "]", "(", ")", "#", "+", "!", "@"):
        escaped = escaped.replace(char, f"\\{char}")
    return escaped.replace("\r\n", "\n").replace("\r", "\n")


def _escape_markdown_inline(value: str) -> str:
    return " ".join(_escape_markdown_text(value).splitlines())


def _safe_markdown_link_target(value: str) -> str:
    stripped = value.strip()
    parsed = urllib.parse.urlparse(stripped)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    if any(char in stripped for char in "\r\n<>"):
        return ""
    return urllib.parse.quote(stripped, safe=":/?#@!$&'*,;=%")


if __name__ == "__main__":
    raise SystemExit(main())
