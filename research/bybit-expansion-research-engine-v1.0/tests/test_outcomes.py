import unittest
from datetime import UTC, datetime, timedelta

import pandas as pd

from src.analysis.outcomes import label_outcome
from src.config import Settings
from src.domain import BreakoutSignal, CandidateStatus, OutcomeClass


def signal() -> BreakoutSignal:
    opened = datetime(2026, 1, 1, tzinfo=UTC)
    return BreakoutSignal(
        symbol="ALTUSDT",
        interval_minutes=15,
        breakout_open_time=opened,
        decision_time=opened + timedelta(minutes=15),
        structure_lookback=20,
        breakout_level=100.0,
        breakout_close=101.0,
        status=CandidateStatus.ADMITTED,
    )


def future(high=103.0, low=100.0, final=101.0, periods=12) -> pd.DataFrame:
    rows = []
    for index in range(periods):
        rows.append(
            {
                "open_time": signal().decision_time + timedelta(minutes=15 * index),
                "open": 101.0,
                "high": high,
                "low": low,
                "close": final if index == periods - 1 else 102.0,
            }
        )
    return pd.DataFrame(rows)


SETTINGS = Settings(database_url="postgresql://unused/research")


class OutcomeTests(unittest.TestCase):
    def test_winner_precedes_reversal_after_threshold_is_reached(self):
        frame = future(high=110.0, low=98.0, final=99.0)

        result = label_outcome(signal(), frame, 0.08, SETTINGS)

        self.assertEqual(result.classification, OutcomeClass.WINNER)
        self.assertTrue(result.threshold_reached)

    def test_reversal_precedes_failed_breakout(self):
        result = label_outcome(
            signal(), future(high=106.0, low=98.0, final=99.0), 0.08, SETTINGS
        )

        self.assertEqual(result.classification, OutcomeClass.REVERSAL)
        self.assertGreaterEqual(result.giveback_fraction, 0.70)

    def test_subthreshold_close_below_level_is_failed_breakout(self):
        result = label_outcome(
            signal(), future(high=103.0, low=98.0, final=99.0), 0.08, SETTINGS
        )

        self.assertEqual(result.classification, OutcomeClass.FAILED_BREAKOUT)

    def test_remaining_subthreshold_candidate_is_weak_expansion(self):
        result = label_outcome(
            signal(), future(high=103.0, low=100.0, final=101.0), 0.08, SETTINGS
        )

        self.assertEqual(result.classification, OutcomeClass.WEAK_EXPANSION)

    def test_metrics_and_window_bounds_are_reproducible(self):
        frame = future(high=106.0, low=97.0, final=99.0)

        result = label_outcome(signal(), frame, 0.08, SETTINGS)

        self.assertAlmostEqual(result.mfe, 106.0 / 101.0 - 1)
        self.assertAlmostEqual(result.mae, 97.0 / 101.0 - 1)
        self.assertAlmostEqual(result.maximum_extension, 106.0 / 100.0 - 1)
        self.assertAlmostEqual(result.final_vs_breakout_level, 99.0 / 100.0 - 1)
        self.assertAlmostEqual(result.giveback_fraction, (106.0 - 99.0) / (106.0 - 101.0))
        self.assertEqual(result.window_start, frame.open_time.iloc[0].to_pydatetime())
        self.assertEqual(
            result.window_end,
            frame.open_time.iloc[-1].to_pydatetime() + timedelta(minutes=15),
        )
        self.assertEqual(result.observed_candles, 12)

    def test_incomplete_window_is_pending(self):
        self.assertIsNone(label_outcome(signal(), future(periods=11), 0.08, SETTINGS))

    def test_thirteenth_candle_is_never_read(self):
        frame = future(periods=13)
        frame.loc[12, "high"] = 1_000.0

        result = label_outcome(signal(), frame, 0.08, SETTINGS)

        self.assertEqual(result.classification, OutcomeClass.WEAK_EXPANSION)
        self.assertFalse(result.threshold_reached)

    def test_nonpositive_future_price_is_rejected(self):
        frame = future()
        frame.loc[3, "low"] = 0

        with self.assertRaisesRegex(ValueError, "positive"):
            label_outcome(signal(), frame, 0.08, SETTINGS)

    def test_future_candles_must_be_contiguous_and_after_breakout(self):
        frame = future()
        frame.loc[0, "open_time"] = signal().breakout_open_time

        with self.assertRaisesRegex(ValueError, "expected outcome timestamps"):
            label_outcome(signal(), frame, 0.08, SETTINGS)


if __name__ == "__main__":
    unittest.main()
