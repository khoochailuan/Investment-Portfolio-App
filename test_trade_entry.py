# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Checks for dated price entry, average-cost reporting and atomic statement imports."""

import fixtures_setup  # noqa: F401  (demo seed for tests)
import unittest
from datetime import date
from decimal import Decimal as D
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, DecodedStreamObject, NameObject
from import_reconciliation import FEES, NET_CASH, selected_trade_totals, statement_difference
from market import PortfolioError, SymbolChoice
from performance import Series, trade_reference
from portfolio import Ledger, Trade, quantity_from_amount
from storage import Store
from test_portfolio import checked, row, t
from trade_import import ImportProfile, commit_rows, drafts, guess, read_source, review
from valuation import SalesReport

DAY = date(2026, 1, 2)


def lookup(symbol):
    if symbol == "NVIDIA":
        raise SymbolChoice("Select the listing.", {"NVDA": "NVIDIA Corporation"})
    if symbol == "ME8U":
        return checked("ME8U.SI", "SGD")
    return checked(symbol, "SGD" if symbol.endswith(".SI") else "USD")


def prepare(file, name="trades.csv", book=None, broker="Syfe", order="DMY"):
    src = read_source(name, file)
    rows = drafts(src, guess(src.headers), src.broker or broker)
    review(rows, src, order, book or Ledger([]), {}, lookup)
    return rows


def pdf(lines, locked=False):
    writer = PdfWriter()
    page = writer.add_blank_page(700, 800)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Courier"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(
        (
            "BT /F1 10 Tf 30 740 Td 16 TL " + " ".join("(" + s + ") Tj T*" for s in lines) + " ET"
        ).encode()
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    if locked:
        writer.encrypt("test-password")
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


class EntryTests(unittest.TestCase):
    def test_amount_uses_gross_and_fee_is_separate_on_either_side(self):
        for side in ("BUY", "SELL"):
            r = row(side, price="3", fee="1.25")
            r.pop("quantity")
            tr = Trade.from_input(1, dict(r, amount="100.00", note="  May execution  "))
            self.assertEqual(
                (tr.qty, tr.gross, tr.amount, tr.note),
                (D("33.333333"), D(100), D(100), "May execution"),
            )
            self.assertEqual(Trade.from_input(1, tr.record()), tr)

    def test_amount_precision_and_old_records(self):
        self.assertIsNone(Trade.from_input(1, row()).amount)
        self.assertEqual(Trade.from_input(1, row()).note, "")
        for value, price, ccy in [
            ("0", "1", "SGD"),
            ("10.001", "1", "SGD"),
            ("0.01", "800000", "USD"),
            ("1.5", "1", "JPY"),
        ]:
            with self.subTest(value=value), self.assertRaises(PortfolioError):
                quantity_from_amount(value, price, ccy)
        with self.assertRaises(PortfolioError):
            Trade.from_input(1, dict(row(), note="x" * 1001))

    def test_exact_date_cached_close_and_weekend(self):
        from dataclasses import replace

        quote = replace(checked(), session_day=date(2026, 1, 5))
        hist = Series(
            "IBM", "USD", DAY, date(2026, 1, 5), "Yahoo Finance", {DAY: D("149.990005493")}
        )
        self.assertEqual(
            trade_reference(quote, DAY, hist, hist.checked), (D("149.99"), "Yahoo closing price")
        )
        self.assertEqual(
            trade_reference(quote, quote.session_day, None, hist.checked),
            (D(100), "Yahoo latest trade"),
        )
        with patch("performance.raw_history", return_value=hist), self.assertRaises(PortfolioError):
            trade_reference(quote, date(2026, 1, 3), hist, hist.checked)

    def test_avco_is_per_broker_and_weighted_by_units(self):
        trades = [
            t(1, qty="10", price="10", fee="2"),
            t(2, qty="20", price="25", fee="1"),
            t(3, qty="10", price="5", broker="Second"),
            t(4, "SELL", "10", "30", "1"),
        ]
        fifo = Ledger(trades)
        avco = Ledger(trades, "AVCO")
        self.assertEqual(fifo.sales[4].basis, D("102"))
        a = avco.sales[4]
        self.assertEqual(
            (a.basis, a.net, a.profit, a.pool_before, a.matches),
            (D(201), D(299), D(98), (D(30), D(603)), ()),
        )
        self.assertEqual(avco.positions["IBM"].basis, D(452))
        self.assertEqual(avco.positions["IBM"].accounts["second"][0].basis, D(50))
        self.assertEqual(SalesReport(avco, {}, "USD").totals["all"].profit, D(98))
        for book in (avco, fifo):
            self.assertEqual(
                book.sales[4].profit + D(30) * D(30) - book.positions["IBM"].basis, D(546)
            )

    def test_average_pool_rounds_last_cent_and_resets_after_full_exit(self):
        tx = [
            t(1, qty="3", price=".50", fee=".01"),
            t(2, "SELL", "1", "1"),
            t(3, "SELL", "1", "1"),
            t(4, "SELL", "1", "1"),
            t(5, qty="1", price="8"),
        ]
        book = Ledger(tx, "AVCO")
        self.assertEqual([s.basis for s in book.sales.values()], [D(".50"), D(".51"), D(".50")])
        self.assertEqual(book.positions["IBM"].basis, D(8))
        with self.assertRaises(PortfolioError):
            Ledger(tx + [t(6, "SELL", "1", broker="other")], "AVCO")

    def test_workspace_method_persists_and_old_journal_defaults_to_fifo(self):
        with TemporaryDirectory() as td:
            path = Path(td)
            store = Store(path, "personal")
            old = store.snapshot()
            self.assertEqual(old.ledger.method, "FIFO")
            store.commit("method", old.revision, "AVCO")
            self.assertEqual(Store(path, "personal").snapshot().ledger.method, "AVCO")
            self.assertEqual(Store(path, "demo").snapshot().ledger.method, "FIFO")
            with self.assertRaises(PortfolioError):
                store.commit("method", old.revision, "FIFO")
            with self.assertRaises(PortfolioError):
                store.commit("method", 1, "LIFO")
            self.assertEqual(store.snapshot().revision, 1)


