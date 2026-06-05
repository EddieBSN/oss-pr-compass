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


def test_parse_score_config_rejects_duplicate_top_level_keys() -> None:
    with pytest.raises(ScoreConfigError, match="duplicate key 'thresholds'"):
        parse_score_config(
            """
            {
              "thresholds": {
                "recent_activity_full_days": 60
              },
              "thresholds": {
                "recent_activity_full_days": 30
              }
            }
            """
        )


def test_parse_score_config_rejects_duplicate_nested_threshold_keys() -> None:
    with pytest.raises(ScoreConfigError, match="thresholds.open_pr_queue_full"):
        parse_score_config(
            """
            {
              "thresholds": {
                "open_pr_queue_full": 10,
                "open_pr_queue_full": 20
              }
            }
            """
        )


def test_parse_score_config_accepts_non_duplicate_nested_keys() -> None:
    config = parse_score_config(
        """
        {
          "thresholds": {
            "open_pr_queue_full": 10,
            "open_pr_queue_partial": 50
          }
        }
        """
    )

    assert config.thresholds.open_pr_queue_full == 10
    assert config.thresholds.open_pr_queue_partial == 50


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


def test_parse_score_config_layers_disabled_signals_over_base() -> None:
    base = parse_score_config('{"disabled_signals": ["Pull request template"]}')

    config = parse_score_config(
        '{"disabled_signals": ["CI and test signals"]}',
        base=base,
    )

    assert config.disabled_signals == frozenset({"Pull request template", "CI and test signals"})


def test_parse_score_config_rejects_non_finite_ratio_values() -> None:
    with pytest.raises(ScoreConfigError, match="unsupported JSON constant NaN"):
        parse_score_config('{"thresholds": {"issue_label_ratio_full": NaN}}')

    with pytest.raises(ScoreConfigError, match="unsupported JSON constant Infinity"):
        parse_score_config('{"thresholds": {"issue_label_ratio_full": Infinity}}')
