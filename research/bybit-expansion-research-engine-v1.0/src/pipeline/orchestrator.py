from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pandas as pd
from sqlalchemy import text

from src.analysis.candidates import historical_liquidity
from src.analysis.events import discover_breakout_candidates
from src.analysis.features import extract_candidate_features
from src.analysis.outcomes import label_outcome
from src.collectors.bybit import BybitPublicClient, DataCollector, INTERVALS
from src.domain import BreakoutSignal, CandidateStatus, LiquiditySnapshot
from src.persistence.repository import ResearchRepository


LOG = logging.getLogger(__name__)


class ResearchOrchestrator:
    def __init__(self, engine, settings, collector=None):
        self.engine, self.settings = engine, settings
        self.repository = ResearchRepository(engine)
        self.collector = collector or DataCollector(
            engine,
            BybitPublicClient(
                settings.request_pause_seconds, settings.request_retries
            ),
            settings.minimum_turnover_24h,
            settings.history_months,
        )

    def create_run(self, start: datetime, end: datetime) -> int:
        return self.repository.create_run(
            self.settings.as_manifest(), {"start": start, "end": end}
        )

    def _candles(self, symbol: str, interval: int) -> pd.DataFrame:
        return pd.read_sql(
            text(
                """
                SELECT open_time,open,high,low,close,volume,turnover
                FROM candles
                WHERE symbol=:symbol AND interval_minutes=:interval
                ORDER BY open_time
                """
            ),
            self.engine,
            params={"symbol": symbol, "interval": interval},
        )

    def _open_interest(self, symbol: str, interval: int) -> pd.DataFrame:
        return pd.read_sql(
            text(
                """
                SELECT observed_at,open_interest FROM open_interest
                WHERE symbol=:symbol AND interval_minutes=:interval
                ORDER BY observed_at
                """
            ),
            self.engine,
            params={"symbol": symbol, "interval": interval},
        )

    def _funding(self, symbol: str) -> pd.DataFrame:
        return pd.read_sql(
            text(
                """
                SELECT funding_time,funding_rate FROM funding_rates
                WHERE symbol=:symbol ORDER BY funding_time
                """
            ),
            self.engine,
            params={"symbol": symbol},
        )

    def collect(self) -> dict:
        symbols = self.collector.refresh_universe()
        totals = {
            "symbol_universe_count": len(symbols),
            "symbols_with_data": 0,
            "candles": 0,
            "oi": 0,
            "funding": 0,
            "errors": 0,
        }
        for number, symbol in enumerate(symbols, 1):
            try:
                saved = self.collector.collect_symbol(symbol)
                totals["symbols_with_data"] += 1
                for key in ("candles", "oi", "funding"):
                    totals[key] += saved[key]
                LOG.info("[%d/%d] %s: %s", number, len(symbols), symbol, saved)
            except Exception:
                totals["errors"] += 1
                LOG.exception("collection failed for %s; continuing", symbol)
        return totals

    def _symbols(self) -> list[str]:
        with self.engine.connect() as connection:
            return [
                row.symbol
                for row in connection.execute(
                    text(
                        """
                        SELECT symbol FROM instruments
                        WHERE status='Trading' AND quote_asset='USDT'
                        ORDER BY symbol
                        """
                    )
                )
            ]

    def detect_candidates(
        self,
        run_id: int,
        symbols: list[str] | None = None,
        intervals: tuple[int, ...] = INTERVALS,
    ) -> dict:
        symbols = symbols or self._symbols()
        stats = {
            "processed": 0,
            "inserted": 0,
            "admitted": 0,
            "rejected_duplicates": 0,
            "eligible": 0,
            "excluded": 0,
            "errors": 0,
        }
        for symbol in symbols:
            liquidity_candles = self._candles(symbol, 15)
            for interval in intervals:
                try:
                    frame = self._candles(symbol, interval)
                    if len(frame) <= self.settings.structure_lookback:
                        continue
                    signals = discover_breakout_candidates(
                        frame, symbol, interval, self.settings
                    )
                    records = []
                    for signal in signals:
                        snapshot = None
                        if signal.status is CandidateStatus.ADMITTED:
                            snapshot = historical_liquidity(
                                liquidity_candles,
                                signal.decision_time,
                                self.settings.minimum_turnover_24h,
                            )
                            stats["admitted"] += 1
                            stats["eligible" if snapshot.eligible else "excluded"] += 1
                        else:
                            stats["rejected_duplicates"] += 1
                        records.append((signal, snapshot))
                    stats["processed"] += len(records)
                    stats["inserted"] += self.repository.upsert_candidates(
                        run_id, records
                    )
                except Exception:
                    stats["errors"] += 1
                    LOG.exception(
                        "candidate detection failed for %s/%s", symbol, interval
                    )
        return stats

    @staticmethod
    def _liquidity_from_row(row: dict) -> LiquiditySnapshot:
        return LiquiditySnapshot(
            decision_time=row["decision_time"],
            volume_24h=float(row["volume_24h_before_event"] or 0),
            turnover_24h=float(row["turnover_24h_before_event"] or 0),
            expected_bars=int(row["liquidity_expected_bars"] or 0),
            observed_bars=int(row["liquidity_observed_bars"] or 0),
            eligible=bool(row["liquidity_eligible"]),
            reason=row["liquidity_reason"],
        )

    @staticmethod
    def _signal_from_row(row: dict) -> BreakoutSignal:
        return BreakoutSignal(
            symbol=row["symbol"],
            interval_minutes=row["interval_minutes"],
            breakout_open_time=row["breakout_open_time"],
            decision_time=row["decision_time"],
            structure_lookback=row["structure_lookback"],
            breakout_level=float(row["breakout_level"]),
            breakout_close=float(row["breakout_close"]),
            status=CandidateStatus(row["candidate_status"]),
        )

    def snapshot_features(self, run_id: int) -> dict:
        rows = self.repository.candidates_missing_features(run_id)
        stats = {"processed": 0, "inserted": 0, "errors": 0}
        cache = {}
        for row in rows:
            try:
                trigger_key = (row["symbol"], row["interval_minutes"])
                context_key = (row["symbol"], 240)
                cache.setdefault(trigger_key, self._candles(*trigger_key))
                cache.setdefault(context_key, self._candles(*context_key))
                snapshot = extract_candidate_features(
                    cache[trigger_key],
                    cache[context_key],
                    row["decision_time"],
                    self._liquidity_from_row(row),
                    self._open_interest(row["symbol"], row["interval_minutes"]),
                    self._funding(row["symbol"]),
                    self.settings,
                )
                stats["processed"] += 1
                stats["inserted"] += int(
                    self.repository.upsert_feature_snapshot(row["id"], snapshot)
                )
            except Exception:
                stats["errors"] += 1
                LOG.exception("feature snapshot failed for candidate %s", row["id"])
        return stats

    def label_outcomes(self, run_id: int) -> dict:
        rows = self.repository.candidates_missing_outcomes(run_id)
        stats = {"processed": 0, "inserted": 0, "pending": 0, "errors": 0}
        cache = {}
        for row in rows:
            try:
                key = (row["symbol"], row["interval_minutes"])
                cache.setdefault(key, self._candles(*key))
                frame = cache[key]
                future = frame[frame.open_time >= pd.Timestamp(row["decision_time"])]
                result = label_outcome(
                    self._signal_from_row(row),
                    future,
                    self.settings.expansion_thresholds[row["interval_minutes"]],
                    self.settings,
                )
                stats["processed"] += 1
                if result is None:
                    stats["pending"] += 1
                    continue
                stats["inserted"] += int(
                    self.repository.upsert_outcome(row["id"], result)
                )
            except Exception:
                stats["errors"] += 1
                LOG.exception("outcome label failed for candidate %s", row["id"])
        return stats

    def analyse(self, run_id: int) -> dict:
        return {
            "population": self.repository.load_analysis_population(run_id),
            "funnel": self.repository.population_funnel(run_id),
        }

    def run(self, *args, **kwargs):
        raise RuntimeError(
            "campaign execution is gated; run the preflight command first"
        )
