from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from oss_pr_compass.cli import _policy_failure_reason, format_assessment, format_markdown, main
from oss_pr_compass.config import MAX_DATE_WINDOW_DAYS
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


def test_format_outputs_include_no_data_issue_triage_confidence() -> None:
    assessment = Assessment(
        repository="owner/repo",
        url="https://github.com/owner/repo",
        score=76,
        max_score=100,
        verdict="strong",
        signals=(
            Signal(
                "Issue triage signals",
                7,
                12,
                "contributor labels: good first issue; no open issues; "
                "label coverage and maintainer response evidence unavailable.",
                confidence="no-data",
            ),
        ),
        recommendations=(),
    )

    text = format_assessment(assessment)
    markdown = format_markdown(assessment)

    assert "confidence: no-data" in text
    assert "no open issues" in text
    assert "confidence: no-data" in markdown
    assert "no open issues" in markdown


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


def test_policy_failure_reason_verdict_threshold_truth_table() -> None:
    expected = {
        ("strong", "promising"): False,
        ("strong", "needs-work"): False,
        ("promising", "promising"): True,
        ("promising", "needs-work"): False,
        ("needs-work", "promising"): True,
        ("needs-work", "needs-work"): True,
    }

    for (actual_verdict, threshold), should_fail in expected.items():
        assessment = Assessment(
            repository="owner/repo",
            url="https://github.com/owner/repo",
            score=80,
            max_score=100,
            verdict=actual_verdict,
            signals=(),
            recommendations=(),
        )

        reason = _policy_failure_reason(
            assessment,
            fail_under=None,
            fail_on_verdict=threshold,
        )

        assert (reason is not None) is should_fail


def test_policy_failure_reason_rejects_high_scoring_needs_work_assessment() -> None:
    assessment = Assessment(
        repository="owner/archived",
        url="https://github.com/owner/archived",
        score=90,
        max_score=100,
        verdict="needs-work",
        signals=(),
        recommendations=(),
        archived=True,
    )

    reason = _policy_failure_reason(
        assessment,
        fail_under=75,
        fail_on_verdict=None,
    )

    assert reason is not None
    assert "needs-work" in reason


def test_main_reports_unsafe_api_url(capsys) -> None:
    assert main(["owner/repo", "--api-url", "http://api.example.test"]) == 2

    captured = capsys.readouterr()
    assert "error: --api-url must be an absolute HTTPS URL." in captured.err


def test_main_rejects_days_above_maximum_before_network(monkeypatch, capsys) -> None:
    class NetworkFailingClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            raise AssertionError("--days validation should fail before GitHub client creation")

    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", NetworkFailingClient)

    with pytest.raises(SystemExit) as exc:
        main(["owner/repo", "--days", str(MAX_DATE_WINDOW_DAYS + 1)])

    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert f"--days must be at most {MAX_DATE_WINDOW_DAYS}" in captured.err


def test_main_accepts_maximum_days_boundary(monkeypatch, capsys) -> None:
    class BoundaryClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            pass

        def fetch_snapshot(self, repository: str, *, merged_since: object) -> RepositorySnapshot:
            assert merged_since is not None
            return _basic_snapshot()

        def fetch_file_text(self, repository: str, path: str) -> str | None:
            return None

    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", BoundaryClient)

    assert main(["owner/repo", "--days", str(MAX_DATE_WINDOW_DAYS)]) == 0

    captured = capsys.readouterr()
    assert f"{MAX_DATE_WINDOW_DAYS} days" in captured.out


def test_main_rejects_warn_only_without_policy_gate_before_network(monkeypatch, capsys) -> None:
    class NetworkFailingClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            raise AssertionError("--warn-only validation should fail before GitHub client creation")

    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", NetworkFailingClient)

    with pytest.raises(SystemExit) as exc:
        main(["owner/repo", "--warn-only"])

    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "--warn-only requires --fail-under or --fail-on-verdict" in captured.err


