from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime

import numpy as np
import pandas as pd

from src.analysis.candidates import historical_liquidity
from src.config import Settings
from src.domain import FeatureSnapshot, LiquiditySnapshot, Regime, TimelineSnapshot


EPS = 1e-12
TIMELINE_HOURS = (72, 48, 24, 12, 6, 3, 1)
CANDLE_COLUMNS = ("open_time", "open", "high", "low", "close", "volume", "turnover")


def ema(series, period):
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def true_range(frame):
    prior = frame.close.shift(1)
    return pd.concat(
        [
            (frame.high - frame.low).abs(),
            (frame.high - prior).abs(),
            (frame.low - prior).abs(),
        ],
        axis=1,
    ).max(axis=1)


def prepare(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy().sort_values("open_time").reset_index(drop=True)
    for column in ("open", "high", "low", "close", "volume", "turnover"):
        result[column] = pd.to_numeric(result[column], errors="raise")
    if (result[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("OHLC prices must be positive")
    if (result[["volume", "turnover"]] < 0).any().any():
        raise ValueError("volume and turnover must be non-negative")
    result["ema20"] = ema(result.close, 20)
    result["ema50"] = ema(result.close, 50)
    result["atr14"] = true_range(result).ewm(
        span=14, adjust=False, min_periods=14
    ).mean()
    result["range"] = (result.high - result.low) / result.close
    result["body"] = (result.close - result.open).abs() / result.close
    result["volume_ema20"] = ema(result.volume, 20)
    result["turnover_ema20"] = ema(result.turnover, 20)
    return result


def percentile(value, historic):
    historic = historic.dropna()
    return float((historic <= value).mean()) if len(historic) else None


def boolf(value):
    return bool(value) if not pd.isna(value) else False


def _as_utc(value) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _infer_interval_minutes(frame: pd.DataFrame) -> int:
    times = pd.to_datetime(frame.open_time, utc=True).drop_duplicates().sort_values()
    if len(times) < 2:
        raise ValueError("at least two candle timestamps are required")
    seconds = times.diff().dropna().dt.total_seconds()
    interval = int(seconds.mode().iloc[0] // 60)
    if interval <= 0:
        raise ValueError("candle interval must be positive")
    return interval


def _bounded_candles(
    frame: pd.DataFrame, as_of: datetime | pd.Timestamp, interval_minutes: int | None = None
) -> pd.DataFrame:
    missing = set(CANDLE_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"missing candle columns: {sorted(missing)}")
    interval = interval_minutes or _infer_interval_minutes(frame)
    result = frame.loc[:, CANDLE_COLUMNS].copy()
    result["open_time"] = pd.to_datetime(result.open_time, utc=True)
    if result.open_time.duplicated().any():
        raise ValueError("candle open_time values must be unique")
    result["close_time"] = result.open_time + pd.Timedelta(minutes=interval)
    result = result[result.close_time <= _as_utc(as_of)].copy()
    return prepare(result)


def _bounded_observations(
    frame: pd.DataFrame | None,
    time_column: str,
    value_column: str,
    as_of,
) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=[time_column, value_column])
    if time_column not in frame or value_column not in frame:
        raise ValueError(f"missing observation columns: {time_column}, {value_column}")
    result = frame.loc[:, [time_column, value_column]].copy()
    result[time_column] = pd.to_datetime(result[time_column], utc=True)
    result[value_column] = pd.to_numeric(result[value_column], errors="raise")
    result = result[result[time_column] <= _as_utc(as_of)]
    return result.sort_values(time_column).drop_duplicates(time_column, keep="last")


def _source_max(
    trigger: pd.DataFrame,
    four_h: pd.DataFrame,
    oi: pd.DataFrame,
    funding: pd.DataFrame,
) -> dict[str, datetime | None]:
    def maximum(frame, column):
        return (
            pd.Timestamp(frame[column].max()).to_pydatetime()
            if not frame.empty
            else None
        )

    return {
        "trigger_candle": maximum(trigger, "close_time"),
        "four_h_candle": maximum(four_h, "close_time"),
        "open_interest": maximum(oi, "observed_at"),
        "funding": maximum(funding, "funding_time"),
    }


def _regime_from_prepared(
    four_h: pd.DataFrame, settings: Settings
) -> Regime:
    required = max(
        50 + settings.ema_slope_lookback_bars,
        settings.volatility_percentile_lookback_bars,
    )
    if len(four_h) < required:
        return Regime.UNKNOWN
    current = four_h.iloc[-1]
    normalized_atr = four_h.atr14.div(four_h.close)
    history = normalized_atr.tail(settings.volatility_percentile_lookback_bars)
    percentile_rank = history.rank(method="average", pct=True).iloc[-1]
    if (
        not pd.isna(current.atr14)
        and percentile_rank >= settings.high_volatility_percentile
    ):
        return Regime.HIGH_VOLATILITY
    past = four_h.iloc[-1 - settings.ema_slope_lookback_bars]
    slope20 = current.ema20 / past.ema20 - 1
    slope50 = current.ema50 / past.ema50 - 1
    if current.ema20 > current.ema50 and slope20 > 0 and slope50 > 0:
        return Regime.BULLISH_TREND
    if current.ema20 < current.ema50 and slope20 < 0 and slope50 < 0:
        return Regime.BEARISH_TREND
    return Regime.RANGE


def regime_at(
    four_h: pd.DataFrame,
    decision_time: datetime | pd.Timestamp,
    settings: Settings | None = None,
) -> Regime:
    settings = settings or Settings(database_url="postgresql://unused/research")
    bounded = _bounded_candles(four_h, decision_time, 240)
    return _regime_from_prepared(bounded, settings)


def _feature_values(
    trigger: pd.DataFrame,
    four_h: pd.DataFrame,
    oi: pd.DataFrame,
    funding: pd.DataFrame,
    liquidity: LiquiditySnapshot | None,
    settings: Settings,
) -> dict[str, object]:
    if len(trigger) < 60 or len(four_h) < 60:
        return {}
    current, context = trigger.iloc[-1], four_h.iloc[-1]
    recent, prior = trigger.tail(12), trigger.iloc[-36:-12]
    atr_now, atr_previous = recent.atr14.mean(), prior.atr14.mean()
    range_now, range_previous = recent.range.mean(), prior.range.mean()
    body_now, body_previous = recent.body.mean(), prior.body.mean()
    left, right = trigger.iloc[-48:-24], trigger.iloc[-24:-12]
    swing_low, pullback_low = float(left.low.min()), float(right.low.min())
    higher_low = pullback_low > swing_low
    failed_breakdowns = int((right.low < swing_low).any() and current.close > swing_low)
    old = trigger.iloc[-13]
    older = trigger.iloc[-25]
    gap = current.ema20 - current.ema50
    old_gap = old.ema20 - old.ema50
    momentum_repair = boolf(
        (current.ema20 / old.ema20 - 1) > (old.ema20 / older.ema20 - 1)
        and gap > old_gap
    )
    volume_ratio = float(current.volume / max(current.volume_ema20, EPS))
    turnover_ratio = float(current.turnover / max(current.turnover_ema20, EPS))
    slope_bars = settings.ema_slope_lookback_bars
    context_past = four_h.iloc[-1 - slope_bars]
    trend_parts = (
        context.ema20 > context.ema50,
        context.ema20 / context_past.ema20 - 1 > 0,
        context.ema50 / context_past.ema50 - 1 > 0,
    )
    values: dict[str, object] = {
        "trend_strength": float(sum(bool(part) for part in trend_parts) / 3),
        "ema20_above_ema50": boolf(context.ema20 > context.ema50),
        "ema20_slope_4h": float(context.ema20 / context_past.ema20 - 1),
        "ema50_slope_4h": float(context.ema50 / context_past.ema50 - 1),
        "ema_separation_4h": float(
            (context.ema20 - context.ema50) / context.close
        ),
        "atr_contraction": float(1 - atr_now / max(atr_previous, EPS)),
        "range_contraction": float(1 - range_now / max(range_previous, EPS)),
        "candle_size_reduction": float(1 - body_now / max(body_previous, EPS)),
        "volatility_percentile": percentile(
            current.atr14 / current.close,
            trigger.atr14.div(trigger.close).tail(
                settings.volatility_percentile_lookback_bars
            ),
        ),
        "compression_score": float(
            np.mean(
                [
                    atr_now < atr_previous,
                    range_now < range_previous,
                    body_now < body_previous,
                ]
            )
        ),
        "higher_low": bool(higher_low),
        "failed_breakdown_attempts": failed_breakdowns,
        "buyer_defense_score": float(
            np.mean([higher_low, bool(failed_breakdowns), current.close > pullback_low])
        ),
        "ema20_slope_improvement": float(
            (current.ema20 / old.ema20 - 1) - (old.ema20 / older.ema20 - 1)
        ),
        "ema_gap_improvement": float((gap - old_gap) / current.close),
        "higher_closes": int((recent.close.diff() > 0).sum()),
        "momentum_repair": bool(momentum_repair),
        "momentum_repair_score": float(
            np.mean([momentum_repair, current.close > current.ema20, gap > 0])
        ),
        "volume_ratio": volume_ratio,
        "turnover_ratio": turnover_ratio,
        "relative_volume": volume_ratio,
        "volume_expansion": bool(volume_ratio >= 1.5),
        "structure_confirmation": bool(current.close > recent.high.iloc[:-1].max()),
        "turnover_24h_before_event": (
            liquidity.turnover_24h if liquidity is not None else None
        ),
        "volume_24h_before_event": (
            liquidity.volume_24h if liquidity is not None else None
        ),
        "liquidity_eligible": liquidity.eligible if liquidity is not None else None,
    }
    values["compression"] = values["compression_score"] >= 2 / 3

    oi_change = None
    if len(oi) >= 2:
        observations = oi.tail(8)
        first = float(observations.open_interest.iloc[0])
        last = float(observations.open_interest.iloc[-1])
        oi_change = last / first - 1 if first > 0 else None
    funding_rate = float(funding.funding_rate.iloc[-1]) if len(funding) else None
    values["oi_change"] = oi_change
    values["oi_expansion"] = None if oi_change is None else bool(oi_change > 0)
    values["funding_rate"] = funding_rate
    if volume_ratio < 1:
        behavior = "weak_participation"
    elif oi_change is not None and oi_change < 0:
        behavior = "short_squeeze"
    elif oi_change is not None and oi_change > 0:
        behavior = "organic_expansion"
    else:
        behavior = "unclassified"
    values["futures_behaviour"] = behavior
    return values


def _bounded_inputs(trigger, four_h, as_of, oi, funding):
    trigger_interval = _infer_interval_minutes(trigger)
    bounded_trigger = _bounded_candles(trigger, as_of, trigger_interval)
    bounded_four_h = _bounded_candles(four_h, as_of, 240)
    bounded_oi = _bounded_observations(oi, "observed_at", "open_interest", as_of)
    bounded_funding = _bounded_observations(
        funding, "funding_time", "funding_rate", as_of
    )
    return bounded_trigger, bounded_four_h, bounded_oi, bounded_funding


def reconstruct_timeline(
    trigger: pd.DataFrame,
    four_h: pd.DataFrame,
    decision_time: datetime,
    oi: pd.DataFrame | None = None,
    funding: pd.DataFrame | None = None,
    settings: Settings | None = None,
    liquidity_candles: pd.DataFrame | None = None,
) -> dict[str, TimelineSnapshot]:
    settings = settings or Settings(database_url="postgresql://unused/research")
    result = {}
    for hours in TIMELINE_HOURS:
        as_of = _as_utc(decision_time) - pd.Timedelta(hours=hours)
        bounded = _bounded_inputs(trigger, four_h, as_of, oi, funding)
        bounded_trigger, bounded_four_h, bounded_oi, bounded_funding = bounded
        horizon_liquidity = None
        if liquidity_candles is not None:
            horizon_liquidity = historical_liquidity(
                liquidity_candles, as_of, settings.minimum_turnover_24h
            )
        values = _feature_values(
            bounded_trigger,
            bounded_four_h,
            bounded_oi,
            bounded_funding,
            horizon_liquidity,
            settings,
        )
        result[f"T-{hours}h"] = TimelineSnapshot(
            horizon=f"T-{hours}h",
            as_of=as_of.to_pydatetime(),
            features=values,
            source_max_timestamps=_source_max(*bounded),
            regime=_regime_from_prepared(bounded_four_h, settings),
            liquidity=horizon_liquidity,
        )
    return result


def _fingerprint(frames, decision_time, liquidity, settings):
    digest = hashlib.sha256()
    digest.update(_as_utc(decision_time).isoformat().encode())
    digest.update(settings.configuration_hash().encode())
    digest.update(
        json.dumps(
            {
                "volume": liquidity.volume_24h,
                "turnover": liquidity.turnover_24h,
                "expected": liquidity.expected_bars,
                "observed": liquidity.observed_bars,
                "eligible": liquidity.eligible,
                "reason": liquidity.reason,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    for frame in frames:
        digest.update(frame.to_csv(index=False, date_format="%Y-%m-%dT%H:%M:%S.%f%z").encode())
    return digest.hexdigest()


def extract_candidate_features(
    trigger: pd.DataFrame,
    four_h: pd.DataFrame,
    decision_time: datetime,
    liquidity: LiquiditySnapshot,
    oi: pd.DataFrame | None = None,
    funding: pd.DataFrame | None = None,
    settings: Settings | None = None,
    liquidity_candles: pd.DataFrame | None = None,
) -> FeatureSnapshot:
    settings = settings or Settings(database_url="postgresql://unused/research")
    bounded = _bounded_inputs(trigger, four_h, decision_time, oi, funding)
    bounded_trigger, bounded_four_h, bounded_oi, bounded_funding = bounded
    values = _feature_values(
        bounded_trigger,
        bounded_four_h,
        bounded_oi,
        bounded_funding,
        liquidity,
        settings,
    )
    timeline = reconstruct_timeline(
        trigger, four_h, decision_time, oi, funding, settings, liquidity_candles
    )
    return FeatureSnapshot(
        decision_time=_as_utc(decision_time).to_pydatetime(),
        regime=_regime_from_prepared(bounded_four_h, settings),
        features=values,
        timeline=timeline,
        source_max_timestamps=_source_max(*bounded),
        input_fingerprint=_fingerprint(
            bounded, decision_time, liquidity, settings
        ),
    )


def extract_features(
    trigger: pd.DataFrame,
    four_h: pd.DataFrame,
    event_time: pd.Timestamp,
    oi: pd.DataFrame | None = None,
    funding: pd.DataFrame | None = None,
) -> tuple[dict, dict, str]:
    """Compatibility facade; new code uses :func:`extract_candidate_features`."""
    interval = _infer_interval_minutes(trigger)
    decision_time = _as_utc(event_time) + pd.Timedelta(minutes=interval)
    settings = Settings(database_url="postgresql://unused/research")
    liquidity = historical_liquidity(
        trigger, decision_time.to_pydatetime(), settings.minimum_turnover_24h
    )
    snapshot = extract_candidate_features(
        trigger,
        four_h,
        decision_time.to_pydatetime(),
        liquidity,
        oi,
        funding,
        settings,
    )
    timeline = {
        name: dict(item.features) for name, item in snapshot.timeline.items()
    }
    return dict(snapshot.features), timeline, snapshot.regime.value
