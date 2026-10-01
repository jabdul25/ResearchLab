import json
import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from src.config import Settings
from src.db import (
    MigrationChecksumError,
    apply_migrations,
    engine_for,
    initialize,
    read_schema_provenance,
)
from src.domain import BreakoutSignal, CandidateStatus


TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://research:research@127.0.0.1:55432/expansion_research_test",
)


class ConfigurationTests(unittest.TestCase):
    def settings(self) -> Settings:
        with patch.dict(os.environ, {"DATABASE_URL": TEST_DATABASE_URL}):
            return Settings.load("config/system.yaml")

    def test_defaults_drive_the_approved_research_behavior(self):
        settings = self.settings()

        self.assertEqual(settings.structure_lookback, 20)
        self.assertEqual(settings.outcome_window_candles, 12)
        self.assertEqual(settings.minimum_turnover_24h, 500_000)
        self.assertEqual(settings.expansion_thresholds, {15: .08, 60: .15, 240: .25})

    def test_manifest_is_stable_and_contains_validation_inputs(self):
        settings = self.settings()

        first = settings.as_manifest()
        second = settings.as_manifest()

        self.assertEqual(first, second)
        self.assertEqual(first["features"]["ema_slope_lookback_bars"], 6)
        self.assertEqual(first["analysis"]["minimum_winners_per_split"], 20)
        self.assertEqual(len(settings.configuration_hash()), 64)

    def test_invalid_research_window_is_rejected(self):
        settings = self.settings()

        with self.assertRaisesRegex(ValueError, "outcome_window_candles"):
            settings.with_overrides(outcome_window_candles=0)

    def test_domain_snapshots_are_frozen(self):
        signal = BreakoutSignal(
            symbol="ALTUSDT",
            interval_minutes=15,
            breakout_open_time=datetime(2026, 1, 1, tzinfo=UTC),
            decision_time=datetime(2026, 1, 1, 0, 15, tzinfo=UTC),
            structure_lookback=20,
            breakout_level=100.0,
            breakout_close=101.0,
            status=CandidateStatus.ADMITTED,
        )

        with self.assertRaises(FrozenInstanceError):
            signal.breakout_close = 99.0


class SchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = engine_for(TEST_DATABASE_URL)
        with cls.engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        initialize(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def test_schema_provenance_contains_applied_checksum(self):
        provenance = read_schema_provenance(self.engine)

        self.assertEqual(provenance.schema_version, "002_candidate_invariants")
        self.assertEqual(
            set(provenance.checksums),
            {"001_candidate_first", "002_candidate_invariants"},
        )
        self.assertEqual(len(provenance.checksums["001_candidate_first"]), 64)
        self.assertEqual(len(provenance.checksums["002_candidate_invariants"]), 64)

    def test_changed_recorded_migration_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            migration = Path(directory) / "999_test.sql"
            migration.write_text("CREATE TABLE migration_probe(id INTEGER PRIMARY KEY);\n")
            apply_migrations(self.engine, Path(directory))
            migration.write_text("CREATE TABLE migration_probe(id BIGINT PRIMARY KEY);\n")

            with self.assertRaises(MigrationChecksumError):
                apply_migrations(self.engine, Path(directory))

        with self.engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS migration_probe"))
            connection.execute(text("DELETE FROM schema_migrations WHERE version='999_test'"))

    def test_research_run_snapshots_reject_update_and_delete(self):
        with self.engine.begin() as connection:
            run_id = connection.execute(
                text("INSERT INTO research_runs(status) VALUES ('running') RETURNING id")
            ).scalar_one()
            connection.execute(
                text(
                    """
                    INSERT INTO research_run_snapshots(
                        run_id, snapshot_phase, git_commit_hash,
                        code_revision_state, configuration_hash,
                        schema_version, schema_checksums, database_identity,
                        campaign_start, campaign_end, symbol_universe_count,
                        candidate_counts, execution_timestamp
                    ) VALUES (
                        :run_id, 'started', 'abc123', CAST(:code AS jsonb),
                        'config-hash', '001_candidate_first', CAST(:checksums AS jsonb),
                        CAST(:database AS jsonb), :start, :end, 0,
                        CAST(:counts AS jsonb), :executed
                    )
                    """
                ),
                {
                    "run_id": run_id,
                    "code": json.dumps({"dirty": False, "fingerprint": "source-hash"}),
                    "checksums": json.dumps({"001_candidate_first": "checksum"}),
                    "database": json.dumps({"database": "expansion_research_test"}),
                    "start": datetime(2025, 10, 1, tzinfo=UTC),
                    "end": datetime(2026, 10, 1, tzinfo=UTC),
                    "counts": json.dumps({"admitted": 0}),
                    "executed": datetime(2026, 10, 1, tzinfo=UTC),
                },
            )

        with self.assertRaises(DBAPIError):
            with self.engine.begin() as connection:
                connection.execute(
                    text("UPDATE research_run_snapshots SET symbol_universe_count=1 WHERE run_id=:run_id"),
                    {"run_id": run_id},
                )

        with self.assertRaises(DBAPIError):
            with self.engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM research_run_snapshots WHERE run_id=:run_id"),
                    {"run_id": run_id},
                )

        with self.engine.connect() as connection:
            count = connection.execute(
                text("SELECT count(*) FROM research_run_snapshots WHERE run_id=:run_id"),
                {"run_id": run_id},
            ).scalar_one()
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
