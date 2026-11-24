# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Checks for current-rate SGD reporting, quote failures and cross-market allocation."""

import asyncio
import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from market import UTC, Listing, NoListing, Quote, fetch_named_quote
from interface import PortfolioApp
from portfolio import Ledger, PortfolioError, Trade
from storage import Store
from valuation import FxRate, Overview, SalesReport, fetch_sgd_rate, reporting_currencies

STAMP = datetime(2025, 8, 6, 6, tzinfo=UTC)


def fx(code="USD", rate="1.30", at=STAMP):
    return FxRate(code, D(rate), at, (code + "SGD=X",))


def stock(symbol, currency, exchange, price):
    identity = Listing(symbol, symbol, exchange, "EQUITY", currency, currency)
    return Quote(symbol, D(price), STAMP, "Yahoo Finance", currency, identity)


def trade(i, symbol, currency, qty, price, side="BUY", fee="0", broker="Syfe"):
    return Trade.from_input(
        i,
        dict(
            symbol=symbol,
            currency=currency,
            quantity=qty,
            price=price,
            side=side,
            fee=fee,
            broker=broker,
            date="2025-08-06",
        ),
    )


def quote_payload(pair, price, target, timestamp=None):
    return {
        "chart": {
            "result": [
                {
                    "meta": dict(
                        symbol=pair,
                        instrumentType="CURRENCY",
                        currency=target,
                        regularMarketPrice=price,
                        regularMarketTime=timestamp or int(STAMP.timestamp()),
                    )
                }
            ]
        }
    }


