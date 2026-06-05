from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

SIGNAL_NAMES = (
    "OSS license",
    "Recent repository activity",
    "Merged pull request activity",
    "Contribution documentation",
    "Pull request template",
    "CI and test signals",
    "Open pull request queue",
    "Issue triage signals",
)
MAX_DATE_WINDOW_DAYS = 36_500
DATE_WINDOW_THRESHOLD_FIELDS = frozenset(
    {
        "recent_activity_full_days",
        "recent_activity_partial_days",
        "stale_unanswered_days",
        "maintainer_response_window_days",
    }
)


class ScoreConfigError(ValueError):
    pass


class _ConfigObject(dict[str, Any]):
    def __init__(self, pairs: list[tuple[str, Any]]):
        super().__init__()
        seen: set[str] = set()
        duplicates = []
        for key, value in pairs:
            if key in seen:
                duplicates.append(key)
            seen.add(key)
            self[key] = value
        self.duplicate_keys = tuple(duplicates)


@dataclass(frozen=True)
class ScoreThresholds:
    recent_activity_full_days: int = 45
    recent_activity_partial_days: int = 90
    merged_prs_full: int = 20
    merged_prs_partial: int = 5
    merged_prs_minimum: int = 1
    open_pr_queue_full: int = 10
    open_pr_queue_partial: int = 50
    open_pr_queue_minimum: int = 100
    open_issue_queue_full: int = 50
    open_issue_queue_partial: int = 100
    issue_label_ratio_full: float = 0.75
    issue_label_ratio_partial: float = 0.50
    stale_unanswered_days: int = 30
    stale_unanswered_partial_ratio: float = 0.10
    stale_unanswered_minimum: int = 2
    maintainer_response_window_days: int = 30
    maintainer_response_full_ratio: float = 0.25
    maintainer_response_partial_ratio: float = 0.10


@dataclass(frozen=True)
class ScoreConfig:
    disabled_signals: frozenset[str] = field(default_factory=frozenset)
    thresholds: ScoreThresholds = field(default_factory=ScoreThresholds)


def load_score_config(path: str | Path, *, base: ScoreConfig | None = None) -> ScoreConfig:
    config_path = Path(path)
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScoreConfigError(f"Could not read scoring config {config_path}: {exc}") from exc
    return parse_score_config(text, source=str(config_path), base=base)


def parse_score_config(
    text: str,
    *,
    source: str = "scoring config",
    base: ScoreConfig | None = None,
) -> ScoreConfig:
    raw = _load_score_config_json(text, source)
    return config_from_mapping(raw, source=source, base=base)


def validate_score_config_fragment(
    text: str,
    *,
    source: str = "scoring config",
) -> None:
    raw = _load_score_config_json(text, source)
    _validate_config_fragment(raw, source)


def _load_score_config_json(text: str, source: str) -> object:
    try:
        raw = json.loads(
            text,
            parse_constant=lambda value: _reject_json_constant(value, source),
            object_pairs_hook=_ConfigObject,
        )
    except json.JSONDecodeError as exc:
        raise ScoreConfigError(f"{source} is not valid JSON: {exc.msg}") from exc
    _reject_duplicate_keys(raw, source)
    return raw


def config_from_mapping(
    raw: object,
    *,
    source: str = "scoring config",
    base: ScoreConfig | None = None,
) -> ScoreConfig:
    if not isinstance(raw, dict):
        raise ScoreConfigError(f"{source} must be a JSON object.")

    base = base or ScoreConfig()
    allowed_keys = {"disabled_signals", "disabled_signals_mode", "thresholds"}
    unknown_keys = sorted(set(raw) - allowed_keys)
    if unknown_keys:
        raise ScoreConfigError(f"{source} contains unknown keys: {', '.join(unknown_keys)}.")

    disabled_signals_mode = raw.get("disabled_signals_mode", "merge")
    if disabled_signals_mode not in {"merge", "replace"}:
        raise ScoreConfigError(
            f"{source} disabled_signals_mode must be either 'merge' or 'replace'."
        )

    disabled_signals = base.disabled_signals
    if "disabled_signals" in raw:
        parsed_disabled_signals = _parse_disabled_signals(raw["disabled_signals"], source)
        if disabled_signals_mode == "replace":
            disabled_signals = parsed_disabled_signals
        else:
            disabled_signals = disabled_signals | parsed_disabled_signals

    thresholds = base.thresholds
    if "thresholds" in raw:
        thresholds = _parse_thresholds(raw["thresholds"], source=source, base=thresholds)

    if len(disabled_signals) == len(SIGNAL_NAMES):
        raise ScoreConfigError(f"{source} cannot disable every scoring signal.")

    return ScoreConfig(disabled_signals=frozenset(disabled_signals), thresholds=thresholds)


