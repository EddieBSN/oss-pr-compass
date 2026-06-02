from __future__ import annotations

from oss_pr_compass.cli import format_assessment
from oss_pr_compass.model import Assessment, Signal


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
