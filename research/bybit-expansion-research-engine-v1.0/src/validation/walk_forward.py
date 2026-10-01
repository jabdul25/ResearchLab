from __future__ import annotations
import pandas as pd
from src.analysis.evidence import BOOLEAN_FEATURES

def walk_forward(events: pd.DataFrame) -> dict:
    """Fixed chronological 6/3/3-month split over the latest available year."""
    data = events.copy(); data["start_time"] = pd.to_datetime(data.start_time, utc=True)
    if data.empty: return {"eligible": False, "reason": "no feature records"}
    end, start = data.start_time.max(), data.start_time.max() - pd.DateOffset(months=12)
    data = data[data.start_time >= start]
    cuts = [start + pd.DateOffset(months=6), start + pd.DateOffset(months=9), end + pd.Timedelta(nanoseconds=1)]
    labels, groups = ("train", "validation", "test"), [data[data.start_time < cuts[0]], data[(data.start_time >= cuts[0]) & (data.start_time < cuts[1])], data[data.start_time >= cuts[1]]]
    result = {"eligible": True, "window_start": str(start), "window_end": str(end), "splits": {}}
    for label, group in zip(labels, groups):
        winner = (group.event_kind.eq("expansion") & (group.percentage_move.astype(float) >= .25))
        failure = group.event_kind.eq("failure")
        sample = group[winner | failure]
        rates = {}
        for feature in BOOLEAN_FEATURES:
            a, b = sample[winner.loc[sample.index]][feature].fillna(False).astype(bool), sample[failure.loc[sample.index]][feature].fillna(False).astype(bool)
            rates[feature] = {"winner": float(a.mean()) if len(a) else None, "failure": float(b.mean()) if len(b) else None,
                              "lift": float(a.mean()-b.mean()) if len(a) and len(b) else None}
        result["splits"][label] = {"n": len(sample), "winners": int(winner.sum()), "failures": int(failure.sum()), "features": rates}
    trains = result["splits"]["train"]["features"]; val = result["splits"]["validation"]["features"]; test = result["splits"]["test"]["features"]
    surviving = [f for f in BOOLEAN_FEATURES if all((x[f]["lift"] or 0) > 0 for x in (trains, val, test))]
    result["surviving_features"] = surviving
    result["robustness"] = len(surviving) / len(BOOLEAN_FEATURES)
    return result
