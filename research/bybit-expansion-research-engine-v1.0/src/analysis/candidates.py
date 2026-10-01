from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pandas as pd

from src.config import Settings
from src.domain import BreakoutSignal, CandidateStatus, LiquiditySnapshot


MARKET_COLUMNS = ("open_time", "open", "high", "low", "close", "volume", "turnover")


def _utc(value) -> datetime:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    return stamp.to_pydatetime()


def _validated_market_frame(frame: pd.DataFrame) -> pd.DataFrame:
    missing = set(MARKET_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"missing candle columns: {sorted(missing)}")
    result = frame.loc[:, MARKET_COLUMNS].copy()
    result["open_time"] = pd.to_datetime(result.open_time, utc=True)
    if result.open_time.duplicated().any():
        raise ValueError("candle open_time values must be unique")
    result = result.sort_values("open_time").reset_index(drop=True)
    for column in ("open", "high", "low", "close", "volume", "turnover"):
        result[column] = pd.to_numeric(result[column], errors="raise")
    if (result[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("OHLC prices must be positive")
    if (result[["volume", "turnover"]] < 0).any().any():
        raise ValueError("volume and turnover must be non-negative")
    return result


def detect_breakout_signals(
    frame: pd.DataFrame,
    symbol: str,
    interval: int,
    settings: Settings,
) -> list[BreakoutSignal]:
    if interval not in settings.expansion_thresholds:
        raise ValueError(f"unsupported interval: {interval}")
    candles = _validated_market_frame(frame)
    lookback = settings.structure_lookback
    signals = []
    for index in range(lookback, len(candles)):
        breakout_level = float(candles.high.iloc[index - lookback : index].max())
        breakout_close = float(candles.close.iloc[index])
        if breakout_close <= breakout_level:
            continue
        opened = _utc(candles.open_time.iloc[index])
        signals.append(
            BreakoutSignal(
                symbol=symbol,
                interval_minutes=interval,
                breakout_open_time=opened,
                decision_time=opened + timedelta(minutes=interval),
                structure_lookback=lookback,
                breakout_level=breakout_level,
                breakout_close=breakout_close,
                status=CandidateStatus.ADMITTED,
            )
        )
    return signals


def admit_candidates(
    signals: list[BreakoutSignal], outcome_window_candles: int
) -> list[BreakoutSignal]:
    if outcome_window_candles <= 0:
        raise ValueError("outcome_window_candles must be positive")
    admitted_by_market: dict[tuple[str, int], BreakoutSignal] = {}
    result = []
    for signal in sorted(
        signals, key=lambda item: (item.symbol, item.interval_minutes, item.decision_time)
    ):
        market = (signal.symbol, signal.interval_minutes)
        canonical = admitted_by_market.get(market)
        active_end = (
            canonical.decision_time
            + timedelta(
                minutes=canonical.interval_minutes * outcome_window_candles
            )
            if canonical
            else None
        )
        if canonical and signal.decision_time <= active_end:
            result.append(
                replace(
                    signal,
                    status=CandidateStatus.REJECTED_DUPLICATE,
                    duplicate_of_key=canonical.key,
                    rejection_reason="inside_outcome_window",
                    duplicate_window_end=active_end,
                )
            )
            continue
        admitted = replace(
            signal,
            status=CandidateStatus.ADMITTED,
            duplicate_of_key=None,
            rejection_reason=None,
            duplicate_window_end=None,
        )
        admitted_by_market[market] = admitted
        result.append(admitted)
    return result


def historical_liquidity(
    candles_15m: pd.DataFrame,
    decision_time: datetime,
    minimum_turnover: float,
) -> LiquiditySnapshot:
    required = {"open_time", "volume", "turnover"}
    missing = required - set(candles_15m.columns)
    if missing:
        raise ValueError(f"missing liquidity columns: {sorted(missing)}")
    if minimum_turnover <= 0:
        raise ValueError("minimum_turnover must be positive")
    decision = pd.Timestamp(_utc(decision_time))
    frame = candles_15m.loc[:, ["open_time", "volume", "turnover"]].copy()
    frame["open_time"] = pd.to_datetime(frame.open_time, utc=True)
    frame["close_time"] = frame.open_time + pd.Timedelta(minutes=15)
    window = frame[
        (frame.close_time > decision - pd.Timedelta(hours=24))
        & (frame.close_time <= decision)
    ].copy()
    if window.close_time.duplicated().any():
        raise ValueError("liquidity candle close times must be unique")
    for column in ("volume", "turnover"):
        window[column] = pd.to_numeric(window[column], errors="raise")
    if (window[["volume", "turnover"]] < 0).any().any():
        raise ValueError("volume and turnover must be non-negative")

    expected_times = set(
        pd.date_range(end=decision, periods=96, freq="15min", tz="UTC")
    )
    observed_times = set(window.close_time)
    complete = observed_times == expected_times
    turnover = float(window.turnover.sum())
    volume = float(window.volume.sum())
    eligible = complete and turnover >= minimum_turnover
    if not complete:
        reason = "incomplete_liquidity_window"
    elif not eligible:
        reason = "below_turnover_threshold"
    else:
        reason = None
    return LiquiditySnapshot(
        decision_time=decision.to_pydatetime(),
        volume_24h=volume,
        turnover_24h=turnover,
        expected_bars=96,
        observed_bars=len(observed_times),
        eligible=eligible,
        reason=reason,
    )