class ExchangeFeedTests(unittest.TestCase):
    def test_selected_quote_recovers_the_exact_listing_name_and_keeps_it_across_refreshes(self):
        quote = stock("ME8U.SI", "SGD", "SES", "1.68")
        names = {
            "quotes": [
                {
                    "symbol": "ME8U.SI",
                    "quoteType": "EQUITY",
                    "exchange": "SES",
                    "longname": "Mapletree Industrial Trust",
                },
                {
                    "symbol": "OTHER",
                    "quoteType": "EQUITY",
                    "exchange": "SES",
                    "longname": "Another issuer",
                },
            ]
        }
        with (
            patch("market.fetch_quote", return_value=quote),
            patch("market.yahoo_json", return_value=names),
        ):
            named = fetch_named_quote("ME8U.SI")
        self.assertEqual(named.listing.name, "Mapletree Industrial Trust")
        with TemporaryDirectory() as directory:
            store = Store(Path(directory), "personal")
            store.save_quotes(
                [named, replace(quote, price=D("1.70"), asof=STAMP + timedelta(minutes=5))]
            )
            saved = store.quotes()[quote.symbol]
            self.assertEqual((saved.listing.name, saved.price), (named.listing.name, D("1.70")))
        with (
            patch("market.fetch_quote", return_value=quote),
            patch("market.yahoo_json", side_effect=PortfolioError("Offline")),
        ):
            self.assertEqual(fetch_named_quote(quote.symbol), quote)

    def test_damaged_fx_cache_allows_live_session_valuation_and_preserves_file(self):
        with TemporaryDirectory() as directory:
            store = Store(Path(directory), "personal")
            store.fx_file.write_text("{damaged")
            app = PortfolioApp.__new__(PortfolioApp)
            app.store, app.rates, app.fx_errors, app.fx_busy = store, {}, {}, {"USD"}
            app.connected, app.tab = True, "Transactions"
            app.load_rates()
            self.assertTrue(app.fx_cache_error)
            with patch("interface.refresh_prices", return_value=({"USD": fx()}, {})):
                asyncio.run(app.update_fx(store, {"USD"}))
            self.assertEqual(app.rates["USD"], fx())
            self.assertFalse(app.fx_busy)
            self.assertTrue(app.fx_cache_error)
            self.assertEqual(store.fx_file.read_text(), "{damaged")
            app.merge_rates({"USD": fx(rate="1.10", at=STAMP - timedelta(days=1))})
            self.assertEqual(app.rates["USD"], fx())

    def test_direct_rate_and_round_trip(self):
        with patch(
            "valuation.yahoo_json", return_value=quote_payload("USDSGD=X", 1.3, "SGD")
        ) as request:
            got = fetch_sgd_rate("USD")
        self.assertEqual(got, fx())
        self.assertEqual(FxRate.from_record(got.record()), got)
        self.assertIn("USDSGD%3DX", request.call_args.args[0])

    def test_inverted_rate_and_cross_rate_use_correct_direction_and_oldest_leg(self):
        missing = NoListing("No pair")
        rev = quote_payload("SGDHKD=X", 5, "HKD")
        with patch("valuation.yahoo_json", side_effect=[missing, rev]):
            got = fetch_sgd_rate("HKD")
        self.assertEqual((got.rate, got.pairs), (D("0.2"), ("SGDHKD=X",)))
        recent = int((STAMP + timedelta(days=1)).timestamp())
        with patch(
            "valuation.yahoo_json",
            side_effect=[
                missing,
                missing,
                missing,
                quote_payload("USDBRL=X", 5, "BRL"),
                quote_payload("USDSGD=X", "1.30", "SGD", recent),
            ],
        ):
            got = fetch_sgd_rate("BRL")
        self.assertEqual((got.rate, got.asof), (D("0.26"), STAMP))
        self.assertEqual(FxRate.from_record(got.record()), got)

    def test_invalid_pair_identity_unit_type_price_time_and_outage_are_rejected(self):
        for invalid in [
            dict(currency="USD"),
            dict(symbol="SGDUSD=X"),
            dict(instrumentType="EQUITY"),
            dict(regularMarketPrice=0),
            dict(regularMarketPrice="NaN"),
            dict(regularMarketTime=0),
            dict(regularMarketTime=4102444800),
        ]:
            data = quote_payload("USDSGD=X", 1.3, "SGD")
            data["chart"]["result"][0]["meta"].update(invalid)
            with (
                self.subTest(invalid=invalid),
                patch("valuation.yahoo_json", return_value=data),
                self.assertRaises(PortfolioError),
            ):
                fetch_sgd_rate("USD")
        with (
            patch(
                "valuation.yahoo_json", side_effect=PortfolioError("Service unavailable")
            ) as fetch,
            self.assertRaises(PortfolioError),
        ):
            fetch_sgd_rate("GBP")
        fetch.assert_called_once()

    def test_offline_cache_upgrades_old_workspaces_and_preserves_latest_observation(self):
        with TemporaryDirectory() as d:
            store = Store(Path(d), "personal")
            self.assertFalse(store.fx_file.exists())
            self.assertEqual(store.fx_rates(), {})
            ledger_before = store.ledger_file.read_bytes()
            store.save_fx_rates([fx()])
            store.save_fx_rates([fx(rate="1.20", at=STAMP - timedelta(days=2)), fx("JPY", "0.008")])
            store2 = Store(Path(d), "personal")
            self.assertEqual(store2.fx_rates()["USD"].rate, D("1.30"))
            self.assertEqual(store2.fx_rates()["JPY"].rate, D("0.008"))
            self.assertEqual(ledger_before, store.ledger_file.read_bytes())
            bad = fx().record()
            bad["target"] = "EUR"
            store.fx_file.write_text(json.dumps(dict(schema=1, rates=[bad])))
            with self.assertRaises(PortfolioError):
                store.fx_rates()


