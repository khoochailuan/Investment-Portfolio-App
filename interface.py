# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Session navigation, transaction forms, quote refresh and drill-down dialogs."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from time import monotonic

import flet as ft

from frost_theme import frost_dialog

import analysis_views as views
import insights_views as insights
import watch_views
import tools_views
from tools_views import DEPOSIT
import copilot
from analytics import risk_report
from market_watch import DEFAULT_WATCH_INTERVAL, Watchlist, check_watches, MailSettings
from market_news import read_feeds, refresh_news
from analysis_metrics import BENCHMARKS
from overview_page import overview
from frost_shell import FrostShell, brand, dimensions, page_parts
from frost_glass import frost_header
from frost_theme import FONTS, FROST, glass, menu
from dividends import PaymentBook, today_sg
from market import UTC, Quote, fetch_named_quote, refresh_prices
from portfolio import DATE_FORMAT, DATE_HINT, PortfolioError, display_date, dollars, unit_price, units
from performance import fetch_history, fx_key, history_inputs
from storage import Store
from split_views import edit_split, split_panel
from trade_entry import TradeEditor
from import_views import ImportEditor
from valuation import SGD, Overview, SalesReport, fetch_sgd_rate, reporting_currencies
from widgets import (
    NAVY,
    MUTED,
    PAPER,
    RED,
    WHITE,
    app_theme,
    choose,
    empty,
    field,
    grid,
    heading,
    sticky_grid,
    tag,
    pager,
    panel,
    primary,
    secondary,
    text,
    tone,
)

TABS = {
    "Overview": ft.Icons.SPACE_DASHBOARD_OUTLINED,
    "Transactions": ft.Icons.SWAP_VERT,
    "Realised P/L": ft.Icons.BAR_CHART,
    "Dividends": ft.Icons.PAID_OUTLINED,
    "Performance": ft.Icons.QUERY_STATS,
    "Risk": ft.Icons.SHIELD_OUTLINED,
    "Rebalance": ft.Icons.BALANCE,
    "Stock inquiry": ft.Icons.SEARCH,
    "Recovery planner": ft.Icons.TUNE,
    "Watchlist": ft.Icons.BOOKMARK_BORDER,
    "Market news": ft.Icons.NEWSPAPER,
    "Digest": ft.Icons.SUMMARIZE_OUTLINED,
    "Copilot": ft.Icons.AUTO_AWESOME,
}
# Tabs that read cached daily history for their calculations.
HISTORY_TABS = ("Performance", "Dividends", "Risk", "Digest", "Copilot", "Overview", "Stock inquiry")