def _validate_config_fragment(raw: object, source: str) -> None:
    if not isinstance(raw, dict):
        raise ScoreConfigError(f"{source} must be a JSON object.")

    allowed_keys = {"disabled_signals", "disabled_signals_mode", "thresholds"}
    unknown_keys = sorted(set(raw) - allowed_keys)
    if unknown_keys:
        raise ScoreConfigError(f"{source} contains unknown keys: {', '.join(unknown_keys)}.")

    disabled_signals_mode = raw.get("disabled_signals_mode", "merge")
    if disabled_signals_mode not in {"merge", "replace"}:
        raise ScoreConfigError(
            f"{source} disabled_signals_mode must be either 'merge' or 'replace'."
        )

    if "disabled_signals" in raw:
        disabled_signals = _parse_disabled_signals(raw["disabled_signals"], source)
        if len(disabled_signals) == len(SIGNAL_NAMES):
            raise ScoreConfigError(f"{source} cannot disable every scoring signal.")

    if "thresholds" in raw:
        _validate_threshold_fragment(raw["thresholds"], source=source)


def _parse_disabled_signals(raw: object, source: str) -> frozenset[str]:
    if not isinstance(raw, list):
        raise ScoreConfigError(f"{source} disabled_signals must be a list of signal names.")

    disabled: set[str] = set()
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            raise ScoreConfigError(
                f"{source} disabled_signals must contain non-empty signal names."
            )
        disabled.add(_resolve_signal_name(value, source))
    return frozenset(disabled)


def _parse_thresholds(
    raw: object,
    *,
    source: str,
    base: ScoreThresholds,
) -> ScoreThresholds:
    if not isinstance(raw, dict):
        raise ScoreConfigError(f"{source} thresholds must be a JSON object.")

    field_names = set(ScoreThresholds.__dataclass_fields__)
    unknown_keys = sorted(set(raw) - field_names)
    if unknown_keys:
        raise ScoreConfigError(f"{source} contains unknown thresholds: {', '.join(unknown_keys)}.")

    updates: dict[str, int | float] = {}
    for key, value in raw.items():
        updates[key] = _parse_threshold_value(key, value, source)

    thresholds = replace(base, **updates)
    _validate_thresholds(thresholds, source)
    return thresholds


def _validate_threshold_fragment(raw: object, *, source: str) -> None:
    if not isinstance(raw, dict):
        raise ScoreConfigError(f"{source} thresholds must be a JSON object.")

    field_names = set(ScoreThresholds.__dataclass_fields__)
    unknown_keys = sorted(set(raw) - field_names)
    if unknown_keys:
        raise ScoreConfigError(f"{source} contains unknown thresholds: {', '.join(unknown_keys)}.")

    updates: dict[str, int | float] = {}
    for key, value in raw.items():
        updates[key] = _parse_threshold_value(key, value, source)

    _validate_threshold_fragment_order(updates, source)


def _validate_thresholds(thresholds: ScoreThresholds, source: str) -> None:
    if thresholds.recent_activity_full_days > thresholds.recent_activity_partial_days:
        raise ScoreConfigError(
            f"{source} recent_activity_full_days must be <= recent_activity_partial_days."
        )
    if not (
        thresholds.merged_prs_full >= thresholds.merged_prs_partial >= thresholds.merged_prs_minimum
    ):
        raise ScoreConfigError(f"{source} merged PR thresholds must descend from full to minimum.")
    if not (
        thresholds.open_pr_queue_full
        <= thresholds.open_pr_queue_partial
        <= thresholds.open_pr_queue_minimum
    ):
        raise ScoreConfigError(
            f"{source} open PR queue thresholds must ascend from full to minimum."
        )
    if thresholds.open_issue_queue_full > thresholds.open_issue_queue_partial:
        raise ScoreConfigError(
            f"{source} open_issue_queue_full must be <= open_issue_queue_partial."
        )
    if thresholds.issue_label_ratio_partial > thresholds.issue_label_ratio_full:
        raise ScoreConfigError(
            f"{source} issue_label_ratio_partial must be <= issue_label_ratio_full."
        )
    if thresholds.maintainer_response_partial_ratio > thresholds.maintainer_response_full_ratio:
        raise ScoreConfigError(
            f"{source} maintainer_response_partial_ratio must be <= maintainer_response_full_ratio."
        )


