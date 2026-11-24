# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Regression checks for income groupings, return attribution, watch alerts and live feed parsing."""

import fixtures_setup  # noqa: F401  (demo seed for tests)
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from analysis_metrics import (
    BENCHMARKS,
    PAYOUT_SOURCES,
    benchmark_return,
    future_payments,
    group_label,
    history_buckets,
    holding_returns,
    holding_series,
    monthly_returns,
    yield_groups,
)
from dividends import Dividend
from interface import PortfolioApp
from market import Listing, Quote, UTC
from market_news import feed_items, feed_url, headlines, refresh_news
from market_watch import (
    AUTO_DIRECTION,
    MailSettings,
    Watchlist,
    check_watches,
    monitor,
    timestamp,
    trailing_pe,
    watch_snapshot,
)
from performance import Observation, Series
from portfolio import Ledger, PortfolioError, Trade
from storage import Store, atomic_json
from valuation import FxRate, complete_sum
from watch_views import watch_distance_text, watch_sort_key

START = date(2025, 1, 1)
NOW = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=12)


def quote(symbol="A", price="10", ccy="SGD", when=NOW):
    return Quote(
        symbol,
        D(price),
        when,
        "Yahoo Finance",
        ccy,
        Listing(symbol, "Example " + symbol, "SES" if ccy == "SGD" else "NMS", "EQUITY", ccy, ccy),
    )


def trade(i, s, ccy, qty, price, days=0, side="BUY"):
    return Trade(i, START + timedelta(days=days), s, side, D(qty), D(price), "Syfe", D(0), ccy)


def payout(i, s, ccy, amount, day, status="Received", ex=None):
    return Dividend(i, s, day, ccy, D(amount), D(0), status, ex, "")


def series(s, c, marks):
    return Series(
        s, c, min(marks), max(marks), "Yahoo Finance", {k: D(str(v)) for k, v in marks.items()}
    )


