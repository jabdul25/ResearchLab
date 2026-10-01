from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd


OUTCOME_CLASSES = ("winner", "reversal", "failed_breakout", "weak_expansion")
FAILURE_COHORTS = {
    "all_failures": ("reversal", "failed_breakout", "weak_expansion"),
    "reversal": ("reversal",),
    "failed_breakout": ("failed_breakout",),
    "weak_expansion": ("weak_expansion",),
}
METADATA_COLUMNS = {
    "id", "candidate_key", "symbol", "interval_minutes", "breakout_open_time",
    "decision_time", "breakout_level", "breakout_close",
    "turnover_24h_before_event", "volume_24h_before_event", "liquidity_eligible",
    "liquidity_reason", "regime", "timeline", "input_fingerprint",
    "classification", "threshold", "threshold_reached", "mfe", "mae",
    "maximum_extension", "final_vs_breakout_level", "giveback_fraction",
    "start_time", "event_kind", "percentage_move",
}


def _finite(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()


def feature_columns(frame: pd.DataFrame) -> tuple[list[str], list[str]]:
    booleans, continuous = [], []
    for column in frame.columns:
        if column in METADATA_COLUMNS:
            continue
        non_null = frame[column].dropna()
        if non_null.empty:
            continue
        values = set(non_null.unique())
        if values <= {True, False, 0, 1}:
            booleans.append(column)
        elif pd.api.types.is_numeric_dtype(non_null):
            continuous.append(column)
    return sorted(booleans), sorted(continuous)


def _coverage(observed: int, total: int) -> float:
    return observed / total if total else 0.0


def _boolean_stat(feature: str, winners: pd.DataFrame, failures: pd.DataFrame) -> dict:
    winner = winners[feature].dropna().astype(bool)
    failure = failures[feature].dropna().astype(bool)
    winner_frequency = float(winner.mean()) if len(winner) else None
    failure_frequency = float(failure.mean()) if len(failure) else None
    difference = (
        winner_frequency - failure_frequency
        if winner_frequency is not None and failure_frequency is not None else None
    )
    return {
        "feature": feature,
        "type": "boolean",
        "winner_n": len(winner),
        "failure_n": len(failure),
        "winner_coverage": _coverage(len(winner), len(winners)),
        "failure_coverage": _coverage(len(failure), len(failures)),
        "winner_frequency": winner_frequency,
        "failure_frequency": failure_frequency,
        "difference": difference,
        "absolute_separation": abs(difference) if difference is not None else None,
    }


def _iqr(values: pd.Series) -> float | None:
    return float(values.quantile(0.75) - values.quantile(0.25)) if len(values) else None


def _continuous_stat(feature: str, winners: pd.DataFrame, failures: pd.DataFrame) -> dict:
    winner, failure = _finite(winners[feature]), _finite(failures[feature])
    winner_median = float(winner.median()) if len(winner) else None
    failure_median = float(failure.median()) if len(failure) else None
    difference = (
        winner_median - failure_median
        if winner_median is not None and failure_median is not None else None
    )
    scale = _iqr(pd.concat([winner, failure], ignore_index=True))
    standardized = difference / scale if difference is not None and scale not in (None, 0) else difference
    return {
        "feature": feature,
        "type": "continuous",
        "winner_n": len(winner),
        "failure_n": len(failure),
        "winner_coverage": _coverage(len(winner), len(winners)),
        "failure_coverage": _coverage(len(failure), len(failures)),
        "winner_median": winner_median,
        "failure_median": failure_median,
        "winner_iqr": _iqr(winner),
        "failure_iqr": _iqr(failure),
        "difference": difference,
        "absolute_separation": abs(standardized) if standardized is not None else None,
    }


def compare_matched_candidates(frame: pd.DataFrame, cohort: Mapping[str, object] | None = None) -> dict:
    """Compare winners only with breakout attempts from the same candidate population."""
    data = frame.copy()
    applied_slice = dict(cohort or {})
    for column, value in applied_slice.items():
        if column not in data:
            raise ValueError(f"unknown cohort column: {column}")
        data = data[data[column] == value]
    if "classification" not in data:
        raise ValueError("classification is required")
    data = data[data.classification.isin(OUTCOME_CLASSES)]
    winners = data[data.classification == "winner"]
    booleans, continuous = feature_columns(data)
    cohorts = {}
    for name, classes in FAILURE_COHORTS.items():
        failures = data[data.classification.isin(classes)]
        comparisons = [
            *(_boolean_stat(feature, winners, failures) for feature in booleans),
            *(_continuous_stat(feature, winners, failures) for feature in continuous),
        ]
        comparisons.sort(
            key=lambda row: row["absolute_separation"] if row["absolute_separation"] is not None else -1,
            reverse=True,
        )
        cohorts[name] = {
            "winner_count": len(winners), "failure_count": len(failures),
            "comparisons": comparisons,
        }
    return {
        "slice": applied_slice,
        "matched_candidate_count": len(data),
        "winner_count": len(winners),
        "failure_count": len(data) - len(winners),
        "cohorts": cohorts,
    }


def candidate_population(frame: pd.DataFrame, raw_counts: Mapping[str, object]) -> dict:
    counts = frame.classification.value_counts().to_dict() if "classification" in frame else {}
    return {
        "total_breakout_candidates": int(raw_counts.get("total", raw_counts.get("raw_signals", len(frame)))),
        "admitted_candidates": int(raw_counts.get("admitted", len(frame))),
        "rejected_duplicates": int(raw_counts.get("duplicates", raw_counts.get("rejected_duplicates", 0))),
        "liquidity_excluded": int(raw_counts.get("liquidity_excluded", raw_counts.get("excluded", 0))),
        "pending_outcomes": int(raw_counts.get("pending", 0)),
        "complete_liquidity": int(raw_counts.get("complete_liquidity", 0)),
        "incomplete_liquidity": int(raw_counts.get("incomplete_liquidity", 0)),
        "exclusions": list(raw_counts.get("exclusions", [])),
        "eligible_labeled_candidates": len(frame),
        "winners": int(counts.get("winner", 0)),
        "reversals": int(counts.get("reversal", 0)),
        "failed_breakouts": int(counts.get("failed_breakout", 0)),
        "weak_expansions": int(counts.get("weak_expansion", 0)),
    }


def compare_winners_failures(frame: pd.DataFrame) -> dict:
    return compare_matched_candidates(frame)


def regime_analysis(frame: pd.DataFrame) -> dict:
    return {str(regime): compare_matched_candidates(group) for regime, group in frame.groupby("regime", dropna=False)}


def feature_importance(frame: pd.DataFrame) -> list[dict]:
    return compare_matched_candidates(frame)["cohorts"]["all_failures"]["comparisons"]
