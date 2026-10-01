import os
import unittest
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import text

from src.collectors.bybit import (
    BybitPublicClient,
    DataCollector,
    collection_start,
    last_completed_open_time,
)
from src.collectors.checkpoints import CollectionCheckpointStore
from src.db import engine_for, initialize


TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://research:research@127.0.0.1:55432/expansion_research_test",
)


def utc_ms(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp() * 1000)


class ScriptedClient(BybitPublicClient):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, path: str, **params):
        self.calls.append((path, params))
        return self.responses.pop(0)


class PaginationTests(unittest.TestCase):
    def test_kline_pages_move_end_backwards_and_normalize_rows(self):
        start = datetime(2026, 1, 1, 0, 10, tzinfo=UTC)
        end = datetime(2026, 1, 1, 0, 20, tzinfo=UTC)
        first_earliest = utc_ms("2026-01-01T00:15:00")
        client = ScriptedClient(
            [
                {
                    "list": [
                        [str(utc_ms("2026-01-01T00:20:00")), "1", "2", "1", "2", "3", "4"],
                        [str(first_earliest), "1", "2", "1", "2", "3", "4"],
                        [str(first_earliest), "1", "2", "1", "2", "3", "4"],
                    ]
                },
                {
                    "list": [
                        [str(utc_ms("2026-01-01T00:10:00")), "1", "2", "1", "2", "3", "4"]
                    ]
                },
            ]
        )

        pages = list(client.kline_pages("ALTUSDT", 15, start, end))

        self.assertEqual(
            [call[1]["end"] for call in client.calls],
            [utc_ms("2026-01-01T00:20:00"), first_earliest - 1],
        )
        self.assertEqual([int(row[0]) for row in pages[0].rows], [first_earliest, utc_ms("2026-01-01T00:20:00")])
        self.assertEqual(pages[-1].stop_reason, "start_reached")

    def test_kline_pages_stop_on_nonadvancing_page(self):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 1, 1, 0, 15, tzinfo=UTC)
        client = ScriptedClient(
            [{"list": [[str(utc_ms("2026-01-01T00:30:00")), "1", "2", "1", "2", "3", "4"]]}]
        )

        pages = list(client.kline_pages("ALTUSDT", 15, start, end))

        self.assertEqual(pages[-1].stop_reason, "no_rows_in_bounds")
        self.assertFalse(pages[-1].complete)

    def test_funding_pages_move_end_before_earliest_record(self):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 1, 2, tzinfo=UTC)
        earliest = utc_ms("2026-01-01T08:00:00")
        client = ScriptedClient(
            [
                {
                    "list": [
                        {"fundingRateTimestamp": str(utc_ms("2026-01-01T16:00:00")), "fundingRate": "0.1"},
                        {"fundingRateTimestamp": str(earliest), "fundingRate": "0.2"},
                    ]
                },
                {"list": [{"fundingRateTimestamp": str(utc_ms("2026-01-01T00:00:00")), "fundingRate": "0.3"}]},
            ]
        )

        pages = list(client.funding_pages("ALTUSDT", start, end))

        self.assertEqual([call[1]["endTime"] for call in client.calls], [utc_ms("2026-01-02T00:00:00"), earliest - 1])
        self.assertEqual(pages[-1].stop_reason, "start_reached")

    def test_open_interest_uses_cursor_and_stops_on_repeated_cursor(self):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 1, 2, tzinfo=UTC)
        client = ScriptedClient(
            [
                {"list": [{"timestamp": str(utc_ms("2026-01-01T08:00:00")), "openInterest": "2"}], "nextPageCursor": "next-1"},
                {"list": [{"timestamp": str(utc_ms("2026-01-01T16:00:00")), "openInterest": "3"}], "nextPageCursor": "next-1"},
            ]
        )

        pages = list(client.open_interest_pages("ALTUSDT", 15, start, end))

        self.assertEqual([call[1].get("cursor") for call in client.calls], [None, "next-1"])
        self.assertEqual(pages[-1].stop_reason, "repeated_cursor")
        self.assertFalse(pages[-1].complete)

    def test_completed_candle_boundary_and_launch_bound(self):
        exact = datetime(2026, 1, 1, 0, 15, tzinfo=UTC)
        self.assertEqual(last_completed_open_time(exact, 15), datetime(2026, 1, 1, tzinfo=UTC))
        history_start = datetime(2025, 1, 1, tzinfo=UTC)
        launch = datetime(2025, 7, 1, tzinfo=UTC)
        self.assertEqual(collection_start(history_start, launch), launch)


