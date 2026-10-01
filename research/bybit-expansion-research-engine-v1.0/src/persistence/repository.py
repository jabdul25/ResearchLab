from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Iterable

import numpy as np
import pandas as pd
from sqlalchemy import text

from src.domain import (
    BreakoutSignal,
    CandidateOutcome,
    CandidateStatus,
    FeatureSnapshot,
    LiquiditySnapshot,
)


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _json(value) -> str:
    return json.dumps(
        value,
        default=_json_default,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class ResearchRepository:
    def __init__(self, engine):
        self.engine = engine

    def create_run(self, manifest: dict, bounds: dict) -> int:
        encoded = _json(manifest)
        configuration_hash = hashlib.sha256(encoded.encode()).hexdigest()
        with self.engine.begin() as connection:
            return connection.execute(
                text(
                    """
                    INSERT INTO research_runs(
                        status,configuration_manifest,configuration_hash,
                        campaign_start,campaign_end
                    ) VALUES (
                        'running',CAST(:manifest AS jsonb),:configuration_hash,
                        :campaign_start,:campaign_end
                    ) RETURNING id
                    """
                ),
                {
                    "manifest": encoded,
                    "configuration_hash": configuration_hash,
                    "campaign_start": bounds["start"],
                    "campaign_end": bounds["end"],
                },
            ).scalar_one()

    def finish_run(self, run_id: int, status: str, summary: dict) -> None:
        if status not in {"completed", "failed"}:
            raise ValueError("terminal run status must be completed or failed")
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE research_runs SET status=:status,completed_at=now(),"
                    "summary=CAST(:summary AS jsonb) WHERE id=:run_id"
                ),
                {"run_id": run_id, "status": status, "summary": _json(summary)},
            )

    def store_artifact(self, run_id: int, name: str, payload: dict) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO research_artifacts(run_id,artifact_name,payload) "
                    "VALUES (:run_id,:name,CAST(:payload AS jsonb))"
                ),
                {"run_id": run_id, "name": name, "payload": _json(payload)},
            )

    def append_run_snapshot(
        self,
        run_id: int,
        phase: str,
        provenance: dict,
        counts: dict,
    ) -> int:
        with self.engine.begin() as connection:
            return connection.execute(
                text(
                    """
                    INSERT INTO research_run_snapshots(
                        run_id,snapshot_phase,git_commit_hash,code_revision_state,
                        configuration_hash,schema_version,schema_checksums,
                        database_identity,campaign_start,campaign_end,
                        symbol_universe_count,candidate_counts,execution_timestamp
                    ) VALUES (
                        :run_id,:phase,:git_commit_hash,CAST(:code_revision_state AS jsonb),
                        :configuration_hash,:schema_version,CAST(:schema_checksums AS jsonb),
                        CAST(:database_identity AS jsonb),:campaign_start,:campaign_end,
                        :symbol_universe_count,CAST(:candidate_counts AS jsonb),
                        :execution_timestamp
                    ) RETURNING id
                    """
                ),
                {
                    "run_id": run_id,
                    "phase": phase,
                    "git_commit_hash": provenance["git_commit_hash"],
                    "code_revision_state": _json(provenance["code_revision_state"]),
                    "configuration_hash": provenance["configuration_hash"],
                    "schema_version": provenance["schema_version"],
                    "schema_checksums": _json(provenance["schema_checksums"]),
                    "database_identity": _json(provenance["database_identity"]),
                    "campaign_start": provenance["campaign_start"],
                    "campaign_end": provenance["campaign_end"],
                    "symbol_universe_count": counts.get("symbol_universe_count", 0),
                    "candidate_counts": _json(counts.get("candidates", {})),
                    "execution_timestamp": provenance["execution_timestamp"],
                },
            ).scalar_one()

    def run_snapshots(self, run_id: int) -> list[dict]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT snapshot_phase,git_commit_hash,code_revision_state,
                           configuration_hash,schema_version,schema_checksums,
                           database_identity,campaign_start,campaign_end,
                           symbol_universe_count,candidate_counts,execution_timestamp
                    FROM research_run_snapshots WHERE run_id=:run_id ORDER BY id
                    """
                ),
                {"run_id": run_id},
            ).mappings()
            return [dict(row) for row in rows]

    def upsert_candidates(
        self,
        run_id: int,
        candidates: list[tuple[BreakoutSignal, LiquiditySnapshot | None]],
    ) -> int:
        inserted = 0
        ids = self._candidate_ids(run_id)
        ordered = sorted(
            candidates,
            key=lambda item: item[0].status is CandidateStatus.REJECTED_DUPLICATE,
        )
        sql = text(
            """
            INSERT INTO breakout_candidates(
                run_id,candidate_key,symbol,interval_minutes,breakout_open_time,
                decision_time,structure_lookback,breakout_level,breakout_close,
                candidate_status,duplicate_of_candidate_id,rejection_reason,
                duplicate_window_end,turnover_24h_before_event,
                volume_24h_before_event,liquidity_expected_bars,
                liquidity_observed_bars,liquidity_eligible,liquidity_reason
            ) VALUES (
                :run_id,:candidate_key,:symbol,:interval,:breakout_open_time,
                :decision_time,:structure_lookback,:breakout_level,:breakout_close,
                :candidate_status,:duplicate_of_candidate_id,:rejection_reason,
                :duplicate_window_end,:turnover,:volume,:expected_bars,
                :observed_bars,:eligible,:liquidity_reason
            ) ON CONFLICT(run_id,symbol,interval_minutes,breakout_open_time,structure_lookback)
              DO NOTHING
            RETURNING id
            """
        )
        with self.engine.begin() as connection:
            for signal, liquidity in ordered:
                duplicate_id = (
                    ids.get(signal.duplicate_of_key)
                    if signal.status is CandidateStatus.REJECTED_DUPLICATE
                    else None
                )
                if signal.status is CandidateStatus.REJECTED_DUPLICATE and duplicate_id is None:
                    raise ValueError(
                        f"duplicate parent not found for candidate {signal.key}"
                    )
                row_id = connection.execute(
                    sql,
                    {
                        "run_id": run_id,
                        "candidate_key": signal.key,
                        "symbol": signal.symbol,
                        "interval": signal.interval_minutes,
                        "breakout_open_time": signal.breakout_open_time,
                        "decision_time": signal.decision_time,
                        "structure_lookback": signal.structure_lookback,
                        "breakout_level": signal.breakout_level,
                        "breakout_close": signal.breakout_close,
                        "candidate_status": signal.status.value,
                        "duplicate_of_candidate_id": duplicate_id,
                        "rejection_reason": signal.rejection_reason,
                        "duplicate_window_end": signal.duplicate_window_end,
                        "turnover": liquidity.turnover_24h if liquidity else None,
                        "volume": liquidity.volume_24h if liquidity else None,
                        "expected_bars": liquidity.expected_bars if liquidity else None,
                        "observed_bars": liquidity.observed_bars if liquidity else None,
                        "eligible": liquidity.eligible if liquidity else None,
                        "liquidity_reason": liquidity.reason if liquidity else None,
                    },
                ).scalar_one_or_none()
                if row_id is not None:
                    inserted += 1
                    ids[signal.key] = row_id
        return inserted

    def _candidate_ids(self, run_id: int) -> dict[str, int]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT candidate_key,id FROM breakout_candidates WHERE run_id=:run_id"
                ),
                {"run_id": run_id},
            ).all()
        return {row.candidate_key: row.id for row in rows}

    def candidate_id(self, run_id: int, candidate_key: str) -> int:
        with self.engine.connect() as connection:
            return connection.execute(
                text(
                    """
                    SELECT id FROM breakout_candidates
                    WHERE run_id=:run_id AND candidate_key=:candidate_key
                    """
                ),
                {"run_id": run_id, "candidate_key": candidate_key},
            ).scalar_one()

    def upsert_feature_snapshot(
        self, candidate_id: int, snapshot: FeatureSnapshot
    ) -> bool:
        source = snapshot.source_max_timestamps
        timeline = {name: asdict(value) for name, value in snapshot.timeline.items()}
        with self.engine.begin() as connection:
            row_id = connection.execute(
                text(
                    """
                    INSERT INTO candidate_features(
                        candidate_id,feature_time,feature_version,regime,features,
                        timeline,candle_input_max_time,oi_input_max_time,
                        funding_input_max_time,input_fingerprint
                    ) VALUES (
                        :candidate_id,:feature_time,'v1',:regime,CAST(:features AS jsonb),
                        CAST(:timeline AS jsonb),:candle_max,:oi_max,:funding_max,
                        :input_fingerprint
                    ) ON CONFLICT(candidate_id) DO NOTHING RETURNING candidate_id
                    """
                ),
                {
                    "candidate_id": candidate_id,
                    "feature_time": snapshot.decision_time,
                    "regime": snapshot.regime.value,
                    "features": _json(dict(snapshot.features)),
                    "timeline": _json(timeline),
                    "candle_max": max(
                        filter(
                            None,
                            [
                                source.get("trigger_candle"),
                                source.get("four_h_candle"),
                            ],
                        ),
                        default=None,
                    ),
                    "oi_max": source.get("open_interest"),
                    "funding_max": source.get("funding"),
                    "input_fingerprint": snapshot.input_fingerprint,
                },
            ).scalar_one_or_none()
        return row_id is not None

    def upsert_outcome(self, candidate_id: int, outcome: CandidateOutcome) -> bool:
        with self.engine.begin() as connection:
            row_id = connection.execute(
                text(
                    """
                    INSERT INTO candidate_outcomes(
                        candidate_id,window_start,window_end,observed_candles,
                        classification,threshold,threshold_reached,mfe,mae,
                        maximum_extension,final_vs_breakout_level,giveback_fraction,
                        label_version
                    ) VALUES (
                        :candidate_id,:window_start,:window_end,:observed_candles,
                        :classification,:threshold,:threshold_reached,:mfe,:mae,
                        :maximum_extension,:final_position,:giveback,'v1'
                    ) ON CONFLICT(candidate_id) DO NOTHING RETURNING candidate_id
                    """
                ),
                {
                    "candidate_id": candidate_id,
                    "window_start": outcome.window_start,
                    "window_end": outcome.window_end,
                    "observed_candles": outcome.observed_candles,
                    "classification": outcome.classification.value,
                    "threshold": outcome.threshold,
                    "threshold_reached": outcome.threshold_reached,
                    "mfe": outcome.mfe,
                    "mae": outcome.mae,
                    "maximum_extension": outcome.maximum_extension,
                    "final_position": outcome.final_vs_breakout_level,
                    "giveback": outcome.giveback_fraction,
                },
            ).scalar_one_or_none()
        return row_id is not None

    def read_outcome(self, run_id: int, candidate_key: str) -> dict | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT o.* FROM candidate_outcomes o
                    JOIN breakout_candidates c ON c.id=o.candidate_id
                    WHERE c.run_id=:run_id AND c.candidate_key=:candidate_key
                    """
                ),
                {"run_id": run_id, "candidate_key": candidate_key},
            ).mappings().one_or_none()
        return dict(row) if row else None

    def candidates_missing_features(self, run_id: int) -> list[dict]:
        return self._missing_rows(run_id, "candidate_features")

    def candidates_missing_outcomes(self, run_id: int) -> list[dict]:
        return self._missing_rows(run_id, "candidate_outcomes")

    def _missing_rows(self, run_id: int, child_table: str) -> list[dict]:
        if child_table not in {"candidate_features", "candidate_outcomes"}:
            raise ValueError("unsupported child table")
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT c.* FROM breakout_candidates c
                    LEFT JOIN {child_table} child ON child.candidate_id=c.id
                    WHERE c.run_id=:run_id AND c.candidate_status='admitted'
                      AND child.candidate_id IS NULL
                    ORDER BY c.decision_time
                    """
                ),
                {"run_id": run_id},
            ).mappings()
            return [dict(row) for row in rows]

    def load_analysis_population(self, run_id: int) -> pd.DataFrame:
        frame = pd.read_sql(
            text(
                """
                SELECT c.id,c.candidate_key,c.symbol,c.interval_minutes,
                       c.breakout_open_time,c.decision_time,c.breakout_level,
                       c.breakout_close,c.turnover_24h_before_event,
                       c.volume_24h_before_event,c.liquidity_eligible,
                       c.liquidity_reason,f.regime,f.features,f.timeline,
                       f.input_fingerprint,o.classification,o.threshold,
                       o.threshold_reached,o.mfe,o.mae,o.maximum_extension,
                       o.final_vs_breakout_level,o.giveback_fraction
                FROM breakout_candidates c
                JOIN candidate_features f ON f.candidate_id=c.id
                JOIN candidate_outcomes o ON o.candidate_id=c.id
                WHERE c.run_id=:run_id AND c.candidate_status='admitted'
                  AND c.liquidity_eligible IS TRUE AND o.observed_candles=12
                ORDER BY c.decision_time,c.id
                """
            ),
            self.engine,
            params={"run_id": run_id},
        )
        if frame.empty:
            return frame
        feature_values = pd.json_normalize(frame.pop("features")).astype(object)
        feature_values = feature_values.where(pd.notna(feature_values), None)
        return pd.concat([frame.reset_index(drop=True), feature_values], axis=1)

    def population_funnel(self, run_id: int) -> dict:
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT
                      count(*) AS raw_signals,
                      count(*) FILTER (WHERE candidate_status='admitted') AS admitted,
                      count(*) FILTER (WHERE candidate_status='rejected_duplicate') AS rejected_duplicates,
                      count(*) FILTER (WHERE candidate_status='admitted' AND liquidity_eligible IS TRUE) AS eligible,
                      count(*) FILTER (WHERE candidate_status='admitted' AND liquidity_eligible IS FALSE) AS excluded
                    FROM breakout_candidates WHERE run_id=:run_id
                    """
                ),
                {"run_id": run_id},
            ).mappings().one()
            pending = connection.execute(
                text(
                    """
                    SELECT count(*) FROM breakout_candidates c
                    LEFT JOIN candidate_outcomes o ON o.candidate_id=c.id
                    WHERE c.run_id=:run_id AND c.candidate_status='admitted'
                      AND c.liquidity_eligible IS TRUE AND o.candidate_id IS NULL
                    """
                ),
                {"run_id": run_id},
            ).scalar_one()
            outcomes = connection.execute(
                text(
                    """
                    SELECT classification,count(*) AS count
                    FROM candidate_outcomes o JOIN breakout_candidates c ON c.id=o.candidate_id
                    WHERE c.run_id=:run_id GROUP BY classification
                    """
                ),
                {"run_id": run_id},
            ).all()
        result = {key: int(value) for key, value in row.items()}
        result["pending"] = int(pending)
        result["outcomes"] = {item.classification: int(item.count) for item in outcomes}
        return result

    def database_identity(self) -> dict:
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT current_database() AS database,
                           inet_server_addr()::text AS server_address,
                           inet_server_port() AS server_port,
                           current_setting('server_version') AS server_version
                    """
                )
            ).mappings().one()
        return dict(row)
