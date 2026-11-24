# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Dividend journal, income calendar and historical portfolio charts."""

from calendar import monthrange
from datetime import timedelta

import flet as ft

from frost_theme import frost_dialog

from analytics_panels import (
    calendar_panel,
    growth_panels,
    records_charts,
    returns_panels,
    yield_panels,
)
from analysis_metrics import holding_series
from charts import trend_chart
from dashboard import fx_notices, rates_panel, sgd
from dividends import net_income, payment_day, today_sg
from performance import dividend_candidates, performance, period_growth
from name_picker import brokerage_picker
from portfolio import DATE_HINT, PortfolioError, cash, display_date, dollars, percent, unit_price, units
from valuation import complete_sum, in_sgd
from widgets import (
    MUTED,
    RED,
    AMBER,
    NAVY,
    choose,
    empty,
    field,
    grid,
    heading,
    Metric,
    metric_cards,
    pager,
    pairs,
    panel,
    primary,
    secondary,
    text,
    tone,
)


def refresh_history(app, with_fx=False):
    def reload(event):
        if with_fx:
            app.request_fx(force=True)
        app.request_history(force=True)
        app.render()

    button = secondary(
        "Loading history…" if app.history_busy else "Refresh market history", reload, ft.Icons.SYNC
    )
    button.disabled = bool(app.history_busy) or not app.book.trades
    return button


def history_notices(app):
    notes = []
    if app.history_busy:
        notes.append(
            text(
                f"Retrieving daily market data for {len(app.history_busy)} instruments…", 13, MUTED
            )
        )
    if app.history_errors:
        notes.append(
            text(
                "History refresh incomplete. Saved observations are used where available. "
                + " ".join(f"{key} · {msg}" for key, msg in sorted(app.history_errors.items())),
                13,
                RED,
            )
        )
    if app.history_file_error:
        notes.append(text(app.history_file_error, 13, RED))
    if any(h.source == "Demo history" for h in app.histories.values()):
        notes.append(
            text(
                "The demo includes illustrative daily prices and distributions. Refresh market history to load available Yahoo Finance observations.",
                12,
                MUTED,
            )
        )
    return notes


def choose_view(app, label, values, current, callback, width=170):
    def change(e):
        callback(e.control.value)
        app.render()

    return choose(label, [(v, str(v)) for v in values], str(current), change, width)


def dividend_form(app, item=None, candidate=None, confirm=False):
    store, revision = app.store, app.payments.revision
    today = today_sg()
    symbols = sorted(set(app.book.currencies) | ({item.symbol} if item else set()))
    if not symbols:
        app.notice("Record a trade before adding its dividend.")
        return
    sym = item.symbol if item else candidate[0] if candidate else symbols[0]
    event = candidate[1] if candidate else None
    currency_code = item.currency if item else (event.currency if event else None)
    currency_code = currency_code or app.book.currencies.get(sym, "")
    gross_value = str(item.gross) if item else ""
    if event and currency_code:
        gross_value = str(cash(event.rate * candidate[2], currency_code))
    currency = field("Payment currency", currency_code, 154, hint="SGD, USD…")
    gross = field("Gross dividend", gross_value, 154)
    tax = field("Tax withheld", str(item.tax) if item else "0", 154)
    error = text("", 14, RED, True)

    def pick(e):
        currency.value = app.book.currencies.get(e.control.value, "")
        app.page.update()

    symbol = choose("Holding", [(s, s) for s in symbols], sym, pick, 194)
    symbol.disabled = bool(candidate)
    pay_day = item.day if item else event.pay_day if event else today
    state = "Received" if confirm or not item else item.status
    if event and pay_day and pay_day > today:
        state = "Expected"
    status = choose("Payment status", [(s, s) for s in ("Received", "Expected")], state, width=168)
    day = field("Payment date", display_date(pay_day), 172, hint=DATE_HINT)
    ex = item.ex_day if item else event.ex_day if event else None
    exdate = field("Ex-dividend date (optional)", display_date(ex), 212, hint=DATE_HINT)
    broker = brokerage_picker(
        app, item.broker if item else "", label="Brokerage (optional)", none_label="Portfolio-wide",
    )

    def save(e):
        if app.store is not store:
            return
        try:
            paid_on = payment_day(day.value)
            store.save_payment(
                revision, dict(symbol=symbol.value, date=paid_on.isoformat(),
                               currency=currency.value, gross=gross.value, tax=tax.value,
                               status=status.value, ex_date=exdate.value, broker=broker.value),
                item.id if item else None,
            )
        except PortfolioError as exc:
            error.value = str(exc)
            dialog.update()
            return
        app.page.pop_dialog()
        app.income_year = paid_on.year
        app.payment_filter = "All"
        app.payment_page = 0
        app.go("Dividends")
        app.notice("Dividend saved.")

    notes = []
    if event:
        rate = unit_price(event.rate, currency_code) if currency_code else units(event.rate)
        notes.append(text(f"{units(candidate[2])} eligible shares · {rate} per share", 14, bold=True))
        details = []
        if not event.pay_day:
            details.append("Enter the payment date from your statement.")
        if not event.currency:
            details.append("The listing currency is suggested. Verify the payout currency and amount.")
        details.append("Confirm the amount and tax before saving.")
        notes.append(text(" ".join(details), 13, MUTED))
    title = ("Review payout" if candidate else "Confirm dividend receipt" if confirm
             else "Edit dividend" if item else "Record dividend")
    dialog = frost_dialog(
        title=text(title, 22, bold=True),
        modal=True, scrollable=True,
        content=ft.Container(
            ft.Column([
                *notes,
                ft.Row([symbol, status, broker], wrap=True, run_spacing=8),
                ft.Row([day, exdate], wrap=True, run_spacing=8),
                ft.Row([currency, gross, tax], wrap=True, run_spacing=8),
                error,
            ], tight=True, spacing=12),
            width=min(610, max(215, (app.page.width or 800) - 99)),
        ),
        actions=[
            secondary("Cancel", lambda e: app.page.pop_dialog()),
            primary("Save dividend", save),
        ],
    )
    app.page.show_dialog(dialog)


