"""Resilient public Bybit V5 client and resumable database collectors."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Iterable, Iterator, Sequence

import requests
from sqlalchemy import text

from src.collectors.checkpoints import CollectionCheckpointStore


LOG = logging.getLogger(__name__)
BASE_URL = "https://api.bybit.com/v5/market"
INTERVALS = (15, 60, 240)


@dataclass(frozen=True)
class MarketPage:
    rows: tuple
    next_end_ms: int | None = None
    next_cursor: str | None = None
    complete: bool = False
    stop_reason: str | None = None


def _milliseconds(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def last_completed_open_time(now: datetime, interval_minutes: int) -> datetime:
    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be positive")
    step = interval_minutes * 60
    timestamp = int(_as_utc(now).timestamp())
    return datetime.fromtimestamp((timestamp // step) * step - step, UTC)


def collection_start(history_start: datetime, launch_time: datetime | None) -> datetime:
    history_start = _as_utc(history_start)
    return max(history_start, _as_utc(launch_time)) if launch_time else history_start


def _unique_in_bounds(rows: Sequence, timestamp, start_ms: int, end_ms: int) -> tuple:
    by_time = {}
    for row in rows:
        observed = int(timestamp(row))
        if start_ms <= observed <= end_ms:
            by_time[observed] = row
    return tuple(by_time[key] for key in sorted(by_time))


class BybitPublicClient:
    def __init__(self, pause_seconds: float = 0.08, retries: int = 5):
        self.session = requests.Session()
        self.pause_seconds, self.retries = pause_seconds, retries

    def get(self, path: str, **params) -> dict:
        url = f"{BASE_URL}/{path}"
        for attempt in range(self.retries):
            try:
                response = self.session.get(url, params=params, timeout=(5, 30))
                if response.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"retryable HTTP {response.status_code}")
                response.raise_for_status()
                body = response.json()
                if body.get("retCode") != 0:
                    raise RuntimeError(f"Bybit {body.get('retCode')}: {body.get('retMsg')}")
                time.sleep(self.pause_seconds)
                return body["result"]
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                if attempt == self.retries - 1:
                    raise
                delay = min(30, 2**attempt) + self.pause_seconds
                LOG.warning(
                    "Bybit request %s failed (%s); retrying in %.1fs",
                    path,
                    exc,
                    delay,
                )
                time.sleep(delay)
        raise AssertionError("unreachable")

    def linear_instruments(self) -> list[dict]:
        cursor, rows, seen = None, [], set()
        while True:
            result = self.get(
                "instruments-info",
                category="linear",
                limit=1000,
                **({"cursor": cursor} if cursor else {}),
            )
            rows.extend(result.get("list", []))
            next_cursor = result.get("nextPageCursor") or None
            if not next_cursor or next_cursor in seen:
                return rows
            seen.add(next_cursor)
            cursor = next_cursor

    def tickers(self) -> dict[str, dict]:
        result = self.get("tickers", category="linear")
        return {row["symbol"]: row for row in result.get("list", [])}

    def kline_pages(
        self,
        symbol: str,
        interval: int,
        start: datetime,
        end: datetime,
    ) -> Iterator[MarketPage]:
        start_ms, page_end = _milliseconds(start), _milliseconds(end)
        while page_end >= start_ms:
            result = self.get(
                "kline",
                category="linear",
                symbol=symbol,
                interval=str(interval),
                start=start_ms,
                end=page_end,
                limit=1000,
            )
            raw = result.get("list", [])
            rows = _unique_in_bounds(raw, lambda row: row[0], start_ms, page_end)
            if not rows:
                reason = "empty_page" if not raw else "no_rows_in_bounds"
                yield MarketPage((), complete=False, stop_reason=reason)
                return
            earliest = int(rows[0][0])
            if earliest <= start_ms:
                yield MarketPage(rows, complete=True, stop_reason="start_reached")
                return
            next_end = earliest - 1
            if next_end >= page_end:
                yield MarketPage(rows, stop_reason="nonadvancing_page")
                return
            yield MarketPage(rows, next_end_ms=next_end)
            page_end = next_end

    def funding_pages(
        self, symbol: str, start: datetime, end: datetime
    ) -> Iterator[MarketPage]:
        start_ms, page_end = _milliseconds(start), _milliseconds(end)
        while page_end >= start_ms:
            result = self.get(
                "funding/history",
                category="linear",
                symbol=symbol,
                startTime=start_ms,
                endTime=page_end,
                limit=200,
            )
            raw = result.get("list", [])
            rows = _unique_in_bounds(
                raw, lambda row: row["fundingRateTimestamp"], start_ms, page_end
            )
            if not rows:
                reason = "empty_page" if not raw else "no_rows_in_bounds"
                yield MarketPage((), complete=False, stop_reason=reason)
                return
            earliest = int(rows[0]["fundingRateTimestamp"])
            if earliest <= start_ms:
                yield MarketPage(rows, complete=True, stop_reason="start_reached")
                return
            next_end = earliest - 1
            if next_end >= page_end:
                yield MarketPage(rows, stop_reason="nonadvancing_page")
                return
            yield MarketPage(rows, next_end_ms=next_end)
            page_end = next_end

    def open_interest_pages(
        self,
        symbol: str,
        interval: int,
        start: datetime,
        end: datetime,
        cursor: str | None = None,
    ) -> Iterator[MarketPage]:
        unit = {15: "15min", 60: "1h", 240: "4h"}[interval]
        start_ms, end_ms = _milliseconds(start), _milliseconds(end)
        seen = {cursor} if cursor else set()
        while True:
            params = {
                "category": "linear",
                "symbol": symbol,
                "intervalTime": unit,
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 200,
            }
            if cursor:
                params["cursor"] = cursor
            result = self.get("open-interest", **params)
            rows = _unique_in_bounds(
                result.get("list", []), lambda row: row["timestamp"], start_ms, end_ms
            )
            next_cursor = result.get("nextPageCursor") or None
            if not next_cursor:
                yield MarketPage(rows, complete=True, stop_reason="cursor_exhausted")
                return
            if next_cursor in seen:
                yield MarketPage(rows, stop_reason="repeated_cursor")
                return
            yield MarketPage(rows, next_cursor=next_cursor)
            seen.add(next_cursor)
            cursor = next_cursor

    def klines(
        self, symbol: str, interval: int, start: datetime, end: datetime
    ) -> Iterable[list[str]]:
        for page in self.kline_pages(symbol, interval, start, end):
            yield from page.rows

    def funding_history(
        self, symbol: str, start: datetime, end: datetime
    ) -> Iterable[dict]:
        for page in self.funding_pages(symbol, start, end):
            yield from page.rows

    def open_interest_history(
        self, symbol: str, interval: int, start: datetime, end: datetime
    ) -> Iterable[dict]:
        for page in self.open_interest_pages(symbol, interval, start, end):
            yield from page.rows


class DataCollector:
    def __init__(
        self,
        engine,
        client: BybitPublicClient,
        min_turnover: float,
        history_months: int,
    ):
        self.engine, self.client = engine, client
        self.min_turnover = Decimal(str(min_turnover))
        self.history_months = history_months
        self.checkpoints = CollectionCheckpointStore(engine)

    def refresh_universe(self) -> list[str]:
        tickers = self.client.tickers()
        prepared = []
        for item in self.client.linear_instruments():
            if (
                item.get("contractType") != "LinearPerpetual"
                or item.get("quoteCoin") != "USDT"
                or item.get("status") != "Trading"
            ):
                continue
            ticker = tickers.get(item["symbol"], {})
            launch_ms = int(item.get("launchTime") or 0)
            prepared.append(
                {
                    "symbol": item["symbol"],
                    "status": item["status"],
                    "base_asset": item.get("baseCoin", ""),
                    "quote_asset": item.get("quoteCoin", ""),
                    "tick_size": Decimal(
                        item.get("priceFilter", {}).get("tickSize", "0")
                    ),
                    "lot_size": Decimal(
                        item.get("lotSizeFilter", {}).get("qtyStep", "0")
                    ),
                    "turnover_24h": Decimal(ticker.get("turnover24h") or "0"),
                    "eligible": True,
                    "launch_time": (
                        datetime.fromtimestamp(launch_ms / 1000, UTC)
                        if launch_ms
                        else None
                    ),
                }
            )
        statement = text(
            """
            INSERT INTO instruments(
                symbol,status,base_asset,quote_asset,tick_size,lot_size,
                turnover_24h,eligible,launch_time,updated_at
            ) VALUES (
                :symbol,:status,:base_asset,:quote_asset,:tick_size,:lot_size,
                :turnover_24h,:eligible,:launch_time,now()
            )
            ON CONFLICT(symbol) DO UPDATE SET
                status=EXCLUDED.status,base_asset=EXCLUDED.base_asset,
                quote_asset=EXCLUDED.quote_asset,tick_size=EXCLUDED.tick_size,
                lot_size=EXCLUDED.lot_size,turnover_24h=EXCLUDED.turnover_24h,
                eligible=EXCLUDED.eligible,launch_time=EXCLUDED.launch_time,
                updated_at=now()
            """
        )
        if prepared:
            with self.engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE instruments SET status='NotTrading',eligible=false,updated_at=now() "
                        "WHERE quote_asset='USDT' AND status='Trading'"
                    )
                )
                connection.execute(statement, prepared)
        return [row["symbol"] for row in prepared]

    def _launch_time(self, symbol: str) -> datetime | None:
        with self.engine.connect() as connection:
            return connection.execute(
                text("SELECT launch_time FROM instruments WHERE symbol=:symbol"),
                {"symbol": symbol},
            ).scalar_one_or_none()

    @staticmethod
    def _next_end(value: int | None) -> datetime | None:
        return datetime.fromtimestamp(value / 1000, UTC) if value is not None else None

    def collect_symbol(
        self, symbol: str, include_derivatives: bool = True,
        requested_start: datetime | None = None, requested_end: datetime | None = None,
    ) -> dict[str, int]:
        now = requested_end or datetime.now(UTC)
        history_start = requested_start or now - timedelta(days=round(self.history_months * 30.4375))
        start = collection_start(history_start, self._launch_time(symbol))
        counts = {"candles": 0, "oi": 0, "funding": 0}
        for interval in INTERVALS:
            end = last_completed_open_time(now, interval)
            if end < start:
                continue
            checkpoint = self.checkpoints.get_or_create(
                "kline", symbol, interval, start, end
            )
            if checkpoint.status != "completed":
                resume_end = checkpoint.next_end or end
                for page in self.client.kline_pages(symbol, interval, start, resume_end):
                    records = [
                        {
                            "symbol": symbol,
                            "interval": interval,
                            "open_time": datetime.fromtimestamp(int(row[0]) / 1000, UTC),
                            "open": Decimal(row[1]),
                            "high": Decimal(row[2]),
                            "low": Decimal(row[3]),
                            "close": Decimal(row[4]),
                            "volume": Decimal(row[5]),
                            "turnover": Decimal(row[6]),
                        }
                        for row in page.rows
                    ]
                    counts["candles"] += self.checkpoints.commit_candle_page(
                        checkpoint.id,
                        records,
                        self._next_end(page.next_end_ms),
                        page.complete,
                    )
            step = interval * 60
            first_epoch = ((int(start.timestamp()) + step - 1) // step) * step
            last_epoch = int(end.timestamp())
            expected = max(0, (last_epoch - first_epoch) // step + 1)
            with self.engine.connect() as connection:
                observed = connection.execute(
                    text(
                        "SELECT count(*) FROM candles WHERE symbol=:symbol "
                        "AND interval_minutes=:interval AND open_time>=:first AND open_time<=:end"
                    ),
                    {
                        "symbol": symbol, "interval": interval,
                        "first": datetime.fromtimestamp(first_epoch, UTC), "end": end,
                    },
                ).scalar_one()
            self.checkpoints.record_coverage(
                checkpoint.id, expected, max(0, expected - int(observed))
            )
            if include_derivatives:
                counts["oi"] += self._collect_oi(symbol, interval, start, end)
        if include_derivatives:
            funding_end = now
            counts["funding"] += self._collect_funding(symbol, start, funding_end)
        return counts

    def _collect_oi(
        self, symbol: str, interval: int, start: datetime, end: datetime
    ) -> int:
        checkpoint = self.checkpoints.get_or_create(
            "open_interest", symbol, interval, start, end
        )
        if checkpoint.status == "completed":
            return 0
        total = 0
        for page in self.client.open_interest_pages(
            symbol, interval, start, end, checkpoint.next_cursor
        ):
            records = [
                {
                    "symbol": symbol,
                    "interval": interval,
                    "observed_at": datetime.fromtimestamp(
                        int(row["timestamp"]) / 1000, UTC
                    ),
                    "open_interest": Decimal(row["openInterest"]),
                }
                for row in page.rows
            ]
            total += self.checkpoints.commit_oi_page(
                checkpoint.id, records, page.next_cursor, page.complete
            )
        return total

    def _collect_funding(
        self, symbol: str, start: datetime, end: datetime
    ) -> int:
        checkpoint = self.checkpoints.get_or_create(
            "funding", symbol, 0, start, end
        )
        if checkpoint.status == "completed":
            return 0
        total = 0
        resume_end = checkpoint.next_end or end
        for page in self.client.funding_pages(symbol, start, resume_end):
            records = [
                {
                    "symbol": symbol,
                    "funding_time": datetime.fromtimestamp(
                        int(row["fundingRateTimestamp"]) / 1000, UTC
                    ),
                    "funding_rate": Decimal(row["fundingRate"]),
                }
                for row in page.rows
            ]
            total += self.checkpoints.commit_funding_page(
                checkpoint.id,
                records,
                self._next_end(page.next_end_ms),
                page.complete,
            )
        return total
