# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Tests for dividend growth, events, insights, move reasons and the extended rebalance."""

import fixtures_setup  # noqa: F401  (demo seed for tests)
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from analytics import plan_rebalance, revised_allocation, risk_report
from dividend_stats import (
    annual_dps, build_insights, dps_growth, growth_streak,
    monthly_by_year, move_reasons, received_buckets, totals_by_group, upcoming_events,
)
from dividends import Dividend
from performance import Distribution, Series
from portfolio import PortfolioError
from storage import Store
from valuation import Overview, SalesReport

TODAY = date(2026, 11, 21)
D = Decimal


def series(payouts, start=date(2019, 1, 1), currency="USD"):
    closes = {start + timedelta(days=i): D(100) for i in range(0, 2800, 7)}
    return Series("X", currency, start, TODAY, "test", closes, "", tuple(payouts), ())


def dist(year, rate, month=3):
    return Distribution(date(year, month, 10), D(rate), "USD")


class GrowthTests(unittest.TestCase):
    def test_annual_dps_uses_complete_years_only(self):
        s = series([dist(2024, "1.0"), dist(2025, "1.1"), dist(2026, "0.3")])
        self.assertEqual(annual_dps(s, TODAY), {2024: D("1.0"), 2025: D("1.1")})

    def test_year_before_history_start_is_incomplete(self):
        s = series([dist(2019, "1.0"), dist(2020, "1.1")], start=date(2019, 6, 1))
        self.assertNotIn(2019, annual_dps(s, TODAY))

    def test_compound_growth(self):
        s = series([dist(2022, "1.00"), dist(2023, "1.10"), dist(2024, "1.21"), dist(2025, "1.331")])
        pct, span = dps_growth(s, TODAY)
        self.assertEqual(span, 3)
        self.assertAlmostEqual(pct, 10.0, places=6)

    def test_growth_needs_two_years_and_a_recent_one(self):
        self.assertIsNone(dps_growth(series([dist(2025, "1")]), TODAY))
        self.assertIsNone(dps_growth(series([dist(2021, "1"), dist(2022, "1.1")]), TODAY))
        self.assertIsNone(dps_growth(None, TODAY))

    def test_streak_stops_at_first_non_increase(self):
        s = series([dist(2021, "1"), dist(2022, "1.2"), dist(2023, "1.1"), dist(2024, "1.2"), dist(2025, "1.3")])
        self.assertEqual(growth_streak(s, TODAY), 2)

    def test_other_currency_payouts_are_ignored(self):
        s = series([Distribution(date(2025, 3, 1), D(5), "SGD"), dist(2025, "1")])
        self.assertEqual(annual_dps(s, TODAY)[2025], D(1))


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        store = Store(Path(cls.tmp.name), "demo")
        cls.book = store.snapshot().ledger
        cls.rates, cls.hist, cls.pay = store.fx_rates(), store.histories(), store.payments()
        cls.rows = list(cls.pay.rows.values())
        sectors = {k: v.sector for k, v in cls.hist.items() if v.sector}
        cls.view = Overview(cls.book, store.quotes(), cls.rates, SalesReport(cls.book, cls.rates), sectors)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class EventTests(Base):
    def test_events_are_sorted_and_only_for_open_holdings(self):
        events = upcoming_events(self.book, self.rows, self.hist, TODAY, back=120, ahead=90)
        self.assertTrue(events)
        self.assertEqual(events, sorted(events, key=lambda e: (e.day, e.kind, e.symbol)))
        self.assertTrue(all(e.symbol in self.book.positions for e in events))
        self.assertTrue(all(e.kind in ("Ex-dividend date", "Dividend payout") for e in events))

    def test_window_excludes_far_events(self):
        events = upcoming_events(self.book, self.rows, self.hist, TODAY, back=0, ahead=0)
        self.assertTrue(all(e.day == TODAY for e in events))

    def test_ex_date_is_not_duplicated_by_history(self):
        events = upcoming_events(self.book, self.rows, self.hist, TODAY, back=200, ahead=0)
        keys = [(e.symbol, e.day) for e in events if e.kind == "Ex-dividend date"]
        self.assertEqual(len(keys), len(set(keys)))