class ImportTests(unittest.TestCase):
    def test_saved_profiles_are_per_broker_layout_and_workspace(self):
        headers = ["Date", "Ticker", "Side", "Shares", "Price", "Charges paid"]
        columns = dict(guess(headers), fee=5)

        def mapping(broker, **updates):
            record = dict(
                broker=broker, headers=headers, columns=columns,
                date_order="MDY", row_order="new", signed=False,
            )
            return ImportProfile.from_record(dict(record, **updates))

        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root, "personal")
            self.assertEqual(store.import_profiles(), [])
            store.save_import_profile(mapping("Syfe"))
            store.save_import_profile(mapping("DBS Vickers", date_order="DMY"))
            reordered = ["Charges paid", "Shares", "Side", "Date", "Price", "Ticker"]
            mapped = dict(date=3, symbol=5, side=2, quantity=1, price=4, fee=0)
            syfe, dbs = Store(root, "personal").import_profiles()
            self.assertEqual(syfe.remap(reordered, False), mapped)
            self.assertEqual((syfe.date_order, syfe.row_order, dbs.broker), ("MDY", "new", "DBS Vickers"))
            self.assertIsNone(syfe.remap(reordered, True))
            self.assertIsNone(syfe.remap([*headers[:-1], "Other charges"], False))
            store.save_import_profile(mapping(" syfe ", headers=reordered, columns=mapped))
            self.assertEqual(len(store.import_profiles()), 2)
            self.assertEqual(Store(root, "demo").import_profiles(), [])

        repeated = [*headers, "Charges paid"]
        profile = mapping("Syfe", headers=repeated)
        self.assertEqual(profile.remap(repeated, False), columns)
        self.assertIsNone(profile.remap(list(reversed(repeated)), False))
        with self.assertRaises(PortfolioError):
            mapping("Syfe", columns=dict(columns, fee=40))

    def test_statement_totals_follow_selection_and_keep_currencies_separate(self):
        rows = prepare(
            b"Date,Symbol,Side,Quantity,Unit Price,Fees,Broker,Currency\n"
            b"2026-01-02,IBM,BUY,2,10,0.50,Syfe,USD\n"
            b"2026-01-03,IBM,SELL,1,15,0.25,syfe,USD\n"
            b"2026-01-02,IBM,BUY,5,10,1,DBS Vickers,USD\n"
            b"2026-01-02,ME8U.SI,BUY,10,1.6,0.50,DBS Vickers,SGD\n"
            b"2026-01-02,ME8U.SI,BUY,10,1.6,0.50,DBS Vickers,SGD\n"
        )
        totals = selected_trade_totals(rows)
        self.assertEqual(len(totals), 3)
        self.assertEqual(totals["syfe", "USD"].amounts,
            {FEES: D(".75"), NET_CASH: D("-5.75"), ("BUY", "IBM"): D(2), ("SELL", "IBM"): D(1)},
        )
        self.assertEqual(totals["dbs vickers", "USD"].amounts[FEES], D(1))
        local = totals["dbs vickers", "SGD"]
        self.assertEqual((local.symbols, local.amounts[NET_CASH]), (["ME8U.SI"], D("-16.50")))
        rows[0].selected = False
        revised = selected_trade_totals(rows)["syfe", "USD"].amounts
        self.assertEqual((revised[("BUY", "IBM")], revised[NET_CASH]), (D(0), D("14.75")))
        self.assertIsNone(statement_difference("", D(20)))
        self.assertEqual(statement_difference("(16.50)", D("-16.50"), currency="SGD", signed=True), 0)
        self.assertEqual(statement_difference("0.25", D("0.50"), currency="USD"), D("0.25"))
        for raw, currency in (("-1", None), ("10.5", "JPY"), ("0.001", "USD")):
            with self.assertRaises(PortfolioError):
                statement_difference(raw, D(20), currency=currency)

    def test_aliases_optional_amount_dates_and_leading_zeroes(self):
        data = b'Security Name,Ticker,Trade Date,Action,Quantity,Price,Gross Amount,Fees,Currency,Note\nMapletree,ME8U,03/01/2026,BUY,,1.60,160,1,SGD,"A, B"\nTencent,0700.HK,04/01/2026,SELL,2,20,,,USD,test\n'
        a, b = prepare(data)
        self.assertEqual(
            (a.trade.day, a.trade.symbol, a.trade.qty, a.trade.note),
            (date(2026, 1, 3), "ME8U.SI", D(100), "A, B"),
        )
        self.assertEqual(b.trade.symbol, "0700.HK")
        self.assertEqual(prepare(data, order="MDY")[0].trade.day, date(2026, 3, 1))

    def test_ibkr_only_stock_trades_signs_timezone_order(self):
        data = b'Account Information,Data,Name,Example\nTrades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,Proceeds,Comm/Fee\nTrades,Data,Order,Stocks,USD,IBM,"2026-01-02, 15:00:00",-1,12,12,-0.50\nTrades,Data,Order,Stocks,USD,IBM,"2026-01-02, 09:30:00",2,10,-20,-1\nTrades,Data,Order,Equity Options,USD,OPT,"2026-01-02, 15:00:00",1,1,10,-1\nTrades,SubTotal,,Stocks,USD,IBM,,,,,\n'
        rows = prepare(data)
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            (rows[0].trade.side, rows[0].trade.broker, rows[0].trade.fee), ("SELL", "IBKR", D(".5"))
        )
        self.assertEqual([r["side"] for r, q in commit_rows(rows)], ["BUY", "SELL"])
        with TemporaryDirectory() as td:
            st = Store(Path(td), "personal")
            book = st.commit("import", 0, commit_rows(rows)).ledger
            self.assertEqual(book.sales[2].profit, D(1))

    def test_matching_ibkr_summaries_and_incomplete_detail_are_not_double_counted(self):
        head = "Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,Comm/Fee"
        rows = [
            f'Trades,Data,{level},Stocks,USD,IBM,"2026-01-02, 09:00:00",{qty},10,-1'
            for level, qty in [("Order", 3), ("Execution", 1), ("Execution", 2)]
        ]
        checked_rows = prepare(("\n".join([head, *rows])).encode())
        self.assertEqual([r.trade.qty for r in checked_rows], [D(1), D(2)])
        partial = prepare(("\n".join([head, *rows[:2]])).encode())
        self.assertEqual(
            [(r.trade.qty, r.selected) for r in partial], [(D(3), False), (D(1), True)]
        )

    def test_fee_components_and_asset_validation(self):
        data = b"Date,Symbol,Side,Quantity,Price,Commission,Clearing Fee,GST,Asset Class\n2026-01-02,IBM,BUY,2,12,1,.25,.10,Stock\n2026-01-02,IBM,BUY,2,12,1,0,0,Options\n"
        good, bad = prepare(data)
        self.assertEqual(good.trade.fee, D("1.35"))
        self.assertIn("asset class", bad.error)
        data = (
            data.replace(b"GST,Asset", b"GST,Total Fees,Asset")
            .replace(b".10,Stock", b".10,1.35,Stock")
            .replace(b"0,Options", b"0,1,Options")
        )
        self.assertEqual(prepare(data)[0].trade.fee, D("1.35"))

    def test_duplicate_currency_ambiguity_precision_and_rollback(self):
        data = b"Date,Symbol,Side,Quantity,Price,Fees,Currency\n2026-01-02,IBM,BUY,2,10,1,USD\n2026-01-02,IBM,BUY,2,10,1,USD\n2026-01-03,IBM,SELL,20,12,1,USD\n2026-01-02,NVIDIA,BUY,1,10,0,USD\n2026-01-02,ME8U,BUY,5,1.6,0,USD\n"
        rows = prepare(data)
        self.assertTrue(rows[1].duplicate)
        self.assertFalse(rows[1].selected)
        self.assertIn("NVDA", rows[3].choices)
        self.assertIn("currency", rows[4].error)
        with TemporaryDirectory() as td:
            store = Store(Path(td), "personal")
            with self.assertRaises(PortfolioError):
                store.commit("import", 0, commit_rows(rows))
            self.assertFalse(store.snapshot().ledger.trades)
            self.assertEqual(store.snapshot().next_id, 1)
            rows[2].selected = False
            snap = store.commit("import", 0, commit_rows(rows))
            self.assertEqual(len(snap.ledger.trades), 1)
            new = prepare(data, book=snap.ledger)
            self.assertTrue(new[0].imported)
            with self.assertRaises(PortfolioError):
                store.commit("import", snap.revision, commit_rows(rows))
            self.assertTrue(prepare(data + b"\n", book=snap.ledger)[0].duplicate)
            record = snap.ledger.by_id[1].record()
            record["note"] = "confirmed"
            updated = store.commit("edit", snap.revision, record, 1, verified_quote=checked())
            self.assertEqual(updated.ledger.by_id[1].import_key, snap.ledger.by_id[1].import_key)

    def test_same_day_without_times_can_reverse_and_wrong_gross_flagged(self):
        data = b"Date,Symbol,Side,Quantity,Unit Price,Gross Amount\n2026-01-02,IBM,SELL,1,12,12\n2026-01-02,IBM,BUY,2,10,20\n2026-01-02,IBM,BUY,2,10,25\n"
        rows = prepare(data)
        self.assertEqual([r["side"] for r, _ in commit_rows(rows, True)], ["BUY", "SELL"])
        self.assertIn("differs", rows[2].error)

    def test_text_pdf_table_contract_and_scan_or_lock(self):
        lines = [
            "DBS Vickers",
            "Trade date    Symbol    Side    Quantity    Unit price    Fees    Currency",
            "2026-01-02    ME8U      BUY     10          1.60          1.00    SGD",
        ]
        a = prepare(pdf(lines), "note.pdf")[0]
        self.assertEqual(
            (a.trade.symbol, a.trade.broker, a.trade.fee), ("ME8U.SI", "DBS Vickers", D(1))
        )
        labelled = [
            "Syfe",
            "Trade Date: 2026-01-02",
            "Symbol: IBM",
            "Side: BUY",
            "Quantity: 10",
            "Unit Price: 12.50",
            "Fees: 1.20",
        ]
        a = prepare(pdf(labelled), "contract.pdf")[0]
        self.assertEqual((a.trade.qty, a.trade.gross, a.trade.fee), (D(10), D(125), D("1.2")))
        for data in [pdf([]), pdf(lines, True), pdf(["Holdings as at 2026-01-01"])]:
            with self.assertRaises(PortfolioError):
                read_source("statement.pdf", data)