class ConsolidatedOverviewTests(unittest.TestCase):
    def test_realised_return_uses_the_combined_matched_cost_with_no_open_position_dependency(self):
        book = Ledger(
            [
                trade(1, "A", "USD", "2", "10", fee="2"),
                trade(2, "B.SI", "SGD", "4", "20"),
                trade(3, "A", "USD", "2", "18", "SELL", "2"),
                trade(4, "B.SI", "SGD", "2", "15", "SELL", "1"),
            ]
        )
        rates = {"USD": fx(rate="1.50")}
        report = SalesReport(book, rates)
        self.assertEqual(report.profit_by_stock(), [("A", D("18")), ("B.SI", D("-11"))])
        total = report.totals["all"]
        self.assertEqual((total.profit, total.basis, total.return_pct), (7, 73, D(700) / 73))
        self.assertEqual(Overview(book, {}, rates, report).realised_return_pct, total.return_pct)
        unavailable = SalesReport(book, {})
        self.assertEqual(unavailable.profit_by_stock(), [("B.SI", D("-11")), ("A", None)])
        self.assertIsNone(unavailable.totals["all"].return_pct)
        self.assertIsNone(SalesReport(Ledger([]), {}).totals["all"].return_pct)

    def test_stock_chart_nets_repeat_sales_and_tracks_month_and_currency(self):
        later = datetime(2025, 9, 1).date()
        book = Ledger(
            [
                trade(1, "A", "USD", "4", "10"),
                trade(2, "X.SI", "SGD", "1", "10"),
                trade(3, "A", "USD", "2", "12", "SELL"),
                replace(trade(4, "A", "USD", "2", "7", "SELL"), day=later),
                replace(trade(5, "X.SI", "SGD", "1", "10", "SELL"), day=later),
            ]
        )
        report = SalesReport(book, {"USD": fx(rate="1.50")})
        self.assertEqual(dict(report.profit_by_stock()), {"A": D("-3"), "X.SI": 0})
        self.assertEqual(dict(report.profit_by_stock("2025-08")), {"A": 6})
        self.assertEqual(dict(report.profit_by_stock("2025-09")), {"A": -9, "X.SI": 0})
        self.assertEqual(SalesReport(book, {}, "USD").profit_by_stock(), [("A", D("-2"))])
        self.assertEqual(sum(v for _, v in report.profit_by_stock()), report.totals["all"].profit)

    def test_realised_lines_months_and_overview_reconcile_after_rounding(self):
        buys = [trade(1, "X", "USD", "2", "1.02"), trade(2, "Y.SI", "SGD", "1", "10")]
        august = trade(3, "X", "USD", "1", "1.03", "SELL")
        september = replace(
            trade(4, "X", "USD", "1", "1.03", "SELL"), day=august.day.replace(month=9)
        )
        local_sale = replace(trade(5, "Y.SI", "SGD", "1", "11", "SELL"), day=september.day)
        book = Ledger(buys + [august, september, local_sale])
        rates = {"USD": fx(rate="1.50")}
        sales = SalesReport(book, rates)
        self.assertEqual(sales.months, ["2025-09", "2025-08"])
        self.assertEqual(sales.totals["2025-08"].profit, D(".02"))
        self.assertEqual(sales.totals["2025-09"].profit, D("1.02"))
        total = sales.totals["all"]
        self.assertEqual(
            (total.net, total.basis, total.profit), (D("14.10"), D("13.06"), D("1.04"))
        )
        self.assertEqual(sum(r.amounts.profit for r in sales.rows_by_month["all"]), total.profit)
        self.assertEqual(Overview(book, {}, rates, sales).realised, total.profit)

    def test_native_sale_audit_works_when_sgd_rate_is_unavailable(self):
        combined = SalesReport(self.ledger, {"USD": self.rates["USD"]})
        self.assertIsNone(combined.totals["all"].profit)
        self.assertEqual(
            {r.sale.trade.symbol for r in combined.rows_by_month["all"]}, {"NVDA", "VOD.L"}
        )
        native = SalesReport(self.ledger, {}, "GBP")
        self.assertEqual(native.totals["all"].profit, D("1"))
        self.assertEqual([r.sale.trade.symbol for r in native.rows_by_month["all"]], ["VOD.L"])

    def test_flat_closed_foreign_position_needs_fx_for_proceeds(self):
        book = Ledger([trade(1, "Z", "GBP", "2", "10"), trade(2, "Z", "GBP", "2", "10", "SELL")])
        self.assertEqual(reporting_currencies(book), {"GBP"})
        unpriced = SalesReport(book, {}).totals["all"]
        self.assertEqual((unpriced.net, unpriced.basis, unpriced.profit), (None, None, D(0)))
        totals = SalesReport(book, {"GBP": fx("GBP", "1.7")}).totals["all"]
        self.assertEqual((totals.net, totals.basis, totals.profit), (D("34"), D("34"), D(0)))

    def test_sales_report_cache_tracks_changes_to_ledger_and_rate(self):
        app = PortfolioApp.__new__(PortfolioApp)
        app.snap = SimpleNamespace(ledger=self.ledger)
        app.rates = self.rates.copy()
        app.sales_cache_key, app.sales_reports = None, {}
        first = app.sales_report()
        self.assertIs(first, app.sales_report())
        self.assertIsNot(first, app.sales_report("USD"))
        app.rates["USD"] = fx("USD", "1.30", STAMP + timedelta(minutes=10))
        self.assertIs(first, app.sales_report())
        app.rates["USD"] = fx("USD", "1.50")
        self.assertEqual(app.sales_report().totals["all"].profit, D("44.60"))
        app.snap = SimpleNamespace(ledger=Ledger([]))
        self.assertFalse(app.sales_report().months)

    def test_broker_drilldown_attributes_only_units_in_that_brokerage(self):
        ledger = Ledger(
            [
                trade(1, "A", "USD", "2", "10", broker="Syfe"),
                trade(2, "A", "USD", "3", "10", broker="IBKR"),
                trade(3, "A", "USD", "1", "10", "SELL", broker="SYFE"),
                trade(4, "B.SI", "SGD", "2", "2", broker="IBKR"),
            ]
        )
        quotes = {"A": stock("A", "USD", "NMS", "1.025"), "B.SI": stock("B.SI", "SGD", "SES", "2")}
        view = Overview(ledger, quotes, {"USD": fx()})
        groups = {g.label: g for g in view.allocation_groups("Brokerage")}
        self.assertEqual(
            [(e.holding.position.symbol, e.qty) for e in groups["Syfe"].entries], [("A", D(1))]
        )
        self.assertEqual(
            [(e.holding.position.symbol, e.qty) for e in groups["IBKR"].entries],
            [("A", D(3)), ("B.SI", D(2))],
        )
        self.assertEqual(sum(g.value for g in groups.values()), view.value)
        for g in groups.values():
            self.assertEqual(sum(e.value for e in g.entries), g.value)

    def test_every_allocation_group_is_listed_and_none_is_folded_into_other(self):
        names = [f"A{i}" for i in range(12)]
        ledger = Ledger([trade(i + 1, n, "SGD", "1", "1") for i, n in enumerate(names)])
        prices = {n: stock(n, "SGD", f"EX{i}", str(i + 1)) for i, n in enumerate(names)}
        view = Overview(ledger, prices, {})
        groups = view.allocation_groups("Market")
        self.assertEqual(len(groups), 12)
        self.assertFalse([g for g in groups if g.label.startswith("Other")])
        self.assertEqual(sum(g.value for g in groups), view.value)

    def test_two_trade_currencies_on_one_exchange_keep_their_distinct_rates(self):
        ledger = Ledger([trade(1, "A.SI", "USD", "2", "10"), trade(2, "B.SI", "SGD", "1", "10")])
        quotes = {
            "A.SI": stock("A.SI", "USD", "SES", "11"),
            "B.SI": stock("B.SI", "SGD", "SES", "11"),
        }
        view = Overview(ledger, quotes, {"USD": fx()})
        self.assertEqual(dict(view.allocation("Market")), {"SGX": D("39.60")})
        self.assertEqual(dict(view.allocation("Currency")), {"USD": D("28.60"), "SGD": D("11")})

    def setUp(self):
        self.ledger = Ledger(
            [
                trade(1, "NVDA", "USD", "10", "100", fee="2"),
                trade(2, "ME8U.SI", "SGD", "1000", "1.6", fee="2"),
                trade(3, "NVDA", "USD", "2", "115", "SELL", "1"),
                trade(4, "VOD.L", "GBP", "5", "1"),
                trade(5, "VOD.L", "GBP", "5", "1.20", "SELL"),
            ]
        )
        self.quotes = {
            "NVDA": stock("NVDA", "USD", "NasdaqGS", "120"),
            "ME8U.SI": stock("ME8U.SI", "SGD", "SES", "1.50"),
        }
        self.rates = {"USD": fx(), "GBP": fx("GBP", "1.70")}

    def test_all_open_and_closed_currencies_valued_once_at_current_rates(self):
        view = Overview(self.ledger, self.quotes, self.rates)
        self.assertEqual({r.position.symbol for r in view.rows}, {"ME8U.SI", "NVDA"})
        self.assertEqual(view.value, D("2748.00"))
        self.assertEqual(view.basis, D("2644.08"))
        self.assertEqual(view.gain, D("103.92"))
        self.assertEqual(view.realised, D("38.88"))
        self.assertEqual(view.return_pct, view.gain * 100 / view.basis)
        self.assertEqual(sum(view.weight(r) for r in view.rows).quantize(D(".0001")), D("100.0000"))
        self.assertEqual(dict(view.allocation("Market")), {"SGX": D("1500"), "Nasdaq": D("1248")})
        self.assertEqual(dict(view.allocation("Currency")), {"SGD": D("1500"), "USD": D("1248")})
        self.assertEqual(reporting_currencies(self.ledger), {"USD", "GBP", "SGD"})

    def test_new_rate_moves_both_open_basis_and_market_value(self):
        self.rates["USD"] = fx(rate="1.50")
        view = Overview(self.ledger, self.quotes, self.rates)
        self.assertEqual(
            (view.value, view.basis, view.gain, view.realised),
            (D("2940"), D("2804.40"), D("135.60"), D("44.60")),
        )

    def test_missing_fx_or_price_suppresses_only_incomplete_figures(self):
        view = Overview(self.ledger, self.quotes, {"GBP": self.rates["GBP"]})
        self.assertEqual(
            (view.value, view.basis, view.gain, view.realised), (None, None, None, None)
        )
        self.assertEqual(view.rows[0].value, D("1500"))
        self.assertIsNone(view.weight(view.rows[0]))
        self.assertEqual(view.allocation("Market"), [])
        view = Overview(self.ledger, {"ME8U.SI": self.quotes["ME8U.SI"]}, self.rates)
        self.assertEqual((view.value, view.basis, view.realised), (None, D("2644.08"), D("38.88")))
        view = Overview(self.ledger, self.quotes, {"USD": self.rates["USD"]})
        self.assertEqual((view.value, view.basis, view.realised), (D("2748"), D("2644.08"), None))

    def test_sgd_identity_and_realised_only_portfolios(self):
        ledger = Ledger([trade(1, "X.SI", "SGD", "5", "2")])
        view = Overview(ledger, {"X.SI": stock("X.SI", "SGD", "SES", "3")}, {})
        self.assertEqual(
            (view.value, view.gain, view.realised, view.missing_fx), (D("15"), D("5"), 0, [])
        )
        closed = Ledger([trade(1, "X", "USD", "5", "2"), trade(2, "X", "USD", "5", "3", "SELL")])
        view = Overview(closed, {}, {"USD": fx()})
        self.assertEqual((view.rows, view.value, view.realised), ([], 0, D("6.50")))
        self.assertEqual(Overview(Ledger([]), {}, {}).value, 0)

    def test_broker_allocation_multiple_lots_rounds_to_exact_market_value(self):
        ledger = Ledger(
            [
                trade(1, "A", "USD", "1", "5", broker="Syfe"),
                trade(2, "A", "USD", "2", "5", broker="IBKR"),
                trade(3, "A", "USD", "1", "5", "SELL", broker="ibkr"),
                trade(4, "B", "USD", "3", "4", broker="SYFE"),
            ]
        )
        quotes = {"A": stock("A", "USD", "NMS", "1.015"), "B": stock("B", "USD", "NYQ", "2.101")}
        view = Overview(ledger, quotes, {"USD": fx()})
        allocated = dict(view.allocation("Brokerage"))
        self.assertEqual(set(allocated), {"Syfe", "IBKR"})
        self.assertEqual(sum(allocated.values()), view.value)
        for group in ("Market", "Holding", "Currency"):
            self.assertEqual(sum(n for _, n in view.allocation(group)), view.value)


if __name__ == "__main__":
    unittest.main()
