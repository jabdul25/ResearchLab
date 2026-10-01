import unittest
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from src.analysis.candidates import historical_liquidity
from src.analysis.features import extract_candidate_features, regime_at
from src.analysis.outcomes import label_outcome
from src.config import Settings
from src.domain import BreakoutSignal, CandidateStatus, Regime


SETTINGS = Settings(database_url="postgresql://unused/research")


def candles(periods, freq, trend=0.02, start="2026-01-01"):
    times = pd.date_range(start, periods=periods, freq=freq, tz="UTC")
    close = 100.0 + np.arange(periods) * trend
    return pd.DataFrame(
        {
            "open_time": times,
            "open": close - 0.1,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 100.0 + np.arange(periods) % 20,
            "turnover": 10_000.0 + np.arange(periods) % 20,
        }
    )


class LeakageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trigger = candles(2_000, "15min")
        cls.four_h = candles(125, "4h", trend=0.5)
        cls.breakout_index = 1_979
        cls.decision_time = (
            cls.trigger.open_time.iloc[cls.breakout_index].to_pydatetime()
            + timedelta(minutes=15)
        )
        cls.liquidity = historical_liquidity(
            cls.trigger, cls.decision_time, SETTINGS.minimum_turnover_24h
        )
        opened = cls.trigger.open_time.iloc[cls.breakout_index].to_pydatetime()
        cls.signal = BreakoutSignal(
            symbol="ALTUSDT",
            interval_minutes=15,
            breakout_open_time=opened,
            decision_time=cls.decision_time,
            structure_lookback=20,
            breakout_level=float(cls.trigger.high.iloc[cls.breakout_index - 20 : cls.breakout_index].max()),
            breakout_close=float(cls.trigger.close.iloc[cls.breakout_index]),
            status=CandidateStatus.ADMITTED,
        )

    def snapshot(self, trigger=None, four_h=None, oi=None, funding=None):
        return extract_candidate_features(
            self.trigger if trigger is None else trigger,
            self.four_h if four_h is None else four_h,
            self.decision_time,
            self.liquidity,
            oi=oi,
            funding=funding,
            settings=SETTINGS,
        )

    def outcome_frame(self, high_multiplier=1.02):
        frame = self.trigger.iloc[self.breakout_index + 1 : self.breakout_index + 13].copy()
        frame["high"] = self.signal.breakout_close * high_multiplier
        frame["low"] = np.minimum(frame.low, self.signal.breakout_close * 0.99)
        frame["open"] = self.signal.breakout_close
        frame["close"] = self.signal.breakout_close
        return frame

    def test_future_mutation_changes_only_outcome(self):
        baseline_snapshot = self.snapshot()
        baseline_liquidity = self.liquidity
        baseline_outcome = label_outcome(
            self.signal, self.outcome_frame(1.02), 0.08, SETTINGS
        )
        mutated_trigger = self.trigger.copy()
        mutated_trigger.loc[self.breakout_index + 1 :, ["high", "close", "turnover"]] *= 20
        mutated_outcome_frame = self.outcome_frame(1.20)

        mutated_snapshot = self.snapshot(trigger=mutated_trigger)
        mutated_liquidity = historical_liquidity(
            mutated_trigger, self.decision_time, SETTINGS.minimum_turnover_24h
        )
        mutated_outcome = label_outcome(
            self.signal, mutated_outcome_frame, 0.08, SETTINGS
        )

        self.assertEqual(mutated_snapshot, baseline_snapshot)
        self.assertEqual(mutated_liquidity, baseline_liquidity)
        self.assertNotEqual(mutated_outcome, baseline_outcome)

    def test_current_and_future_turnover_do_not_change_historical_eligibility(self):
        baseline = self.liquidity
        mutated = self.trigger.copy()
        mutated.loc[self.breakout_index + 1 :, "turnover"] = 999_999_999.0
        current_ticker_turnover = 0

        result = historical_liquidity(
            mutated, self.decision_time, SETTINGS.minimum_turnover_24h
        )

        self.assertEqual(current_ticker_turnover, 0)
        self.assertEqual(result, baseline)

    def test_features_work_when_all_future_data_is_removed(self):
        bounded = self.trigger.iloc[: self.breakout_index + 1].copy()
        snapshot = self.snapshot(trigger=bounded)
        empty_future = pd.DataFrame(columns=["open_time", "open", "high", "low", "close"])

        self.assertTrue(snapshot.features)
        self.assertIsNone(label_outcome(self.signal, empty_future, 0.08, SETTINGS))

    def test_input_max_timestamps_never_exceed_decision_time(self):
        oi = pd.DataFrame(
            {
                "observed_at": [
                    self.decision_time - timedelta(hours=2),
                    self.decision_time - timedelta(hours=1),
                    self.decision_time + timedelta(hours=1),
                ],
                "open_interest": [100.0, 110.0, 9_999.0],
            }
        )
        funding = pd.DataFrame(
            {
                "funding_time": [
                    self.decision_time - timedelta(hours=8),
                    self.decision_time + timedelta(hours=8),
                ],
                "funding_rate": [0.001, 9.0],
            }
        )

        snapshot = self.snapshot(oi=oi, funding=funding)

        for timestamp in snapshot.source_max_timestamps.values():
            if timestamp is not None:
                self.assertLessEqual(timestamp, self.decision_time)
        self.assertAlmostEqual(snapshot.features["oi_change"], 0.10)
        self.assertEqual(snapshot.features["funding_rate"], 0.001)

    def test_missing_futures_data_remains_null(self):
        snapshot = self.snapshot(oi=pd.DataFrame(), funding=pd.DataFrame())

        self.assertIsNone(snapshot.features["oi_change"])
        self.assertIsNone(snapshot.features["oi_expansion"])
        self.assertIsNone(snapshot.features["funding_rate"])

    def test_timeline_has_seven_independently_bounded_horizons(self):
        snapshot = self.snapshot()

        self.assertEqual(
            set(snapshot.timeline),
            {"T-72h", "T-48h", "T-24h", "T-12h", "T-6h", "T-3h", "T-1h"},
        )
        for item in snapshot.timeline.values():
            for timestamp in item.source_max_timestamps.values():
                if timestamp is not None:
                    self.assertLessEqual(timestamp, item.as_of)

    def test_invalid_historical_price_is_rejected_but_future_is_not_read(self):
        historical = self.trigger.copy()
        historical.loc[self.breakout_index, "close"] = 0
        with self.assertRaisesRegex(ValueError, "positive"):
            self.snapshot(trigger=historical)

        future_only = self.trigger.copy()
        future_only.loc[self.breakout_index + 1, "close"] = 0
        self.assertEqual(self.snapshot(trigger=future_only), self.snapshot())


class RegimeTests(unittest.TestCase):
    def at_end(self, frame):
        decision = frame.open_time.iloc[-1].to_pydatetime() + timedelta(hours=4)
        return regime_at(frame, decision, SETTINGS)

    def test_bullish_bearish_and_range_regimes(self):
        self.assertEqual(self.at_end(candles(120, "4h", trend=0.5)), Regime.BULLISH_TREND)
        bearish = candles(120, "4h", trend=-0.2)
        bearish.loc[:89, "high"] += 3.0
        bearish.loc[:89, "low"] -= 3.0
        self.assertEqual(self.at_end(bearish), Regime.BEARISH_TREND)
        self.assertEqual(self.at_end(candles(120, "4h", trend=0.0)), Regime.RANGE)

    def test_high_volatility_has_precedence(self):
        frame = candles(120, "4h", trend=0.5)
        frame.loc[119, "high"] = 300.0
        frame.loc[119, "low"] = 1.0

        self.assertEqual(self.at_end(frame), Regime.HIGH_VOLATILITY)

    def test_insufficient_history_is_unknown(self):
        self.assertEqual(self.at_end(candles(50, "4h", trend=0.5)), Regime.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
