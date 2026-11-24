# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Regression cases covering currency display, dividend cash and time-series calculations."""

import fixtures_setup  # noqa: F401  (demo seed for tests)
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import flet as ft

from charts import bars_png, doughnut, lines_png, monthly_profit_chart, monthly_profit_png, ring_png
from dividends import Dividend, income_breakdown, income_estimates, net_income
from market import NoListing
from performance import (
    Distribution,
    Observation,
    Series,
    dividend_candidates,
    fetch_history,
    period_growth,
    performance,
    raw_history,
)
from portfolio import ZERO, Ledger, PortfolioError, Trade
from storage import Store
from test_valuation import fx, stock
from valuation import AllocationGroup, Overview, SalesReport
from widgets import choose

DAY = date(2025, 8, 4)


def t(i, s, c, qty, price, day=0, side="BUY", fee="0", broker="Syfe"):
    return Trade.from_input(
        i,
        dict(
            symbol=s,
            currency=c,
            quantity=qty,
            price=price,
            date=str(DAY + timedelta(days=day)),
            side=side,
            fee=fee,
            broker=broker,
        ),
    )


def d(i=1, s="X", c="USD", gross="4", tax="1", state="Received", day=1, ex=1, broker=""):
    return Dividend.parse(
        i,
        dict(
            symbol=s,
            currency=c,
            gross=gross,
            tax=tax,
            status=state,
            date=str(DAY + timedelta(days=day)),
            ex_date=str(DAY + timedelta(days=ex)),
            broker=broker,
        ),
    )


def series(key, c, prices):
    closes = {DAY + timedelta(days=i): D(str(v)) for i, v in prices.items()}
    return Series(key, c, min(closes), max(closes), "Yahoo Finance", closes)


