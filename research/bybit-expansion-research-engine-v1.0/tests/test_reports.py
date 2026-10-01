from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from src.reports.generator import write_reports


def report_payload() -> dict:
    horizons = {name: {"available": 0, "total": 2} for name in ("T-72h", "T-48h", "T-24h", "T-12h", "T-6h", "T-3h", "T-1h")}
    return {
        "manifest": {"candidates": {"structure_lookback": 20}},
        "provenance": {
            "run_id": 7, "git_commit_hash": "abc123", "configuration_hash": "cfg123",
            "schema_version": "002_candidate_invariants", "schema_checksums": {"001": "sha"},
            "database_identity": {"database": "research"},
        },
        "population": {
            "total_breakout_candidates": 20, "admitted_candidates": 16,
            "rejected_duplicates": 4, "liquidity_excluded": 3,
            "pending_outcomes": 5, "eligible_labeled_candidates": 8,
            "winners": 2, "reversals": 2, "failed_breakouts": 2, "weak_expansions": 2,
        },
        "evidence": {
            "winner_count": 2, "failure_count": 6,
            "cohorts": {"all_failures": {"comparisons": [
                {"feature": "volume_expansion", "type": "boolean", "winner_n": 2,
                 "failure_n": 6, "winner_frequency": 1.0, "failure_frequency": 1/3,
                 "difference": 2/3, "winner_coverage": 1.0, "failure_coverage": 1.0},
                {"feature": "oi_expansion", "type": "boolean", "winner_n": 0,
                 "failure_n": 0, "winner_frequency": None, "failure_frequency": None,
                 "difference": None, "winner_coverage": 0.0, "failure_coverage": 0.0},
            ]}},
        },
        "timeline_coverage": horizons,
        "regimes": {"bullish_trend": {"winner_count": 2, "failure_count": 3}},
        "validation": {
            "train": {"status": "sufficient", "definitions": {"volume_expansion": {"direction": "higher"}}},
            "validation": {"status": "sufficient", "definitions": {"volume_expansion": {"direction": "higher"}}},
            "test": {"status": "sufficient", "definitions": {"volume_expansion": {"direction": "higher"}}},
            "surviving_features": ["volume_expansion"], "robustness": 1.0,
        },
        "leakage": {
            "status": "PASS", "point_in_time_liquidity": "PASS",
            "decision_time_features": "PASS", "future_only_labels": "PASS",
        },
        "validated_market_state": {
            "name": "Expansion Continuation State", "status": "RESEARCH_ONLY",
            "interpretation": "Historically associated market conditions; no causal claim.",
            "conditions": ["volume_expansion: higher among winners"],
        },
    }


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_all_five_reports_and_machine_readable_payload_are_generated(self):
        paths = write_reports(self.directory, report_payload())
        self.assertEqual(
            {path.name for path in paths},
            {"01_expansion_anatomy.md", "02_failed_breakouts.md", "03_feature_separation.md",
             "04_leakage_audit.md", "05_candidate_population.md", "research_results.json"},
        )
        self.assertEqual(json.loads((self.directory / "research_results.json").read_text())["provenance"]["run_id"], 7)
        self.assertFalse(list(self.directory.glob("*.tmp")))

    def test_reports_include_population_provenance_validation_and_association_language(self):
        write_reports(self.directory, report_payload())
        population = (self.directory / "05_candidate_population.md").read_text()
        anatomy = (self.directory / "01_expansion_anatomy.md").read_text()
        separation = (self.directory / "03_feature_separation.md").read_text()
        self.assertIn("Total breakout candidates: 20", population)
        self.assertIn("Admitted + rejected duplicates: 16 + 4 = 20", population)
        for text in ("Winners: 2", "Reversals: 2", "Failed breakouts: 2", "Weak expansions: 2", "Liquidity excluded: 3"):
            self.assertIn(text, population)
        self.assertIn("git commit: abc123", separation)
        self.assertIn("configuration hash: cfg123", separation)
        self.assertIn("schema version: 002_candidate_invariants", separation)
        self.assertIn("Historically associated", separation)
        self.assertIn("train: sufficient", separation)
        for horizon in ("T-72h", "T-48h", "T-24h", "T-12h", "T-6h", "T-3h", "T-1h"):
            self.assertIn(f"{horizon}: 0/2", anatomy)

    def test_missing_oi_is_reported_as_missing_not_zero(self):
        write_reports(self.directory, report_payload())
        anatomy = (self.directory / "01_expansion_anatomy.md").read_text()
        self.assertIn("OI coverage: 0/8", anatomy)
        self.assertNotIn("OI expansion: 0.0%", anatomy)

    def test_invalid_population_accounting_is_rejected(self):
        payload = report_payload()
        payload["population"]["admitted_candidates"] = 15
        with self.assertRaises(ValueError):
            write_reports(self.directory, payload)

    def test_same_payload_is_offered_for_database_artifact_storage(self):
        captured = []
        payload = report_payload()
        write_reports(self.directory, payload, lambda name, value: captured.append((name, value)))
        self.assertEqual(captured, [("research_results", payload)])


if __name__ == "__main__":
    unittest.main()
