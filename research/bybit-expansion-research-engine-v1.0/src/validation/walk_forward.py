from __future__ import annotations

import pandas as pd

from src.analysis.evidence import compare_matched_candidates
from src.config import Settings


def _definitions(train: pd.DataFrame) -> dict:
    rows = compare_matched_candidates(train)["cohorts"]["all_failures"]["comparisons"]
    return {
        row["feature"]: {
            "type": row["type"],
            "direction": "higher" if (row["difference"] or 0) >= 0 else "lower",
            "train_difference": row["difference"],
            "train_absolute_separation": row["absolute_separation"],
        }
        for row in rows if row["difference"] is not None
    }


def _evaluate(group: pd.DataFrame, definitions: dict, settings: Settings, months: int) -> dict:
    evidence = compare_matched_candidates(group)
    rows = {row["feature"]: row for row in evidence["cohorts"]["all_failures"]["comparisons"]}
    winners, failures = evidence["winner_count"], evidence["failure_count"]
    sufficient = winners >= settings.minimum_winners_per_split and failures >= settings.minimum_failures_per_split
    evaluated = {}
    for feature, definition in definitions.items():
        row = rows.get(feature)
        difference = row["difference"] if row else None
        consistent = difference is not None and (
            difference >= 0 if definition["direction"] == "higher" else difference <= 0
        )
        evaluated[feature] = {
            "difference": difference,
            "direction_consistent": consistent if sufficient else None,
            "winner_n": row["winner_n"] if row else 0,
            "failure_n": row["failure_n"] if row else 0,
        }
    return {
        "months": months,
        "status": "sufficient" if sufficient else "insufficient_sample",
        "candidate_count": evidence["matched_candidate_count"],
        "winner_count": winners,
        "failure_count": failures,
        "definitions": definitions,
        "features": evaluated,
        "surviving_features": [name for name, row in evaluated.items() if row["direction_consistent"]] if sufficient else None,
    }


def walk_forward(frame: pd.DataFrame, settings: Settings | None = None) -> dict:
    """Evaluate immutable train definitions over the first fixed 6/3/3 months."""
    settings = settings or Settings(database_url="postgresql://compatibility")
    if frame.empty:
        return {"status": "insufficient_sample", "reason": "no candidates", "robustness": None}
    data = frame.copy()
    if "decision_time" not in data and "start_time" in data:
        data["decision_time"] = data["start_time"]
    legacy_shape = "classification" not in data and "event_kind" in data
    if legacy_shape:
        data["classification"] = "weak_expansion"
        data.loc[data.event_kind == "failure", "classification"] = "failed_breakout"
        data.loc[
            (data.event_kind == "expansion")
            & (pd.to_numeric(data.percentage_move, errors="coerce") >= 0.25),
            "classification",
        ] = "winner"
    data["decision_time"] = pd.to_datetime(data.decision_time, utc=True)
    start = data.decision_time.min()
    train_end = start + pd.DateOffset(months=6)
    validation_end = train_end + pd.DateOffset(months=3)
    test_end = validation_end + pd.DateOffset(months=3)
    data = data[(data.decision_time >= start) & (data.decision_time < test_end)]
    train = data[data.decision_time < train_end]
    validation = data[(data.decision_time >= train_end) & (data.decision_time < validation_end)]
    test = data[(data.decision_time >= validation_end) & (data.decision_time < test_end)]
    definitions = _definitions(train)
    result = {
        "window_start": start.isoformat(), "window_end": test_end.isoformat(),
        "train": _evaluate(train, definitions, settings, 6),
        "validation": _evaluate(validation, definitions, settings, 3),
        "test": _evaluate(test, definitions, settings, 3),
    }
    if any(result[name]["status"] != "sufficient" for name in ("train", "validation", "test")):
        result["surviving_features"] = None
        result["robustness"] = None
    else:
        surviving = [
            feature for feature in definitions
            if all(result[split]["features"][feature]["direction_consistent"] for split in ("train", "validation", "test"))
        ]
        result["surviving_features"] = surviving
        result["robustness"] = len(surviving) / len(definitions) if definitions else None
    if legacy_shape:
        result["splits"] = {name: result[name] for name in ("train", "validation", "test")}
    return result
