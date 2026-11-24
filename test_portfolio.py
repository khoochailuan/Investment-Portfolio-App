# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Independent acceptance checks. Run 'python -m unittest -v' in the project folder."""

import fixtures_setup  # noqa: F401  (demo seed for tests)
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from io import BytesIO

from market import (
    UTC,
    Quote,
    Listing,
    NoListing,
    SymbolChoice,
    fetch_quote,
    refresh_prices,
    resolve_symbol,
    yahoo_json,
)
from portfolio import (
    Ledger,
    PortfolioError,
    Position,
    Trade,
    break_even,
    cash,
    dollars,
    mark,
    scenario,
    unit_price,
)
from storage import Store


def row(side="BUY", qty="10", price="10", fee="", broker="Syfe", day="2026-01-02", symbol="IBM"):
    return dict(
        date=day, symbol=symbol, side=side, quantity=qty, price=price, broker=broker, fee=fee
    )


def t(i, *args, **kwargs):
    return Trade.from_input(i, row(*args, **kwargs))


def checked(symbol="IBM", currency="USD", price="100.00"):
    listing = Listing(symbol, symbol, "Test exchange", "EQUITY", currency, currency)
    return Quote(symbol, D(price), datetime.now(UTC), "Yahoo Finance", currency, listing)


class AccountingTests(unittest.TestCase):
    def test_fifo_crosses_two_lots_and_keeps_fees(self):
        ledger = Ledger(
            [
                t(1, qty="20", price="150", fee="2"),
                t(2, qty="15", price="160", fee="1.50"),
                t(3, "SELL", "25", "170", "2.50"),
            ]
        )
        sale = ledger.sales[3]
        self.assertEqual(
            (sale.net, sale.basis, sale.profit), (D("4247.50"), D("3802.50"), D("445.00"))
        )
        self.assertEqual([m.qty for m in sale.matches], [D("20"), D("5")])
        self.assertEqual(ledger.positions["IBM"].average, D("160.10"))

    def test_partial_lots_conserve_the_last_cent(self):
        ledger = Ledger(
            [t(1, qty="3", price="0.50", fee="0.01")] + [t(i, "SELL", "1", "1") for i in (2, 3, 4)]
        )
        self.assertEqual(sum(s.basis for s in ledger.sales.values()), D("1.51"))
        self.assertEqual(ledger.realised_by_currency["USD"], D("1.49"))
        self.assertFalse(ledger.positions)

    def test_broker_inventory_is_separate(self):
        with self.assertRaisesRegex(PortfolioError, "available at B"):
            Ledger([t(1, broker="A"), t(2, "SELL", "1", broker="B")])

    def test_same_day_entry_order_and_casefolded_broker(self):
        ledger = Ledger([t(3, "SELL", "5", broker="SYFE"), t(1, broker=" syfe ")])
        self.assertEqual(ledger.positions["IBM"].qty, D("5"))
        with self.assertRaises(PortfolioError):
            Ledger([t(1, "SELL"), t(2)])

    def test_fractional_history_closes_and_reopens(self):
        ledger = Ledger(
            [t(1, qty=".375", price="10"), t(2, "SELL", ".375", "12"), t(3, qty="1.25", price="8")]
        )
        self.assertEqual(ledger.positions["IBM"].basis, D("10.00"))
        self.assertEqual(ledger.realised_by_currency["USD"], D(".75"))

    def test_month_is_sale_date_and_negative_fees_rejected(self):
        ledger = Ledger(
            [
                t(1, qty="2"),
                t(2, "SELL", "1", "9", "0.10", day="2026-02-01"),
                t(3, "SELL", "1", "13", "0.20", day="2026-03-02"),
            ]
        )
        self.assertEqual(
            [ledger.months_by_currency["USD"][k].profit for k in ("2026-02", "2026-03")],
            [D("-1.10"), D("2.80")],
        )
        with self.assertRaises(PortfolioError):
            t(1, fee="-0.50")

    def test_unknown_prices_and_zero_cost_percent(self):
        p = Position("IBM", D("1"), D("10"))
        self.assertIsNone(mark(p, None).profit)
        self.assertEqual(mark(p, D("11")).return_pct, D("10"))
        self.assertIsNone(mark(Position("IBM", D(".000001"), D("0")), D("1")).return_pct)

    def test_invalid_inputs(self):
        for kwargs in [
            dict(qty="NaN"),
            dict(qty="0"),
            dict(price="Infinity"),
            dict(fee="1.001"),
            dict(price="1e100"),
            dict(broker=" "),
            dict(day="2026-02-30"),
            dict(symbol="A/B"),
            dict(side="HOLD"),
            dict(day="9999-12-31"),
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(PortfolioError):
                t(1, **kwargs)


class PlanningTests(unittest.TestCase):
    p = Position("DIS", D("20"), D("2202.00"))

    def test_enquiry_and_round_up(self):
        self.assertEqual(break_even(self.p.basis, self.p.qty, D("2")), D("110.20"))
        self.assertEqual(break_even(D("1"), D("3"), D("0")), D("0.34"))

    def test_minimum_further_shares_and_cash(self):
        leg = dict(quantity="10", price="90", fee="1")
        r = scenario(self.p, [leg], "100", "2", "90", "1")
        self.assertEqual(
            (r.profit, r.exit_price, r.extra, r.further_outlay),
            (D("-105"), D("103.50"), 11, D("991")),
        )
        for n, expected in [(10, D("-6")), (11, D("4"))]:
            p = scenario(
                self.p, [leg, dict(quantity=str(n), price="90", fee="1")], "100", "2", "90", "1"
            )
            self.assertEqual(p.profit, expected)

    def test_multiple_buys_no_mutation(self):
        r = scenario(
            self.p,
            [dict(quantity=".5", price="80", fee="1"), dict(quantity="1", price="90", fee="2")],
            "105",
            "1",
            "92",
            "1",
        )
        self.assertEqual((r.qty, r.basis, r.outlay), (D("21.5"), D("2335"), D("133")))
        self.assertEqual((self.p.qty, self.p.basis), (D("20"), D("2202")))

    def test_infeasible_or_already_break_even(self):
        for price in ("100", "110"):
            self.assertIsNone(scenario(self.p, [], "100", "2", price, "1").extra)
        self.assertEqual(scenario(self.p, [], "120", "2", "125", "10").extra, 0)
        self.assertEqual(scenario(self.p, [], "110.20", "2", "90", "99").further_outlay, 0)

    def test_fractional_money_settles_per_order(self):
        p = Position("X", D(".25"), D("1.00"))
        r = scenario(p, [dict(quantity=".5", price="1.01", fee="")], "1.01", "0", "0.99", "0.01")
        self.assertEqual(r.basis, D("1.51"))
        self.assertEqual(r.profit, D("-0.75"))
        self.assertEqual(r.extra, 38)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.store = Store(self.path, "personal")

    def test_crud_and_restart(self):
        self.store.commit("add", 0, row(), verified_quote=checked())
        self.store.commit("edit", 1, row(qty="12"), 1, verified_quote=checked())
        after_restart = Store(self.path, "personal").snapshot()
        self.assertEqual(after_restart.ledger.positions["IBM"].qty, 12)
        self.store.commit("delete", 2, trade_id=1)
        later = self.store.commit("add", 3, row(), verified_quote=checked())
        self.assertEqual(list(later.ledger.by_id), [2])

    def test_backdated_invalid_changes_leave_file_intact(self):
        self.store.commit("add", 0, row(), verified_quote=checked())
        self.store.commit("add", 1, row("SELL", day="2026-03-01"), verified_quote=checked())
        original = self.store.ledger_file.read_bytes()
        for action, data in [
            ("edit", row(qty="9")),
            ("edit", row(day="2026-04-01")),
            ("delete", None),
        ]:
            with self.subTest(action=action, data=data), self.assertRaises(PortfolioError):
                self.store.commit(action, 2, data, 1, verified_quote=checked())
        self.assertEqual(self.store.ledger_file.read_bytes(), original)

    def test_separate_workspaces(self):
        ledger = Store(self.path, "demo").snapshot().ledger
        self.assertEqual(len(ledger.trades), 14)
        self.assertEqual(ledger.realised_by_currency["USD"], D("578.30"))
        self.assertFalse(self.store.snapshot().ledger.trades)

    def test_concurrent_writers_do_not_lose_updates(self):
        other = Store(self.path, "personal")

        def submit(store):
            try:
                store.commit("add", 0, row(), verified_quote=checked())
                return True
            except PortfolioError:
                return False

        with ThreadPoolExecutor(2) as pool:
            outcome = list(pool.map(submit, (other, self.store)))
        self.assertCountEqual(outcome, [True, False])
        self.assertEqual(len(self.store.snapshot().ledger.trades), 1)

    def test_failed_replace_preserves_preceding_state(self):
        self.store.commit("add", 0, row(), verified_quote=checked())
        original = self.store.ledger_file.read_bytes()
        with (
            patch("storage.os.replace", side_effect=OSError("disk error")),
            self.assertRaises(PortfolioError),
        ):
            self.store.commit("delete", 1, trade_id=1)
        self.assertEqual(self.store.ledger_file.read_bytes(), original)
        self.assertEqual(self.store.snapshot().revision, 1)

    def test_malformed_file_is_preserved(self):
        self.store.ledger_file.write_text("{bad", encoding="utf-8")
        with self.assertRaises(PortfolioError):
            self.store.snapshot()
        self.assertEqual(self.store.ledger_file.read_text(), "{bad")

    def test_late_refresh_keeps_newer_quote(self):
        now = datetime.now(UTC)
        new = Quote("IBM", D("10"), now, "Manual entry")
        old = Quote("IBM", D("9"), now - timedelta(days=1), "Yahoo Finance")
        self.store.save_quotes([new])
        self.assertEqual(self.store.save_quotes([old]), set())
        self.assertEqual(Store(self.path, "personal").quotes()["IBM"].price, D("10"))


class FeedTests(unittest.TestCase):
    def test_transient_connection_failure_retries_and_rate_limit_fails_closed(self):
        from urllib.error import HTTPError

        with (
            patch(
                "market.urlopen", side_effect=[TimeoutError(), BytesIO(b'{"quotes":[]}')]
            ) as fetch,
            patch("market.sleep"),
        ):
            self.assertEqual(yahoo_json("/v1/finance/search", {"q": "ME8U"}), {"quotes": []})
            self.assertEqual(fetch.call_count, 2)
        with (
            patch(
                "market.urlopen", side_effect=HTTPError("", 429, "Too many requests", {}, None)
            ) as fetch,
            self.assertRaisesRegex(PortfolioError, "limiting requests"),
        ):
            resolve_symbol("NVDA")
        fetch.assert_called_once()

    def test_partial_refresh_returns_failures(self):
        def feed(symbol):
            if symbol == "V":
                raise PortfolioError("Unavailable.")
            return Quote(symbol, D("10"), datetime.now(UTC), "Manual entry")

        ok, errors = refresh_prices(["IBM", "V"], feed)
        self.assertEqual(set(ok), {"IBM"})
        self.assertEqual(set(errors), {"V"})

    def test_provider_currency_symbol_and_timestamp(self):
        meta = dict(
            symbol="IBM",
            currency="USD",
            exchangeName="NYSE",
            instrumentType="EQUITY",
            regularMarketTime=1750000000,
            regularMarketPrice=100.25,
        )
        for change, valid in [
            ({}, True),
            ({"currency": None}, False),
            ({"symbol": "V"}, False),
            ({"regularMarketPrice": None}, False),
            ({"regularMarketTime": 4102444800}, False),
        ]:
            payload = {"chart": {"result": [{"meta": dict(meta, **change)}]}}
            with (
                self.subTest(change=change),
                patch("market.urlopen", return_value=BytesIO(json.dumps(payload).encode())),
            ):
                if valid:
                    self.assertEqual(fetch_quote("IBM").price, D("100.25"))
                else:
                    with self.assertRaises(PortfolioError):
                        fetch_quote("IBM")


class GlobalMarketTests(unittest.TestCase):
    def test_local_code_resolves_and_company_name_needs_selection(self):
        def fetch(symbol):
            if symbol == "ME8U.SI":
                return checked(symbol, "SGD", "1.67")
            raise NoListing("No chart")

        with (
            patch("market.fetch_quote", side_effect=fetch),
            patch("market.search_listings", return_value={"ME8U.SI": "Mapletree"}),
        ):
            self.assertEqual(resolve_symbol(" me8u ").currency, "SGD")
        with (
            patch("market.fetch_quote", side_effect=NoListing("No chart")),
            patch("market.search_listings", return_value={"NVDA": "NVIDIA · Nasdaq"}),
        ):
            with self.assertRaises(SymbolChoice) as err:
                resolve_symbol("NVIDIA")
            self.assertIn("NVDA", err.exception.choices)
        with (
            patch("market.fetch_quote", side_effect=NoListing("No chart")),
            patch("market.search_listings", return_value={"ABC.T": "Tokyo", "ABC.L": "London"}),
        ):
            with self.assertRaises(SymbolChoice):
                resolve_symbol("ABC")

    def test_exact_symbol_honours_all_exchange_suffixes_and_leading_zeroes(self):
        for symbol, currency in [
            ("0005.HK", "HKD"),
            ("7203.T", "JPY"),
            ("M&M.NS", "INR"),
            ("BBD-B.TO", "CAD"),
            ("ABC.NEW", "EUR"),
        ]:
            with (
                self.subTest(symbol=symbol),
                patch("market.fetch_quote", return_value=checked(symbol, currency)) as feed,
                patch("market.search_listings") as search,
            ):
                self.assertEqual(resolve_symbol(symbol.lower()).symbol, symbol)
                feed.assert_called_once_with(symbol)
                search.assert_not_called()

    def test_unknown_symbol_provider_outage_and_unsupported_asset(self):
        with (
            patch("market.fetch_quote", side_effect=NoListing("Missing")),
            patch("market.search_listings", return_value={}),
        ):
            with self.assertRaises(NoListing):
                resolve_symbol("FAKEFAKE")
        with (
            patch("market.fetch_quote", side_effect=PortfolioError("network")),
            patch("market.search_listings") as search,
        ):
            with self.assertRaisesRegex(PortfolioError, "network"):
                resolve_symbol("IBM")
            search.assert_not_called()
        for kind in ["INDEX", "CRYPTOCURRENCY", "CURRENCY", "OPTION", "FUTURE"]:
            m = dict(
                symbol="X",
                instrumentType=kind,
                currency="USD",
                exchangeName="Test",
                regularMarketPrice=10,
                regularMarketTime=1750000000,
            )
            with (
                self.subTest(kind=kind),
                patch("market.yahoo_json", return_value={"chart": {"result": [{"meta": m}]}}),
                self.assertRaises(PortfolioError),
            ):
                fetch_quote("X")

    def test_subunit_quotes_and_minor_units(self):
        for raw, major, expected in [
            ("GBp", "GBP", "1.1625"),
            ("ZAc", "ZAR", "1.1625"),
            ("ILA", "ILS", "1.1625"),
            ("KWF", "KWD", "0.11625"),
            ("GBP", "GBP", "116.25"),
            ("HKD", "HKD", "116.25"),
        ]:
            m = dict(
                symbol="VOD.L",
                currency=raw,
                exchangeName="LSE",
                instrumentType="EQUITY",
                regularMarketPrice=116.25,
                regularMarketTime=1750000000,
            )
            with (
                self.subTest(raw=raw),
                patch("market.yahoo_json", return_value={"chart": {"result": [{"meta": m}]}}),
            ):
                q = fetch_quote("VOD.L")
                self.assertEqual(
                    (q.price, q.currency, q.listing.price_unit), (D(expected), major, raw)
                )
        self.assertEqual(dollars(D("3.6"), currency="JPY"), "JPY 4")
        self.assertEqual(dollars(D("0.341"), currency="KWD"), "KWD 0.341")
        self.assertEqual(unit_price(D("1.675"), "SGD"), "S$1.675")

    def test_mixed_currencies_separate_sale_and_month_and_closed_positions(self):
        us = t(1, qty="2", price="50")
        sg = Trade.from_input(
            2, dict(row(qty="100", price="1.60", fee="1", symbol="ME8U.SI"), currency="SGD")
        )
        sg_sale = Trade.from_input(
            3, dict(row("SELL", "100", "1.70", "1", symbol="ME8U.SI"), currency="SGD")
        )
        ledger = Ledger([us, sg, sg_sale, t(4, "SELL", "1", "51")])
        self.assertEqual(dict(ledger.realised_by_currency), {"USD": D("1"), "SGD": D("8")})
        self.assertEqual(ledger.months_by_currency["SGD"]["2026-01"].profit, D("8"))
        self.assertNotIn("ME8U.SI", ledger.positions)
        with self.assertRaises(PortfolioError):
            Ledger([us, Trade.from_input(2, dict(row(), currency="JPY"))])

    def test_saved_trade_requires_verified_identity_and_persists_quote(self):
        with TemporaryDirectory() as d:
            store = Store(Path(d), "personal")
            old = store.ledger_file.read_bytes()
            for q in [
                None,
                Quote("NVIDIA", D("1"), datetime.now(UTC), "Manual entry"),
                checked("NVDA"),
            ]:
                with self.subTest(q=q), self.assertRaises(PortfolioError):
                    store.commit("add", 0, row(symbol="NVIDIA"), verified_quote=q)
                self.assertEqual(store.ledger_file.read_bytes(), old)
            with self.assertRaises(PortfolioError):
                store.commit(
                    "add",
                    0,
                    dict(row(symbol="ME8U.SI"), currency="USD"),
                    verified_quote=checked("ME8U.SI", "SGD"),
                )
            q = checked("ME8U.SI", "SGD", "1.67")
            store.commit(
                "add",
                0,
                dict(row(qty="100", price="1.60", fee="1", symbol=q.symbol), currency=q.currency),
                verified_quote=q,
            )
            store = Store(Path(d), "personal")
            pos = store.snapshot().ledger.positions[q.symbol]
            q2 = store.quotes()[q.symbol]
            self.assertEqual(
                (pos.currency, mark(pos, q2.price).profit, q2.listing.currency),
                ("SGD", D("6"), "SGD"),
            )

    def test_trade_cannot_be_committed_if_either_file_write_fails(self):
        from storage import atomic_json

        for filename in ("quotes.json", "transactions.json"):
            with self.subTest(filename=filename), TemporaryDirectory() as directory:
                store = Store(Path(directory), "personal")
                quote = checked("NVDA")

                def fail_selected_file(path, content):
                    if path.name == filename:
                        raise OSError("simulated write failure")
                    atomic_json(path, content)

                with (
                    patch("storage.atomic_json", side_effect=fail_selected_file),
                    self.assertRaisesRegex(PortfolioError, "write failure"),
                ):
                    store.commit("add", 0, row(symbol="NVDA"), verified_quote=quote)
                self.assertFalse(store.snapshot().ledger.trades)
                self.assertEqual(store.snapshot().revision, 0)
                store.commit("add", 0, row(symbol="NVDA"), verified_quote=quote)
                self.assertEqual(set(store.snapshot().ledger.positions), {"NVDA"})
                self.assertEqual(store.quotes()["NVDA"].price, quote.price)

    def test_two_counters_on_one_exchange_use_their_reported_currencies(self):
        for symbol, currency, kind in [
            ("ME8U.SI", "SGD", "EQUITY"),
            ("TEST.SI", "USD", "ETF"),
            ("EXAMPLE.SI", "SGD", "REIT"),
        ]:
            meta = dict(
                symbol=symbol,
                currency=currency,
                exchangeName="SES",
                instrumentType=kind,
                regularMarketPrice=2.4,
                regularMarketTime=1750000000,
            )
            with (
                self.subTest(symbol=symbol),
                patch("market.yahoo_json", return_value={"chart": {"result": [{"meta": meta}]}}),
            ):
                q = fetch_quote(symbol)
                self.assertEqual((q.symbol, q.currency, q.listing.kind), (symbol, currency, kind))

    def test_legacy_typo_correction_with_dependent_sales(self):
        with TemporaryDirectory() as d:
            store = Store(Path(d), "personal")
            rows = [
                dict(row(symbol="NVIDIA", qty="10", price="100"), id=1),
                dict(row("SELL", "4", "120", symbol="NVIDIA"), id=2),
            ]
            store.ledger_file.write_text(
                json.dumps(dict(schema=1, revision=3, next_id=3, trades=rows))
            )
            data = dict(rows[0], symbol="NVDA", currency="USD")
            q = checked("NVDA", "USD", "110")
            with self.assertRaises(PortfolioError):
                store.commit("edit", 3, data, 1, verified_quote=q)
            s = store.commit("edit", 3, data, 1, verified_quote=q, correct_all=True)
            self.assertEqual(list(s.ledger.positions), ["NVDA"])
            self.assertEqual(s.ledger.positions["NVDA"].qty, 6)
            self.assertEqual(s.ledger.sales[2].profit, D("80"))
            self.assertEqual(store.quotes()["NVDA"].price, 110)
            self.assertEqual(store.snapshot().next_id, 3)

    def test_fractional_share_prices_minimum_matches_exhaustive_oracle(self):
        for currency, entry in [
            ("SGD", "1.621"),
            ("GBP", "0.43251"),
            ("JPY", "86.652"),
            ("KWD", "1.2345"),
        ]:
            for step, bump in [("0.004", "0.035"), ("0.0037", "0.09"), ("0.0019", "0.13")]:
                p = Position(
                    "X", D("7.25"), cash(D(entry) * D("7.25") + D(bump), currency), currency
                )
                target, buy = D(entry), D(entry) - D(step)
                r = scenario(p, [], str(target), "0", str(buy), "0")
                oracle = next(
                    n
                    for n in range(10000)
                    if cash((p.qty + n) * target, currency) - cash(n * buy, currency) >= p.basis
                )
                self.assertEqual(r.extra, oracle, (currency, step))


if __name__ == "__main__":
    unittest.main()
