# Contributing

Thanks for helping improve `oss-pr-compass`.

## Development Setup

```bash
python -m pip install -e ".[dev]"
```

Run the checks before opening a pull request:

```bash
ruff check .
ruff format --check .
pytest
```

## Pull Request Guidelines

- Keep changes focused and easy to review.
- Explain the user-facing behavior being changed.
- Add or update tests for code changes.
- Update the README when changing CLI behavior or scoring semantics.
- Do not include generated files, local virtual environments, or credentials.
- Link the issue the pull request closes when one exists.
- Keep `main` releasable; pull requests should pass CI before merge.
- Use squash merges while the project is small unless a maintainer calls out a reason not to.

The pull request template asks for summary, behavior changes, tests, and a linked issue. For release process and
maintainer expectations, see [MAINTAINERS.md](MAINTAINERS.md).

## Reporting Issues

When reporting a bug, include:

- the command you ran
- the repository being inspected
- whether you used authenticated GitHub API access
- the observed output
- the expected output
