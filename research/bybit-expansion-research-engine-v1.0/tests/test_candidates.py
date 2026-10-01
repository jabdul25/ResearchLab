import unittest
from datetime import UTC, datetime, timedelta

import pandas as pd

from src.analysis.candidates import (
    admit_candidates,
    detect_breakout_signals,
    historical_liquidity,
)
from src.config import Settings
from src.domain import BreakoutSignal, CandidateStatus


def settings(**overrides) -> Settings:
    return Settings(database_url="postgresql://unused/research").with_overrides(**overrides)


def breakout_frame(lookback: int, interval: int = 15) -> pd.DataFrame:
    times = pd.date_range("2026-01-01", periods=lookback + 1, freq=f"{interval}min", tz="UTC")
    rows = []
    for index, stamp in enumerate(times):
        close = 99.0 if index < lookback else 101.0
        rows.append(
            {
                "open_time": stamp,
                "open": close,
                "high": 100.0 if index < lookback else 102.0,
                "low": 98.0,
                "close": close,
                "volume": 10.0,
                "turnover": 100.0,
            }
        )
    return pd.DataFrame(rows)


def signal_at(index: int, interval: int = 15) -> BreakoutSignal:
    opened = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index * interval)
    return BreakoutSignal(
        symbol="ALTUSDT",
        interval_minutes=interval,
        breakout_open_time=opened,
        decision_time=opened + timedelta(minutes=interval),
        structure_lookback=20,
        breakout_level=100.0,
        breakout_close=101.0,
        status=CandidateStatus.ADMITTED,
    )


class CandidateDetectionTests(unittest.TestCase):
    def test_breakout_level_uses_preceding_highs_not_breakout_high(self):
        frame = breakout_frame(20)

        signals = detect_breakout_signals(frame, "ALTUSDT", 15, settings())

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].breakout_level, 100.0)
        self.assertEqual(signals[0].breakout_close, 101.0)
        self.assertEqual(
            signals[0].decision_time,
            frame.open_time.iloc[20].to_pydatetime() + timedelta(minutes=15),
        )

    def test_supported_structure_lookbacks_need_no_code_change(self):
        for lookback in (10, 20, 30, 50):
            with self.subTest(lookback=lookback):
                result = detect_breakout_signals(
                    breakout_frame(lookback),
                    "ALTUSDT",
                    15,
                    settings(structure_lookback=lookback),
                )
                self.assertEqual(result[0].structure_lookback, lookback)

    def test_overlapping_signals_are_retained_as_rejected_duplicates(self):
        admitted, overlapping, after_window = admit_candidates(
            [signal_at(0), signal_at(12), signal_at(13)], 12
        )

        self.assertEqual(admitted.status, CandidateStatus.ADMITTED)
        self.assertEqual(overlapping.status, CandidateStatus.REJECTED_DUPLICATE)
        self.assertEqual(overlapping.duplicate_of_key, admitted.key)
        self.assertEqual(overlapping.rejection_reason, "inside_outcome_window")
        self.assertEqual(after_window.status, CandidateStatus.ADMITTED)

    def test_invalid_market_values_are_rejected(self):
        frame = breakout_frame(20)
        frame.loc[0, "close"] = 0

        with self.assertRaisesRegex(ValueError, "positive"):
            detect_breakout_signals(frame, "ALTUSDT", 15, settings())


class LiquidityTests(unittest.TestCase):
    def liquidity_frame(self, periods=97):
        return pd.DataFrame(
            {
                "open_time": pd.date_range(
                    "2026-01-01", periods=periods, freq="15min", tz="UTC"
                ),
                "volume": [2.0] * periods,
                "turnover": [6_000.0] * periods,
            }
        )

    def test_uses_exactly_96_completed_bars(self):
        frame = self.liquidity_frame()
        decision_time = frame.open_time.iloc[95].to_pydatetime() + timedelta(minutes=15)

        snapshot = historical_liquidity(frame, decision_time, 500_000)

        self.assertEqual(snapshot.observed_bars, 96)
        self.assertEqual(snapshot.volume_24h, 192.0)
        self.assertEqual(snapshot.turnover_24h, 576_000.0)
        self.assertTrue(snapshot.eligible)
        self.assertIsNone(snapshot.reason)

    def test_future_turnover_is_excluded_at_exact_decision_boundary(self):
        frame = self.liquidity_frame()
        decision_time = frame.open_time.iloc[95].to_pydatetime() + timedelta(minutes=15)
        baseline = historical_liquidity(frame, decision_time, 500_000)
        frame.loc[96, "turnover"] = 999_999_999.0

        mutated = historical_liquidity(frame, decision_time, 500_000)

        self.assertEqual(mutated, baseline)

    def test_incomplete_history_is_stored_ineligible(self):
        frame = self.liquidity_frame(periods=40)
        decision_time = frame.open_time.iloc[-1].to_pydatetime() + timedelta(minutes=15)

        snapshot = historical_liquidity(frame, decision_time, 500_000)

        self.assertEqual(snapshot.observed_bars, 40)
        self.assertFalse(snapshot.eligible)
        self.assertEqual(snapshot.reason, "incomplete_liquidity_window")

    def test_negative_historical_turnover_is_rejected(self):
        frame = self.liquidity_frame(periods=96)
        frame.loc[10, "turnover"] = -1
        decision_time = frame.open_time.iloc[-1].to_pydatetime() + timedelta(minutes=15)

        with self.assertRaisesRegex(ValueError, "non-negative"):
            historical_liquidity(frame, decision_time, 500_000)


if __name__ == "__main__":
    unittest.main()