class MonthlyTests(Base):
    def test_monthly_totals_match_received_net(self):
        by_year, incomplete = monthly_by_year(self.rows, self.rates)
        self.assertFalse(incomplete)
        total = sum(sum(v) for v in by_year.values())
        expected = sum(
            (d.net if d.currency == "SGD" else d.net * self.rates[d.currency].rate)
            for d in self.rows if d.status == "Received"
        )
        self.assertAlmostEqual(float(total), float(expected), delta=0.1)
        self.assertTrue(all(len(v) == 12 for v in by_year.values()))

    def test_missing_rate_marks_the_year_incomplete(self):
        by_year, incomplete = monthly_by_year(self.rows, {})
        self.assertTrue(incomplete)


class InsightTests(Base):
    def test_cards_have_titles_and_detail(self):
        risk = risk_report(self.view, self.hist, TODAY)
        for card in build_insights(self.view, risk, self.hist, self.rows, self.rates, TODAY):
            self.assertTrue(card.title and card.summary and card.detail)

    def test_income_concentration_card(self):
        rows = [
            Dividend(1, "AAPL", date(2026, 8, 1), "SGD", D(900), D(0), "Received", None, ""),
            Dividend(2, "MSFT", date(2026, 8, 2), "SGD", D(100), D(0), "Received", None, ""),
        ]
        cards = build_insights(self.view, None, self.hist, rows, self.rates, TODAY)
        income = [c for c in cards if c.key == "income"]
        self.assertEqual(len(income), 1)
        self.assertIn("AAPL", income[0].summary)

    def test_no_dividends_gives_no_income_card(self):
        cards = build_insights(self.view, None, self.hist, [], self.rates, TODAY)
        self.assertFalse([c for c in cards if c.key == "income"])


class MoveTests(Base):
    def test_threshold_and_headline_matching(self):
        now = datetime(2026, 11, 21, tzinfo=timezone.utc)
        every = move_reasons(self.view, self.hist, self.rates, {}, now, threshold=0)
        self.assertTrue(every)
        symbol = every[0].symbol
        feeds = {symbol: {"articles": [
            {"title": "Old", "url": "https://a.test/1", "source": "A", "time": "2026-11-01T00:00:00+00:00"},
            {"title": "Fresh", "url": "https://a.test/2", "source": "B", "time": "2026-11-20T12:00:00+00:00"},
        ]}}
        got = {m.symbol: m for m in move_reasons(self.view, self.hist, self.rates, feeds, now, threshold=0)}
        self.assertEqual(got[symbol].headline, "Fresh")
        self.assertIsNone(move_reasons(self.view, self.hist, self.rates, {}, now, 0)[0].headline)
        self.assertEqual(move_reasons(self.view, self.hist, self.rates, {}, now, threshold=1000), [])


class RebalanceOptionTests(Base):
    def targets(self):
        return {r.position.symbol: D("16.7") for r in self.view.rows}

    def test_fractional_shares_use_six_decimals_and_never_oversell(self):
        plan = plan_rebalance(self.view, self.targets(), D(500), fractional=True)
        self.assertTrue(any(l.shares != l.shares.to_integral_value() for l in plan.lines))
        self.assertTrue(all(-l.shares <= self.book.positions[l.symbol].qty for l in plan.lines))
        self.assertGreaterEqual(plan.cash_left, 0)

    def test_whole_shares_by_default(self):
        plan = plan_rebalance(self.view, self.targets(), D(500))
        self.assertTrue(all(l.shares == l.shares.to_integral_value() for l in plan.lines))

    def test_minimum_trade_skips_small_trades(self):
        floor = D(300)
        plan = plan_rebalance(self.view, self.targets(), D(500), min_trade=floor)
        self.assertTrue(all(abs(l.amount) >= floor for l in plan.lines if l.shares))

    def test_withdrawal_raises_cash_by_selling(self):
        current = {r.position.symbol: self.view.weight(r) for r in self.view.rows}
        plan = plan_rebalance(self.view, current, D(0), withdraw=D(1000), band=D(0))
        sold = -sum(l.amount for l in plan.lines if l.shares < 0)
        self.assertGreater(sold, 0)
        self.assertLessEqual(sold, D(1000) + max(r.value for r in self.view.rows))
        self.assertGreater(plan.cash_left, D(-1000))

    def test_withdrawal_needs_sales(self):
        with self.assertRaises(PortfolioError):
            plan_rebalance(self.view, {}, D(0), "Buy only", withdraw=D(100))

    def test_withdrawal_larger_than_portfolio_is_rejected(self):
        with self.assertRaises(PortfolioError):
            plan_rebalance(self.view, {}, D(0), withdraw=self.view.value + 1)

    def test_negative_options_are_rejected(self):
        for kwargs in ({"min_trade": D(-1)}, {"withdraw": D(-1)}):
            with self.assertRaises(PortfolioError):
                plan_rebalance(self.view, {}, D(0), **kwargs)