class PortfolioApp:
    def __init__(self, page: ft.Page, root: Path, *, snapshot_demo=False):
        self.page, self.root = page, root
        self.snapshot_demo = snapshot_demo
        self.shell = None
        page.fonts = FONTS
        self.workspace, self.tab = "demo", "Overview"
        self.symbol, self.month = None, "all"
        self.search, self.side, self.inquiry_fees = "", "ALL", {}
        self.sales_currency = None
        self.trade_page = self.sale_page = self.lot_page = 0
        self.refreshing = False
        self.refresh_report = ""
        self.editor = None
        self.fx_clock = self.watch_clock = None
        self.connected = True
        self.render_width = page.width
        self.import_picker = ft.FilePicker()
        page.services.append(self.import_picker)
        page.on_resize = self.resize_charts
        page.on_disconnect = self.stop_refresh_clocks
        page.on_connect = self.start_refresh_clocks
        page.title = "Folio Ledger"
        page.theme_mode = ft.ThemeMode.LIGHT
        page.theme = app_theme()
        page.bgcolor, page.padding = PAPER, 0
        page.scroll = ft.ScrollMode.AUTO
        page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
        try:
            # When a personal ledger exists, return to it on the next launch.
            if (root / "personal" / "transactions.json").is_file():
                personal = Store(root, "personal")
                if (
                    personal.snapshot().ledger.trades
                    or (personal.directory / "watchlist.json").is_file()
                ):
                    self.workspace = "personal"
            self.select(self.workspace)
            self.start_refresh_clocks()
        except (PortfolioError, OSError) as exc:
            self.file_error(exc)

    @property
    def content_width(self):
        return dimensions(self.page.width)[3]

    @property
    def table_width(self):
        return max(220, int(self.content_width - 42))

    @property
    def book(self):
        return self.snap.ledger

    def select(self, workspace):
        try:
            store = Store(self.root, workspace)
            snap, prices = store.snapshot(), store.quotes()
        except (PortfolioError, OSError) as exc:
            self.file_error(exc)
            return
        self.editor = None
        self.store, self.workspace = store, workspace
        self.snap, self.prices = snap, prices
        self.rates, self.fx_cache_error, self.rates_expanded = {}, "", False
        self.payments, self.histories = PaymentBook(0, 1, {}), {}
        self.history_busy, self.history_errors, self.history_tried = set(), {}, {}
        self.income_year, self.payment_filter, self.payment_page = (
            today_sg().year,
            "All",
            0,
        )
        self.performance_period, self.perf_cache_key = "Since inception", None
        self.growth_measure = "Gain in SGD"
        self.payout_source = "Current-share estimate"
        self.yield_metric, self.yield_group = "Yield (%)", "Holdings"
        self.income_mix, self.record_group = "Holdings", "Holdings"
        self.income_interval = "Monthly"
        self.income_period = "12 trailing months"
        self.yield_page = self.holding_return_page = 0
        self.dynamics_period, self.holding_period, self.benchmark = (
            "1 year",
            "Since inception",
            "No benchmark",
        )
        self.load_insights()
        self.load_rates()
        self.fx_busy, self.fx_tried, self.fx_errors = set(), {}, {}
        self.quote_busy, self.quote_tried, self.quote_errors = set(), {}, {}
        self.update_holding_panel = None
        self.holdings_order = "Market value"
        self.allocation_group = (
            "Sector"
            if any(self.histories[s].sector for s in self.book.positions if s in self.histories)
            else "Market"
        )
        self.tab, self.month, self.symbol = "Overview", "all", None
        self.search, self.side, self.inquiry_fees = "", "ALL", {}
        self.sales_currency = None
        self.stock_chart_page, self.realised_year = 0, None
        self.sales_cache_key, self.sales_reports = None, {}
        self.trade_page = self.sale_page = self.lot_page = 0
        self.refresh_report, self.refreshing = "", False
        self.watch_sort, self.inquiry_period = "Symbol", "6M"
        self.risk_group, self.rb_mode = "Sector", "Full rebalance"
        self.rb_plan, self.rb_error, self.rb_cash = None, "", "0"
        self.rb_action, self.rb_sales, self.rb_fractional, self.rb_min = DEPOSIT, True, False, "0"
        self.rb_group, self.rb_view = "Holding", "After trades"
        try:
            self.rb_targets, self.rb_band = store.rebalance_settings()
        except (PortfolioError, OSError):
            self.rb_targets, self.rb_band = {}, "2"
        self.copilot_log, self.copilot_busy = [], False
        self.watch = Watchlist(store)
        self.watch_state = dict(
            items={}, events=[], errors={}, last_check=None, refresh_seconds=DEFAULT_WATCH_INTERVAL,
        )
        self.watch_again = False
        self.watch_busy, self.watch_last, self.watch_error = False, None, ""
        self.watch_page = self.alert_page = self.news_page = 0
        self.dismissed_insights, self.mover_news_tried = set(), set()
        self.autoloaded = set()
        self.news_scope, self.news_feeds, self.news_busy, self.news_error = (
            None,
            {},
            set(),
            "",
        )
        self.news_symbol = None
        try:
            self.news_feeds = read_feeds(store)
        except PortfolioError:
            pass
        self.load_watch()
        self.notified_events = {e["id"] for e in self.watch_state["events"]}
        if not store.demo:
            self.request_watch()
        self.request_fx()
        self.render()
        self.restart_watch_clock()

    def load_watch(self):
        try:
            self.watch_state = self.watch.snapshot()
            self.watch_error = ""
        except PortfolioError as exc:
            self.watch_error = str(exc)

    def request_watch(self, force=False):
        if not self.connected:
            return
        if self.watch_busy:
            self.watch_again |= force
            return
        if not self.watch_state["items"]:
            return
        now = monotonic()
        if (
            not force and self.watch_last is not None
            and now - self.watch_last < self.watch_state["refresh_seconds"]
        ):
            return
        self.watch_busy, self.watch_last = True, now
        self.page.run_task(self.update_watch, self.watch)
        self.update_watch_display(content=False)

    def update_watch_display(self, *, content=True):
        if self.connected and self.tab == "Watchlist" and self.editor is None and self.update_watch_panel:
            self.update_watch_panel(content=content)
            self.page.update()

    async def set_watch_refresh(self, watch, seconds):
        if self.watch is not watch:
            return
        try:
            self.watch_state = watch.set_refresh_interval(seconds)
        except PortfolioError as exc:
            self.notice(str(exc))
            return
        self.restart_watch_clock()
        self.update_watch_display()

    async def update_watch(self, watch):
        try:
            await asyncio.to_thread(check_watches, watch, mail=MailSettings.from_env())
            error = ""
        except (OSError, PortfolioError) as exc:
            error = str(exc)
        if self.watch is not watch:
            return
        self.watch_busy = False
        # Read the current file after the request so a new interval cannot be overwritten
        # in the interface by the preferences from the start of a slow download.
        self.load_watch()
        if error:
            self.watch_error = error
        if self.connected:
            self.update_watch_display()
            new = [
                e
                for e in self.watch_state["events"]
                if not e["read"] and e["id"] not in self.notified_events
            ]
            self.notified_events.update(e["id"] for e in self.watch_state["events"])
            if new:
                self.notice(
                    new[0]["message"]
                    if len(new) == 1
                    else f"{len(new)} price targets reached. Open Watchlist to see your alerts."
                )

        if self.watch_again:
            self.watch_again = False
            self.request_watch(force=True)

    def request_news(self, force=False):
        keys = set(watch_views.news_keys(self)) - self.news_busy
        if not keys:
            return
        self.news_busy.update(keys)
        self.page.run_task(self.update_news, self.store, keys, force)
        if force and self.tab == "Market news":
            self.render()

    def autoload(self, view):
        """Fetch what a new holding needs, once, so adding trades is the only step.

        Prices for holdings that have no quote yet, and their market history. A symbol is tried
        once per session, so a failed lookup is not retried in a loop. The Refresh button still
        works for anything later.
        """
        if self.refreshing or not view.missing_quotes:
            return
        fresh = set(view.missing_quotes) - self.autoloaded
        if not fresh:
            return
        self.autoloaded |= fresh
        self.page.run_task(self.refresh, None)
        self.request_history()

    def request_mover_news(self, symbols):
        """Fetch headlines once per session for holdings that moved sharply."""
        keys = (set(symbols) - self.news_busy) - self.mover_news_tried
        if not keys:
            return
        self.mover_news_tried |= keys
        self.news_busy.update(keys)
        self.page.run_task(self.update_news, self.store, keys, False)

    async def update_news(self, store, keys, force):
        try:
            await asyncio.to_thread(refresh_news, store, keys, force)
            feeds, error = await asyncio.to_thread(read_feeds, store), ""
        except (OSError, PortfolioError) as exc:
            feeds, error = {}, str(exc)
        if self.store is not store:
            return
        self.news_busy.difference_update(keys)
        self.news_feeds.update(feeds)
        self.news_error = error
        if self.connected and self.tab in ("Market news", "Overview") and self.editor is None:
            self.render()

    def file_error(self, exc):
        self.shell = None
        viewport, _, gutter, width = dimensions(self.page.width)
        card = glass(
            ft.Column(
                [
                    brand(),
                    heading(
                        "Unable to open this portfolio",
                        "Check the data folder, then reopen the portfolio.",
                        size=30,
                    ),
                    text(str(exc), 15, RED),
                    ft.Row(
                        [
                            secondary("Open demo", lambda e: self.select("demo")),
                            secondary("Open my portfolio", lambda e: self.select("personal")),
                        ],
                        wrap=True,
                    ),
                ],
                spacing=12,
            ),
            padding=20,
            width=min(650, width),
        )
        stage = ft.Container(
            card,
            width=viewport,
            alignment=ft.Alignment.CENTER,
            padding=ft.Padding.symmetric(horizontal=gutter, vertical=28),
        )
        self.page.controls.clear()
        self.page.add(frost_header(stage, viewport, compact=True))

    def reload(self):
        self.snap, self.prices = self.store.snapshot(), self.store.quotes()
        self.load_rates()
        self.load_insights()

    def load_insights(self):
        try:
            self.payments = self.store.payments()
            self.payment_error = ""
        except PortfolioError as exc:
            self.payment_error = str(exc)
        try:
            self.histories = self.store.histories()
            self.history_file_error = ""
        except (OSError, PortfolioError) as exc:
            self.history_file_error = str(exc)

    def request_history(self, force=False):
        today = today_sg()
        payments = tuple(self.payments.rows.values())
        dates = [d.day for d in payments] + [t.day for t in self.book.trades]
        if not dates:
            return
        # Daily history includes a buffer for holidays before the first cash flow.
        start = max(date(1970, 1, 15), min(dates + [today])) - timedelta(days=14)
        required = history_inputs(self.book, payments)
        if self.tab == "Performance" and self.benchmark in BENCHMARKS:
            symbol, ccy = BENCHMARKS[self.benchmark]
            required[symbol] = ccy
            if ccy != SGD:
                required[fx_key(ccy)] = SGD
        pending = {}
        for key, code in required.items():
            h = self.histories.get(key)
            current = (
                h
                and h.currency == code
                and (h.source == "Demo history" or (h.start <= start and h.checked >= today))
            )
            if key not in self.history_busy and (
                force or (not current and self.history_tried.get(key) != (start, today))
            ):
                pending[key] = code
        if pending:
            self.history_tried.update(dict.fromkeys(pending, (start, today)))
            self.history_busy.update(pending)
            self.page.run_task(self.update_history, self.store, pending, start, today)

    async def update_history(self, store, required, start, end):
        def get(key):
            return fetch_history(key, required[key], start, end)

        fresh, errors = await asyncio.to_thread(refresh_prices, required, get)
        try:
            available = await asyncio.to_thread(store.save_histories, list(fresh.values()))
            cache_error = ""
        except (OSError, PortfolioError) as exc:
            available, cache_error = fresh, str(exc)
        if self.store is not store:
            return
        self.histories.update(available)
        self.history_file_error = cache_error
        self.history_busy.difference_update(required)
        for key in required:
            self.history_errors.pop(key, None)
        self.history_errors.update(errors)
        self.perf_cache_key = None
        if (
            self.connected
            and self.editor is None
            and (
                self.tab in HISTORY_TABS and self.tab != "Copilot"
            )
        ):
            self.render()

    def merge_rates(self, observed):
        for code, rate in observed.items():
            if code not in self.rates or rate.asof >= self.rates[code].asof:
                self.rates[code] = rate

    def sales_report(self, native_currency=None):
        # A different ledger or an actual rate change invalidates the cached sale lines.
        key = (
            self.book,
            tuple(
                (c, self.rates[c].rate if c in self.rates else None)
                for c in sorted(self.book.months_by_currency)
            ),
        )
        if key != self.sales_cache_key:
            self.sales_cache_key, self.sales_reports = key, {}
        if native_currency not in self.sales_reports:
            self.sales_reports[native_currency] = SalesReport(
                self.book, self.rates, native_currency
            )
        return self.sales_reports[native_currency]

    def overview_view(self):
        """Current valuation of every holding. One place so all screens agree."""
        sectors = {k: v.sector for k, v in self.histories.items() if v.sector}
        return Overview(self.book, self.prices, self.rates, self.sales_report(), sectors)

    def copilot_context(self):
        view = self.overview_view()
        try:
            band = Decimal(self.rb_band)
        except Exception:
            band = Decimal(2)
        targets = {}
        try:
            targets = {s: Decimal(v) for s, v in self.rb_targets.items()}
        except Exception:
            pass
        return copilot.Context(
            view, self.sales_report(), self.histories, list(self.payments.rows.values()),
            self.rates, risk_report(view, self.histories, today_sg()), today_sg(), targets, band,
        )

    async def ask_copilot(self, question):
        """Run the copilot off the UI thread because a model call can take several seconds."""
        store = self.store
        self.copilot_log.append(dict(role="user", text=question))
        self.copilot_busy = True
        self.render()
        try:
            ctx = self.copilot_context()
            cfg = copilot.LLMConfig.from_env()
            reply = await asyncio.to_thread(copilot.answer, ctx, question, cfg)
            entry = dict(role="assistant", text=reply.text, source=reply.source,
                         facts=reply.facts, note=reply.note)
        except Exception:  # never let a model or data problem crash the UI
            entry = dict(role="assistant", text="Something went wrong while answering. Please try again.",
                         source="error", facts={})
        if self.store is not store:
            return
        self.copilot_log.append(entry)
        self.copilot_busy = False
        if self.connected and self.tab == "Copilot" and self.editor is None:
            self.render()

    def uses_sgd(self):
        return (
            self.tab in ("Overview", "Realised P/L", "Dividends", "Risk", "Rebalance", "Digest", "Copilot")
            and self.editor is None
            and (self.tab != "Realised P/L" or self.sales_currency is None)
        )

    def refresh_fx_view(self, event):
        self.request_fx(force=True)
        self.render()

    def load_rates(self):
        # A damaged optional FX cache must not prevent access to the ledger.
        try:
            self.merge_rates(self.store.fx_rates())
            self.fx_cache_error = ""
        except PortfolioError as exc:
            self.fx_cache_error = str(exc)

    def go(self, tab, symbol=None):
        try:
            self.reload()
        except PortfolioError as exc:
            self.file_error(exc)
            return
        self.editor = None
        if tab == "Realised P/L" and self.tab != tab:
            self.sales_currency, self.month, self.sale_page = None, "all", 0
            self.stock_chart_page, self.realised_year = 0, None
        self.tab = tab
        if tab in ("Watchlist", "Market news"):
            self.load_watch()
        if tab == "Overview":
            try:
                self.news_feeds.update(read_feeds(self.store))
            except PortfolioError:
                pass
        if tab == "Watchlist":
            self.request_watch()
        if tab == "Market news":
            try:
                self.news_feeds = read_feeds(self.store)
            except PortfolioError as exc:
                self.news_error = str(exc)
            if self.news_scope is None:
                has_watchlist_only = not self.book.positions and bool(self.watch_state["items"])
                self.news_scope = watch_views.WATCHLIST_NEWS if has_watchlist_only else watch_views.HOLDINGS_NEWS
            if self.news_symbol not in watch_views.news_symbols(self):
                self.news_symbol = None
            self.request_news()
        if symbol:
            self.symbol = symbol
        self.lot_page = 0
        if self.uses_sgd():
            self.request_fx()
        if self.tab == "Digest":
            try:
                self.news_feeds = read_feeds(self.store)
            except PortfolioError:
                pass
        if self.tab in HISTORY_TABS:
            self.request_history()
        self.render()
        self.scroll_to_top()

    def scroll_to_top(self):
        self.page.run_task(self.page.scroll_to, offset=0, duration=100)

    def change_page(self, name, index):
        setattr(self, name, index)
        self.render()

    def render(self):
        self.render_width = self.page.width
        if self.symbol not in self.book.positions:
            symbols = sorted(self.book.positions)
            self.symbol = symbols[0] if symbols else None
        is_overview = self.tab == "Overview" and self.editor is None
        builders = {
            "Transactions": lambda a: a.history(),
            "Realised P/L": views.realised,
            "Dividends": insights.dividends_view,
            "Performance": insights.performance_view,
            "Stock inquiry": views.inquiry,
            "Recovery planner": views.planner,
            "Watchlist": watch_views.watchlist_view,
            "Market news": watch_views.news_view,
            "Risk": tools_views.risk_view,
            "Rebalance": tools_views.rebalance_view,
            "Digest": tools_views.digest_view,
            "Copilot": tools_views.copilot_view,
        }
        self.update_holding_panel = self.resize_planner = self.update_watch_panel = None
        if is_overview:
            intro, body = overview(self)
        else:
            controls = self.editor if self.editor is not None else builders[self.tab](self)
            intro, body = page_parts(controls)
        self.shell = FrostShell(self, TABS, intro, body, overview=is_overview)
        self.page.controls.clear()
        self.page.add(self.shell.frame)
        if self.update_holding_panel and self.workspace == "personal":
            self.request_stock_quote()

    async def resize_charts(self, event):
        width = self.page.width
        await asyncio.sleep(0.15)
        if self.page.width != width or not self.connected or self.shell is None:
            return
        if width == self.render_width:
            self.shell.resize_height()
            self.page.update()
            return
        if self.editor is None and self.tab in (
            "Overview",
            "Risk",
            "Realised P/L",
            "Dividends",
            "Performance",
            "Stock inquiry",
            "Rebalance",
            "Digest",
            "Market news",
        ):
            self.render()
        else:
            self.render_width = width
            self.shell.resize(width)
            if self.tab == "Recovery planner" and self.editor is None and self.resize_planner:
                self.resize_planner()
            self.page.update()

    def request_stock_quote(self, force=False):
        symbol = self.symbol
        if self.update_holding_panel is None or symbol in self.quote_busy:
            return
        now = monotonic()
        if not force and symbol in self.quote_tried and now - self.quote_tried[symbol] < 300:
            return
        self.quote_tried[symbol] = now
        self.quote_busy.add(symbol)
        self.quote_errors.pop(symbol, None)
        self.update_holding_panel()
        self.page.run_task(
            self.refresh_stock_quote,
            self.store,
            symbol,
            self.book.positions[symbol].currency,
        )

    async def refresh_stock_quote(self, store, symbol, currency):
        def latest():
            quote = fetch_named_quote(symbol)
            if quote.currency != currency:
                raise PortfolioError(f"The quote currency differs from the holding’s {currency}.")
            store.save_quotes([quote])
            return store.quotes()[symbol]

        try:
            quote = await asyncio.to_thread(latest)
            error = None
        except (OSError, PortfolioError) as exc:
            quote, error = None, str(exc)
        if self.store is not store:
            return
        self.quote_busy.discard(symbol)
        if error:
            self.quote_errors[symbol] = error
        elif symbol not in self.prices or quote.asof >= self.prices[symbol].asof:
            self.prices[symbol] = quote
        # A late response must never redraw a different holding or reset a planner draft.
        if self.connected and self.symbol == symbol and self.update_holding_panel:
            self.update_holding_panel()

    def notice(self, message):
        self.page.show_dialog(
            ft.SnackBar(
                content=text(message, 14, WHITE),
                bgcolor=NAVY,
                show_close_icon=True,
                close_icon_color=WHITE,
                duration=4500,
            )
        )

    def open_editor(self, controls, section):
        self.editor, self.editor_section = controls, section
        self.render()
        self.scroll_to_top()

    def trade_form(self, e=None, trade=None):
        TradeEditor(self, trade).show()

    def import_form(self, e=None):
        ImportEditor(self).show()

    def set_cost_method(self, value):
        if value == self.book.method:
            return
        try:
            self.store.commit("method", self.snap.revision, value)
        except PortfolioError as exc:
            self.go(self.tab)
            self.notice(str(exc))
            return
        self.go(self.tab)

    def cost_method_control(self):
        # Same popup as Overview so the control looks identical on every tab.
        return menu(
            FROST,
            "Cost basis method",
            self.book.method,
            ("FIFO", "AVCO"),
            self.set_cost_method,
            prefix="Cost basis · ",
        )

    def delete_form(self, trade):
        revision = self.snap.revision
        err = text("", 14, RED, True)

        def confirm(event):
            try:
                self.store.commit("delete", revision, trade_id=trade.id)
            except PortfolioError as exc:
                err.value = str(exc)
                self.page.update()
                return
            self.page.pop_dialog()
            self.go("Transactions")
            self.notice(f"Transaction #{trade.id} deleted.")

        dialog = frost_dialog(
            modal=True,
            title=text(f"Delete transaction #{trade.id}?", 21, bold=True),
            content=ft.Column(
                [
                    text(
                        f"{display_date(trade.day)} · {trade.side} {units(trade.qty)} {trade.symbol} at {trade.broker}"
                    ),
                    text("Holdings and realised results will be recalculated.", 14, MUTED),
                    err,
                ],
                tight=True,
            ),
            actions=[
                secondary("Cancel", lambda e: self.page.pop_dialog()),
                ft.FilledButton("Delete transaction", on_click=confirm, bgcolor=RED, color=WHITE),
            ],
        )
        self.page.show_dialog(dialog)

    def history(self):
        out = [
            heading(
                "Transactions",
                "Review buys and sells across your brokerages.",
                [
                    self.cost_method_control(),
                    secondary(
                        "Reload ledger",
                        lambda e: self.go("Transactions"),
                        ft.Icons.REFRESH,
                    ),
                    secondary("Import trades", self.import_form, ft.Icons.UPLOAD_FILE),
                    secondary("Record stock split", lambda e: edit_split(self)),
                    primary("Add transaction", self.trade_form, ft.Icons.ADD),
                ],
            )
        ]
        if self.book.splits:
            out.append(split_panel(self))
        if not self.book.trades:
            return out + [
                empty(
                    "Your transaction history is empty",
                    "Add your first buy to open a position.",
                )
            ]
        search = field(
            "Symbol, brokerage or note",
            self.search,
            320,
            hint="e.g. DIS or Syfe",
        )
        side = choose(
            "Transaction type",
            [("ALL", "All transactions"), ("BUY", "Buys"), ("SELL", "Sells")],
            self.side,
            width=190,
        )

        def apply(e):
            self.search, self.side, self.trade_page = (
                search.value.strip(),
                side.value,
                0,
            )
            self.render()

        def clear(e):
            self.search, self.side, self.trade_page = "", "ALL", 0
            self.render()

        search.on_submit = side.on_select = apply
        out.append(
            ft.Row(
                [
                    search,
                    side,
                    secondary("Apply filter", apply),
                    ft.TextButton("Clear", on_click=clear),
                ],
                wrap=True,
            )
        )
        query = self.search.casefold()
        matches = [
            t
            for t in reversed(self.book.trades)
            if (self.side == "ALL" or self.side == t.side)
            and (
                query in t.symbol.casefold()
                or query in t.broker.casefold()
                or query in t.note.casefold()
            )
        ]
        if not matches:
            return out + [empty("No matching transactions", "Clear the filters to see all trades.")]
        size = 25
        self.trade_page = min(self.trade_page, (len(matches) - 1) // size)
        closed = self.book.closed_sales
        rows = []
        for t in matches[self.trade_page * size : (self.trade_page + 1) * size]:
            actions = ft.Row(
                [
                    ft.TextButton(
                        "Edit",
                        style=ft.ButtonStyle(color=NAVY),
                        tooltip=f"Edit transaction {t.id}",
                        on_click=lambda e, tr=t: self.trade_form(trade=tr),
                    ),
                    ft.TextButton(
                        "Delete",
                        style=ft.ButtonStyle(color=RED),
                        tooltip=f"Delete transaction {t.id}",
                        on_click=lambda e, tr=t: self.delete_form(tr),
                    ),
                ],
                spacing=0,
                tight=True,
            )
            rows.append(
                [
                    str(t.id),
                    display_date(t.day),
                    text(t.symbol, 14, bold=True),
                    (
                        ft.Row([text(t.side, 14), tag("Sold in full")], spacing=6, tight=True)
                        if t.id in closed
                        else t.side
                    ),
                    t.broker,
                    units(t.qty),
                    unit_price(t.price, t.currency),
                    dollars(t.fee, currency=t.currency),
                    (
                        ft.TextButton(
                            "View note",
                            on_click=lambda e, tr=t: self.show_trade_note(tr),
                        )
                        if t.note
                        else "—"
                    ),
                    actions,
                ]
            )
        return out + [
            panel(
                [
                    sticky_grid(
                        [
                            "ID",
                            "Date",
                            "Symbol",
                            "Type",
                            "Brokerage",
                            "Units",
                            "Unit price",
                            "Fee",
                            "Note",
                            "Actions",
                        ],
                        rows,
                        (4, 9, 9, 16, 11, 8, 11, 9, 10, 15),
                        numeric=(5, 6, 7),
                        width=self.table_width,
                        min_width=1000,
                    ),
                    pager(
                        self.trade_page,
                        len(matches),
                        size,
                        lambda p: self.change_page("trade_page", p),
                    ),
                ]
            )
        ]

    def show_trade_note(self, trade):
        self.page.show_dialog(
            frost_dialog(
                title=text(f"{trade.symbol} · transaction #{trade.id}", 20, bold=True),
                content=text(trade.note, 15),
                scrollable=True,
                actions=[secondary("Close", lambda e: self.page.pop_dialog())],
            )
        )

    def sale_detail(self, sale, reported=None):
        record = sale.trade
        rows = []
        for match in sale.matches:
            buy = self.book.by_id[match.buy_id]
            rows.append(
                [
                    f"#{buy.id}",
                    display_date(buy.day),
                    units(match.qty),
                    unit_price(buy.price, buy.currency),
                    dollars(match.basis, currency=record.currency),
                ]
            )
        dialog = frost_dialog(
            title=text(f"{record.symbol} · sale #{record.id}", 21, bold=True),
            content=ft.Container(
                ft.Column(
                    [
                        text(f"{display_date(record.day)} · {record.broker} · {units(record.qty)} units"),
                        text(
                            f"Net proceeds {dollars(sale.net, currency=record.currency)} less {self.book.method} cost {dollars(sale.basis, currency=record.currency)}",
                            14,
                            MUTED,
                        ),
                        text(
                            f"Realised P/L {dollars(sale.profit, True, record.currency)}",
                            20,
                            tone(sale.profit),
                            True,
                        ),
                        *(
                            [
                                text(
                                    f"SGD equivalent at the displayed rates · Net {dollars(reported.net, currency=SGD)} · "
                                    f"{self.book.method} cost {dollars(reported.basis, currency=SGD)} · P/L {dollars(reported.profit, True, SGD)}",
                                    13,
                                    MUTED,
                                )
                            ]
                            if reported is not None and record.currency != SGD
                            else []
                        ),
                        text(
                            f"Before this sale, {record.broker} held {units(sale.pool_before[0])} shares "
                            f"with a total cost of {dollars(sale.pool_before[1], currency=record.currency)}. "
                            f"Average cost per share was {unit_price(sale.pool_before[1] / sale.pool_before[0], record.currency)}.",
                            14,
                            MUTED,
                        )
                        if sale.pool_before
                        else grid(
                            [
                                "Buy ID",
                                "Buy date",
                                "Matched units",
                                "Buy price",
                                "Matched cost",
                            ],
                            rows,
                            510,
                            520,
                        ),
                    ],
                    tight=True,
                ),
                width=560,
            ),
            scrollable=True,
            actions=[secondary("Close", lambda e: self.page.pop_dialog())],
        )
        self.page.show_dialog(dialog)

    def price_form(self, e=None):
        symbol = self.symbol
        currency = self.book.positions[symbol].currency
        price = field("Market price", width=210, prefix=currency + " ")
        stamp = field("Quote time in UTC", datetime.now(UTC).strftime(f"{DATE_FORMAT} %H:%M:%S"), 250)
        err = text("", 14, RED, True)

        def save(ev):
            try:
                try:
                    asof = datetime.strptime(stamp.value.strip(), f"{DATE_FORMAT} %H:%M:%S").replace(
                        tzinfo=UTC
                    )
                except ValueError:
                    raise PortfolioError(f"Use {DATE_HINT} HH:MM:SS in UTC.") from None
                q = Quote.from_record(
                    dict(
                        symbol=symbol,
                        price=price.value,
                        asof=asof.isoformat(),
                        source="Manual entry",
                        currency=currency,
                    )
                )
                if not self.store.save_quotes([q]):
                    raise PortfolioError("A later quote is already saved. Check the quote time.")
            except PortfolioError as exc:
                err.value = str(exc)
                self.page.update()
                return
            self.page.pop_dialog()
            self.go("Stock inquiry", symbol)
            self.notice("Price saved and portfolio values recalculated.")

        dialog = frost_dialog(
            title=text(f"Enter price for {symbol}", 21, bold=True),
            modal=True,
            scrollable=True,
            content=ft.Container(
                ft.Column(
                    [
                        price,
                        stamp,
                        text("Use the price and time shown by your brokerage.", 13, MUTED),
                        err,
                    ],
                    tight=True,
                ),
                width=340,
            ),
            actions=[
                secondary("Cancel", lambda e: self.page.pop_dialog()),
                primary("Save price", save),
            ],
        )
        self.page.show_dialog(dialog)

    def start_refresh_clocks(self, event=None):
        self.connected = True
        if self.fx_clock is None or self.fx_clock.done():
            self.fx_clock = self.page.run_task(self.watch_rates)
        if self.watch_clock is None or self.watch_clock.done():
            self.restart_watch_clock()
        if event and hasattr(self, "store") and self.uses_sgd():
            self.request_fx()
            self.render()

    def stop_refresh_clocks(self, event=None):
        self.connected = False
        for clock in (self.fx_clock, self.watch_clock):
            if clock:
                clock.cancel()
        self.fx_clock = self.watch_clock = None

    async def watch_rates(self):
        while True:
            await asyncio.sleep(300)
            if hasattr(self, "store") and self.uses_sgd():
                self.request_fx(force=True)

    def restart_watch_clock(self):
        if self.watch_clock:
            self.watch_clock.cancel()
        self.watch_clock = (
            self.page.run_task(self.watch_prices, self.watch)
            if self.connected and hasattr(self, "watch") else None
        )

    async def watch_prices(self, watch):
        anchor = self.watch_last if self.watch_last is not None else monotonic()
        while self.connected and self.watch is watch:
            if self.watch_last is not None:
                anchor = max(anchor, self.watch_last)
            interval = self.watch_state["refresh_seconds"]
            await asyncio.sleep(max(0, anchor + interval - monotonic()))
            if not self.connected or self.watch is not watch:
                return
            if self.watch_last is not None and self.watch_last > anchor:
                continue  # A manual check set a later start time.
            self.load_watch()
            anchor = monotonic()
            if (
                not watch.store.demo
                or self.tab == "Watchlist"
                or any(i["target"] for i in self.watch_state["items"].values())
            ):
                self.request_watch()

    def request_fx(self, force=False):
        if self.snapshot_demo and self.workspace == "demo":
            return
        codes = (
            self.book.months_by_currency.keys()
            if self.tab == "Realised P/L"
            else reporting_currencies(self.book)
        )
        if self.tab == "Dividends":
            codes = {d.currency for d in self.payments.rows.values()} | {
                p.currency for p in self.book.positions.values()
            }
        needed = codes - {SGD} - self.fx_busy
        now = monotonic()
        due = {c for c in needed if force or c not in self.fx_tried or now - self.fx_tried[c] >= 60}
        if due:
            self.fx_busy.update(due)
            self.fx_tried.update(dict.fromkeys(due, now))
            self.page.run_task(self.update_fx, self.store, due)

    async def update_fx(self, store, currencies):
        fresh, errors = await asyncio.to_thread(refresh_prices, currencies, fetch_sgd_rate)
        try:
            available = await asyncio.to_thread(store.save_fx_rates, fresh.values())
            cache_error = ""
        except PortfolioError as exc:
            available, cache_error = fresh, str(exc)
        if self.store is not store:
            return
        self.merge_rates(available)
        self.fx_cache_error = cache_error
        self.fx_busy.difference_update(currencies)
        for code in currencies:
            self.fx_errors.pop(code, None)
        self.fx_errors.update(errors)
        if self.connected and self.uses_sgd():
            self.render()

    async def refresh(self, e):
        if self.refreshing:
            return
        self.request_fx(force=True)
        if not self.book.positions:
            self.render()
            return
        store = self.store
        expected = {s: p.currency for s, p in self.book.positions.items()}
        symbols = tuple(expected)
        self.refreshing = True
        self.refresh_report = "Requesting the latest available quote for each holding…"
        self.render()
        try:
            results, errors = await asyncio.to_thread(refresh_prices, symbols)
            for symbol in list(results):
                if results[symbol].currency != expected[symbol]:
                    errors[symbol] = (
                        "The exchange currency differs from the journal. Edit the original trade to confirm its listing."
                    )
                    del results[symbol]
            saved = await asyncio.to_thread(store.save_quotes, results.values())
            parts = [f"Refreshed {len(saved)} of {len(symbols)} holdings."]
            older = sorted(set(results) - saved)
            if older:
                parts.append("Kept more recent prices for " + ", ".join(older) + ".")
            for symbol, message in sorted(errors.items()):
                parts.append(f"{symbol}: {message}")
            if errors:
                parts.append(
                    "Prior prices are retained. For an unrecognised ticker, edit its entry in Transactions and choose the matching listing."
                )
            message = " ".join(parts)
        except PortfolioError as exc:
            message = str(exc)
        finally:
            if self.store is store:
                self.refreshing = False
        if self.store is store:
            self.refresh_report = message
            # Read the current ledger again: another window may have traded during the request.
            if self.tab == "Overview" and self.editor is None:
                try:
                    self.reload()
                except PortfolioError as exc:
                    self.file_error(exc)
                else:
                    self.render()
            else:
                # Preserve a draft form or scenario while an earlier request finishes.
                try:
                    self.prices = store.quotes()
                    if self.connected and self.update_holding_panel:
                        self.update_holding_panel()
                except PortfolioError as exc:
                    self.notice(str(exc))
