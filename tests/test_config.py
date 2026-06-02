from __future__ import annotations

import pytest

from oss_pr_compass.config import ScoreConfigError, parse_score_config


def test_parse_score_config_accepts_signal_aliases_and_thresholds() -> None:
    config = parse_score_config(
        """
        {
          "disabled_signals": ["pull_request_template"],
          "thresholds": {
            "recent_activity_full_days": 60,
            "open_issue_queue_full": 25,
            "issue_label_ratio_full": 0.8
          }
        }
        """
    )

    assert config.disabled_signals == frozenset({"Pull request template"})
    assert config.thresholds.recent_activity_full_days == 60
    assert config.thresholds.open_issue_queue_full == 25
    assert config.thresholds.issue_label_ratio_full == 0.8


def test_parse_score_config_rejects_unknown_thresholds() -> None:
    with pytest.raises(ScoreConfigError, match="unknown thresholds"):
        parse_score_config(
            """
            {
              "thresholds": {
                "recent_activity_days": 90
              }
            }
            """
        )


def test_parse_score_config_rejects_invalid_threshold_order() -> None:
    with pytest.raises(ScoreConfigError, match="recent_activity_full_days"):
        parse_score_config(
            """
            {
              "thresholds": {
                "recent_activity_full_days": 120,
                "recent_activity_partial_days": 90
              }
            }
            """
        )
