# Security Policy

`oss-pr-compass` reads public GitHub repository metadata and optionally uses a GitHub token for higher API limits. It
does not need write access to GitHub.

## Reporting a Vulnerability

Please do not publish security-sensitive findings as public issues before the maintainer has had a chance to review
them. Report vulnerabilities through GitHub private vulnerability reporting if enabled, or contact the repository owner
directly.

## Token Handling

When using `--token` or `GITHUB_TOKEN`, pass a read-only token whenever possible. The CLI does not print token values and
does not persist credentials.

