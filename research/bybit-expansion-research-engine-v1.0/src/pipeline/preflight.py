from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Callable
from urllib.request import urlopen

import pandas as pd
from sqlalchemy import inspect, text

from src.analysis.evidence import candidate_population, compare_matched_candidates
from src.config import Settings
from src.db import MIGRATIONS, read_schema_provenance
from src.persistence.repository import ResearchRepository
from src.pipeline.orchestrator import ResearchOrchestrator
from src.reports.generator import write_reports
from src.strategy.candidate import ValidatedMarketState
from src.validation.walk_forward import walk_forward


REQUIRED_TABLES = {
    "instruments", "candles", "open_interest", "funding_rates", "collection_jobs",
    "breakout_candidates", "candidate_features", "candidate_outcomes", "research_runs",
    "research_run_snapshots", "research_artifacts", "schema_migrations", "preflight_results",
}


class PreflightRequired(RuntimeError):
    pass


@dataclass(frozen=True)
class PreflightResult:
    preflight_id: int | None
    passed: bool
    failures: tuple[str, ...]
    payload: dict
    report_path: Path
    json_path: Path


def _code_revision_state(project_root: Path) -> dict:
    root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=project_root,
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=project_root,
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    relative = str(project_root.resolve().relative_to(Path(root).resolve()))
    scope = "." if relative == "." else relative
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all", "--", scope],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout
    diff = subprocess.run(
        ["git", "diff", "--binary", "HEAD", "--", scope], cwd=root,
        check=True, capture_output=True,
    ).stdout
    digest = hashlib.sha256(commit.encode() + status.encode() + diff).hexdigest()
    return {
        "git_commit_hash": commit,
        "dirty": bool(status.strip()),
        "project_scope": scope,
        "status": status.splitlines(),
        "fingerprint": digest,
    }


def _expected_schema_checksums() -> dict[str, str]:
    return {
        path.stem: hashlib.sha256(path.read_text().encode()).hexdigest()
        for path in sorted(MIGRATIONS.glob("*.sql"))
    }


def _default_bybit_probe() -> bool:
    with urlopen("https://api.bybit.com/v5/market/time", timeout=10) as response:
        return response.status == 200


