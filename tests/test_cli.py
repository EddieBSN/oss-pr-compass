from __future__ import annotations

from oss_pr_compass.cli import _policy_failure_reason, format_assessment, format_markdown, main
from oss_pr_compass.github import GitHubError
from oss_pr_compass.model import Assessment, Recommendation, RepositorySnapshot, Signal


def test_format_assessment_includes_signals_and_recommendations() -> None:
    assessment = Assessment(
        repository="owner/repo",
        url="https://github.com/owner/repo",
        score=65,
        max_score=100,
        verdict="promising",
        signals=(
            Signal("OSS license", 15, 15, "Detected MIT."),
            Signal("Pull request template", 0, 10, "No pull request template found."),
        ),
        recommendations=("Add a pull request template.",),
    )

    output = format_assessment(assessment)

    assert "Score: 65/100 (promising)" in output
    assert "PASS OSS license" in output
    assert "MISS Pull request template" in output
    assert "Add a pull request template." in output


def test_format_markdown_uses_table_and_recommendations() -> None:
    assessment = Assessment(
        repository="owner/repo",
        url="https://github.com/owner/repo",
        score=65,
        max_score=100,
        verdict="promising",
        signals=(
            Signal("OSS license", 15, 15, "Detected MIT."),
            Signal("Pull request template", 0, 10, "No pull request template found."),
        ),
        recommendations=("Add a pull request template.",),
    )

    output = format_markdown(assessment)

    assert "## oss-pr-compass: owner/repo" in output
    assert "| OSS license | PASS | 15/15 | Detected MIT. |" in output
    assert "| Pull request template | MISS | 0/10 | No pull request template found. |" in output
    assert "### Recommendations" in output


def test_format_markdown_escapes_untrusted_text() -> None:
    assessment = Assessment(
        repository="owner/repo`](/x)\n### injected",
        url="https://github.com/owner/repo)\n<script>",
        score=1,
        max_score=2,
        verdict="needs-work",
        signals=(
            Signal(
                "Issue | triage",
                0,
                2,
                "label [click](https://evil.example) <script>@team</script>",
            ),
        ),
        recommendations=("Add [link](javascript:alert(1))\n- injected",),
    )

    output = format_markdown(assessment)

    assert "### injected" not in output
    assert "[Repository](https://github.com/owner/repo)" not in output
    assert "Repository: https://github.com/owner/repo\\)" in output
    assert "\\[click\\]\\(https://evil.example\\)" in output
    assert "&lt;script&gt;\\@team&lt;/script&gt;" in output
    assert "\\[link\\]\\(javascript:alert\\(1\\)\\) - injected" in output


def test_format_markdown_includes_sampling_and_recommendation_details() -> None:
    assessment = Assessment(
        repository="owner/repo",
        url="https://github.com/owner/repo",
        score=88,
        max_score=100,
        verdict="strong",
        signals=(
            Signal(
                "Issue triage signals",
                10,
                12,
                "100/100 sampled open issues labeled; 250 total open issues.",
                confidence="sampled",
                sampled=True,
                sample_size=100,
                sample_total=250,
            ),
        ),
        recommendations=("Keep stale unanswered issues triaged.",),
        recommendation_details=(
            Recommendation(
                id="improve-issue-triage",
                signal="Issue triage signals",
                priority="low",
                points_lost=2,
                why_it_matters="Issue labels help contributors find scoped work.",
                next_action="Keep stale unanswered issues triaged.",
                evidence=("Sampled 100/250 open issues.",),
            ),
        ),
    )

    output = format_markdown(assessment)

    assert "sampled 100/250" in output
    assert "confidence: sampled" in output
    assert "### Recommendation Details" in output
    assert "| low | Issue triage signals | 2 | Keep stale unanswered issues triaged." in output


def test_policy_failure_reason_checks_score_and_verdict() -> None:
    assessment = Assessment(
        repository="owner/repo",
        url="https://github.com/owner/repo",
        score=54,
        max_score=100,
        verdict="needs-work",
        signals=(),
        recommendations=(),
    )

    reason = _policy_failure_reason(
        assessment,
        fail_under=70,
        fail_on_verdict="promising",
    )

    assert reason is not None
    assert "score 54.0 is below 70" in reason
    assert "verdict is 'needs-work'" in reason
    assert (
        _policy_failure_reason(
            assessment,
            fail_under=50,
            fail_on_verdict=None,
        )
        is None
    )


def test_main_reports_unsafe_api_url(capsys) -> None:
    assert main(["owner/repo", "--api-url", "http://api.example.test"]) == 2

    captured = capsys.readouterr()
    assert "error: --api-url must be an absolute HTTPS URL." in captured.err


def test_main_rejects_invalid_repository_before_api_paths(monkeypatch, capsys) -> None:
    class InvalidInputClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            pass

        def fetch_snapshot(self, repository: str) -> object:
            raise AssertionError("invalid repository input should fail before API paths are built")

    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", InvalidInputClient)

    assert main(["https://github.com/owner/repo/issues/1"]) == 2

    captured = capsys.readouterr()
    assert "error: repository must look like" in captured.err


def test_main_uses_canonical_repository_for_remote_config(monkeypatch, capsys) -> None:
    remote_config_repositories: list[str] = []

    class CanonicalClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            pass

        def fetch_snapshot(self, repository: str) -> RepositorySnapshot:
            return RepositorySnapshot(
                full_name="new/repo",
                html_url="https://github.com/new/repo",
                description="Example",
                stars=1,
                forks=2,
                archived=False,
                pushed_at=None,
                default_branch="main",
                license_spdx="MIT",
                topics=("python",),
                root_entries=frozenset({"README.md", "CONTRIBUTING.md"}),
                workflow_entries=frozenset({"ci.yml"}),
                merged_prs=(),
                open_pr_count=0,
                labels=("good first issue",),
                open_issues=(),
                open_issue_count=0,
            )

        def fetch_file_text(self, repository: str, path: str) -> str | None:
            remote_config_repositories.append(repository)
            return None

    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", CanonicalClient)

    assert main(["old/repo"]) == 0

    captured = capsys.readouterr()
    assert "Repository: new/repo" in captured.out
    assert remote_config_repositories == ["new/repo"]


def test_main_reports_github_timeout(monkeypatch, capsys) -> None:
    class TimeoutClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            pass

        def fetch_snapshot(self, repository: str) -> object:
            raise GitHubError("GitHub API timed out for /repos/owner/repo: timed out")

    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", TimeoutClient)

    assert main(["owner/repo"]) == 2

    captured = capsys.readouterr()
    assert "error: GitHub API timed out for /repos/owner/repo: timed out" in captured.err
