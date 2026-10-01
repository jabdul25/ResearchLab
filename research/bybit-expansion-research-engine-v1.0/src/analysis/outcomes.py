from __future__ import annotations

from datetime import timedelta

import pandas as pd

from src.config import Settings
from src.domain import (
    BreakoutSignal,
    CandidateOutcome,
    CandidateStatus,
    OutcomeClass,
)


def label_outcome(
    signal: BreakoutSignal,
    future: pd.DataFrame,
    threshold: float,
    settings: Settings,
) -> CandidateOutcome | None:
    if signal.status is not CandidateStatus.ADMITTED:
        raise ValueError("only admitted candidates may receive outcomes")
    if threshold <= 0:
        raise ValueError("threshold must be positive")
    required = {"open_time", "open", "high", "low", "close"}
    missing = required - set(future.columns)
    if missing:
        raise ValueError(f"missing outcome columns: {sorted(missing)}")

    window_size = settings.outcome_window_candles
    window = future.iloc[:window_size].copy()
    window["open_time"] = pd.to_datetime(window.open_time, utc=True)
    expected = pd.date_range(
        start=pd.Timestamp(signal.decision_time),
        periods=len(window),
        freq=f"{signal.interval_minutes}min",
        tz="UTC",
    )
    if list(window.open_time) != list(expected):
        raise ValueError("future candles do not match expected outcome timestamps")

    for column in ("open", "high", "low", "close"):
        window[column] = pd.to_numeric(window[column], errors="raise")
    if (window[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("future OHLC prices must be positive")
    if (
        (window.high < window[["open", "close", "low"]].max(axis=1)).any()
        or (window.low > window[["open", "close", "high"]].min(axis=1)).any()
    ):
        raise ValueError("future OHLC relationships are invalid")
    if len(window) < window_size:
        return None

    peak = float(window.high.max())
    trough = float(window.low.min())
    final_close = float(window.close.iloc[-1])
    breakout_close = float(signal.breakout_close)
    breakout_level = float(signal.breakout_level)
    mfe = peak / breakout_close - 1
    mae = trough / breakout_close - 1
    maximum_extension = peak / breakout_level - 1
    final_position = final_close / breakout_level - 1
    giveback = (
        (peak - final_close) / (peak - breakout_close)
        if peak > breakout_close
        else None
    )
    threshold_reached = mfe >= threshold

    if threshold_reached:
        classification = OutcomeClass.WINNER
    elif (
        mfe >= threshold * settings.reversal_min_threshold_fraction
        and giveback is not None
        and giveback >= settings.reversal_giveback_fraction
        and final_close < breakout_level
    ):
        classification = OutcomeClass.REVERSAL
    elif final_close < breakout_level:
        classification = OutcomeClass.FAILED_BREAKOUT
    else:
        classification = OutcomeClass.WEAK_EXPANSION

    interval = timedelta(minutes=signal.interval_minutes)
    return CandidateOutcome(
        window_start=window.open_time.iloc[0].to_pydatetime(),
        window_end=window.open_time.iloc[-1].to_pydatetime() + interval,
        observed_candles=window_size,
        classification=classification,
        threshold=threshold,
        threshold_reached=threshold_reached,
        mfe=mfe,
        mae=mae,
        maximum_extension=maximum_extension,
        final_vs_breakout_level=final_position,
        giveback_fraction=giveback,
    )
