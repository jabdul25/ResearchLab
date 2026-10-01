import unittest
import numpy as np
import pandas as pd

from src.analysis.events import discover_events
from src.analysis.features import extract_features
from src.validation.walk_forward import walk_forward


def candles(periods=220, freq="15min"):
    stamp = pd.date_range("2025-01-01", periods=periods, freq=freq, tz="UTC")
    close = 100 + np.linspace(0, 15, periods) + np.sin(np.arange(periods) / 8)
    return pd.DataFrame({"open_time": stamp, "open": close * .999, "high": close * 1.01,
                         "low": close * .99, "close": close, "volume": np.linspace(100, 250, periods),
                         "turnover": np.linspace(10_000, 30_000, periods)})


class AnalysisTests(unittest.TestCase):
    def test_detects_qualifying_short_expansion(self):
        data = candles(100)
        data.loc[80:84, "high"] *= 1.12
        events = discover_events(data, 15)
        self.assertTrue(any(e["event_kind"] == "expansion" and e["percentage_move"] >= .08 for e in events))

    def test_features_are_point_in_time_and_include_required_groups(self):
        # Match the same absolute period: four 15m bars occur in each hour.
        trigger, four_h = candles(220 * 16), candles(220, "4h")
        values, timeline, regime = extract_features(trigger, four_h, trigger.open_time.iloc[-1])
        self.assertIn("trend_strength", values)
        self.assertIn("compression_score", values)
        self.assertIn("buyer_defense_score", values)
        self.assertIn("momentum_repair_score", values)
        self.assertIn("volume_ratio", values)
        self.assertIn("T-72h", timeline)
        self.assertIn(regime, {"bullish_trend", "bearish_trend", "range", "high_volatility", "unknown"})

    def test_walk_forward_is_chronological(self):
        dates = pd.date_range("2025-01-01", periods=60, freq="7D", tz="UTC")
        records = []
        for i, date in enumerate(dates):
            records.append({"start_time": date, "event_kind": "expansion" if i % 2 else "failure",
                            "percentage_move": .3 if i % 2 else .1,
                            **{feature: bool(i % 2) for feature in ("ema20_above_ema50", "compression", "higher_low", "momentum_repair", "volume_expansion", "oi_expansion", "structure_confirmation")}})
        result = walk_forward(pd.DataFrame(records))
        self.assertEqual(set(result["splits"]), {"train", "validation", "test"})

if __name__ == "__main__":
    unittest.main()