class IncomeAnalyticsTests(TestCase):
    def setUp(self):
        self.book = Ledger(
            [
                trade(1, "A", "SGD", 10, 10),
                trade(2, "B", "USD", 10, 20),
                trade(3, "A", "SGD", 10, 10, 40),
            ]
        )
        self.quotes = {"A": quote("A"), "B": quote("B", "20", "USD")}
        self.fx = {"USD": FxRate("USD", D("1.25"), NOW, ("USDSGD=X",))}
        self.day = date(2025, 3, 1)
        self.payments = [
            payout(1, "A", "SGD", 10, date(2025, 1, 20), ex=date(2025, 1, 10)),
            payout(2, "B", "USD", 4, date(2025, 1, 21), ex=date(2025, 1, 12)),
            payout(3, "A", "USD", 8, date(2025, 4, 15), "Expected"),
        ]

    def calc(self, by, source=PAYOUT_SOURCES[0], rates=None):
        return yield_groups(
            self.book,
            self.payments,
            {},
            self.quotes,
            self.fx if rates is None else rates,
            self.day,
            by,
            source,
        )

    def test_estimate_rescales_shares_and_groups_weighted_yields(self):
        groups, total, count = self.calc("Asset classes")
        self.assertEqual((count, total.covered, total.holdings), (2, 2, 2))
        self.assertEqual((groups[0].label, groups[0].payout, groups[0].value), ("Equity", 25, 450))
        self.assertEqual(total.percentage(total.value), D(25) * 100 / 450)

    def test_sources_isolate_expected_received_and_payment_currency(self):
        groups, total, _ = self.calc("Currencies", PAYOUT_SOURCES[2])
        self.assertEqual({g.label: g.payout for g in groups}, {"SGD": 10, "USD": None})
        self.assertEqual(self.calc("Holdings", PAYOUT_SOURCES[1])[1].payout, 15)
        self.assertEqual(total.holdings, 2)
        keys, amounts = history_buckets(
            self.payments, self.fx, 2025, "Monthly", "Expected", lambda p: p.currency
        )
        self.assertEqual((len(keys), amounts["USD"][3], sum(amounts["USD"])), (12, 10, 10))

    def test_missing_fx_does_not_turn_into_zero_or_full_coverage(self):
        groups, total, _ = self.calc("Sectors", rates={})
        self.assertIsNone(total.payout)
        self.assertIsNone(groups[0].percentage(groups[0].value))
        self.assertEqual(group_label("B", "Markets", "USD", self.quotes, {}), "Nasdaq")

    def test_rolling_schedule_includes_both_month_boundaries_once(self):
        d = date(2024, 2, 29)
        rows = [
            payout(i, "A", "SGD", n, day, "Expected")
            for i, (n, day) in enumerate(
                [(90, date(2024, 2, 28)), (2, d), (3, date(2025, 2, 27)), (75, date(2025, 2, 28))],
                1,
            )
        ]
        end, bins, amounts = future_payments(rows, {}, d)
        self.assertEqual(
            (end, len(bins), amounts[0], amounts[-1], sum(amounts)),
            (date(2025, 2, 28), 13, 2, 3, 5),
        )
        self.assertEqual(len(future_payments([], {}, date(2025, 1, 1))[1]), 12)
        self.assertIsNone(
            complete_sum(future_payments([payout(1, "A", "JPY", 10, d, "Expected")], {}, d)[2])
        )

    def test_annual_record_history_includes_sold_names_and_separate_status(self):
        rows = self.payments + [
            payout(6, "C", "SGD", 30, date(2024, 12, 31)),
            payout(7, "C", "SGD", 1, date(2025, 1, 2)),
        ]
        years, sums = history_buckets(rows, self.fx, 1999, "Annual", "Received", lambda p: p.symbol)
        self.assertEqual(years, [2024, 2025])
        self.assertEqual(sums["C"], (30, 1))
        self.assertEqual(sums["B"], (0, 5))

    def test_brokerage_spelling_is_reused_on_trades_and_dividends(self):
        with TemporaryDirectory() as folder:
            store = Store(Path(folder), "personal")
            q = quote()
            row = trade(1, "A", "SGD", 10, 10).record()
            row["broker"] = "DBS Vickers"
            snapshot = store.commit("add", 0, row, verified_quote=q)
            snapshot = store.commit(
                "add", snapshot.revision, dict(row, broker=" dbs    VICKERS "), verified_quote=q
            )
            self.assertEqual([t.broker for t in snapshot.ledger.trades], ["DBS Vickers"] * 2)
            self.assertEqual(snapshot.ledger.positions["A"].accounts.keys(), {"dbs vickers"})
            store.save_payment(
                store.payments().revision,
                dict(payout(1, "A", "SGD", "1", START).record(), broker="DBS   VICKERS"),
            )
            self.assertEqual(store.payments().rows[1].broker, "DBS Vickers")


class ReturnAnalyticsTests(TestCase):
    def test_monthly_return_uses_prior_close_and_excludes_new_investments(self):
        def obs(d, mark, invested, paid=0):
            return Observation(date.fromisoformat(d), D(mark), D(invested), D(paid))

        rows = [
            obs("2025-01-01", 100, 100),
            obs("2025-01-31", 110, 100),
            obs("2025-02-14", 210, 200),
            obs("2025-02-28", 226, 200),
        ]
        months = monthly_returns(rows)
        self.assertEqual([m[1] for m in months], [D(10), D(10)])
        self.assertEqual(months[1][2], date(2025, 1, 31))
        self.assertEqual(monthly_returns(rows, date(2025, 2, 1))[0][1], 10)

    def test_holdings_include_actual_trades_dividend_and_fx(self):
        d1, d2 = START, START + timedelta(days=1)
        h = {
            "A": series("A", "USD", {d1: 10, d2: 11}),
            "USDSGD=X": series("USDSGD=X", "SGD", {d1: 1, d2: 1.2}),
        }
        book = Ledger(
            [
                trade(1, "A", "USD", 10, 10),
                trade(2, "B", "SGD", 2, 1),
                trade(3, "B", "SGD", 2, 1, 1, "SELL"),
            ]
        )
        tracks = holding_series(book, [payout(1, "A", "USD", 2, d2)], h, d2)
        self.assertEqual(set(tracks), {"A"})
        self.assertEqual(holding_returns(tracks, h, "1 day", d2), [("A", D("34.4"))])
        self.assertEqual(holding_returns(tracks, {}, "1 day", d2), [("A", None)])

    def test_benchmark_price_returns_use_historical_fx_and_reject_unmatched_splits(self):
        a, b = START, START + timedelta(days=7)
        hist = {
            "SPY": series("SPY", "USD", {a: 100, b: 110}),
            "USDSGD=X": series("USDSGD=X", "SGD", {a: 1.2, b: 1.5}),
        }
        opt = next(iter(BENCHMARKS))
        self.assertEqual(benchmark_return(hist, opt, a, b), D("37.5"))
        self.assertIsNone(benchmark_return({"SPY": hist["SPY"]}, opt, a, b))
        hist["SPY"].splits = (b,)
        self.assertIsNone(benchmark_return(hist, opt, a, b))


class WatchAlertsTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name), "personal")
        self.w = Watchlist(self.store)

    def tick(self, price, i, watch=None, mail=None):
        when = NOW + timedelta(seconds=i)
        return check_watches(
            watch or self.w, lambda s, p, n: (quote(s, str(price), when=when), {}), when, mail
        )

    def test_refresh_preferences_survive_reopen_and_quote_checks(self):
        self.w.set(quote(), "10")
        legacy = self.w.snapshot()
        legacy.pop("refresh_seconds")
        atomic_json(self.w.path, legacy)
        self.assertEqual(self.w.snapshot()["refresh_seconds"], 60)
        for seconds in (10, 30, 60, 120, 180):
            self.w.set_refresh_interval(seconds)
            self.tick(11, seconds)
            self.assertEqual(Watchlist(self.store).snapshot()["refresh_seconds"], seconds)
        with self.assertRaises(PortfolioError):
            self.w.set_refresh_interval(300)
        self.assertEqual(self.w.snapshot()["refresh_seconds"], 180)
        self.assertEqual(Watchlist(Store(self.store.root, "demo")).snapshot()["refresh_seconds"], 60)

    def test_watch_clock_waits_until_the_interval_after_a_manual_check(self):
        now = 100
        delays, checks = [], []
        watch = SimpleNamespace(store=self.store)
        app = SimpleNamespace(
            connected=True, watch=watch, watch_last=now, tab="Overview",
            watch_state=dict(refresh_seconds=10, items={}), load_watch=lambda: None,
        )

        async def advance(seconds):
            nonlocal now
            now += seconds
            delays.append(seconds)
            if len(delays) == 1:
                app.watch_last = 104
            if len(delays) == 3:
                app.connected = False

        app.request_watch = lambda: checks.append(now)
        with patch("interface.monotonic", side_effect=lambda: now), patch("interface.asyncio.sleep", advance):
            asyncio.run(PortfolioApp.watch_prices(app, watch))
        self.assertEqual(delays, [10, 4, 10])
        self.assertEqual(checks, [114])

    def test_headless_monitor_uses_the_saved_interval(self):
        self.w.set_refresh_interval(30)
        with (
            patch("market_watch.check_watches", return_value=[]),
            patch("market_watch.monotonic", side_effect=[20, 23]),
            patch("market_watch.sleep", side_effect=KeyboardInterrupt) as pause,
            redirect_stdout(StringIO()),
        ):
            with self.assertRaises(KeyboardInterrupt):
                monitor(self.store.root)
        pause.assert_called_once_with(27)

    def test_fast_quotes_skip_expensive_stats_during_their_retry_window(self):
        with (
            patch("market_watch.quote_and_metadata", return_value=(quote(), {})) as prices,
            patch("market_watch.trailing_pe", side_effect=PortfolioError("Try again")) as pe,
            patch("market_watch.trailing_fundamentals", side_effect=PortfolioError("Try again")) as funds,
            patch("market_watch.trailing_chart", side_effect=PortfolioError("Try again")) as chart,
        ):
            _, stats = watch_snapshot("A", {}, NOW)
            _, stats = watch_snapshot("A", stats, NOW + timedelta(seconds=10))
            self.assertEqual((prices.call_count, pe.call_count, funds.call_count, chart.call_count), (2, 1, 1, 1))
            watch_snapshot("A", stats, NOW + timedelta(minutes=5))
            self.assertEqual((prices.call_count, pe.call_count, funds.call_count, chart.call_count), (3, 2, 2, 2))

    def test_both_directions_deduplicate_rearm_and_survive_restart(self):
        for direction, prices in [
            ("At or above", (9, 10, 11, 9, 10)),
            ("At or below", (11, 10, 9, 11, 10)),
        ]:
            with self.subTest(direction=direction):
                old = self.w.snapshot()["items"].get("A")
                self.w.set(quote(), "10", direction, version=old["version"] if old else None)
                found = [len(self.tick(p, i, Watchlist(self.store))) for i, p in enumerate(prices)]
                self.assertEqual(found, [0, 1, 0, 0, 1])
                self.assertEqual(self.tick(10, 4), [])
                v = self.w.snapshot()["items"]["A"]["version"]
                self.w.remove("A", v)
        self.assertEqual(len(self.w.snapshot()["events"]), 4)
        self.w.mark_read({self.w.snapshot()["events"][0]["id"]})
        self.assertTrue(self.w.snapshot()["events"][0]["read"])

    def test_automatic_target_uses_newest_quote_and_keeps_its_crossing_direction(self):
        self.w.set(quote(), "9", AUTO_DIRECTION)
        initial = self.w.snapshot()["items"]["A"]
        self.assertEqual(initial["direction"], "At or below")
        self.assertFalse(initial["email"])
        event, = self.tick(8, 1)
        self.assertEqual(event["delivery"], "In app")
        self.w.set(quote(), "9", AUTO_DIRECTION, version=initial["version"])
        after = self.w.snapshot()["items"]["A"]
        self.assertTrue(after["met"])
        self.assertEqual(after["direction"], "At or below")
        # The cached quote is 8, though the editor opened when it was 10.
        self.w.set(quote(), "9.50", AUTO_DIRECTION, version=after["version"])
        self.assertEqual(self.w.snapshot()["items"]["A"]["direction"], "At or above")
        self.assertEqual(len(self.tick("9.50", 2)), 1)

    def test_watch_distance_and_order_share_a_currency_independent_percentage(self):
        near = quote("A", "9.75")
        far = quote("B", "102", "USD")
        self.assertEqual(watch_distance_text(near, "10"), ("S$0.25", "2.50% below target"))
        self.assertEqual(watch_distance_text(far, "100"), ("US$2.00", "2.00% above target"))
        self.assertEqual(watch_distance_text(far, "102"), ("US$0.00", "0.00% · At target"))
        self.assertEqual(watch_distance_text(far, None), ("—", ""))
        items = {
            "A": dict(quote=near.record(), stats={}, target="10"),
            "B": dict(quote=far.record(), stats={}, target="100"),
            "C": dict(quote=quote("C").record(), stats={}, target=None),
        }
        sorted_symbols = sorted(items, key=lambda s: watch_sort_key("Closest to watch price", s, items[s]))
        self.assertEqual(sorted_symbols, ["B", "A", "C"])

    def test_stale_wrong_currency_and_demo_quotes_never_alert(self):
        self.w.set(quote(), "10")
        for q in [
            replace(quote(), asof=NOW - timedelta(days=5)),
            replace(quote(), source="Demo snapshot"),
            quote(ccy="USD"),
        ]:
            check_watches(self.w, lambda s, p, n: (q, {}), NOW)
        self.assertEqual(self.w.snapshot()["events"], [])

    def test_target_edit_during_fetch_ignored_and_equal_price_initially_triggers(self):
        self.w.set(quote(), "9")
        old = self.w.snapshot()["items"]["A"]
        self.w.set(quote(), "10", version=old["version"])
        self.assertEqual(self.w.observe({"A": (quote(), {})}, {}, {"A": old["version"]}, NOW), [])
        self.assertEqual(len(self.tick(10, 0)), 1)
        with self.assertRaises(PortfolioError):
            self.w.remove("A", old["version"])
        with self.assertRaises(PortfolioError):
            self.w.set(quote(), "0")

    def test_email_retries_persist_and_concurrent_workers_claim_one_send(self):
        self.w.set(quote(), "10", email=True)
        mail = MagicMock()
        mail.send.side_effect = OSError()
        self.tick(10, 0, mail=mail)
        self.assertEqual(self.w.snapshot()["events"][0]["delivery"], "Retry")
        self.w.deliver(mail.send, NOW + timedelta(minutes=1))
        self.assertEqual(mail.send.call_count, 1)
        mail.send.side_effect = None

        def deliver(i):
            Watchlist(Store(Path(self.temp.name), "personal")).deliver(
                mail.send, NOW + timedelta(minutes=11)
            )

        with ThreadPoolExecutor(2) as pool:
            list(pool.map(deliver, range(2)))
        self.assertEqual(mail.send.call_count, 2)
        self.assertEqual(self.w.snapshot()["events"][0]["delivery"], "Sent")
        self.assertEqual(self.w.snapshot()["events"][0]["when"], timestamp(NOW))

    def test_metadata_converts_pence_and_pe_is_dated(self):
        q = quote("UK.L", "2", "GBP")
        q = replace(q, listing=replace(q.listing, price_unit="GBp"))
        meta = dict(fiftyTwoWeekLow=120, fiftyTwoWeekHigh=245, regularMarketChangePercent=-2)
        pe = dict(
            timeseries={
                "result": [
                    {
                        "meta": {"symbol": ["UK.L"]},
                        "trailingPeRatio": [
                            {"asOfDate": "2025-01-31", "reportedValue": {"raw": 10.2}},
                            {"asOfDate": "2025-01-12", "reportedValue": {"raw": 9}},
                        ],
                    }
                ]
            }
        )
        with (
            patch("market_watch.quote_and_metadata", return_value=(q, meta)),
            patch("market_watch.yahoo_json", return_value=pe),
        ):
            _, stats = watch_snapshot("UK.L", {}, NOW)
            self.assertEqual(
                (stats["low"], stats["high"], stats["pe"], stats["pe_asof"]),
                ("1.2", "2.45", "10.2", "2025-01-31"),
            )
            self.assertEqual(trailing_pe("OTHER", NOW), (None, None))

    def test_old_workspace_blank_and_demo_mail_disabled(self):
        self.assertEqual(self.w.snapshot()["items"], {})
        demo = Watchlist(Store(Path(self.temp.name), "demo"))
        demo.set(quote(), "10", email=True)
        self.assertFalse(demo.snapshot()["items"]["A"]["email"])
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(MailSettings.from_env())

    def test_email_uses_tls_and_does_not_store_password(self):
        settings = MailSettings(
            "smtp.example",
            587,
            "from@example.org",
            "to@example.org",
            "starttls",
            "user",
            "test-password",
        )
        with patch("market_watch.smtplib.SMTP") as factory:
            settings.send(
                dict(symbol="A", message="Reached target", id="event1", when=timestamp(NOW))
            )
            client = factory.return_value
            client.starttls.assert_called_once()
            client.login.assert_called_once_with("user", "test-password")
            msg = client.send_message.call_args.args[0]
            self.assertIn("Reached target", msg.get_content())
            self.assertNotIn("test-password", msg.as_string())


