from __future__ import annotations
import math
import numpy as np
import pandas as pd

BOOLEAN_FEATURES = ("ema20_above_ema50", "compression", "higher_low", "momentum_repair", "volume_expansion", "oi_expansion", "structure_confirmation")
CONTINUOUS_FEATURES = ("trend_strength", "compression_score", "buyer_defense_score", "momentum_repair_score", "volume_ratio", "turnover_ratio", "ema20_slope_4h", "ema_separation_4h")

def _finite(series): return pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()

def compare_winners_failures(events: pd.DataFrame) -> dict:
    winners = events[(events.event_kind == "expansion") & (events.percentage_move.astype(float) >= .25)]
    failures = events[events.event_kind == "failure"]
    results = []
    for feature in BOOLEAN_FEATURES:
        w, f = winners[feature].fillna(False).astype(bool), failures[feature].fillna(False).astype(bool)
        results.append({"feature": feature, "winner_frequency": float(w.mean()) if len(w) else None,
                        "failure_frequency": float(f.mean()) if len(f) else None,
                        "difference": float(w.mean()-f.mean()) if len(w) and len(f) else None})
    for feature in CONTINUOUS_FEATURES:
        w, f = _finite(winners[feature]), _finite(failures[feature])
        results.append({"feature": feature, "winner_mean": float(w.mean()) if len(w) else None,
                        "failure_mean": float(f.mean()) if len(f) else None,
                        "difference": float(w.mean()-f.mean()) if len(w) and len(f) else None})
    results.sort(key=lambda r: abs(r.get("difference") or 0), reverse=True)
    return {"winner_count": len(winners), "failure_count": len(failures), "comparisons": results}

def regime_analysis(events: pd.DataFrame) -> dict:
    out = {}
    for regime, group in events.groupby("regime"):
        expansions = group[group.event_kind == "expansion"]
        out[regime] = {"events": len(group), "expansions": len(expansions),
                       "large_or_better": int((expansions.percentage_move.astype(float) >= .25).sum()),
                       "mean_expansion": float(expansions.percentage_move.astype(float).mean()) if len(expansions) else None}
    return out

def feature_importance(events: pd.DataFrame) -> list[dict]:
    """Univariate, label-permuted-free effect ranking—not an optimized model."""
    data = events[events.event_kind.isin(["expansion", "failure"])].copy()
    data["winner"] = ((data.event_kind == "expansion") & (data.percentage_move.astype(float) >= .25)).astype(int)
    rows = []
    for feature in BOOLEAN_FEATURES + CONTINUOUS_FEATURES:
        x = pd.to_numeric(data[feature], errors="coerce") if feature in CONTINUOUS_FEATURES else data[feature].fillna(False).astype(int)
        valid = x.notna()
        if valid.sum() < 20 or data.loc[valid, "winner"].nunique() < 2: continue
        # standardized point-biserial correlation, transparent and stable.
        corr = float(np.corrcoef(x[valid], data.loc[valid, "winner"])[0, 1])
        if not math.isnan(corr): rows.append({"feature": feature, "effect_correlation": corr, "absolute_effect": abs(corr), "n": int(valid.sum())})
    return sorted(rows, key=lambda x: x["absolute_effect"], reverse=True)