def _default_leakage_probe(project_root: Path) -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "unittest", "-q", "tests.test_leakage"],
        cwd=project_root, capture_output=True, text=True,
    )
    return {
        "passed": completed.returncode == 0,
        "command": "python -m unittest -q tests.test_leakage",
        "output": (completed.stdout + completed.stderr)[-4000:],
    }


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def run_preflight(engine, settings: Settings, project_root: Path, probes: dict | None = None) -> PreflightResult:
    probes = probes or {}
    utc_now = probes.get("utc_now", lambda: datetime.now(UTC))()
    try:
        disk_free = probes.get("disk_free_bytes", lambda path: shutil.disk_usage(path).free)(project_root)
        disk_error = None
    except Exception as error:
        disk_free, disk_error = 0, str(error)
    try:
        bybit_reachable = bool(probes.get("bybit_reachable", _default_bybit_probe)())
        bybit_error = None
    except Exception as error:
        bybit_reachable, bybit_error = False, str(error)
    try:
        leakage = probes.get("leakage_tests", _default_leakage_probe)(project_root)
    except Exception as error:
        leakage = {"passed": False, "error": str(error)}
    try:
        code = _code_revision_state(project_root)
        code_error = None
    except Exception as error:
        code = {"git_commit_hash": "unavailable", "dirty": True, "fingerprint": "unavailable"}
        code_error = str(error)
    repository = ResearchRepository(engine)
    checks: dict[str, dict] = {}

    try:
        with engine.connect() as connection:
            db_now = connection.execute(text("SELECT now() AT TIME ZONE 'UTC'")).scalar_one().replace(tzinfo=UTC)
            connection.execute(text("SELECT 1")).scalar_one()
            active_jobs = connection.execute(
                text("SELECT count(*) FROM collection_jobs WHERE status='running'")
            ).scalar_one()
        database_ok = True
    except Exception as error:
        database_ok, db_now, active_jobs = False, utc_now, -1
        checks["database_connectivity"] = {"passed": False, "detail": str(error)}
    else:
        checks["database_connectivity"] = {"passed": True}

    tables = set(inspect(engine).get_table_names()) if database_ok else set()
    missing_tables = sorted(REQUIRED_TABLES - tables)
    checks["required_tables"] = {"passed": not missing_tables, "missing": missing_tables}
    schema = read_schema_provenance(engine) if database_ok and "schema_migrations" in tables else None
    expected_checksums = _expected_schema_checksums()
    schema_ok = schema is not None and dict(schema.checksums) == expected_checksums
    checks["schema_provenance"] = {
        "passed": schema_ok,
        "recorded": dict(schema.checksums) if schema else {},
        "expected": expected_checksums,
    }
    clock_delta = abs((db_now - utc_now).total_seconds())
    checks["utc_clock"] = {"passed": clock_delta <= 5, "delta_seconds": clock_delta}
    checks["disk_space"] = {"passed": disk_free >= 50 * 1024**3, "free_bytes": disk_free, "detail": disk_error}
    checks["bybit_reachability"] = {"passed": bybit_reachable, "detail": bybit_error}
    checks["code_revision"] = {"passed": code_error is None, "detail": code_error}
    checks["active_collection_jobs"] = {"passed": active_jobs == 0, "count": active_jobs}
    checks["leakage_invariants"] = {"passed": bool(leakage.get("passed")), "evidence": leakage}
    checks["campaign_bounds"] = {"passed": settings.history_months >= 12, "target_months": settings.history_months}

    campaign_end = utc_now
    campaign_start = (pd.Timestamp(utc_now) - pd.DateOffset(months=settings.history_months)).to_pydatetime()
    database_identity = repository.database_identity() if database_ok else {}
    failures = tuple(name for name, result in checks.items() if not result["passed"])
    payload = {
        "passed": not failures,
        "failures": list(failures),
        "checks": checks,
        "system": {"disk_free_gb": disk_free / 1024**3, "utc_time": utc_now.isoformat()},
        "database": {
            "identity": database_identity,
            "schema_version": schema.schema_version if schema else None,
            "schema_checksums": dict(schema.checksums) if schema else {},
            "migration_checksums_verified": schema_ok,
        },
        "configuration_hash": settings.configuration_hash(),
        "code_revision_state": code,
        "campaign_bounds": {
            "start": campaign_start.isoformat(), "end": campaign_end.isoformat(),
            "target_months": settings.history_months,
        },
    }
    preflight_id = None
    if "preflight_results" in tables:
        with engine.begin() as connection:
            preflight_id = connection.execute(
                text(
                    """
                    INSERT INTO preflight_results(
                        passed,configuration_hash,schema_checksums,code_revision_state,
                        database_identity,campaign_start,campaign_end,payload
                    ) VALUES (
                        :passed,:configuration_hash,CAST(:schema AS jsonb),CAST(:code AS jsonb),
                        CAST(:database AS jsonb),:start,:end,CAST(:payload AS jsonb)
                    ) RETURNING id
                    """
                ),
                {
                    "passed": not failures, "configuration_hash": settings.configuration_hash(),
                    "schema": json.dumps(dict(schema.checksums) if schema else {}, sort_keys=True),
                    "code": json.dumps(code, sort_keys=True),
                    "database": json.dumps(database_identity, sort_keys=True),
                    "start": campaign_start, "end": campaign_end,
                    "payload": json.dumps(payload, sort_keys=True),
                },
            ).scalar_one()
    payload["preflight_id"] = preflight_id
    directory = settings.reports_dir
    directory.mkdir(parents=True, exist_ok=True)
    report_path, json_path = directory / "00_preflight.md", directory / "00_preflight.json"
    lines = ["# Research Campaign Preflight", "", f"Overall: {'PASS' if not failures else 'FAIL'}", ""]
    lines.extend(f"- {name}: {'PASS' if result['passed'] else 'FAIL'}" for name, result in checks.items())
    lines.extend(["", "Historical coverage is audited during the campaign after collection; it is not claimed by preflight.", ""])
    _atomic_write(report_path, "\n".join(lines))
    _atomic_write(json_path, json.dumps(payload, sort_keys=True, indent=2) + "\n")
    return PreflightResult(preflight_id, not failures, failures, payload, report_path, json_path)


def _current_gate_state(engine, settings: Settings, project_root: Path) -> dict:
    schema = read_schema_provenance(engine)
    return {
        "configuration_hash": settings.configuration_hash(),
        "schema_checksums": dict(schema.checksums),
        "code_revision_state": _code_revision_state(project_root),
        "database_identity": ResearchRepository(engine).database_identity(),
    }