class DividendTests(TestCase):
    def test_native_rows_survive_fx_outage_and_valuation_moves(self):
        book = Ledger([t(1, "X", "USD", "4", "10", fee="1"), t(2, "X.SI", "SGD", "2", "8")])
        prices = {"X": stock("X", "USD", "NMS", "12"), "X.SI": stock("X.SI", "SGD", "SES", "7")}
        for rate in ({}, {"USD": fx()}, {"USD": fx(rate="1.5")}):
            view = Overview(book, prices, rate)
            self.assertEqual(
                [(r.position.basis, r.local.value, r.local.profit) for r in view.rows],
                [(41, 48, 7), (16, 14, -2)],
            )
            self.assertEqual(view.gain, 7 * rate["USD"].rate - 2 if rate else None)
        missing = Overview(book, {}, {}).rows[0]
        self.assertEqual(
            (missing.position.basis, missing.local.value, missing.local.profit), (41, None, None)
        )

    def test_broker_cost_uses_actual_lots_and_reconciles(self):
        book = Ledger(
            [
                t(1, "X", "USD", "2", "8", fee="1"),
                t(2, "X", "USD", "1", "20", broker="B"),
                t(3, "X", "USD", "1", "9", day=1, side="SELL"),
            ]
        )
        view = Overview(book, {"X": stock("X", "USD", "NMS", "16")}, {"USD": fx()})
        groups = view.allocation_groups("Brokerage")
        self.assertEqual({g.label: g.basis for g in groups}, {"Syfe": D("11.05"), "B": D("26")})
        self.assertEqual(sum(g.basis for g in groups), view.basis)
        self.assertEqual(sum(g.value - g.basis for g in groups), view.gain)

    def test_receipts_and_schedule_have_independent_totals(self):
        received = d()
        scheduled = d(2, "Y.SI", "SGD", "5", "0", "Expected")
        rate = {"USD": fx()}
        self.assertEqual(net_income([received], rate), D("3.90"))
        self.assertEqual(
            income_breakdown([received, scheduled], rate),
            {(8, "Received"): D("3.90"), (8, "Expected"): D(5)},
        )
        self.assertIsNone(net_income([received], {}))
        with self.assertRaises(PortfolioError):
            d(gross="2", tax="3")
        with self.assertRaises(PortfolioError):
            d(day=1, ex=2)
        with self.assertRaises(PortfolioError):
            d(day=9999)
        self.assertEqual(d(day=9999, state="Expected").status, "Expected")

    def test_cash_records_atomic_edits_no_double_booking_and_older_workspace(self):
        with TemporaryDirectory() as directory:
            store = Store(Path(directory), "personal")
            from storage import atomic_json

            trade = t(1, "X", "USD", "1", "9")
            atomic_json(
                store.ledger_file, dict(schema=1, revision=1, next_id=2, trades=[trade.record()])
            )
            self.assertFalse(store.payment_file.exists())
            self.assertEqual(store.payments().rows, {})
            record = d(state="Expected").record()
            store.save_payment(0, record)
            before = store.payment_file.read_bytes()
            for rev, entry in [
                (0, record),
                (1, dict(record, broker="Syfe")),
                (1, dict(record, symbol="NVIDIA")),
            ]:
                with self.assertRaises(PortfolioError):
                    store.save_payment(rev, entry)
                self.assertEqual(store.payment_file.read_bytes(), before)
            store.save_payment(1, dict(record, status="Received"), 1)
            self.assertEqual(
                [a.status for a in Store(Path(directory), "personal").payments().rows.values()],
                ["Received"],
            )
            store.save_payment(2, payment_id=1)
            self.assertEqual(store.payments().rows, {})
            self.assertFalse(store.histories())

    def test_entitlement_excludes_ex_date_buys_and_covers_closed_positions(self):
        b = Ledger(
            [
                t(1, "X", "USD", "10", "10"),
                t(2, "X", "USD", "3", "10", day=1),
                t(3, "X", "USD", "13", "12", day=2, side="SELL"),
            ]
        )
        h = series("X", "USD", {0: 10, 1: 10, 2: 12})
        h.payouts = (
            Distribution(DAY, D("0.25"), "USD"),
            Distribution(DAY + timedelta(days=1), D(".25"), "USD"),
        )
        events = dividend_candidates(b, [], {"X": h})
        self.assertEqual(
            [(s, p.ex_day, q) for s, p, q in events], [("X", DAY + timedelta(days=1), 10)]
        )
        self.assertFalse(dividend_candidates(b, [d()], {"X": h}))
        h.splits = (DAY + timedelta(days=1),)
        self.assertFalse(dividend_candidates(b, [], {"X": h}))

    def test_annual_cash_estimate_uses_receiving_broker_and_current_shares(self):
        b = Ledger(
            [
                t(1, "X", "USD", "10", "10"),
                t(2, "X", "USD", "5", "10", day=1),
                t(3, "X", "USD", "25", "8", broker="Other"),
                t(4, "X", "USD", "5", "10", day=2, side="SELL"),
            ]
        )
        actual = d(broker="Syfe")
        est, n = income_estimates(
            b, [actual, d(2, state="Expected")], {}, {"USD": fx()}, DAY + timedelta(days=5)
        )
        self.assertEqual((est, n), ({"X": D("3.90")}, 1))
        self.assertEqual(
            income_estimates(b, [actual], {}, {}, DAY + timedelta(days=5))[0], {"X": None}
        )