def test_main_rejects_fail_on_verdict_strong_before_network(monkeypatch, capsys) -> None:
    class NetworkFailingClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            raise AssertionError(
                "--fail-on-verdict validation should fail before GitHub client creation"
            )

    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", NetworkFailingClient)

    with pytest.raises(SystemExit) as exc:
        main(["owner/repo", "--fail-on-verdict", "strong"])

    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "invalid choice: 'strong'" in captured.err


def test_main_warn_only_allows_fail_under_policy_warning(monkeypatch, capsys) -> None:
    class PolicyClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            pass

        def fetch_snapshot(self, repository: str, *, merged_since: object) -> RepositorySnapshot:
            return _basic_snapshot()

        def fetch_file_text(self, repository: str, path: str) -> str | None:
            return None

    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", PolicyClient)

    assert main(["owner/repo", "--warn-only", "--fail-under", "100"]) == 0

    captured = capsys.readouterr()
    assert "warning: oss-pr-compass policy failed: score" in captured.err


def test_main_warn_only_allows_fail_on_verdict_policy_warning(monkeypatch, capsys) -> None:
    class PolicyClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            pass

        def fetch_snapshot(self, repository: str, *, merged_since: object) -> RepositorySnapshot:
            return _basic_snapshot()

        def fetch_file_text(self, repository: str, path: str) -> str | None:
            return None

    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", PolicyClient)

    assert main(["owner/repo", "--warn-only", "--fail-on-verdict", "needs-work"]) == 0

    captured = capsys.readouterr()
    assert "warning: oss-pr-compass policy failed: verdict" in captured.err


