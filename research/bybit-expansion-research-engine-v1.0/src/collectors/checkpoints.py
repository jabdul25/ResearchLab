from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping

from sqlalchemy import text


@dataclass(frozen=True)
class CollectionCheckpoint:
    id: int
    source: str
    symbol: str
    interval_minutes: int
    requested_start: datetime
    requested_end: datetime
    next_end: datetime | None
    next_cursor: str | None
    status: str


class CollectionCheckpointStore:
    def __init__(self, engine):
        self.engine = engine

    def get_or_create(
        self,
        source: str,
        symbol: str,
        interval_minutes: int,
        requested_start: datetime,
        requested_end: datetime,
    ) -> CollectionCheckpoint:
        params = {
            "source": source,
            "symbol": symbol,
            "interval": interval_minutes,
            "start": requested_start,
            "end": requested_end,
        }
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO collection_jobs(
                        source,symbol,interval_minutes,requested_start,requested_end,next_end
                    ) VALUES (:source,:symbol,:interval,:start,:end,:end)
                    ON CONFLICT(source,symbol,interval_minutes,requested_start,requested_end)
                    DO NOTHING
                    """
                ),
                params,
            )
            row = connection.execute(
                text(
                    """
                    SELECT id,source,symbol,interval_minutes,requested_start,requested_end,
                           next_end,next_cursor,status
                    FROM collection_jobs
                    WHERE source=:source AND symbol=:symbol AND interval_minutes=:interval
                      AND requested_start=:start AND requested_end=:end
                    """
                ),
                params,
            ).one()
        return CollectionCheckpoint(**row._mapping)

    def _commit_page(
        self,
        job_id: int,
        insert_sql,
        records: list[Mapping[str, object]],
        observed_times: list[datetime],
        next_end: datetime | None,
        next_cursor: str | None,
        complete: bool,
    ) -> int:
        with self.engine.begin() as connection:
            inserted = connection.execute(insert_sql, records).rowcount if records else 0
            observed_start = min(observed_times) if observed_times else None
            observed_end = max(observed_times) if observed_times else None
            connection.execute(
                text(
                    """
                    UPDATE collection_jobs
                    SET next_end=:next_end,
                        next_cursor=:next_cursor,
                        observed_start=CASE
                            WHEN :observed_start IS NULL THEN observed_start
                            WHEN observed_start IS NULL THEN :observed_start
                            ELSE LEAST(observed_start,:observed_start)
                        END,
                        observed_end=CASE
                            WHEN :observed_end IS NULL THEN observed_end
                            WHEN observed_end IS NULL THEN :observed_end
                            ELSE GREATEST(observed_end,:observed_end)
                        END,
                        stored_count=stored_count+:inserted,
                        status=:status,
                        last_error=NULL,
                        updated_at=now()
                    WHERE id=:job_id
                    """
                ),
                {
                    "job_id": job_id,
                    "next_end": next_end,
                    "next_cursor": next_cursor,
                    "observed_start": observed_start,
                    "observed_end": observed_end,
                    "inserted": inserted,
                    "status": "completed" if complete else "running",
                },
            )
        return inserted

    def commit_candle_page(
        self,
        job_id: int,
        records: list[Mapping[str, object]],
        next_end: datetime | None,
        complete: bool,
    ) -> int:
        sql = text(
            """
            INSERT INTO candles(
                symbol,interval_minutes,open_time,open,high,low,close,volume,turnover
            ) VALUES (
                :symbol,:interval,:open_time,:open,:high,:low,:close,:volume,:turnover
            ) ON CONFLICT(symbol,interval_minutes,open_time) DO NOTHING
            """
        )
        return self._commit_page(
            job_id,
            sql,
            records,
            [record["open_time"] for record in records],
            next_end,
            None,
            complete,
        )

    def commit_oi_page(
        self,
        job_id: int,
        records: list[Mapping[str, object]],
        next_cursor: str | None,
        complete: bool,
    ) -> int:
        sql = text(
            """
            INSERT INTO open_interest(symbol,interval_minutes,observed_at,open_interest)
            VALUES (:symbol,:interval,:observed_at,:open_interest)
            ON CONFLICT(symbol,interval_minutes,observed_at) DO NOTHING
            """
        )
        return self._commit_page(
            job_id,
            sql,
            records,
            [record["observed_at"] for record in records],
            None,
            next_cursor,
            complete,
        )

    def commit_funding_page(
        self,
        job_id: int,
        records: list[Mapping[str, object]],
        next_end: datetime | None,
        complete: bool,
    ) -> int:
        sql = text(
            """
            INSERT INTO funding_rates(symbol,funding_time,funding_rate)
            VALUES (:symbol,:funding_time,:funding_rate)
            ON CONFLICT(symbol,funding_time) DO NOTHING
            """
        )
        return self._commit_page(
            job_id,
            sql,
            records,
            [record["funding_time"] for record in records],
            next_end,
            None,
            complete,
        )

    def mark_error(self, job_id: int, error: Exception) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE collection_jobs
                    SET status='failed', retry_count=retry_count+1,
                        last_error=:error, updated_at=now()
                    WHERE id=:job_id
                    """
                ),
                {"job_id": job_id, "error": str(error)},
            )