class ReceivedChartTests(Base):
    def buckets(self, period, interval):
        return received_buckets(self.rows, self.rates, period, interval, TODAY)[:2]

    def test_trailing_window_is_twelve_months_ending_this_month(self):
        labels, values = self.buckets("12 trailing months", "Monthly")
        self.assertEqual((labels[0], labels[-1], len(values)), ("Dec 25", "Nov 26", 12))

    def test_year_to_date_and_calendar_year(self):
        labels, _ = self.buckets("Year to date", "Monthly")
        self.assertEqual((labels[0], labels[-1], len(labels)), ("Jan 26", "Nov 26", 11))
        labels, _ = self.buckets("2025", "Monthly")
        self.assertEqual((labels[0], labels[-1]), ("Jan 25", "Dec 25"))

    def test_all_time_starts_at_the_first_received_payment(self):
        labels, values = self.buckets("All time", "Monthly")
        first = min(d.day for d in self.rows if d.status == "Received")
        self.assertEqual(labels[0], f"{first:%b %y}")
        self.assertEqual(labels[-1], "Nov 26")

    def test_quarterly_buckets_sum_to_the_monthly_total(self):
        _, monthly = self.buckets("12 trailing months", "Monthly")
        labels, quarterly = self.buckets("12 trailing months", "Quarterly")
        self.assertTrue(all(l.startswith("Q") for l in labels))
        self.assertEqual(sum(quarterly), sum(monthly))

    def test_annual_lists_each_year_with_a_record(self):
        years, sums = self.buckets("12 trailing months", "Annual")
        self.assertEqual(years, sorted(years))
        self.assertEqual(len(years), len(sums))

    def test_first_partial_quarter_excludes_months_outside_the_selection(self):
        from dividends import Dividend

        outside = Dividend(999, "A", date(2025, 10, 1), "SGD", D("100"), D("0"), "Received", None, "")
        inside = Dividend(1000, "A", date(2025, 12, 1), "SGD", D("5"), D("0"), "Received", None, "")
        for frequency in ("Monthly", "Quarterly", "Annual"):
            _, totals, scoped = received_buckets(
                [outside, inside], {}, "12 trailing months", frequency, TODAY
            )
            self.assertEqual(sum(totals), D("5"))
            self.assertEqual(scoped, [inside])

    def test_missing_rate_gives_none(self):
        _, values, _ = received_buckets(self.rows, {}, "All time", "Monthly", TODAY)
        self.assertIn(None, values)

    def test_every_group_is_listed_largest_first_and_none_is_folded(self):
        rows = totals_by_group(self.rows, self.rates, "Received", lambda p: p.symbol)
        self.assertEqual(rows, sorted(rows, key=lambda r: (-r[1], r[0])))
        self.assertEqual(len(rows), len({d.symbol for d in self.rows if d.status == "Received"}))
        self.assertFalse([r for r in rows if r[0].startswith("Other")])


class RevisedAllocationTests(Base):
    def plan(self, **kw):
        t = {r.position.symbol: D(100) / len(self.view.rows) for r in self.view.rows}
        return plan_rebalance(self.view, t, D(2000), **kw)

    def test_after_trades_totals_match_the_plan(self):
        plan = self.plan()
        self.assertGreaterEqual(plan.cash_left, 0)
        for group in ("Holding", "Sector", "Market", "Currency"):
            got = revised_allocation(self.view, plan, group, True)
            self.assertEqual(sum(x.value for x in got), plan.total)

    def test_current_equals_portfolio_value_and_has_no_cash(self):
        got = revised_allocation(self.view, self.plan(), "Market", False)
        self.assertEqual(sum(x.value for x in got), self.view.value)
        self.assertNotIn("Cash", [x.label for x in got])


