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
- Fail or warn in CI when a repository falls below a score or verdict policy.
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
oss-pr-compass pypa/pipx --fail-under 75
```

Repository input can be `owner/name` or a GitHub repository URL. Query strings, fragments, and a repository `.git`
suffix are normalized away; issue, pull request, tree, and blob URLs are rejected.

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

Example workflow step:

```yaml
name: Repository readiness

on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  compass:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"
      - name: Install oss-pr-compass
        run: python -m pip install oss-pr-compass
      - name: Score repository
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: oss-pr-compass pypa/pipx --fail-on-verdict needs-work --github-step-summary
```

## CI Policy Gates

Use policy flags when a workflow should fail on low-readiness repositories:

```bash
oss-pr-compass pypa/pipx --fail-under 75
oss-pr-compass pypa/pipx --fail-on-verdict needs-work
oss-pr-compass pypa/pipx --fail-under 75 --warn-only
```

`--fail-under` checks the normalized score from 0 to 100. `--fail-on-verdict` fails when the verdict is the selected
value or lower, so `needs-work` fails only `needs-work`, while `promising` fails both `promising` and `needs-work`.
`--warn-only` prints the same policy failure to stderr without changing the exit status.

JSON output includes both `recommendations` and `recommendation_details`. The detailed form includes priority, points
lost, why the signal matters, the next action, and evidence from the underlying signal. Signals may also include
`confidence`, `sampled`, `sample_size`, and `sample_total` when a large repository requires sampled issue triage.

## Scoring

The score is intentionally simple and inspectable:

| Signal | Max points | Full credit | Partial credit |
| --- | ---: | --- | --- |
| OSS license | 12 | GitHub reports license metadata. | None. |
| Recent repository activity | 14 | Repository is not archived and was pushed within 45 days. | 8 points when pushed within 90 days. |
| Merged pull request activity | 18 | At least 20 external human merged PRs in the lookback window. | 14 points for at least 5, or 7 points for at least 1. Maintainer and bot PRs are reported but do not count toward outside-contribution credit. |
| Contribution documentation | 14 | `CONTRIBUTING.md` and `CODE_OF_CONDUCT.md` are present. | 9 points for contributing docs, 5 points for code of conduct. |
| Pull request template | 8 | A supported pull request template is present in the root, `docs/`, `.github/`, or a `PULL_REQUEST_TEMPLATE/` directory. | None. |
| CI and test signals | 14 | CI workflows and a `tests` or `test` directory are present. | 7 points for CI, 7 points for tests. |
| Open pull request queue | 8 | 10 or fewer ready-for-review open PRs. | 6 points for 50 or fewer, or 3 points for 100 or fewer. Draft PRs are reported but excluded from review queue pressure. |
| Issue triage signals | 12 | Contributor labels, labeled open issues, manageable issue count, few stale unanswered issues, and recent maintainer responses are all present. | Subscores are contributor labels 3, labeled issues 3, issue queue 2, stale unanswered issues 2, maintainer responses 2. |

Scores are grouped into three verdicts:

- `strong`: likely healthy for outside contributions
- `promising`: useful signals exist, but check context carefully
- `needs-work`: missing signals or inactive review flow

Archived repositories always receive a `needs-work` verdict, even when they still have otherwise strong repository
metadata.

GitHub API collection follows Link-header pagination with endpoint-specific caps to avoid unbounded workflow runtime.
Merged PR activity, open PR queues, and open issue queues come from GitHub Search. Merged PR activity uses a
lookback-bound `is:merged merged:>=...` query instead of capped recently updated closed PR pages, then classifies
merged PR authors as external human, maintainer, or bot/app before scoring. Open PR queue pressure uses `draft:false`
for ready-for-review PRs and separately reports `draft:true` PRs. Issue triage samples use `type:issue` Search so PRs
cannot crowd actual issues out of the inspected sample, and stale-unanswered checks use a separate oldest-updated open
issue sample so old quiet issues are not hidden by recent activity. If GitHub Search reports incomplete count results or
cannot return the merged PR items needed for classification within the bounded page limit, `oss-pr-compass` exits with a
GitHub API error instead of treating those totals as exact. Idempotent GitHub GET requests retry transient network
failures, HTTP 502/503/504 responses, and short `Retry-After` windows for 429 or secondary-rate-limit 403 responses.
Pull request template detection covers root, `docs/`, `.github/`, and supported `PULL_REQUEST_TEMPLATE/` directories.
Issue triage quality samples recently updated open issues and comments; large repositories, or repositories where the
issue sample is smaller than the total issue count, are marked with sampled confidence metadata.

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

Accepted `disabled_signals` values are the signal names from the scoring table. The parser also accepts lowercase,
dash-separated, or underscore-separated aliases such as `pull_request_template`.

Supported threshold keys:

| Key | Default | Type | Validation |
| --- | ---: | --- | --- |
| `recent_activity_full_days` | 45 | integer | positive, must be <= `recent_activity_partial_days` |
| `recent_activity_partial_days` | 90 | integer | positive |
| `merged_prs_full` | 20 | integer | positive, must be >= `merged_prs_partial` |
| `merged_prs_partial` | 5 | integer | positive, must be >= `merged_prs_minimum` |
| `merged_prs_minimum` | 1 | integer | positive |
| `open_pr_queue_full` | 10 | integer | positive, must be <= `open_pr_queue_partial` |
| `open_pr_queue_partial` | 50 | integer | positive, must be <= `open_pr_queue_minimum` |
| `open_pr_queue_minimum` | 100 | integer | positive |
| `open_issue_queue_full` | 50 | integer | positive, must be <= `open_issue_queue_partial` |
| `open_issue_queue_partial` | 100 | integer | positive |
| `issue_label_ratio_full` | 0.75 | number | between 0 and 1, must be >= `issue_label_ratio_partial` |
| `issue_label_ratio_partial` | 0.50 | number | between 0 and 1 |
| `stale_unanswered_days` | 30 | integer | positive |
| `stale_unanswered_partial_ratio` | 0.10 | number | between 0 and 1 |
| `stale_unanswered_minimum` | 2 | integer | non-negative |
| `maintainer_response_window_days` | 30 | integer | positive |
| `maintainer_response_full_ratio` | 0.25 | number | between 0 and 1, must be >= `maintainer_response_partial_ratio` |
| `maintainer_response_partial_ratio` | 0.10 | number | between 0 and 1 |

## Troubleshooting

- Invalid repository: use `owner/name` or `https://github.com/owner/name`, not issue, pull request, tree, or blob URLs.
- Rate limits: set `GITHUB_TOKEN` or pass `--token` for higher GitHub API limits.
- Missing step summary: `--github-step-summary` needs a path when `GITHUB_STEP_SUMMARY` is not set.
- Policy failure: use `--warn-only` while tuning thresholds before enforcing `--fail-under` or `--fail-on-verdict`.
- Invalid config: `.oss-pr-compass.json` must be strict JSON with only supported keys.
- Unknown thresholds or signals: check the tables above for accepted names.
- GitHub 404s: confirm the repository is public and the API token can read it.

## Project Status

This project is new. The first release focuses on repository-level signals that are available from public GitHub APIs.
See [MAINTAINERS.md](MAINTAINERS.md) for maintainer workflow and release expectations.

## Contributing

Issues and pull requests are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before sending a change.