class PerformanceTests(TestCase):
    def test_historical_fx_fees_receipts_and_full_liquidation(self):
        b = Ledger(
            [
                t(1, "X", "USD", "10", "10", fee="2"),
                t(2, "B.SI", "SGD", "5", "20", day=1, fee="1"),
                t(3, "X", "USD", "10", "11.5", day=2, side="SELL", fee="1"),
            ]
        )
        data = {
            "X": series("X", "USD", {0: 10, 1: 11}),
            "B.SI": series("B.SI", "SGD", {1: 20, 2: 21}),
            "USDSGD=X": series("USDSGD=X", "SGD", {0: 1.3, 1: 1.4, 2: 1.5}),
        }
        rows, warnings = performance(
            b, [d(), d(2, "B.SI", "SGD", "10000", "0", "Expected")], data, DAY + timedelta(days=2)
        )
        self.assertFalse(warnings)
        self.assertEqual(
            [(r.value, r.invested, r.income, r.gain) for r in rows],
            [
                (130, D("132.60"), 0, D("-2.60")),
                (254, D("233.60"), D("4.20"), D("24.60")),
                (105, D("62.60"), D("4.20"), D("46.60")),
            ],
        )

    def test_no_forward_filling_from_the_future_and_explicit_gap(self):
        b = Ledger([t(1, "X", "USD", "1", "10")])
        data = {
            "X": series("X", "USD", {0: 10, 20: 15}),
            "USDSGD=X": series("USDSGD=X", "SGD", {1: 1.3, 20: 1.4}),
        }
        rows, w = performance(b, [], data, DAY + timedelta(days=20))
        self.assertIsNone(rows[0].value)
        self.assertIsNone(rows[-1].invested)
        self.assertEqual(rows[-1].value, 21)
        self.assertIsNone(next(r for r in rows if r.day == DAY + timedelta(days=11)).value)
        self.assertIn("USDSGD=X", w)

    def test_splits_suppress_unreconciled_position(self):
        b = Ledger([t(1, "X.SI", "SGD", "10", "10")])
        h = series("X.SI", "SGD", {0: 10, 1: 5})
        h.splits = (DAY + timedelta(days=1),)
        rows, w = performance(b, [], {"X.SI": h}, DAY + timedelta(days=1))
        self.assertEqual(rows[0].value, 100)
        self.assertIsNone(rows[1].value)
        self.assertTrue(any("split" in label for label in w))

    def test_period_return_excludes_new_money_and_rebases_at_range_start(self):
        a = [
            Observation(DAY, D(100), D(100), D(0)),
            Observation(DAY + timedelta(days=5), D(200), D(200), D(0)),
            Observation(DAY + timedelta(days=10), D(215), D(200), D(0)),
        ]
        g, _ = period_growth(a)
        self.assertEqual([r.gain for r in g], [0, 0, 15])
        self.assertEqual(g[-1].return_pct, 10)
        short, start = period_growth(a, DAY + timedelta(days=6))
        self.assertEqual((start, short[-1].return_pct), (DAY + timedelta(days=5), D("7.5")))
        a[-1] = Observation(a[-1].day, None, D(200), D(0))
        self.assertIsNone(period_growth(a)[0][-1].return_pct)

    def test_verified_source_unit_split_and_currency_direction(self):
        ticks = [
            int(datetime(2025, 8, 4 + i, 12, tzinfo=timezone.utc).timestamp()) for i in range(2)
        ]
        info = dict(
            meta=dict(
                symbol="A.L",
                currency="GBp",
                instrumentType="EQUITY",
                exchangeTimezoneName="Europe/London",
            ),
            timestamp=ticks,
            indicators={"quote": [{"close": [100, 105]}]},
            events={
                "splits": {"x": dict(date=ticks[1], numerator=2, denominator=1)},
                "dividends": {"x": dict(date=ticks[0], amount=1)},
            },
        )
        with patch("performance.yahoo_json", return_value={"chart": {"result": [info]}}):
            h = raw_history("A.L", "GBP", DAY, DAY + timedelta(days=1))
            self.assertEqual((h.at(DAY), h.at(DAY + timedelta(days=1))), (2, D("1.05")))
            self.assertIsNone(h.payouts[0].currency)
            with self.assertRaises(PortfolioError):
                raw_history("A.L", "USD", DAY, DAY + timedelta(days=1))
        inv = series("SGDHKD=X", "HKD", {0: 5})
        with patch("performance.raw_history", side_effect=[NoListing("none"), inv]):
            got = fetch_history("HKDSGD=X", "SGD", DAY, DAY)
        self.assertEqual(got.at(DAY), D(".2"))

    def test_history_cache_keeps_wider_coverage_and_demo_is_isolated(self):
        with TemporaryDirectory() as directory:
            store = Store(Path(directory), "personal")
            old = series("X", "USD", {0: 1, 5: 2})
            new = series("X", "USD", {3: 4, 6: 5})
            for h in (old, new, old):
                store.save_histories([h])
            h = store.histories()["X"]
            self.assertEqual(
                (h.start, h.checked, h.at(DAY), h.at(DAY + timedelta(days=6))),
                (old.start, new.checked, 1, 5),
            )
            demo = Store(Path(directory), "demo")
            self.assertGreater(len(demo.payments().rows), 0)
            self.assertNotIn("AAPL", store.histories())
            self.assertFalse(store.payments().rows)

    def test_rendered_charts_have_png_content_including_single_holding(self):
        ring = ring_png(((1.0, 0.0, "#76A7F8"),), "#252A38")
        bar = bars_png(tuple((D(i), ZERO) for i in range(12)), 400)
        line = lines_png((0, 1), (((D("1.2"), None), "#76A7F8"),), ((0, "Aug"), (1, "Sep")), 400)
        monthly = monthly_profit_png(
            ((D(-3), 1), (D(0), 1), (D(5), 2), (None, 1)) + ((ZERO, 0),) * 8, 400, 3
        )
        for img in (ring, bar, line, monthly):
            self.assertTrue(img.startswith(b"\x89PNG\r\n"))

    def test_monthly_chart_distinguishes_missing_fx_no_sales_and_zero_profit(self):
        book = Ledger(
            [
                t(1, "A", "USD", "2", "4"),
                t(2, "X.SI", "SGD", "1", "3"),
                t(3, "A", "USD", "2", "5", day=1, side="SELL"),
                t(4, "X.SI", "SGD", "1", "3", day=30, side="SELL"),
            ]
        )
        report = SalesReport(book, {})
        chart, detail, warning = monthly_profit_chart(report, "2025", "SGD", 400, None)
        control = chart.controls[0]
        self.assertIn("missing exchange rate", warning.value)
        for month, expected in [
            (7, "S$0.00 · 0 completed sales"),
            (8, "Awaiting exchange rate · 1 completed sale"),
            (9, "S$0.00 · 1 completed sale"),
        ]:
            x = 55 + (month - 0.5) / 12 * (400 - 72)
            with patch.object(type(detail), "update"):
                control.on_tap_down(
                    ft.TapEvent("tap_down", control, local_position=ft.Offset(x, 75))
                )
            self.assertIn(expected, detail.value)

    def test_allocation_taps_use_flet_local_coordinates_and_ignore_the_hole(self):
        groups = [AllocationGroup("SGD", D(50), []), AllocationGroup("USD", D(50), [])]
        selected = []
        ring = doughnut(groups, D(100), None, ["#226655", "#5588AA"], "#FFFFFF", selected.append)
        self.assertIsInstance(ring.content.src, bytes)
        for point in [None, ft.Offset(119, 119), ft.Offset(203, 119), ft.Offset(35, 119)]:
            ring.on_tap_down(ft.TapEvent("tap_down", ring, local_position=point))
        self.assertEqual(selected, groups)

    def test_dropdown_delivers_the_selected_key_to_the_handler(self):
        choices = [("all", "All transactions"), ("SELL", "Sells")]
        selections = []
        menu = choose(
            "Transaction type", choices, "all", lambda e: selections.append(e.control.value)
        )
        self.assertEqual([(o.key, o.text) for o in menu.options], choices)
        menu.value = "SELL"
        menu.on_select(ft.Event("select", menu))
        self.assertEqual(selections, ["SELL"])