def _validate_threshold_fragment_order(updates: dict[str, int | float], source: str) -> None:
    _validate_ordered_pair(
        updates,
        "recent_activity_full_days",
        "recent_activity_partial_days",
        source,
        "recent_activity_full_days must be <= recent_activity_partial_days.",
    )
    _validate_ordered_pair(
        updates,
        "merged_prs_partial",
        "merged_prs_full",
        source,
        "merged PR thresholds must descend from full to minimum.",
    )
    _validate_ordered_pair(
        updates,
        "merged_prs_minimum",
        "merged_prs_partial",
        source,
        "merged PR thresholds must descend from full to minimum.",
    )
    _validate_ordered_pair(
        updates,
        "merged_prs_minimum",
        "merged_prs_full",
        source,
        "merged PR thresholds must descend from full to minimum.",
    )
    _validate_ordered_pair(
        updates,
        "open_pr_queue_full",
        "open_pr_queue_partial",
        source,
        "open PR queue thresholds must ascend from full to minimum.",
    )
    _validate_ordered_pair(
        updates,
        "open_pr_queue_partial",
        "open_pr_queue_minimum",
        source,
        "open PR queue thresholds must ascend from full to minimum.",
    )
    _validate_ordered_pair(
        updates,
        "open_pr_queue_full",
        "open_pr_queue_minimum",
        source,
        "open PR queue thresholds must ascend from full to minimum.",
    )
    _validate_ordered_pair(
        updates,
        "open_issue_queue_full",
        "open_issue_queue_partial",
        source,
        "open_issue_queue_full must be <= open_issue_queue_partial.",
    )
    _validate_ordered_pair(
        updates,
        "issue_label_ratio_partial",
        "issue_label_ratio_full",
        source,
        "issue_label_ratio_partial must be <= issue_label_ratio_full.",
    )
    _validate_ordered_pair(
        updates,
        "maintainer_response_partial_ratio",
        "maintainer_response_full_ratio",
        source,
        "maintainer_response_partial_ratio must be <= maintainer_response_full_ratio.",
    )


def _validate_ordered_pair(
    values: dict[str, int | float],
    lower_field: str,
    upper_field: str,
    source: str,
    message: str,
) -> None:
    lower = values.get(lower_field)
    upper = values.get(upper_field)
    if lower is not None and upper is not None and lower > upper:
        raise ScoreConfigError(f"{source} {message}")


def _resolve_signal_name(value: str, source: str) -> str:
    normalized = _normalize_signal_name(value)
    aliases = {_normalize_signal_name(name): name for name in SIGNAL_NAMES}
    for name in SIGNAL_NAMES:
        aliases[name.lower().replace(" ", "_")] = name
        aliases[name.lower().replace(" ", "-")] = name

    if normalized in aliases:
        return aliases[normalized]
    if value.lower() in aliases:
        return aliases[value.lower()]

    known = ", ".join(SIGNAL_NAMES)
    raise ScoreConfigError(f"{source} references unknown signal {value!r}. Known signals: {known}.")


def _normalize_signal_name(value: str) -> str:
    return " ".join(value.strip().lower().replace("_", " ").replace("-", " ").split())


def _parse_threshold_value(key: str, value: Any, source: str) -> int | float:
    field_name = f"{source} thresholds.{key}"
    if "ratio" in key:
        return _parse_ratio(value, field_name)
    if key == "stale_unanswered_minimum":
        return _parse_non_negative_int(value, field_name)
    if key in DATE_WINDOW_THRESHOLD_FIELDS:
        return _parse_positive_int(value, field_name, max_value=MAX_DATE_WINDOW_DAYS)
    return _parse_positive_int(value, field_name)


def _parse_positive_int(value: Any, field_name: str, *, max_value: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ScoreConfigError(f"{field_name} must be a positive integer.")
    if max_value is not None and value > max_value:
        raise ScoreConfigError(f"{field_name} must be at most {max_value}.")
    return value


def _parse_non_negative_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ScoreConfigError(f"{field_name} must be a non-negative integer.")
    return value


def _parse_ratio(value: Any, field_name: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ScoreConfigError(f"{field_name} must be a number between 0 and 1.")
    ratio = float(value)
    if not math.isfinite(ratio) or ratio < 0 or ratio > 1:
        raise ScoreConfigError(f"{field_name} must be a number between 0 and 1.")
    return ratio


def _reject_json_constant(value: str, source: str) -> None:
    raise ScoreConfigError(f"{source} contains unsupported JSON constant {value}.")


def _reject_duplicate_keys(value: object, source: str, path: str = "") -> None:
    if isinstance(value, _ConfigObject):
        if value.duplicate_keys:
            duplicate = value.duplicate_keys[0]
            key_path = f"{path}.{duplicate}" if path else duplicate
            raise ScoreConfigError(f"{source} contains duplicate key {key_path!r}.")
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            _reject_duplicate_keys(child, source, child_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_duplicate_keys(item, source, f"{path}[{index}]")
