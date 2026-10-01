"""Resilient public Bybit V5 client and resumable database collectors."""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Iterable

import requests
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

LOG = logging.getLogger(__name__)
BASE_URL = "https://api.bybit.com/v5/market"
INTERVALS = (15, 60, 240)


class BybitPublicClient:
    def __init__(self, pause_seconds: float = .08, retries: int = 5):
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
                delay = min(30, 2 ** attempt) + self.pause_seconds
                LOG.warning("Bybit request %s failed (%s); retrying in %.1fs", path, exc, delay)
                time.sleep(delay)
        raise AssertionError("unreachable")

    def linear_instruments(self) -> list[dict]:
        cursor, rows = None, []
        while True:
            result = self.get("instruments-info", category="linear", limit=1000, **({"cursor": cursor} if cursor else {}))
            rows.extend(result.get("list", []))
            cursor = result.get("nextPageCursor")
            if not cursor:
                return rows

    def tickers(self) -> dict[str, dict]:
        result = self.get("tickers", category="linear")
        return {row["symbol"]: row for row in result.get("list", [])}

    def klines(self, symbol: str, interval: int, start: datetime, end: datetime) -> Iterable[list[str]]:
        # V5 returns reverse chronological rows, max 1000.  Moving the inclusive
        # cursor forward makes retries/restarts idempotent under the PK upsert.
        cursor_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        while cursor_ms < end_ms:
            result = self.get("kline", category="linear", symbol=symbol, interval=str(interval),
                              start=cursor_ms, end=end_ms, limit=1000)
            rows = result.get("list", [])
            if not rows:
                break
            rows.sort(key=lambda r: int(r[0]))
            yield from rows
            next_ms = int(rows[-1][0]) + interval * 60_000
            if next_ms <= cursor_ms:
                break
            cursor_ms = next_ms

    def funding_history(self, symbol: str, start: datetime, end: datetime) -> Iterable[dict]:
        cursor, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        while cursor < end_ms:
            result = self.get("funding/history", category="linear", symbol=symbol, startTime=cursor, endTime=end_ms, limit=200)
            rows = sorted(result.get("list", []), key=lambda x: int(x["fundingRateTimestamp"]))
            if not rows: return
            yield from rows
            next_ms = int(rows[-1]["fundingRateTimestamp"]) + 1
            if next_ms <= cursor: return
            cursor = next_ms

    def open_interest_history(self, symbol: str, interval: int, start: datetime, end: datetime) -> Iterable[dict]:
        # OI history is public but retention/interval availability varies by Bybit.
        unit = {15: "15min", 60: "1h", 240: "4h"}[interval]
        cursor, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        while cursor < end_ms:
            result = self.get("open-interest", category="linear", symbol=symbol, intervalTime=unit,
                              startTime=cursor, endTime=end_ms, limit=200)
            rows = sorted(result.get("list", []), key=lambda x: int(x["timestamp"]))
            if not rows: return
            yield from rows
            next_ms = int(rows[-1]["timestamp"]) + 1
            if next_ms <= cursor: return
            cursor = next_ms


