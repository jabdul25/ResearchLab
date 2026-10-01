from __future__ import annotations
import math
import numpy as np
import pandas as pd

EPS = 1e-12

def ema(series, period): return series.ewm(span=period, adjust=False, min_periods=period).mean()

def true_range(df):
    prior = df.close.shift(1)
    return pd.concat([(df.high-df.low).abs(), (df.high-prior).abs(), (df.low-prior).abs()], axis=1).max(axis=1)

def prepare(frame: pd.DataFrame) -> pd.DataFrame:
    d = frame.copy().sort_values("open_time").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume", "turnover"): d[col] = d[col].astype(float)
    d["ema20"], d["ema50"] = ema(d.close, 20), ema(d.close, 50)
    d["atr14"] = true_range(d).ewm(span=14, adjust=False, min_periods=14).mean()
    d["range"] = (d.high-d.low)/d.close
    d["body"] = (d.close-d.open).abs()/d.close
    d["volume_ema20"] = ema(d.volume, 20)
    d["turnover_ema20"] = ema(d.turnover, 20)
    return d

def percentile(value, historic):
    historic = historic.dropna()
    return float((historic <= value).mean()) if len(historic) else float("nan")

def boolf(value): return bool(value) if not pd.isna(value) else False

def regime_at(four_h: pd.DataFrame, at: pd.Timestamp) -> str:
    d = prepare(four_h[four_h.open_time <= at])
    if len(d) < 60: return "unknown"
    x = d.iloc[-1]; atr_pct = x.atr14 / x.close
    if atr_pct >= d.atr14.div(d.close).tail(90).quantile(.8): return "high_volatility"
    slope20, slope50 = x.ema20 / d.ema20.iloc[-7] - 1, x.ema50 / d.ema50.iloc[-7] - 1
    if x.ema20 > x.ema50 and slope20 > 0 and slope50 > 0: return "trending"
    return "range"

def extract_features(trigger: pd.DataFrame, four_h: pd.DataFrame, event_time: pd.Timestamp,
                     oi: pd.DataFrame | None = None, funding: pd.DataFrame | None = None) -> tuple[dict, dict, str]:
    """Causal snapshot. Every series is truncated at event start (no lookahead)."""
    t = prepare(trigger[trigger.open_time <= event_time])
    h = prepare(four_h[four_h.open_time <= event_time])
    if len(t) < 60 or len(h) < 60: return {}, {}, "unknown"
    x, hlast = t.iloc[-1], h.iloc[-1]
    recent, prior = t.tail(12), t.iloc[-36:-12]
    atr_now, atr_prev = recent.atr14.mean(), prior.atr14.mean()
    range_now, range_prev = recent.range.mean(), prior.range.mean()
    body_now, body_prev = recent.body.mean(), prior.body.mean()
    # Swing structure uses a 48-candle lookback and two 12-candle pullback legs.
    left, right = t.iloc[-48:-24], t.iloc[-24:-12]
    swing_low, pullback_low = left.low.min(), right.low.min()
    higher_low = boolf(pullback_low > swing_low)
    failed_breakdowns = int((right.low < swing_low).sum() > 0 and x.close > swing_low)
    gap = x.ema20-x.ema50
    old = t.iloc[-13]
    momentum_repair = boolf((x.ema20 / old.ema20 - 1) > (old.ema20 / t.iloc[-25].ema20 - 1) and gap > (old.ema20-old.ema50))
    volume_ratio, turnover_ratio = x.volume / max(x.volume_ema20, EPS), x.turnover / max(x.turnover_ema20, EPS)
    features = {
      "trend_strength": float(((hlast.ema20 > hlast.ema50) + (hlast.ema20/h.ema20.iloc[-7]-1 > 0) + (hlast.ema50/h.ema50.iloc[-7]-1 > 0)) / 3),
      "ema20_above_ema50": boolf(hlast.ema20 > hlast.ema50), "ema20_slope_4h": float(hlast.ema20/h.ema20.iloc[-7]-1),
      "ema50_slope_4h": float(hlast.ema50/h.ema50.iloc[-7]-1), "ema_separation_4h": float((hlast.ema20-hlast.ema50)/hlast.close),
      "atr_contraction": float(1-atr_now/max(atr_prev, EPS)), "range_contraction": float(1-range_now/max(range_prev, EPS)),
      "candle_size_reduction": float(1-body_now/max(body_prev, EPS)), "volatility_percentile": percentile(x.atr14/x.close, t.atr14.div(t.close).tail(180)),
      "compression_score": float(np.mean([atr_now < atr_prev, range_now < range_prev, body_now < body_prev])),
      "higher_low": higher_low, "failed_breakdown_attempts": failed_breakdowns, "buyer_defense_score": float(np.mean([higher_low, failed_breakdowns, x.close > pullback_low])),
      "ema20_slope_improvement": float((x.ema20/old.ema20-1)-(old.ema20/t.iloc[-25].ema20-1)), "ema_gap_improvement": float((gap-(old.ema20-old.ema50))/x.close),
      "higher_closes": int((recent.close.diff() > 0).sum()), "momentum_repair": momentum_repair, "momentum_repair_score": float(np.mean([momentum_repair, x.close > x.ema20, gap > 0])),
      "volume_ratio": float(volume_ratio), "turnover_ratio": float(turnover_ratio), "relative_volume": float(volume_ratio),
      "volume_expansion": boolf(volume_ratio >= 1.5), "structure_confirmation": boolf(x.close > recent.high.iloc[:-1].max()),
    }
    features["compression"] = boolf(features["compression_score"] >= 2 / 3)
    oi_change, funding_rate = np.nan, np.nan
    if oi is not None and not oi.empty:
        o = oi[oi.observed_at <= event_time].sort_values("observed_at").tail(8)
        if len(o) >= 2: oi_change = float(o.open_interest.iloc[-1] / o.open_interest.iloc[0] - 1)
    if funding is not None and not funding.empty:
        f = funding[funding.funding_time <= event_time].sort_values("funding_time").tail(1)
        if len(f): funding_rate = float(f.funding_rate.iloc[-1])
    features["oi_change"] = oi_change; features["oi_expansion"] = boolf(oi_change > 0); features["funding_rate"] = funding_rate
    if volume_ratio < 1: features["futures_behaviour"] = "weak_expansion"
    elif not math.isnan(oi_change) and oi_change < 0: features["futures_behaviour"] = "short_squeeze"
    elif not math.isnan(oi_change) and oi_change > 0: features["futures_behaviour"] = "organic_expansion"
    else: features["futures_behaviour"] = "unclassified"
    timeline = {}
    for hours in (72, 48, 24, 12, 6, 3, 1):
        point = t[t.open_time <= event_time-pd.Timedelta(hours=hours)]
        if len(point):
            p = point.iloc[-1]; timeline[f"T-{hours}h"] = {"close": float(p.close), "ema20": float(p.ema20), "ema50": float(p.ema50), "volume_ratio": float(p.volume/max(p.volume_ema20, EPS))}
    return features, timeline, regime_at(four_h, event_time)