def test_main_warn_only_allows_both_policy_gates(monkeypatch, capsys) -> None:
    class PolicyClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            pass

        def fetch_snapshot(self, repository: str, *, merged_since: object) -> RepositorySnapshot:
            return _basic_snapshot()

        def fetch_file_text(self, repository: str, path: str) -> str | None:
            return None

    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", PolicyClient)

    assert (
        main(
            [
                "owner/repo",
                "--warn-only",
                "--fail-under",
                "100",
                "--fail-on-verdict",
                "needs-work",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "warning: oss-pr-compass policy failed:" in captured.err
    assert "score" in captured.err
    assert "verdict" in captured.err


def test_main_without_warn_only_and_without_policy_gate_is_unchanged(monkeypatch, capsys) -> None:
    class NormalClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            pass

        def fetch_snapshot(self, repository: str, *, merged_since: object) -> RepositorySnapshot:
            return _basic_snapshot()

        def fetch_file_text(self, repository: str, path: str) -> str | None:
            return None

    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", NormalClient)

    assert main(["owner/repo"]) == 0

    captured = capsys.readouterr()
    assert "Repository: owner/repo" in captured.out
    assert captured.err == ""


def test_main_fail_under_fails_high_scoring_archived_repository(monkeypatch, capsys) -> None:
    class ArchivedClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            pass

        def fetch_snapshot(self, repository: str, *, merged_since: object) -> RepositorySnapshot:
            return _high_scoring_snapshot(archived=True)

        def fetch_file_text(self, repository: str, path: str) -> str | None:
            return None

    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", ArchivedClient)

    assert main(["owner/repo", "--fail-under", "75"]) == 1

    captured = capsys.readouterr()
    assert "Score:" in captured.out
    assert "needs-work" in captured.out
    assert "error: oss-pr-compass policy failed:" in captured.err
    assert "needs-work" in captured.err


def test_main_warn_only_warns_for_high_scoring_archived_repository(monkeypatch, capsys) -> None:
    class ArchivedClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            pass

        def fetch_snapshot(self, repository: str, *, merged_since: object) -> RepositorySnapshot:
            return _high_scoring_snapshot(archived=True)

        def fetch_file_text(self, repository: str, path: str) -> str | None:
            return None

    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", ArchivedClient)

    assert main(["owner/repo", "--fail-under", "75", "--warn-only"]) == 0

    captured = capsys.readouterr()
    assert "warning: oss-pr-compass policy failed:" in captured.err
    assert "needs-work" in captured.err


def test_main_fail_under_preserves_non_archived_score_behavior(monkeypatch, capsys) -> None:
    class ActiveClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            pass

        def fetch_snapshot(self, repository: str, *, merged_since: object) -> RepositorySnapshot:
            return _high_scoring_snapshot(archived=False)

        def fetch_file_text(self, repository: str, path: str) -> str | None:
            return None

    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", ActiveClient)

    assert main(["owner/repo", "--fail-under", "75"]) == 0

    captured = capsys.readouterr()
    assert "strong" in captured.out
    assert captured.err == ""


def test_main_rejects_invalid_repository_before_api_paths(monkeypatch, capsys) -> None:
    class InvalidInputClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            pass

        def fetch_snapshot(self, repository: str, *, merged_since: object) -> object:
            raise AssertionError("invalid repository input should fail before API paths are built")

    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", InvalidInputClient)

    assert main(["https://github.com/owner/repo/issues/1"]) == 2

    captured = capsys.readouterr()
    assert "error: repository must look like" in captured.err


def test_main_validates_bad_local_config_before_network_methods(
    monkeypatch, capsys, tmp_path
) -> None:
    class NetworkFailingClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            pass

        def fetch_snapshot(self, repository: str, *, merged_since: object) -> RepositorySnapshot:
            raise AssertionError("bad local config should fail before snapshot fetch")

        def fetch_file_text(self, repository: str, path: str) -> str | None:
            raise AssertionError("bad local config should fail before remote config fetch")

    bad_config = tmp_path / "bad-score.json"
    bad_config.write_text('{"thresholds": {"open_pr_queue_full": 0}}', encoding="utf-8")
    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", NetworkFailingClient)

    assert main(["owner/repo", "--config", str(bad_config)]) == 2

    captured = capsys.readouterr()
    assert str(bad_config) in captured.err
    assert "open_pr_queue_full must be a positive integer" in captured.err


def test_main_validates_bad_no_remote_local_config_before_snapshot(
    monkeypatch, capsys, tmp_path
) -> None:
    class NetworkFailingClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            pass

        def fetch_snapshot(self, repository: str, *, merged_since: object) -> RepositorySnapshot:
            raise AssertionError("bad local config should fail before snapshot fetch")

        def fetch_file_text(self, repository: str, path: str) -> str | None:
            raise AssertionError("--no-remote-config should not fetch remote config")

    bad_config = tmp_path / "bad-score.json"
    bad_config.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", NetworkFailingClient)

    assert main(["owner/repo", "--no-remote-config", "--config", str(bad_config)]) == 2

    captured = capsys.readouterr()
    assert str(bad_config) in captured.err
    assert "is not valid JSON" in captured.err


def test_main_uses_canonical_repository_for_remote_config(monkeypatch, capsys) -> None:
    remote_config_repositories: list[str] = []

    class CanonicalClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            pass

        def fetch_snapshot(self, repository: str, *, merged_since: object) -> RepositorySnapshot:
            assert merged_since is not None
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

        def fetch_snapshot(self, repository: str, *, merged_since: object) -> object:
            raise GitHubError("GitHub API timed out for /repos/owner/repo: timed out")

    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", TimeoutClient)

    assert main(["owner/repo"]) == 2

    captured = capsys.readouterr()
    assert "error: GitHub API timed out for /repos/owner/repo: timed out" in captured.err


def test_main_reports_malformed_github_payload_without_traceback(monkeypatch, capsys) -> None:
    class MalformedPayloadClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            pass

        def fetch_snapshot(self, repository: str, *, merged_since: object) -> object:
            raise GitHubError(
                "Expected string for labels[0].name in open issues from /search/issues."
            )

    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", MalformedPayloadClient)

    assert main(["owner/repo"]) == 2

    captured = capsys.readouterr()
    assert "error: Expected string for labels[0].name" in captured.err
    assert "Traceback" not in captured.err


def test_main_reports_duplicate_remote_config_keys(monkeypatch, capsys) -> None:
    class DuplicateRemoteConfigClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            pass

        def fetch_snapshot(self, repository: str, *, merged_since: object) -> RepositorySnapshot:
            return RepositorySnapshot(
                full_name="owner/repo",
                html_url="https://github.com/owner/repo",
                description="Example",
                stars=1,
                forks=2,
                archived=False,
                pushed_at=None,
                default_branch="main",
                license_spdx="MIT",
                topics=("python",),
                root_entries=frozenset({"README.md"}),
                workflow_entries=frozenset(),
                merged_prs=(),
                open_pr_count=0,
            )

        def fetch_file_text(self, repository: str, path: str) -> str | None:
            return '{"thresholds": {"open_pr_queue_full": 10, "open_pr_queue_full": 20}}'

    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", DuplicateRemoteConfigClient)

    assert main(["owner/repo"]) == 2

    captured = capsys.readouterr()
    assert "error:" in captured.err
    assert "duplicate key 'thresholds.open_pr_queue_full'" in captured.err


def test_main_rejects_huge_remote_date_window_before_assessment(monkeypatch, capsys) -> None:
    class HugeRemoteDateWindowClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            pass

        def fetch_snapshot(self, repository: str, *, merged_since: object) -> RepositorySnapshot:
            return _basic_snapshot()

        def fetch_file_text(self, repository: str, path: str) -> str | None:
            return '{"thresholds": {"stale_unanswered_days": 1000000000}}'

    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", HugeRemoteDateWindowClient)

    assert main(["owner/repo"]) == 2

    captured = capsys.readouterr()
    assert "stale_unanswered_days must be at most" in captured.err
    assert "Traceback" not in captured.err


def test_main_json_includes_remote_config_provenance(monkeypatch, capsys) -> None:
    class RemoteConfigClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            pass

        def fetch_snapshot(self, repository: str, *, merged_since: object) -> RepositorySnapshot:
            return _basic_snapshot()

        def fetch_file_text(self, repository: str, path: str) -> str | None:
            return """
            {
              "disabled_signals": ["Pull request template"],
              "thresholds": {
                "open_pr_queue_full": 25
              }
            }
            """

    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", RemoteConfigClient)

    assert main(["owner/repo", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    provenance = data["config_provenance"]
    assert provenance["sources"] == ["defaults", "owner/repo:.oss-pr-compass.json"]
    assert provenance["remote_config"]["loaded"] is True
    assert provenance["remote_config"]["ignored"] is False
    assert provenance["local_config"]["loaded"] is False
    assert provenance["disabled_signals"] == ["Pull request template"]
    assert provenance["threshold_overrides"] == {"open_pr_queue_full": 25}


def test_main_json_includes_local_over_remote_config_provenance(
    monkeypatch, capsys, tmp_path
) -> None:
    class RemoteConfigClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            pass

        def fetch_snapshot(self, repository: str, *, merged_since: object) -> RepositorySnapshot:
            return _basic_snapshot()

        def fetch_file_text(self, repository: str, path: str) -> str | None:
            return """
            {
              "disabled_signals": ["Pull request template"],
              "thresholds": {
                "open_pr_queue_full": 20
              }
            }
            """

    local_config = tmp_path / "score.json"
    local_config.write_text(
        """
        {
          "disabled_signals_mode": "replace",
          "disabled_signals": ["CI and test signals"],
          "thresholds": {
            "open_pr_queue_full": 30
          }
        }
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", RemoteConfigClient)

    assert main(["owner/repo", "--json", "--config", str(local_config)]) == 0

    provenance = json.loads(capsys.readouterr().out)["config_provenance"]
    assert provenance["sources"] == [
        "defaults",
        "owner/repo:.oss-pr-compass.json",
        str(local_config),
    ]
    assert provenance["disabled_signals"] == ["CI and test signals"]
    assert provenance["threshold_overrides"] == {"open_pr_queue_full": 30}
    assert provenance["local_config"]["loaded"] is True


def test_main_valid_local_config_layers_on_remote_base(monkeypatch, capsys, tmp_path) -> None:
    class RemoteConfigClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            pass

        def fetch_snapshot(self, repository: str, *, merged_since: object) -> RepositorySnapshot:
            return _basic_snapshot()

        def fetch_file_text(self, repository: str, path: str) -> str | None:
            return """
            {
              "disabled_signals": ["Pull request template"],
              "thresholds": {
                "open_pr_queue_partial": 100
              }
            }
            """

    local_config = tmp_path / "score.json"
    local_config.write_text(
        """
        {
          "disabled_signals": ["CI and test signals"],
          "thresholds": {
            "open_pr_queue_full": 80
          }
        }
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", RemoteConfigClient)

    assert main(["owner/repo", "--json", "--config", str(local_config)]) == 0

    provenance = json.loads(capsys.readouterr().out)["config_provenance"]
    assert provenance["sources"] == [
        "defaults",
        "owner/repo:.oss-pr-compass.json",
        str(local_config),
    ]
    assert provenance["disabled_signals"] == ["CI and test signals", "Pull request template"]
    assert provenance["threshold_overrides"] == {
        "open_pr_queue_full": 80,
        "open_pr_queue_partial": 100,
    }


def test_main_json_discloses_no_remote_config(monkeypatch, capsys) -> None:
    class NoRemoteConfigClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            pass

        def fetch_snapshot(self, repository: str, *, merged_since: object) -> RepositorySnapshot:
            return _basic_snapshot()

        def fetch_file_text(self, repository: str, path: str) -> str | None:
            raise AssertionError("remote config should not be fetched")

    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", NoRemoteConfigClient)

    assert main(["owner/repo", "--json", "--no-remote-config"]) == 0

    provenance = json.loads(capsys.readouterr().out)["config_provenance"]
    assert provenance["sources"] == ["defaults"]
    assert provenance["remote_config"]["ignored"] is True
    assert provenance["remote_config"]["loaded"] is False


def test_main_text_and_markdown_include_config_provenance(monkeypatch, capsys) -> None:
    class RemoteConfigClient:
        def __init__(self, *, token: str | None, api_url: str) -> None:
            pass

        def fetch_snapshot(self, repository: str, *, merged_since: object) -> RepositorySnapshot:
            return _basic_snapshot()

        def fetch_file_text(self, repository: str, path: str) -> str | None:
            return '{"thresholds": {"open_pr_queue_full": 25}}'

    monkeypatch.setattr("oss_pr_compass.cli.GitHubClient", RemoteConfigClient)

    assert main(["owner/repo"]) == 0
    text_output = capsys.readouterr().out
    assert "Config:" in text_output
    assert "owner/repo:.oss-pr-compass.json" in text_output
    assert "open_pr_queue_full=25" in text_output

    assert main(["owner/repo", "--markdown"]) == 0
    markdown_output = capsys.readouterr().out
    assert "**Config:**" in markdown_output
    assert "owner/repo:.oss-pr-compass.json" in markdown_output
    assert "open\\_pr\\_queue\\_full=25" in markdown_output


def _basic_snapshot() -> RepositorySnapshot:
    return RepositorySnapshot(
        full_name="owner/repo",
        html_url="https://github.com/owner/repo",
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
    )


def _high_scoring_snapshot(*, archived: bool) -> RepositorySnapshot:
    return RepositorySnapshot(
        full_name="owner/repo",
        html_url="https://github.com/owner/repo",
        description="Example",
        stars=5000,
        forks=300,
        archived=archived,
        pushed_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        default_branch="main",
        license_spdx="MIT",
        topics=("python",),
        root_entries=frozenset(
            {
                "LICENSE",
                "CONTRIBUTING.md",
                "CODE_OF_CONDUCT.md",
                ".github/PULL_REQUEST_TEMPLATE.md",
                "tests",
            }
        ),
        workflow_entries=frozenset({"ci.yml"}),
        merged_prs=tuple({"merged_at": "2026-06-01T00:00:00Z"} for _ in range(24)),
        open_pr_count=0,
        labels=("good first issue",),
        open_issues=(),
        open_issue_count=0,
    )
