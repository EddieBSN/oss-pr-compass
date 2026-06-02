# Maintainers

`oss-pr-compass` is maintained by Eddie (`EddieBSN` on GitHub).

## Review Expectations

- Issues should be triaged with a type label such as `bug`, `enhancement`, or `documentation`.
- Pull requests should link an issue unless they are documentation-only or housekeeping changes.
- Maintainers should ask for tests when behavior, scoring, CLI output, or GitHub API handling changes.
- Security-sensitive reports should follow `SECURITY.md` instead of public issue discussion.

## Merge Policy

- `main` should stay releasable.
- Pull requests should pass the full CI workflow before merge.
- Squash merges are preferred while the project is small.
- The PR description should explain user-facing behavior, tests run, and any scoring or output compatibility impact.

## Release Checklist

1. Update `CHANGELOG.md`.
2. Confirm `pyproject.toml` has the intended version.
3. Run `ruff check .`, `ruff format --check .`, and `pytest`.
4. Confirm CI is green on `main`.
5. Build wheel and sdist with `python -m build`.
6. Smoke test the installed wheel with `oss-pr-compass --help`.
7. Create a Git tag for the release.
8. Publish artifacts when packaging is ready for distribution.

## Maintainer Workflow

Maintainers can use the project itself to review contribution readiness changes:

- run the CLI against candidate repositories to compare scoring behavior;
- use Markdown summaries in pull request discussions or scheduled repository checks;
- use issue labels to keep bug reports, feature requests, and documentation work visible;
- use AI-assisted review for issue triage, pull request review, release preparation, and security review, while keeping final maintainer decisions human-owned.