class UniverseClient:
    def tickers(self):
        return {
            "HIGHUSDT": {"turnover24h": "1000000"},
            "LOWUSDT": {"turnover24h": "1"},
        }

    def linear_instruments(self):
        common = {
            "contractType": "LinearPerpetual",
            "quoteCoin": "USDT",
            "status": "Trading",
            "priceFilter": {"tickSize": "0.1"},
            "lotSizeFilter": {"qtyStep": "1"},
            "launchTime": str(utc_ms("2025-01-01T00:00:00")),
        }
        return [
            {**common, "symbol": "HIGHUSDT", "baseCoin": "HIGH"},
            {**common, "symbol": "LOWUSDT", "baseCoin": "LOW"},
        ]


class DatabaseCollectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = engine_for(TEST_DATABASE_URL)
        initialize(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        with self.engine.begin() as connection:
            connection.execute(text("DELETE FROM collection_jobs WHERE symbol IN ('HIGHUSDT','LOWUSDT','CPTESTUSDT')"))
            connection.execute(text("DELETE FROM candles WHERE symbol IN ('HIGHUSDT','LOWUSDT','CPTESTUSDT')"))
            connection.execute(text("DELETE FROM instruments WHERE symbol IN ('HIGHUSDT','LOWUSDT','CPTESTUSDT')"))

    def test_universe_includes_low_current_turnover_contract(self):
        collector = DataCollector(self.engine, UniverseClient(), 500_000, 12)

        symbols = collector.refresh_universe()

        self.assertEqual(set(symbols), {"HIGHUSDT", "LOWUSDT"})
        with self.engine.connect() as connection:
            eligible = connection.execute(
                text("SELECT symbol,eligible FROM instruments ORDER BY symbol")
            ).all()
        self.assertEqual(eligible, [("HIGHUSDT", True), ("LOWUSDT", True)])

    def test_checkpoint_page_replay_is_idempotent(self):
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO instruments(symbol,status,base_asset,quote_asset,turnover_24h,eligible) "
                    "VALUES ('CPTESTUSDT','Trading','CPTEST','USDT',0,true)"
                )
            )
        store = CollectionCheckpointStore(self.engine)
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 1, 2, tzinfo=UTC)
        checkpoint = store.get_or_create("kline", "CPTESTUSDT", 15, start, end)
        record = {
            "symbol": "CPTESTUSDT",
            "interval": 15,
            "open_time": start,
            "open": Decimal("1"),
            "high": Decimal("2"),
            "low": Decimal("1"),
            "close": Decimal("2"),
            "volume": Decimal("3"),
            "turnover": Decimal("4"),
        }

        store.commit_candle_page(checkpoint.id, [record], start, complete=False)
        store.commit_candle_page(checkpoint.id, [record], start, complete=True)

        with self.engine.connect() as connection:
            candle_count = connection.execute(
                text("SELECT count(*) FROM candles WHERE symbol='CPTESTUSDT'")
            ).scalar_one()
            job = connection.execute(
                text("SELECT stored_count,status FROM collection_jobs WHERE id=:id"),
                {"id": checkpoint.id},
            ).one()
        self.assertEqual(candle_count, 1)
        self.assertEqual((job.stored_count, job.status), (1, "completed"))


if __name__ == "__main__":
    unittest.main()