def verify_database(engine, settings: Settings) -> dict:
    """Read-only verification of database identity, migrations, and safety triggers."""
    repository = ResearchRepository(engine)
    tables = set(inspect(engine).get_table_names())
    missing_tables = sorted(REQUIRED_TABLES - tables)
    schema = read_schema_provenance(engine) if "schema_migrations" in tables else None
    recorded = dict(schema.checksums) if schema else {}
    expected = _expected_schema_checksums()
    required_triggers = {
        "research_run_snapshots_immutable", "preflight_results_immutable",
        "breakout_candidates_immutable", "breakout_candidates_valid_insert",
        "candidate_features_admitted_only", "candidate_outcomes_admitted_only",
        "candidate_features_immutable", "candidate_outcomes_immutable",
    }
    with engine.connect() as connection:
        triggers = set(
            connection.execute(
                text(
                    "SELECT tgname FROM pg_trigger "
                    "WHERE NOT tgisinternal AND tgname = ANY(:names)"
                ),
                {"names": list(required_triggers)},
            ).scalars()
        )
    missing_triggers = sorted(required_triggers - triggers)
    passed = not missing_tables and recorded == expected and not missing_triggers
    return {
        "passed": passed,
        "database_identity": repository.database_identity(),
        "configuration_hash": settings.configuration_hash(),
        "schema_version": schema.schema_version if schema else None,
        "schema_checksums": recorded,
        "expected_schema_checksums": expected,
        "migration_checksums_verified": recorded == expected,
        "required_tables_verified": not missing_tables,
        "missing_tables": missing_tables,
        "required_constraints_verified": not missing_triggers,
        "missing_safety_triggers": missing_triggers,
    }


def _require_matching_preflight(engine, settings: Settings, project_root: Path, preflight_id: int | None):
    if preflight_id is None:
        raise PreflightRequired("a passing preflight ID is required")
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT * FROM preflight_results WHERE id=:id"), {"id": preflight_id}
        ).mappings().one_or_none()
    if row is None or not row["passed"]:
        raise PreflightRequired("preflight is missing or did not pass")
    current = _current_gate_state(engine, settings, project_root)
    for field in ("configuration_hash", "schema_checksums", "code_revision_state", "database_identity"):
        if current[field] != row[field]:
            raise PreflightRequired(f"preflight is stale: {field} changed")
    return row, current


def check_campaign_gate(engine, settings: Settings, project_root: Path, preflight_id: int | None) -> dict:
    """Check a preflight without creating a run or starting collection."""
    row, _current = _require_matching_preflight(engine, settings, project_root, preflight_id)
    with engine.connect() as connection:
        counts = {
            "research_runs": connection.execute(text("SELECT count(*) FROM research_runs")).scalar_one(),
            "collection_jobs": connection.execute(text("SELECT count(*) FROM collection_jobs")).scalar_one(),
            "breakout_candidates": connection.execute(text("SELECT count(*) FROM breakout_candidates")).scalar_one(),
        }
    return {
        "status": "ready", "preflight_id": preflight_id,
        "campaign_start": row["campaign_start"].isoformat(),
        "campaign_end": row["campaign_end"].isoformat(),
        "counts_unchanged": counts,
    }


def launch_campaign(engine, settings: Settings, project_root: Path, preflight_id: int | None, campaign_runner: Callable | None = None) -> dict:
    row, current = _require_matching_preflight(engine, settings, project_root, preflight_id)

    bounds = {"start": row["campaign_start"], "end": row["campaign_end"]}
    repository = ResearchRepository(engine)
    orchestrator = ResearchOrchestrator(engine, settings)
    run_id = orchestrator.create_run(**bounds)
    provenance = {
        "git_commit_hash": current["code_revision_state"]["git_commit_hash"],
        "code_revision_state": current["code_revision_state"],
        "configuration_hash": current["configuration_hash"],
        "schema_version": read_schema_provenance(engine).schema_version,
        "schema_checksums": current["schema_checksums"],
        "database_identity": current["database_identity"],
        "campaign_start": bounds["start"], "campaign_end": bounds["end"],
        "execution_timestamp": datetime.now(UTC),
    }
    def universe_size() -> int:
        with engine.connect() as connection:
            return connection.execute(
                text("SELECT count(*) FROM instruments WHERE status='Trading' AND quote_asset='USDT'")
            ).scalar_one()

    universe_count = universe_size()
    repository.append_run_snapshot(run_id, "started", provenance, {"symbol_universe_count": universe_count, "candidates": {}})
    try:
        result = campaign_runner(orchestrator, run_id, bounds) if campaign_runner else _run_full_campaign(orchestrator, run_id, bounds, provenance)
    except Exception:
        counts = repository.population_funnel(run_id)
        repository.append_run_snapshot(
            run_id, "failed", {**provenance, "execution_timestamp": datetime.now(UTC)},
            {"symbol_universe_count": universe_size(), "candidates": counts},
        )
        repository.finish_run(run_id, "failed", {"candidate_counts": counts})
        raise
    counts = repository.population_funnel(run_id)
    repository.append_run_snapshot(
        run_id, "completed", {**provenance, "execution_timestamp": datetime.now(UTC)},
        {"symbol_universe_count": universe_size(), "candidates": counts},
    )
    repository.finish_run(run_id, "completed", {"candidate_counts": counts})
    return {"run_id": run_id, "result": result, "candidate_counts": counts}


