# oss-pr-compass

`oss-pr-compass` is a small CLI for checking whether a public GitHub repository looks ready for healthy outside
contributions. It gathers public repository signals, recent merged pull requests, open pull request load, contribution
docs, pull request templates, CI presence, issue triage metadata, and license metadata, then produces a compact
readiness score.

The goal is not to replace maintainer judgment. The tool gives contributors and maintainers a fast, reproducible first
pass before investing time in a repository.

## Features

- Inspect public GitHub repositories with the GitHub REST API.
- Score contribution readiness from transparent, deterministic signals.
- Report recent merged pull request activity and open pull request queue pressure.
- Inspect issue triage health from contributor-friendly labels, labeled issue coverage, stale unanswered issues, and
  recent maintainer responses.
- Detect repository basics such as license, contribution docs, pull request templates, tests, and CI.
- Output human-readable text, JSON, or Markdown for automation.
- Respect optional repository-local scoring configuration from `.oss-pr-compass.json`.

## Installation

```bash
python -m pip install oss-pr-compass
```

For local development from a checkout:

```bash
python -m pip install -e ".[dev]"
```

## Usage

```bash
oss-pr-compass pypa/pipx
oss-pr-compass pypa/pipx --json
oss-pr-compass pypa/pipx --markdown
oss-pr-compass pypa/pipx --days 30
```

Authenticated requests get higher GitHub API limits:

```bash
GITHUB_TOKEN=ghp_... oss-pr-compass pypa/pipx
```

You can also pass a token explicitly:

```bash
oss-pr-compass pypa/pipx --token "$GITHUB_TOKEN"
```

For GitHub Actions, write Markdown to the step summary:

```bash
oss-pr-compass pypa/pipx --github-step-summary
```

You can also write the same Markdown to an explicit file:

```bash
oss-pr-compass pypa/pipx --github-step-summary compass-summary.md
```

## Scoring

The score is intentionally simple and inspectable:

- OSS license
- Recent repository activity
- Merged pull request activity
- Contribution documentation
- Pull request template
- CI and test signals
- Manageable open pull request queue
- Issue triage signals

Scores are grouped into three verdicts:

- `strong`: likely healthy for outside contributions
- `promising`: useful signals exist, but check context carefully
- `needs-work`: missing signals or inactive review flow

## Scoring Configuration

Repositories can include `.oss-pr-compass.json` at the repository root to tune thresholds or disable signals that do
not apply to their governance model. Local config can be layered on top with `--config`, and remote config can be
ignored with `--no-remote-config`.

Example:

```json
{
  "disabled_signals": ["Pull request template"],
  "thresholds": {
    "recent_activity_full_days": 60,
    "recent_activity_partial_days": 120,
    "merged_prs_full": 15,
    "merged_prs_partial": 4,
    "open_pr_queue_full": 15,
    "open_issue_queue_full": 75,
    "issue_label_ratio_full": 0.8,
    "stale_unanswered_days": 45,
    "maintainer_response_window_days": 45
  }
}
```

Unknown keys are rejected so configuration mistakes are visible in CI.

## Project Status

This project is new. The first release focuses on repository-level signals that are available from public GitHub APIs.

## Contributing

Issues and pull requests are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before sending a change.