class DataCollector:
    def __init__(self, engine, client: BybitPublicClient, min_turnover: float, history_months: int):
        self.engine, self.client = engine, client
        self.min_turnover, self.history_months = Decimal(str(min_turnover)), history_months

    def refresh_universe(self) -> list[str]:
        tickers = self.client.tickers()
        prepared = []
        for item in self.client.linear_instruments():
            # linear USDT perpetuals only; exclude prelaunch/delivery/non-trading contracts.
            if item.get("contractType") != "LinearPerpetual" or item.get("quoteCoin") != "USDT":
                continue
            ticker = tickers.get(item["symbol"], {})
            turnover = Decimal(ticker.get("turnover24h") or "0")
            prepared.append(dict(symbol=item["symbol"], status=item.get("status", ""), base_asset=item.get("baseCoin", ""),
                                 quote_asset=item.get("quoteCoin", ""), tick_size=Decimal(item.get("priceFilter", {}).get("tickSize", "0")),
                                 lot_size=Decimal(item.get("lotSizeFilter", {}).get("qtyStep", "0")), turnover_24h=turnover,
                                 eligible=item.get("status") == "Trading" and turnover >= self.min_turnover))
        statement = text("""
            INSERT INTO instruments(symbol,status,base_asset,quote_asset,tick_size,lot_size,turnover_24h,eligible,updated_at)
            VALUES (:symbol,:status,:base_asset,:quote_asset,:tick_size,:lot_size,:turnover_24h,:eligible,now())
            ON CONFLICT(symbol) DO UPDATE SET status=EXCLUDED.status,base_asset=EXCLUDED.base_asset,
              quote_asset=EXCLUDED.quote_asset,tick_size=EXCLUDED.tick_size,lot_size=EXCLUDED.lot_size,
              turnover_24h=EXCLUDED.turnover_24h,eligible=EXCLUDED.eligible,updated_at=now()""")
        with self.engine.begin() as conn:
            conn.execute(statement, prepared)
        return [r["symbol"] for r in prepared if r["eligible"]]

    def _resume_from(self, symbol: str, interval: int, default: datetime) -> datetime:
        with self.engine.connect() as conn:
            latest = conn.execute(text("SELECT max(open_time) FROM candles WHERE symbol=:s AND interval_minutes=:i"), {"s": symbol, "i": interval}).scalar()
        return max(default, latest.replace(tzinfo=UTC) + timedelta(minutes=interval)) if latest else default

    def collect_symbol(self, symbol: str, include_derivatives: bool = True) -> dict[str, int]:
        end = datetime.now(UTC).replace(second=0, microsecond=0)
        start = end - timedelta(days=round(self.history_months * 30.4375))
        counts = {"candles": 0, "oi": 0, "funding": 0}
        candle_sql = text("""INSERT INTO candles(symbol,interval_minutes,open_time,open,high,low,close,volume,turnover)
          VALUES (:symbol,:interval,:open_time,:open,:high,:low,:close,:volume,:turnover)
          ON CONFLICT(symbol,interval_minutes,open_time) DO NOTHING""")
        for interval in INTERVALS:
            resume = self._resume_from(symbol, interval, start)
            batch = []
            for row in self.client.klines(symbol, interval, resume, end):
                batch.append(dict(symbol=symbol, interval=interval, open_time=datetime.fromtimestamp(int(row[0]) / 1000, UTC),
                                  open=Decimal(row[1]), high=Decimal(row[2]), low=Decimal(row[3]), close=Decimal(row[4]), volume=Decimal(row[5]), turnover=Decimal(row[6])))
                if len(batch) >= 1000:
                    with self.engine.begin() as conn: counts["candles"] += conn.execute(candle_sql, batch).rowcount
                    batch.clear()
            if batch:
                with self.engine.begin() as conn: counts["candles"] += conn.execute(candle_sql, batch).rowcount
            if include_derivatives:
                counts["oi"] += self._store_oi(symbol, interval, start, end)
        if include_derivatives:
            counts["funding"] += self._store_funding(symbol, start, end)
        return counts

    def _store_oi(self, symbol, interval, start, end) -> int:
        values = [dict(symbol=symbol, interval=interval, observed_at=datetime.fromtimestamp(int(x["timestamp"])/1000, UTC), open_interest=Decimal(x["openInterest"])) for x in self.client.open_interest_history(symbol, interval, start, end)]
        if not values: return 0
        sql = text("INSERT INTO open_interest(symbol,interval_minutes,observed_at,open_interest) VALUES (:symbol,:interval,:observed_at,:open_interest) ON CONFLICT DO NOTHING")
        with self.engine.begin() as conn: return conn.execute(sql, values).rowcount

    def _store_funding(self, symbol, start, end) -> int:
        values = [dict(symbol=symbol, funding_time=datetime.fromtimestamp(int(x["fundingRateTimestamp"])/1000, UTC), funding_rate=Decimal(x["fundingRate"])) for x in self.client.funding_history(symbol, start, end)]
        if not values: return 0
        sql = text("INSERT INTO funding_rates(symbol,funding_time,funding_rate) VALUES (:symbol,:funding_time,:funding_rate) ON CONFLICT DO NOTHING")
        with self.engine.begin() as conn: return conn.execute(sql, values).rowcount
