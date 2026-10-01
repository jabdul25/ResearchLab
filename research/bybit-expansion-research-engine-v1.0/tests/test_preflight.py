from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

from sqlalchemy import text

from src.config import Settings
from src.db import engine_for, initialize
from src.pipeline.preflight import (
    PreflightRequired,
    check_campaign_gate,
    launch_campaign,
    run_preflight,
    verify_database,
)
from run_research import build_parser, main


TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://research:research@127.0.0.1:55432/expansion_research_test",
)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PreflightTests(unittest.TestCase):
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

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            database_url=TEST_DATABASE_URL,
            reports_dir=Path(self.temp.name),
            history_months=12,
        )
        self.probes = {
            "bybit_reachable": lambda: True,
            "disk_free_bytes": lambda _path: 60 * 1024**3,
            "leakage_tests": lambda _root: {"passed": True, "tests": 7},
            "utc_now": lambda: datetime.now(UTC),
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_successful_preflight_writes_markdown_and_json(self):
        result = run_preflight(self.engine, self.settings, PROJECT_ROOT, self.probes)
        self.assertTrue(result.passed)
        self.assertEqual(result.report_path.name, "00_preflight.md")
        self.assertTrue(result.json_path.exists())
        self.assertTrue(result.payload["database"]["migration_checksums_verified"])
        self.assertGreaterEqual(result.payload["system"]["disk_free_gb"], 50)
        self.assertEqual(result.payload["campaign_bounds"]["target_months"], 12)
        self.assertNotIn("historical_coverage_complete", result.payload)
        self.assertIn("fingerprint", result.payload["code_revision_state"])

    def test_no_cli_subcommand_prints_help_without_touching_database(self):
        output = StringIO()
        with redirect_stdout(output):
            code = main([])
        self.assertEqual(code, 0)
        self.assertIn("{preflight,campaign}", output.getvalue())

    def test_cli_exposes_read_only_database_and_gate_checks(self):
        parser = build_parser()
        self.assertTrue(parser.parse_args(["preflight", "--verify-database"]).verify_database)
        self.assertTrue(parser.parse_args(["campaign", "--preflight-id", "1", "--check-only"]).check_only)

    def test_database_verification_reports_provenance_and_constraints(self):
        result = verify_database(self.engine, self.settings)
        self.assertTrue(result["passed"])
        self.assertEqual(result["schema_version"], "004_immutable_research_rows")
        self.assertTrue(result["migration_checksums_verified"])
        self.assertTrue(result["required_constraints_verified"])

    def test_preflight_fails_on_schema_checksum_mismatch(self):
        with self.engine.begin() as connection:
            original = connection.execute(
                text("SELECT checksum FROM schema_migrations WHERE version='001_candidate_first'")
            ).scalar_one()
            connection.execute(
                text("UPDATE schema_migrations SET checksum='wrong' WHERE version='001_candidate_first'")
            )
        try:
            result = run_preflight(self.engine, self.settings, PROJECT_ROOT, self.probes)
            self.assertFalse(result.passed)
            self.assertIn("schema_provenance", result.failures)
        finally:
            with self.engine.begin() as connection:
                connection.execute(
                    text("UPDATE schema_migrations SET checksum=:checksum WHERE version='001_candidate_first'"),
                    {"checksum": original},
                )

    def test_preflight_checks_active_jobs_and_all_required_invariants(self):
        result = run_preflight(self.engine, self.settings, PROJECT_ROOT, self.probes)
        expected = {
            "bybit_reachability", "utc_clock", "disk_space", "database_connectivity",
            "required_tables", "schema_provenance", "campaign_bounds",
            "active_collection_jobs", "leakage_invariants",
        }
        self.assertTrue(expected <= set(result.payload["checks"]))

    def test_probe_exception_fails_closed_and_is_reported(self):
        probes = dict(self.probes)
        probes["bybit_reachable"] = lambda: (_ for _ in ()).throw(OSError("offline"))
        result = run_preflight(self.engine, self.settings, PROJECT_ROOT, probes)
        self.assertFalse(result.passed)
        self.assertIn("bybit_reachability", result.failures)
        self.assertIn("offline", result.payload["checks"]["bybit_reachability"]["detail"])

    def test_campaign_rejects_missing_or_stale_preflight(self):
        with self.assertRaises(PreflightRequired):
            launch_campaign(self.engine, self.settings, PROJECT_ROOT, None, lambda *_: {})
        result = run_preflight(self.engine, self.settings, PROJECT_ROOT, self.probes)
        changed = self.settings.with_overrides(structure_lookback=30)
        with self.assertRaises(PreflightRequired):
            launch_campaign(self.engine, changed, PROJECT_ROOT, result.preflight_id, lambda *_: {})

    def test_gate_check_is_read_only(self):
        result = run_preflight(self.engine, self.settings, PROJECT_ROOT, self.probes)
        with self.engine.connect() as connection:
            before = connection.execute(text("SELECT count(*) FROM research_runs")).scalar_one()
        gate = check_campaign_gate(self.engine, self.settings, PROJECT_ROOT, result.preflight_id)
        with self.engine.connect() as connection:
            after = connection.execute(text("SELECT count(*) FROM research_runs")).scalar_one()
        self.assertEqual(gate["status"], "ready")
        self.assertEqual(before, after)

    def test_campaign_always_writes_started_and_terminal_snapshot(self):
        result = run_preflight(self.engine, self.settings, PROJECT_ROOT, self.probes)

        def fail(_orchestrator, _run_id, _bounds):
            raise RuntimeError("deliberate stage failure")

        with self.assertRaises(RuntimeError):
            launch_campaign(self.engine, self.settings, PROJECT_ROOT, result.preflight_id, fail)
        with self.engine.connect() as connection:
            run_id = connection.execute(text("SELECT max(id) FROM research_runs")).scalar_one()
            phases = connection.execute(
                text("SELECT snapshot_phase FROM research_run_snapshots WHERE run_id=:id ORDER BY id"),
                {"id": run_id},
            ).scalars().all()
            snapshot = connection.execute(
                text("SELECT * FROM research_run_snapshots WHERE run_id=:id AND snapshot_phase='failed'"),
                {"id": run_id},
            ).mappings().one()
        self.assertEqual(phases, ["started", "failed"])
        for field in (
            "git_commit_hash", "code_revision_state", "configuration_hash", "schema_checksums",
            "database_identity", "campaign_start", "campaign_end", "symbol_universe_count",
            "candidate_counts", "execution_timestamp",
        ):
            self.assertIsNotNone(snapshot[field])

    def test_successful_campaign_attempt_writes_completed_snapshot(self):
        result = run_preflight(self.engine, self.settings, PROJECT_ROOT, self.probes)
        launched = launch_campaign(
            self.engine, self.settings, PROJECT_ROOT, result.preflight_id,
            lambda _orchestrator, _run_id, _bounds: {"stages": "complete"},
        )
        with self.engine.connect() as connection:
            phases = connection.execute(
                text("SELECT snapshot_phase FROM research_run_snapshots WHERE run_id=:id ORDER BY id"),
                {"id": launched["run_id"]},
            ).scalars().all()
        self.assertEqual(phases, ["started", "completed"])


if __name__ == "__main__":
    unittest.main()