class AllocationCountTests(Base):
    def test_every_holding_gets_its_own_group(self):
        groups = self.view.allocation_groups("Holding")
        self.assertEqual(len(groups), len(self.view.rows))
        self.assertFalse([g for g in groups if g.label.startswith("Other")])

    def test_revised_allocation_is_not_folded_by_default(self):
        t = {r.position.symbol: D(100) / len(self.view.rows) for r in self.view.rows}
        got = revised_allocation(self.view, plan_rebalance(self.view, t, D(500)), "Holding")
        self.assertGreaterEqual(len([x for x in got if x.label != "Cash"]), len(self.view.rows))
        self.assertFalse([x for x in got if x.label.startswith("Other")])

    def test_shipped_demo_counts_all_holdings(self):
        import storage

        saved = storage.SEED_DIR
        storage.SEED_DIR = Path(storage.__file__).resolve().parent / "assets"
        try:
            with tempfile.TemporaryDirectory() as folder:
                store = Store(Path(folder), "demo")
                book, rates = store.snapshot().ledger, store.fx_rates()
                sectors = {k: v.sector for k, v in store.histories().items() if v.sector}
                view = Overview(book, store.quotes(), rates, SalesReport(book, rates), sectors)
                self.assertEqual(len(view.allocation_groups("Holding")), len(book.positions))
                self.assertGreater(len(book.positions), 8)
        finally:
            storage.SEED_DIR = saved

    def test_palette_gives_each_slice_its_own_colour(self):
        from frost_theme import FROST

        colours = FROST.colours(24)
        self.assertEqual(len(set(colours)), 24)
        self.assertEqual(colours[:8], FROST.chart)


class GetStartedTests(unittest.TestCase):
    def app(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from portfolio import Ledger

        noop = lambda *a, **k: None
        return SimpleNamespace(
            book=Ledger([]), trade_form=noop, import_form=noop, page=MagicMock()
        )

    def test_empty_portfolio_asks_only_for_holdings(self):
        import flet as ft
        import tools_views

        app = self.app()
        panel = tools_views.checklist(app, Overview(app.book, {}, {}))
        texts, offenders = [], []

        def walk(c):
            if isinstance(c, ft.Text):
                texts.append(str(c.value))
            if isinstance(c, ft.Row) and c.wrap:
                offenders.extend(k for k in c.controls if getattr(k, "expand", None))
            for k in getattr(c, "controls", None) or []:
                walk(k)
            if getattr(c, "content", None) is not None:
                walk(c.content)

        walk(panel)
        self.assertEqual(offenders, [])  # an expanding child in a wrapping row drew a grey box
        self.assertEqual(
            [t for t in texts if t.strip()][:2],
            ["Get started", "Add your holdings to build a working dashboard"],
        )
        self.assertFalse([t for t in texts if "Required steps" in t])
        column = panel.content
        self.assertEqual(column.alignment, ft.MainAxisAlignment.CENTER)
        self.assertEqual(column.horizontal_alignment, ft.CrossAxisAlignment.CENTER)
        self.assertIsNone(panel.height)

    def test_autoload_fetches_a_new_holdings_prices_once(self):
        import interface

        calls = []
        stub = type("Stub", (), {})()
        stub.refreshing, stub.autoloaded = False, set()
        stub.page = type("P", (), {"run_task": lambda self, *a: calls.append(a)})()
        stub.refresh = lambda e: None
        stub.request_history = lambda: calls.append("history")
        view = type("V", (), {"missing_quotes": ["KO"]})()
        interface.PortfolioApp.autoload(stub, view)
        interface.PortfolioApp.autoload(stub, view)  # same symbol: not retried
        self.assertEqual(len(calls), 2)  # one price refresh and one history request
        stub.refreshing = True
        view.missing_quotes = ["PEP"]
        interface.PortfolioApp.autoload(stub, view)  # busy: waits
        self.assertEqual(len(calls), 2)


class PairsTests(unittest.TestCase):
    def test_pairs_two_per_row_and_leaves_an_odd_card_full_width(self):
        import flet as ft
        from widgets import pairs

        cards = [ft.Container() for _ in range(5)]
        rows = pairs(cards, True, weights=(2, 3))
        self.assertEqual([type(r).__name__ for r in rows], ["Row", "Row", "Container"])
        self.assertEqual((cards[0].expand, cards[1].expand), (2, 3))
        self.assertEqual(pairs(cards, False), cards)


if __name__ == "__main__":
    unittest.main()
