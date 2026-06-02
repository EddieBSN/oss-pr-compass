from __future__ import annotations

from oss_pr_compass.cli import format_assessment, format_markdown
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
