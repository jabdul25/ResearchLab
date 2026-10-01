import os
import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.db import engine_for, initialize, read_schema_provenance
from src.domain import (
    BreakoutSignal,
    CandidateOutcome,
    CandidateStatus,
    FeatureSnapshot,
    LiquiditySnapshot,
    OutcomeClass,
    Regime,
)
from src.persistence.repository import ResearchRepository
from src.pipeline.orchestrator import ResearchOrchestrator
from src.config import Settings


TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://research:research@127.0.0.1:55432/expansion_research_test",
)
BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


def signal_at(index=0, status=CandidateStatus.ADMITTED, duplicate_of_key=None):
    opened = BASE_TIME + timedelta(hours=index)
    return BreakoutSignal(
        symbol="ALTUSDT",
        interval_minutes=15,
        breakout_open_time=opened,
        decision_time=opened + timedelta(minutes=15),
        structure_lookback=20,
        breakout_level=100.0,
        breakout_close=101.0,
        status=status,
        duplicate_of_key=duplicate_of_key,
        rejection_reason=("inside_outcome_window" if duplicate_of_key else None),
        duplicate_window_end=(opened + timedelta(hours=3) if duplicate_of_key else None),
    )


def liquidity(signal, eligible=True, reason=None):
    return LiquiditySnapshot(
        decision_time=signal.decision_time,
        volume_24h=960.0,
        turnover_24h=600_000.0 if eligible else 100_000.0,
        expected_bars=96,
        observed_bars=96,
        eligible=eligible,
        reason=reason if reason else (None if eligible else "below_turnover_threshold"),
    )


def feature(signal, oi_change=None):
    return FeatureSnapshot(
        decision_time=signal.decision_time,
        regime=Regime.BULLISH_TREND,
        features={"higher_low": True, "oi_change": oi_change, "oi_expansion": None},
        timeline={},
        source_max_timestamps={
            "trigger_candle": signal.decision_time,
            "four_h_candle": signal.decision_time,
            "open_interest": None,
            "funding": None,
        },
        input_fingerprint=f"fingerprint-{signal.key}",
    )


def outcome(signal, classification=OutcomeClass.WINNER):
    return CandidateOutcome(
        window_start=signal.decision_time,
        window_end=signal.decision_time + timedelta(minutes=15 * 12),
        observed_candles=12,
        classification=classification,
        threshold=0.08,
        threshold_reached=classification is OutcomeClass.WINNER,
        mfe=0.10 if classification is OutcomeClass.WINNER else 0.02,
        mae=-0.01,
        maximum_extension=0.11,
        final_vs_breakout_level=0.03,
        giveback_fraction=0.2,
    )


class RepositoryIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = engine_for(TEST_DATABASE_URL)
        with cls.engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        initialize(cls.engine)
        cls.repository = ResearchRepository(cls.engine)
        with cls.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO instruments(
                        symbol,status,base_asset,quote_asset,turnover_24h,eligible
                    ) VALUES ('ALTUSDT','Trading','ALT','USDT',0,true)
                    """
                )
            )

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def create_run(self):
        return self.repository.create_run(
            {"candidates": {"structure_lookback": 20}},
            {"start": BASE_TIME - timedelta(days=365), "end": BASE_TIME},
        )

    def test_decision_and_outcome_rows_are_separate_and_idempotent(self):
        run_id = self.create_run()
        candidate = signal_at(run_id)

        self.assertEqual(
            self.repository.upsert_candidates(run_id, [(candidate, liquidity(candidate))]),
            1,
        )
        self.assertEqual(
            self.repository.upsert_candidates(run_id, [(candidate, liquidity(candidate))]),
            0,
        )
        self.assertIsNone(self.repository.read_outcome(run_id, candidate.key))

    def test_rejected_duplicate_cannot_receive_features_or_outcome(self):
        run_id = self.create_run()
        admitted = signal_at(run_id * 10)
        duplicate = signal_at(
            run_id * 10 + 1,
            CandidateStatus.REJECTED_DUPLICATE,
            admitted.key,
        )
        self.repository.upsert_candidates(
            run_id, [(admitted, liquidity(admitted)), (duplicate, None)]
        )
        duplicate_id = self.repository.candidate_id(run_id, duplicate.key)

        with self.assertRaises(DBAPIError):
            self.repository.upsert_outcome(duplicate_id, outcome(duplicate))

    def test_analysis_population_excludes_ineligible_pending_and_duplicates(self):
        run_id = self.create_run()
        eligible = signal_at(run_id * 100)
        ineligible = signal_at(run_id * 100 + 20)
        pending = signal_at(run_id * 100 + 40)
        duplicate = signal_at(
            run_id * 100 + 1,
            CandidateStatus.REJECTED_DUPLICATE,
            eligible.key,
        )
        self.repository.upsert_candidates(
            run_id,
            [
                (eligible, liquidity(eligible)),
                (duplicate, None),
                (ineligible, liquidity(ineligible, False)),
                (pending, liquidity(pending)),
            ],
        )
        for candidate in (eligible, ineligible, pending):
            candidate_id = self.repository.candidate_id(run_id, candidate.key)
            self.repository.upsert_feature_snapshot(candidate_id, feature(candidate))
        self.repository.upsert_outcome(
            self.repository.candidate_id(run_id, eligible.key), outcome(eligible)
        )
        self.repository.upsert_outcome(
            self.repository.candidate_id(run_id, ineligible.key), outcome(ineligible)
        )

        population = self.repository.load_analysis_population(run_id)
        funnel = self.repository.population_funnel(run_id)

        self.assertEqual(population.candidate_key.tolist(), [eligible.key])
        self.assertEqual(funnel["raw_signals"], 4)
        self.assertEqual(funnel["admitted"], 3)
        self.assertEqual(funnel["rejected_duplicates"], 1)
        self.assertEqual(funnel["eligible"], 2)
        self.assertEqual(funnel["pending"], 1)

    def test_liquidity_snapshot_is_immutable(self):
        run_id = self.create_run()
        candidate = signal_at(run_id * 1_000)
        self.repository.upsert_candidates(run_id, [(candidate, liquidity(candidate))])
        candidate_id = self.repository.candidate_id(run_id, candidate.key)

        with self.assertRaises(DBAPIError):
            with self.engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE breakout_candidates "
                        "SET turnover_24h_before_event=1 WHERE id=:id"
                    ),
                    {"id": candidate_id},
                )

    def test_feature_and_outcome_rows_are_immutable(self):
        run_id = self.create_run()
        candidate = signal_at(run_id * 2_000)
        self.repository.upsert_candidates(run_id, [(candidate, liquidity(candidate))])
        candidate_id = self.repository.candidate_id(run_id, candidate.key)
        self.repository.upsert_feature_snapshot(candidate_id, feature(candidate))
        self.repository.upsert_outcome(candidate_id, outcome(candidate))
        for statement in (
            "UPDATE candidate_features SET regime='range' WHERE candidate_id=:id",
            "UPDATE candidate_outcomes SET classification='failed_breakout' WHERE candidate_id=:id",
        ):
            with self.assertRaises(DBAPIError):
                with self.engine.begin() as connection:
                    connection.execute(text(statement), {"id": candidate_id})

    def test_run_audit_uses_persisted_timestamps_and_windows(self):
        run_id = self.create_run()
        candidate = signal_at(run_id * 3_000)
        self.repository.upsert_candidates(run_id, [(candidate, liquidity(candidate))])
        candidate_id = self.repository.candidate_id(run_id, candidate.key)
        self.repository.upsert_feature_snapshot(candidate_id, feature(candidate))
        self.repository.upsert_outcome(candidate_id, outcome(candidate))
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO candles(symbol,interval_minutes,open_time,open,high,low,close,volume,turnover)
                    VALUES ('ALTUSDT',15,:open_time,100,101,99,100,10,6250)
                    ON CONFLICT DO NOTHING
                    """
                ),
                [
                    {"open_time": candidate.decision_time - timedelta(minutes=15 * offset)}
                    for offset in range(96, 0, -1)
                ],
            )
        audit = self.repository.audit_run(run_id, 500_000)
        self.assertTrue(audit["passed"])
        self.assertTrue(all(audit["checks"].values()))

    def test_manifest_and_null_futures_values_round_trip(self):
        run_id = self.create_run()
        candidate = signal_at(run_id * 10_000)
        self.repository.upsert_candidates(run_id, [(candidate, liquidity(candidate))])
        candidate_id = self.repository.candidate_id(run_id, candidate.key)
        self.repository.upsert_feature_snapshot(candidate_id, feature(candidate))
        self.repository.upsert_outcome(candidate_id, outcome(candidate))

        with self.engine.connect() as connection:
            manifest = connection.execute(
                text("SELECT configuration_manifest FROM research_runs WHERE id=:id"),
                {"id": run_id},
            ).scalar_one()
        population = self.repository.load_analysis_population(run_id)

        self.assertEqual(manifest, {"candidates": {"structure_lookback": 20}})
        self.assertIsNone(population.iloc[0].oi_change)
        self.assertIsNone(population.iloc[0].oi_expansion)

    def test_started_and_terminal_provenance_snapshots_are_append_only(self):
        run_id = self.create_run()
        schema = read_schema_provenance(self.engine)
        provenance = {
            "git_commit_hash": "abc123",
            "code_revision_state": {"dirty": False, "fingerprint": "source-hash"},
            "configuration_hash": "config-hash",
            "schema_version": schema.schema_version,
            "schema_checksums": dict(schema.checksums),
            "database_identity": {"database": "expansion_research_test"},
            "campaign_start": BASE_TIME - timedelta(days=365),
            "campaign_end": BASE_TIME,
            "execution_timestamp": BASE_TIME,
        }

        self.repository.append_run_snapshot(
            run_id, "started", provenance, {"symbol_universe_count": 0, "candidates": {}}
        )
        self.repository.append_run_snapshot(
            run_id,
            "failed",
            {**provenance, "execution_timestamp": BASE_TIME + timedelta(hours=1)},
            {"symbol_universe_count": 2, "candidates": {"admitted": 3}},
        )

        with self.assertRaises(IntegrityError):
            self.repository.append_run_snapshot(
                run_id, "started", provenance, {"symbol_universe_count": 99, "candidates": {}}
            )
        snapshots = self.repository.run_snapshots(run_id)
        self.assertEqual([row["snapshot_phase"] for row in snapshots], ["started", "failed"])
        self.assertEqual(snapshots[0]["symbol_universe_count"], 0)
        self.assertEqual(snapshots[1]["candidate_counts"], {"admitted": 3})

    def test_candidate_detection_stage_is_restartable(self):
        with self.engine.begin() as connection:
            connection.execute(text("DELETE FROM candles WHERE symbol='ALTUSDT'"))
            records = []
            for index in range(120):
                opened = BASE_TIME + timedelta(minutes=15 * index)
                close = 101.0 if index == 119 else 99.0
                records.append(
                    {
                        "symbol": "ALTUSDT",
                        "interval": 15,
                        "open_time": opened,
                        "open": close,
                        "high": 102.0 if index == 119 else 100.0,
                        "low": 98.0,
                        "close": close,
                        "volume": 10.0,
                        "turnover": 6_000.0,
                    }
                )
            connection.execute(
                text(
                    """
                    INSERT INTO candles(
                        symbol,interval_minutes,open_time,open,high,low,close,volume,turnover
                    ) VALUES (
                        :symbol,:interval,:open_time,:open,:high,:low,:close,:volume,:turnover
                    )
                    """
                ),
                records,
            )
        run_id = self.create_run()
        orchestrator = ResearchOrchestrator(
            self.engine, Settings(database_url=TEST_DATABASE_URL)
        )

        first = orchestrator.detect_candidates(run_id, symbols=["ALTUSDT"], intervals=(15,))
        second = orchestrator.detect_candidates(run_id, symbols=["ALTUSDT"], intervals=(15,))

        self.assertEqual(first["inserted"], 1)
        self.assertEqual(first["eligible"], 1)
        self.assertEqual(second["inserted"], 0)


if __name__ == "__main__":
    unittest.main()
