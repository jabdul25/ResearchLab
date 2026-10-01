from __future__ import annotations

import unittest

import pandas as pd

from src.analysis.evidence import candidate_population, compare_matched_candidates
from src.config import Settings
from src.strategy.candidate import ValidatedMarketState
from src.validation.walk_forward import walk_forward


def population_frame() -> pd.DataFrame:
    classes = [
        "winner", "winner", "reversal", "reversal", "failed_breakout",
        "failed_breakout", "weak_expansion", "weak_expansion",
    ]
    return pd.DataFrame(
        {
            "decision_time": pd.date_range("2025-01-01", periods=8, freq="15min", tz="UTC"),
            "symbol": ["ALTUSDT"] * 4 + ["COINUSDT"] * 4,
            "interval_minutes": [15] * 6 + [60] * 2,
            "regime": ["bullish_trend"] * 4 + ["range"] * 4,
            "classification": classes,
            "ema20_above_ema50": [True, True, False, True, False, False, True, False],
            "volume_expansion": [True, True, False, False, False, True, False, False],
            "trend_strength": [2.0, 4.0, 1.0, None, 0.0, 1.0, 1.5, 0.5],
        }
    )


class MatchedEvidenceTests(unittest.TestCase):
    def test_comparison_uses_only_matched_outcome_classes(self):
        frame = pd.concat(
            [population_frame(), pd.DataFrame([{"classification": "pending"}])],
            ignore_index=True,
        )
        result = compare_matched_candidates(frame)
        self.assertEqual(result["winner_count"], 2)
        self.assertEqual(result["failure_count"], 6)
        self.assertEqual(
            set(result["cohorts"]),
            {"all_failures", "reversal", "failed_breakout", "weak_expansion"},
        )

    def test_statistics_include_counts_coverage_and_robust_summaries(self):
        result = compare_matched_candidates(population_frame())
        boolean = next(
            row for row in result["cohorts"]["all_failures"]["comparisons"]
            if row["feature"] == "volume_expansion"
        )
        continuous = next(
            row for row in result["cohorts"]["all_failures"]["comparisons"]
            if row["feature"] == "trend_strength"
        )
        self.assertEqual(boolean["winner_n"], 2)
        self.assertEqual(boolean["failure_n"], 6)
        self.assertEqual(boolean["winner_frequency"], 1.0)
        self.assertIn("winner_coverage", continuous)
        self.assertEqual(continuous["winner_median"], 3.0)
        self.assertIn("failure_iqr", continuous)

    def test_timeframe_regime_and_requested_cohort_slices_are_explicit(self):
        frame = population_frame()
        result = compare_matched_candidates(frame, cohort={"interval_minutes": 15, "regime": "bullish_trend"})
        self.assertEqual(result["slice"], {"interval_minutes": 15, "regime": "bullish_trend"})
        self.assertEqual(result["winner_count"], 2)
        self.assertEqual(result["failure_count"], 2)

    def test_candidate_population_exposes_entire_funnel(self):
        report = candidate_population(
            population_frame(),
            {"total": 20, "duplicates": 4, "liquidity_excluded": 3, "pending": 5},
        )
        self.assertEqual(report["total_breakout_candidates"], 20)
        self.assertEqual(report["eligible_labeled_candidates"], 8)
        self.assertEqual(report["winners"], 2)
        self.assertEqual(report["rejected_duplicates"], 4)


class WalkForwardTests(unittest.TestCase):
    def settings(self, minimum=1):
        return Settings(
            database_url="postgresql://unused",
            minimum_winners_per_split=minimum,
            minimum_failures_per_split=minimum,
        )

    def frame(self):
        rows = []
        for month in range(12):
            timestamp = pd.Timestamp("2025-01-15", tz="UTC") + pd.DateOffset(months=month)
            rows.extend(
                [
                    {"decision_time": timestamp, "classification": "winner", "signal": month != 10},
                    {"decision_time": timestamp + pd.Timedelta(minutes=15), "classification": "failed_breakout", "signal": month == 10},
                ]
            )
        return pd.DataFrame(rows)

    def test_exact_six_three_three_chronology_and_train_definitions(self):
        result = walk_forward(self.frame(), self.settings())
        self.assertEqual(result["train"]["months"], 6)
        self.assertEqual(result["validation"]["months"], 3)
        self.assertEqual(result["test"]["months"], 3)
        self.assertEqual(result["test"]["definitions"], result["train"]["definitions"])
        self.assertEqual(result["validation"]["definitions"], result["train"]["definitions"])
        self.assertEqual(result["train"]["definitions"]["signal"]["direction"], "higher")

    def test_campaign_start_fixes_split_even_when_first_candidate_is_late(self):
        result = walk_forward(
            self.frame(), self.settings(),
            campaign_start=pd.Timestamp("2025-01-01", tz="UTC"),
            campaign_end=pd.Timestamp("2026-01-01", tz="UTC"),
        )
        self.assertEqual(result["window_start"], "2025-01-01T00:00:00+00:00")
        self.assertEqual(result["window_end"], "2026-01-01T00:00:00+00:00")

    def test_small_split_is_insufficient_not_zero_lift(self):
        result = walk_forward(self.frame(), self.settings(minimum=20))
        self.assertEqual(result["validation"]["status"], "insufficient_sample")
        self.assertIsNone(result["validation"]["surviving_features"])
        self.assertIsNone(result["robustness"])

    def test_validated_market_state_is_descriptive_only(self):
        validation = walk_forward(self.frame(), self.settings())
        state = ValidatedMarketState.from_validation(validation)
        self.assertEqual(state.status, "RESEARCH_ONLY")
        self.assertNotIn("entry", state.as_dict())
        self.assertNotIn("stop", state.as_dict())


if __name__ == "__main__":
    unittest.main()
