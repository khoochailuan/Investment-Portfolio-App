# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Regressions for date-only input and split-adjusted ledger replay."""

from datetime import date
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from performance import Distribution, Series, dividend_candidates, performance
from portfolio import Ledger, PortfolioError, StockSplit, Trade, display_date, parse_day
from storage import Store
from watch_views import HOLDINGS_NEWS, WATCHLIST_NEWS, news_keys
from types import SimpleNamespace


def trade(key, day, side, qty, price, broker="Broker A"):
    return Trade.from_input(
        key,
        dict(
            symbol="SNDK",
            date=day,
            side=side,
            quantity=qty,
            price=price,
            broker=broker,
            currency="SGD",
            fee="0",
        ),
    )


class DateAndSplitTests(TestCase):
    def test_date_format_and_saved_iso_dates(self):
        expected = date(2024, 2, 29)
        for value in ("29-02-2024", "2024-02-29"):
            self.assertEqual(parse_day(value), expected)
            self.assertEqual(display_date(parse_day(value)), "29-02-2024")
        with self.assertRaisesRegex(PortfolioError, "DD-MM-YYYY"):
            parse_day("31-02-2024")

    def test_split_preserves_cost_and_pre_split_sales(self):
        early = trade(1, "01-06-2025", "BUY", "10", "800")
        before = trade(2, "02-06-2025", "SELL", "2", "850")
        later = trade(3, "05-06-2025", "SELL", "4", "110")
        other_broker = trade(4, "03-06-2025", "BUY", "1", "800", "Broker B")
        event = StockSplit("SNDK", later.day, 8, 1)
        for method in ("FIFO", "AVCO"):
            ledger = Ledger([early, before, later, other_broker], method, [event])
            position = ledger.positions["SNDK"]
            self.assertEqual(
                (position.qty, position.basis, position.average), (D(68), D(6800), D(100))
            )
            self.assertEqual(ledger.sales[2].basis, D(1600))
            self.assertEqual(ledger.sales[3].profit, D(40))
            self.assertEqual(ledger.by_id[1].qty, D(10))
            self.assertEqual(position.accounts["broker b"][0].qty, D(8))

    def test_split_storage_and_duplicate_guard(self):
        with TemporaryDirectory() as folder:
            store = Store(Path(folder), "demo")
            revision = store.snapshot().revision
            code = next(iter(store.snapshot().ledger.currencies))
            event = dict(symbol=code, date="08-11-2026", new="8", old="1")
            new = store.commit("split", revision, event)
            reopened = Store(Path(folder), "demo").snapshot()
            self.assertEqual(reopened.ledger.splits, new.ledger.splits)
            with self.assertRaisesRegex(PortfolioError, "already exists"):
                store.commit("split", new.revision, event)

    def test_split_performance_and_dividend_use_post_split_inventory(self):
        buy = trade(1, "01-06-2025", "BUY", "10", "800")
        event = StockSplit("SNDK", date(2025, 6, 5), 8, 1)
        end = date(2025, 6, 6)
        payout = Distribution(end, D("0.20"), "SGD", date(2025, 6, 20))
        history = Series(
            "SNDK",
            "SGD",
            buy.day,
            end,
            "Fixture",
            {buy.day: D(800), event.day: D(100), end: D(110)},
            payouts=(payout,),
            splits=(event.day,),
            split_factors={event.day: D(8)},
        )
        ledger = Ledger([buy], splits=[event])
        rows, warnings = performance(ledger, [], {"SNDK": history}, end)
        self.assertFalse(warnings)
        self.assertEqual([r.value for r in rows], [D(8000), D(8000), D(8800)])
        self.assertEqual(dividend_candidates(ledger, [], {"SNDK": history})[0][2], D(80))
        incomplete = Series("SNDK", "SGD", buy.day, end, "Fixture", {buy.day: D(800)}, splits=(event.day,))
        self.assertIsNone(incomplete.at(event.day))
        restored = Series.parse(history.record())
        self.assertEqual(restored.payouts[0].pay_day, payout.pay_day)
        self.assertEqual(restored.comparable_closes(history.days)[buy.day], D(100))

    def test_news_uses_only_the_active_list_and_rejects_an_unrelated_ticker(self):
        app = SimpleNamespace(
            book=SimpleNamespace(positions={"SNDK": None, "AAPL": None}),
            watch_state={"items": {"AAPL": None, "ME8U.SI": None}},
            news_scope=HOLDINGS_NEWS, news_symbol=None,
        )
        self.assertEqual(news_keys(app), ["AAPL", "SNDK"])
        app.news_symbol = "SNDK"
        self.assertEqual(news_keys(app), ["SNDK"])
        app.news_scope = WATCHLIST_NEWS
        self.assertEqual(news_keys(app), [])
        app.news_symbol = None
        self.assertEqual(news_keys(app), ["AAPL", "ME8U.SI"])
        app.news_symbol = "ME8U.SI"
        self.assertEqual(news_keys(app), ["ME8U.SI"])
