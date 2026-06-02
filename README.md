# oss-pr-compass

`oss-pr-compass` is a small CLI for checking whether a public GitHub repository looks ready for healthy outside
contributions. It gathers public repository signals, recent merged pull requests, open pull request load, contribution
docs, pull request templates, CI presence, and license metadata, then produces a compact readiness score.

The goal is not to replace maintainer judgment. The tool gives contributors and maintainers a fast, reproducible first
pass before investing time in a repository.

## Features

- Inspect public GitHub repositories with the GitHub REST API.
- Score contribution readiness from transparent, deterministic signals.
- Report recent merged pull request activity and open pull request queue pressure.
- Detect repository basics such as license, contribution docs, pull request templates, tests, and CI.
- Output either human-readable text or JSON for automation.

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

## Scoring

The score is intentionally simple and inspectable:

- OSS license
- Recent repository activity
- Merged pull request activity
- Contribution documentation
- Pull request template
- CI and test signals
- Manageable open pull request queue

Scores are grouped into three verdicts:

- `strong`: likely healthy for outside contributions
- `promising`: useful signals exist, but check context carefully
- `needs-work`: missing signals or inactive review flow

## Project Status

This project is new. The first release focuses on repository-level signals that are available from public GitHub APIs.
Future work may add GitHub Actions output, issue-label analysis, and repository-local configuration.

## Contributing

Issues and pull requests are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before sending a change.