def _run_full_campaign(orchestrator: ResearchOrchestrator, run_id: int, bounds: dict, provenance: dict) -> dict:
    orchestrator.set_bounds(bounds["start"], bounds["end"])
    collection = orchestrator.collect()
    coverage = orchestrator.audit_collection(collection["symbols"])
    if collection["errors"] or not coverage["passed"]:
        raise RuntimeError(f"collection did not pass completeness gate: {coverage}")
    stages = {"collection": collection, "coverage": coverage}
    for name, operation in (
        ("candidates", lambda: orchestrator.detect_candidates(run_id, collection["symbols"])),
        ("features", lambda: orchestrator.snapshot_features(run_id)),
        ("outcomes", lambda: orchestrator.label_outcomes(run_id)),
    ):
        stages[name] = operation()
        if stages[name].get("errors"):
            raise RuntimeError(f"{name} stage failed closed: {stages[name]}")
    analysis = orchestrator.analyse(run_id)
    frame, funnel = analysis["population"], analysis["funnel"]
    evidence = compare_matched_candidates(frame)
    population = candidate_population(frame, funnel)
    validation = walk_forward(
        frame, orchestrator.settings, bounds["start"], bounds["end"]
    )
    regimes = {str(name): compare_matched_candidates(group) for name, group in frame.groupby("regime", dropna=False)}
    timeframes = {
        str(name): compare_matched_candidates(group)
        for name, group in frame.groupby("interval_minutes", dropna=False)
    }
    horizons = ("T-72h", "T-48h", "T-24h", "T-12h", "T-6h", "T-3h", "T-1h")
    timeline_coverage = {
        horizon: {
            "available": int(
                frame.timeline.map(
                    lambda value: bool((value or {}).get(horizon, {}).get("features"))
                ).sum()
            ), "total": len(frame)
        }
        for horizon in horizons
    }
    leakage = orchestrator.repository.audit_run(
        run_id, orchestrator.settings.minimum_turnover_24h
    )
    leakage["collection_coverage"] = coverage
    leakage["status"] = "PASS" if leakage["passed"] and coverage["passed"] else "FAIL"
    if leakage["status"] != "PASS":
        raise RuntimeError(f"run leakage audit failed: {leakage}")
    winners = frame[frame.classification == "winner"]
    expansion_summary = {
        "average_mfe": float(winners.mfe.mean()) if len(winners) else None,
        "major_expansions": int((winners.mfe >= 0.50).sum()) if len(winners) else 0,
        "large_expansions": int(((winners.mfe >= 0.25) & (winners.mfe < 0.50)).sum()) if len(winners) else 0,
    }
    payload = {
        "manifest": orchestrator.settings.as_manifest(), "provenance": {"run_id": run_id, **provenance},
        "symbol_universe": collection["symbols"],
        "population": population, "evidence": evidence, "timeline_coverage": timeline_coverage,
        "regimes": regimes, "timeframes": timeframes,
        "validation": validation, "leakage": leakage,
        "campaign_bounds": {"start": bounds["start"], "end": bounds["end"]},
        "expansion_summary": expansion_summary,
        "validated_market_state": ValidatedMarketState.from_validation(validation).as_dict(),
    }
    write_reports(
        orchestrator.settings.reports_dir, payload,
        lambda name, value: orchestrator.repository.store_artifact(run_id, name, value),
    )
    return {"stages": stages, "reports": 6}