def remove_dividend(app, item):
    store, rev = app.store, app.payments.revision
    err = text("", 13, RED)

    def remove(e):
        try:
            store.save_payment(rev, payment_id=item.id)
        except PortfolioError as exc:
            err.value = str(exc)
            dialog.update()
            return
        app.page.pop_dialog()
        app.go("Dividends")
        app.notice("Dividend removed.")

    dialog = frost_dialog(
        modal=True,
        title=text(f"Remove {item.symbol} dividend?", 20, bold=True),
        content=ft.Column(
            [text(f"{item.day} · {dollars(item.net, currency=item.currency)} net"), err], tight=True
        ),
        actions=[
            secondary("Cancel", lambda e: app.page.pop_dialog()),
            primary("Remove dividend", remove),
        ],
    )
    app.page.show_dialog(dialog)


def records_panel(app, rows):
    filters = ["All", "Received", "Expected"]

    def set_filter(value):
        app.payment_filter = value
        app.payment_page = 0

    visible = sorted(
        (d for d in rows if app.payment_filter == "All" or d.status == app.payment_filter),
        key=lambda d: (d.day, d.id),
        reverse=True,
    )
    index = min(app.payment_page, max(0, (len(visible) - 1) // 8))
    lines = []
    for d in visible[index * 8 : index * 8 + 8]:
        actions = ft.Row(
            [
                ft.IconButton(
                    ft.Icons.EDIT_OUTLINED,
                    tooltip=f"Edit dividend {d.id}",
                    on_click=lambda e, x=d: dividend_form(app, x),
                ),
                ft.IconButton(
                    ft.Icons.DELETE_OUTLINE,
                    tooltip=f"Remove dividend {d.id}",
                    on_click=lambda e, x=d: remove_dividend(app, x),
                ),
            ],
            spacing=0,
            tight=True,
        )
        lines.append(
            [
                display_date(d.day),
                d.symbol,
                d.broker or "Portfolio",
                d.status,
                dollars(d.gross, currency=d.currency),
                dollars(d.tax, currency=d.currency),
                dollars(d.net, currency=d.currency),
                sgd(in_sgd(d.net, d.currency, app.rates)),
                actions,
            ]
        )
    controls = [
        ft.Row(
            [
                text("Dividend records", 20, bold=True),
                choose_view(app, "Show payments", filters, app.payment_filter, set_filter),
            ],
            wrap=True,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )
    ]
    if lines:
        controls.append(
            grid(
                [
                    "Pay date",
                    "Holding",
                    "Brokerage",
                    "Status",
                    "Gross",
                    "Tax",
                    "Net",
                    "Net · SGD",
                    "Actions",
                ],
                lines,
                920,
                app.table_width,
                numeric=(4, 5, 6, 7),
            )
        )
        if len(visible) > 8:
            controls.append(
                pager(index, len(visible), 8, lambda i: app.change_page("payment_page", i))
            )
    else:
        controls.append(text("No matching payments in the selected year.", 14, MUTED))
    return panel(controls)


def dividends_view(app):
    today = today_sg()
    btn = primary("Record dividend", lambda e: dividend_form(app), ft.Icons.ADD)
    btn.disabled = not app.book.currencies or bool(app.payment_error)
    out = [
        heading(
            "Dividend income",
            "Cash received, withholding and expected payments across all markets.",
            [
                refresh_history(app, True),
                btn,
            ],
        )
    ]
    if app.payment_error:
        return out + [text(app.payment_error, 14, RED)]
    all_rows = list(app.payments.rows.values())
    if not all_rows and not app.book.currencies:
        return out + [
            empty("No dividends yet", "Record a stock purchase to start tracking its income.")
        ]

    def year_change(value):
        app.income_year = int(value)
        app.payment_page = 0

    year = app.income_year
    years = sorted({today.year, year, *(d.day.year for d in all_rows)}, reverse=True)
    out.append(choose_view(app, "Income year", list(map(str, years)), year, year_change))
    rows = [d for d in all_rows if d.day.year == year]
    received = [d for d in rows if d.status == "Received"]
    upcoming = sorted((d for d in all_rows if d.status == "Expected"), key=lambda d: (d.day, d.id))
    last_year = today.replace(
        year=today.year - 1, day=min(today.day, monthrange(today.year - 1, today.month)[1])
    )
    next_year = today.replace(
        year=today.year + 1, day=min(today.day, monthrange(today.year + 1, today.month)[1])
    )
    trailing = net_income(
        [d for d in all_rows if d.status == "Received" and last_year < d.day <= today], app.rates
    )
    booked = net_income([d for d in upcoming if today <= d.day < next_year], app.rates)
    paid = net_income(received, app.rates)
    taxes = complete_sum(in_sgd(d.tax, d.currency, app.rates) for d in received)
    out.append(
        metric_cards(
            app.content_width,
            [
                Metric("Net received", sgd(paid), f"After withholding · {year}", ft.Icons.PAID_OUTLINED),
                Metric("Tax withheld", sgd(taxes), f"Received dividends · {year}", ft.Icons.RECEIPT_LONG_OUTLINED),
                Metric(
                    "Monthly average",
                    sgd(trailing / 12 if trailing is not None else None),
                    "Net received · last 12 months ÷ 12",
                    ft.Icons.SCHEDULE,
                ),
                Metric("Scheduled income", sgd(booked), "Expected net · next 12 months", ft.Icons.CALENDAR_MONTH_OUTLINED),
            ],
            header=True,
        )
    )
    codes = {d.currency for d in all_rows} | {p.currency for p in app.book.positions.values()}
    out += fx_notices(app, codes)
    out.extend(rates_panel(app, codes))
    wide = app.table_width >= 1000
    full_width = app.table_width - 8
    paired_width = (app.table_width - 16) // 2 - 38 if wide else full_width
    yield_card, income_mix = yield_panels(app, all_rows, today)
    received, by_group = records_charts(app, all_rows, today, paired_width)
    out.append(yield_card)
    out.extend(pairs([received, by_group], wide))
    out.append(calendar_panel(app, all_rows, today, dividend_form, full_width))
    out.append(income_mix)
    out.extend(growth_panels(app, all_rows, today, full_width))
    out.append(records_panel(app, rows))
    events = dividend_candidates(app.book, all_rows, app.histories)

    def suggestion(row):
        symbol, event, qty = row
        rate = (
            unit_price(event.rate, event.currency)
            if event.currency
            else f"{units(event.rate)} · verify currency"
        )
        return [
            display_date(event.ex_day),
            symbol,
            units(qty),
            rate,
            ft.TextButton(
                "Review payout", on_click=lambda e, x=row: dividend_form(app, candidate=x)
            ),
        ]

    out.append(
        panel(
            [
                text("Reported ex-dividend events", 20, bold=True),
                text(
                    "Events use shares held before the ex-date. Review the payment date and tax on your statement before recording cash.",
                    13,
                    MUTED,
                ),
                grid(
                    ["Ex-date", "Holding", "Eligible shares", "Per share", ""],
                    [suggestion(r) for r in events[:40]],
                    620,
                    app.table_width,
                    numeric=(2, 3),
                )
                if events
                else text("No unrecorded eligible events in the saved history.", 14, MUTED),
                *(
                    [text(f"Showing the 40 most recent of {len(events)} events.", 12, MUTED)]
                    if len(events) > 40
                    else []
                ),
            ]
        )
    )
    out.extend(history_notices(app))
    return out


def performance_view(app):
    today = today_sg()
    out = [
        heading(
            "Portfolio performance",
            "Historical holdings and cash flows, valued in SGD on each date.",
            [refresh_history(app)],
        )
    ]
    if app.payment_error:
        return out + [text(app.payment_error, 14, RED)]
    if not app.book.trades and not app.payments.rows:
        return out + [
            empty(
                "No performance history yet",
                "Record a purchase to start your performance timeline.",
            )
        ]
    cache_key = (
        app.book,
        app.payments.revision,
        today,
        tuple((k, id(v)) for k, v in sorted(app.histories.items())),
    )
    if cache_key != app.perf_cache_key:
        app.perf_rows, app.perf_warnings = performance(
            app.book, tuple(app.payments.rows.values()), app.histories, today
        )
        app.per_symbol_series = holding_series(
            app.book, tuple(app.payments.rows.values()), app.histories, today
        )
        app.perf_cache_key = cache_key
    opts = {"1 month": 30, "3 months": 90, "6 months": 180, "1 year": 365, "Since inception": None}
    out.append(
        choose_view(
            app,
            "Chart period",
            list(opts),
            app.performance_period,
            lambda v: setattr(app, "performance_period", v),
            185,
        )
    )
    window = opts[app.performance_period]
    rows = (
        app.perf_rows
        if window is None
        else [p for p in app.perf_rows if p.day >= today - timedelta(days=window)]
    )
    notices = history_notices(app)
    if not rows:
        return out + notices + [text("No observations in this period.", 14, MUTED)]
    growth, base_day = period_growth(
        app.perf_rows, today - timedelta(days=window) if window is not None else None
    )
    result = growth[-1]
    last = rows[-1]
    out.append(
        metric_cards(
            app.content_width,
            [
                Metric("Holdings value", sgd(last.value), f"As of {last.day:%d %b %Y}", ft.Icons.ACCOUNT_BALANCE_WALLET_OUTLINED),
                Metric(
                    "Net invested", sgd(last.invested),
                    "Purchases less sales, incl. fees" if last.invested is not None else "Missing historical FX",
                    ft.Icons.SWAP_HORIZ,
                ),
                Metric(
                    "Net dividends", sgd(last.income),
                    "Received at payment-date FX" if last.income is not None else "Missing historical FX",
                    ft.Icons.PAID_OUTLINED,
                ),
                Metric(
                    "Period gain",
                    sgd(result.gain, True),
                    "Modified Dietz return" if result.gain is not None else "Incomplete historical inputs",
                    ft.Icons.SHOW_CHART,
                    percentage=percent(result.return_pct),
                    color=tone(result.gain),
                ),
            ],
            header=True,
        )
    )
    out += notices
    if app.perf_warnings:
        out.append(
            text(
                "Some dates have incomplete prices, FX or share history. Affected values are left open. "
                + ", ".join(sorted(app.perf_warnings)),
                13,
                MUTED,
            )
        )
    out.extend(returns_panels(app, today))
    out.append(
        panel(
            [
                text("Holdings value and net invested", 20, bold=True),
                *trend_chart(
                    rows,
                    [("value", "Holdings value", NAVY), ("invested", "Net invested", AMBER)],
                    app.table_width - 8,
                ),
            ]
        )
    )
    out.append(
        panel(
            [
                text("Total gain including dividends", 20, bold=True),
                choose_view(
                    app,
                    "Growth measure",
                    ["Gain in SGD", "Return (%)"],
                    app.growth_measure,
                    lambda v: setattr(app, "growth_measure", v),
                ),
                *trend_chart(
                    growth,
                    [("gain", "Investment gain · SGD", NAVY)]
                    if app.growth_measure == "Gain in SGD"
                    else [("return_pct", "Modified Dietz return · %", NAVY)],
                    app.table_width - 8,
                    as_percent=app.growth_measure == "Return (%)",
                ),
                text(
                    f"Period starts {base_day:%d %b %Y}. Modified Dietz weights purchases, sales and dividend payouts by their timing. A return requires positive weighted capital.",
                    12,
                    MUTED,
                ),
            ]
        )
    )
    by_month = {r.day.strftime("%Y-%m"): r for r in rows}
    out.append(
        panel(
            [
                ft.ExpansionTile(
                    title=text("Monthly closing values", 16, bold=True),
                    controls=[
                        grid(
                            [
                                "Date",
                                "Holdings · SGD",
                                "Net invested · SGD",
                                "Net dividends · SGD",
                                "Total gain · SGD",
                            ],
                            [
                                [
                                    str(r.day),
                                    sgd(r.value),
                                    sgd(r.invested),
                                    sgd(r.income),
                                    sgd(r.gain, True),
                                ]
                                for r in reversed(by_month.values())
                            ],
                            710,
                            app.table_width,
                            numeric=(1, 2, 3, 4),
                        )
                    ],
                ),
                text(
                    "Dates use the latest available daily close on or before that day, for up to ten calendar days. "
                    "Market value includes shares only. Net invested deducts net sale proceeds. "
                    "Total gain includes received dividends after withholding and historical currency movements.",
                    12,
                    MUTED,
                ),
            ]
        )
    )
    return out