class NewsTests(TestCase):
    body = b"<rss><channel><item><title>Company update</title><link>https://example.com/story</link><pubDate>Fri, 14 Nov 2025 12:00:00 GMT</pubDate></item><item><title>Bad link</title><link>file:///bad</link></item></channel></rss>"

    def test_sources_and_story_deduplication(self):
        articles = feed_items(self.body, "Yahoo Finance")
        self.assertEqual(len(articles), 1)
        self.assertIn("region=SG", feed_url("ME8U.SI")[0])
        with self.assertRaises(PortfolioError):
            feed_url("@business")
        merged = headlines({"A": dict(articles=articles), "B": dict(articles=articles)}, ["B", "A"])
        self.assertEqual((len(merged), merged[0]["symbols"]), (1, {"A", "B"}))

    def test_news_cache_preserves_headlines_during_outage(self):
        with TemporaryDirectory() as folder:
            store = Store(Path(folder), "personal")
            fetch = MagicMock(return_value=feed_items(self.body, "Example"))
            refresh_news(store, ["A"], fetcher=fetch, now=NOW)
            refresh_news(store, ["A"], fetcher=fetch, now=NOW + timedelta(minutes=5))
            self.assertEqual(fetch.call_count, 1)
            fetch.side_effect = PortfolioError("Feed unavailable")
            state = refresh_news(store, ["A"], True, fetch, NOW + timedelta(minutes=6))
            self.assertEqual(state["A"]["error"], "Feed unavailable")
            self.assertEqual(len(headlines(state, ["A"])), 1)
