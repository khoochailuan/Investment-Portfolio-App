# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Flet views for holdings, realised sales, individual stocks and recovery scenarios."""

from __future__ import annotations

from datetime import date

import flet as ft


from charts import monthly_profit_chart, price_chart, stock_profit_chart
from dashboard import fx_notices, rates_panel
from dividend_stats import dividend_facts
from dividends import today_sg
from valuation import SGD, market_name

from portfolio import (
    display_date,
    PortfolioError,
    break_even,
    dollars,
    fee_value,
    mark,
    percent,
    minor_unit,
    scenario,
    unit_price,
    units,
)
from widgets import (
    NAVY,
    MUTED,
    RED,
    RULE,
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
    tag,
    text,
    tone,
    when,
)


def stock_selector(app, handler):
    return choose("Stock", [(s, s) for s in sorted(app.book.positions)], app.symbol, handler, 185)


def realised_charts(app, report, currency):
    years = sorted({month[:4] for month in report.months}, reverse=True)
    if app.month != "all":
        app.realised_year = app.month[:4]
    elif app.realised_year not in years:
        app.realised_year = years[0]
    year = app.realised_year
    selected = int(app.month[-2:]) if app.month != "all" else None
    # The app shell has 22 px side gutters and the panel has 19 px padding.
    inside = min(app.page.width or 1200, 1200) - 44 - 40
    side_by_side = inside >= 900
    width = max(180, int((inside - 32) / 2 if side_by_side else inside))
    period = month_name(app.month) if selected else "All months"
    stock = ft.Column(
        [
            text("Net realised P/L by stock", 19, bold=True),
            text(f"{period} · {currency}", 13, MUTED),
            stock_profit_chart(
                report.profit_by_stock(app.month),
                currency,
                width,
                app.stock_chart_page,
                lambda n: app.change_page("stock_chart_page", n),
            ),
        ]
    )

    def change_year(event):
        app.realised_year = event.control.value
        app.render()

    year_control = (
        choose("Chart year", [(y, y) for y in years], year, change_year, width=140)
        if len(years) > 1 and not selected
        else text(year, 13, MUTED, True)
    )
    monthly = ft.Column(
        [
            ft.Row(
                [text("Monthly realised P/L", 19, bold=True), year_control],
                wrap=True,
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            text(f"Net after trading fees · {currency}", 13, MUTED),
            *monthly_profit_chart(report, year, currency, width, selected),
        ]
    )
    stock.spacing = monthly.spacing = 13
    stock.col = monthly.col = 6 if side_by_side else 12
    return panel(
        [
            ft.ResponsiveRow(
                [stock, monthly],
                spacing=32,
                run_spacing=25,
                vertical_alignment=ft.CrossAxisAlignment.START,
            )
        ]
    )


def realised(app):
    book = app.book
    if app.sales_currency not in book.months_by_currency:
        app.sales_currency = None
    report = app.sales_report(app.sales_currency)
    combined = app.sales_currency is None
    currency = app.sales_currency or SGD
    if app.month not in report.rows_by_month:
        app.month = "all"

    def display(event):
        key = event.control.value
        app.sales_currency = None if key == "all_markets" else key
        app.month, app.sale_page, app.stock_chart_page, app.realised_year = "all", 0, 0, None
        if app.sales_currency is None:
            app.request_fx()
        app.render()

    def pick(event):
        app.month, app.sale_page, app.stock_chart_page = event.control.value, 0, 0
        app.render()

    if combined:
        subtitle = "Completed sales from every market, converted to SGD at current exchange rates."
    else:
        subtitle = f"Sales traded in {currency}, shown in their original currency."
    actions = [app.cost_method_control()]
    if combined and set(book.months_by_currency) - {SGD}:
        actions.append(secondary("Refresh FX", app.refresh_fx_view, ft.Icons.SYNC))
    out = [heading("Realised profit and loss", subtitle, actions)]
    if not report.months:
        return out + [
            empty(
                "No completed sales",
                "Record a sale to see its proceeds, cost and gain or loss.",
            ),
        ]

    controls = [
        choose(
            "Sale month",
            [("all", "All months")] + [(m, month_name(m)) for m in report.months],
            app.month,
            pick,
        )
    ]
    if set(book.months_by_currency) - {SGD}:
        options = [("all_markets", "All markets · SGD")] + [
            (c, f"{c} sales · {c}") for c in sorted(book.months_by_currency)
        ]
        controls.append(
            choose("Sales shown", options, app.sales_currency or "all_markets", display, width=223)
        )
    periods = report.months if app.month == "all" else [app.month]
    totals = report.totals[app.month]
    out += [
        ft.Row(controls, wrap=True),
        metric_cards(
            app.content_width,
            [
                Metric(
                    "Realised P/L",
                    dollars(totals.profit, True, currency),
                    "on cost of shares sold",
                    ft.Icons.TASK_ALT,
                    percentage=percent(totals.return_pct),
                    color=tone(totals.profit),
                ),
                Metric(
                    "Net proceeds",
                    dollars(totals.net, currency=currency),
                    "Gross sales less selling fees",
                    ft.Icons.PAYMENTS_OUTLINED,
                ),
                Metric(
                    "Matched cost",
                    dollars(totals.basis, currency=currency),
                    "Includes allocated purchase fees",
                    ft.Icons.LAYERS_OUTLINED,
                ),
            ],
            header=True,
        ),
    ]

    sales = report.rows_by_month[app.month]
    if combined:
        currencies = report.currencies_by_month[app.month]
        out += fx_notices(app, currencies) + rates_panel(app, currencies)
    out.append(realised_charts(app, report, currency))
    monthly_rows = []
    for month in periods:
        amounts = report.totals[month]
        monthly_rows.append(
            [
                month_name(month),
                str(len(report.rows_by_month[month])),
                dollars(amounts.net, currency=currency),
                dollars(amounts.basis, currency=currency),
                text(dollars(amounts.profit, True, currency), 14, tone(amounts.profit), True),
            ]
        )
    out.append(
        panel(
            [
                text(f"Monthly summary · {currency}", 19, bold=True),
                grid(
                    ["Sale month", "Sales", "Net proceeds", "Matched cost", "Realised P/L"],
                    monthly_rows,
                    600,
                    app.table_width,
                    numeric=(1, 2, 3, 4),
                ),
            ]
        )
    )
    size = 10
    app.sale_page = min(app.sale_page, max(0, (len(sales) - 1) // size))
    rows = []
    for line in sales[app.sale_page * size : (app.sale_page + 1) * size]:
        sale, amounts = line.sale, line.amounts
        t = sale.trade
        rows.append(
            [
                display_date(t.day),
                t.symbol,
                t.currency,
                t.broker,
                units(t.qty),
                dollars(amounts.net, currency=currency),
                dollars(amounts.basis, currency=currency),
                text(dollars(amounts.profit, True, currency), 14, tone(amounts.profit), True),
                ft.TextButton(
                    "View pool" if book.method == "AVCO" else "View lots",
                    on_click=lambda e, s=sale, a=amounts: app.sale_detail(
                        s, a if combined else None
                    ),
                ),
            ]
        )
    detail = [
        text(f"Sales and {book.method} cost · {currency}", 19, bold=True),
        grid(
            [
                "Date",
                "Symbol",
                "Trade CCY",
                "Brokerage",
                "Units",
                "Net proceeds",
                f"{book.method} cost",
                "Realised P/L",
                "Details",
            ],
            rows,
            940,
            app.table_width,
            numeric=(4, 5, 6, 7),
        ),
    ]
    if len(sales) > size:
        detail.append(
            pager(app.sale_page, len(sales), size, lambda p: app.change_page("sale_page", p))
        )
    return out + [panel(detail)]


def month_name(month):
    return date.fromisoformat(month + "-01").strftime("%B %Y")


def holding_quote(app, position):
    quote = app.prices.get(position.symbol)
    if quote and quote.currency == position.currency:
        last_split = app.book.latest_split.get(position.symbol)
        if not last_split or (quote.session_day or quote.asof.date()) >= last_split:
            return quote
    return None


def holding_context(app, position, on_quote=None):
    symbol, currency = position.symbol, position.currency
    refresh = secondary(
        "Refresh quote", lambda e: app.request_stock_quote(force=True), ft.Icons.REFRESH
    )
    actions = [stock_selector(app, lambda e: app.go(app.tab, e.control.value)), refresh]
    if app.tab == "Stock inquiry":
        actions.append(secondary("Enter market price", app.price_form, ft.Icons.EDIT_OUTLINED))
    controls = ft.Row(actions, wrap=True, spacing=10, run_spacing=8)
    summary = ft.Column(spacing=12, data="page-summary")

    def draw(update=False):
        quote = holding_quote(app, position)
        marked = mark(position, quote.price if quote else None)
        listing = quote.listing if quote else None
        name = (
            listing.name
            if listing and listing.name.upper() != symbol
            else "Listing name unavailable"
        )
        brokerage_count = len(position.accounts)
        exchange = f"{market_name(quote)} · {currency}" if listing else currency
        brokerages = f"{brokerage_count} brokerage" + ("s" if brokerage_count != 1 else "")
        identity = ft.Column(
            [
                ft.Row([tag(symbol), text(exchange, 13, MUTED)], spacing=8),
                text(name, 22, bold=True),
            ],
            spacing=5,
            col={"xs": 12, "sm": 7},
        )
        quote_info = ft.Column(
            [
                text(quote.source if quote else "Awaiting a matching quote", 13, NAVY, True),
                *([text(when(quote), 12, MUTED)] if quote else []),
            ],
            spacing=2,
            col={"xs": 12, "sm": 5},
        )
        figures = [
            Metric(
                "Market price", unit_price(quote.price, currency) if quote else "—",
                f"Per share · {currency}", ft.Icons.SHOW_CHART,
            ),
            Metric(
                "Units held", units(position.qty), brokerages, ft.Icons.LAYERS_OUTLINED,
            ),
            Metric(
                "Open cost basis", dollars(position.basis, currency=currency),
                f"Average cost {unit_price(position.average, currency)} per share", ft.Icons.PAYMENTS_OUTLINED,
            ),
            Metric(
                "Unrealised P/L",
                dollars(marked.profit, True, currency),
                f"Market value {dollars(marked.value, currency=currency)}",
                ft.Icons.QUERY_STATS,
                percentage=percent(marked.return_pct),
                color=tone(marked.profit),
            ),
        ]
        info = [
            ft.ResponsiveRow(
                [identity, quote_info],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                run_spacing=10,
            ),
        ]
        refresh.disabled = symbol in app.quote_busy
        if refresh.disabled:
            info.append(text("Checking the latest quote…", 12, MUTED))
        elif symbol in app.quote_errors:
            info.append(text(f"Quote refresh unavailable. {app.quote_errors[symbol]}", 12, RED))
        summary.controls = [panel(info, pad=14), metric_cards(app.content_width, figures)]
        if update:
            if quote and on_quote:
                on_quote(quote)
            app.page.update()

    draw()
    app.update_holding_panel = lambda: draw(update=True)
    return [controls, summary], draw


CARD_HEIGHT = 490


def dividend_panel(app, position, width=None):
    """Dividend facts for the selected holding, from saved history and the dividend ledger."""
    symbol, currency = position.symbol, position.currency
    quote = holding_quote(app, position)
    facts = dividend_facts(
        symbol,
        position,
        quote.price if quote else None,
        app.histories.get(symbol),
        list(app.payments.rows.values()),
        today_sg(),
    )

    def pct(v):
        return "—" if v is None else f"{v:,.2f}%"

    growth = (
        f"{facts.growth[0]:,.2f}% a year over {facts.growth[1]} years" if facts.growth else "—"
    )
    rows = [
        ["Dividend per share, last 12 months",
         unit_price(facts.annual_per_share, currency) if facts.annual_per_share is not None else "—"],
        ["Dividend yield", pct(facts.yield_pct)],
        ["Yield on your cost", pct(facts.yield_on_cost)],
        ["Payment frequency", facts.frequency],
        ["Dividend growth", growth],
        ["Years of consecutive increases", str(facts.streak) if facts.growth else "—"],
        ["Next ex-dividend date", facts.next_ex.isoformat() if facts.next_ex else "None recorded"],
        ["Next payment date", facts.next_pay.isoformat() if facts.next_pay else "None recorded"],
    ]
    return panel(
        [
            text("Dividends", 22, bold=True),
            text(
                "Per-share figures come from saved Yahoo history. Growth and increases use complete "
                "calendar years only. A dash means the history is too short or the price is missing.",
                13,
                MUTED,
            ),
            ft.Column(
                [
                    ft.Container(
                        ft.Row(
                            [text(name, 13, MUTED, expand=True), text(value, 14, bold=True)],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        height=40,
                        padding=ft.Padding.symmetric(horizontal=4),
                        border=ft.Border.only(bottom=ft.BorderSide(0.6, RULE)),
                        alignment=ft.Alignment.CENTER_LEFT,
                    )
                    for name, value in rows
                ],
                spacing=0,
            ),
        ]
    )


def inquiry(app):
    out = [
        heading(
            "Stock inquiry",
            "Inspect a holding, its remaining cost and the price needed to exit after fees.",
        )
    ]
    if not app.symbol:
        return out + [empty("No open holdings", "Record a buy before inspecting a stock.")]
    p = app.book.positions[app.symbol]
    currency = p.currency
    holding, _ = holding_context(app, p)
    out.extend(holding)
    series = app.histories.get(app.symbol)
    side_by_side = app.table_width >= 1000
    room = app.table_width - 16 - 76  # inner width left for two cards
    price_width, dividend_width = room * 3 // 5, room * 2 // 5
    if series is None:
        body = [text("Loading price history…" if app.history_busy else "Price history is not available yet. Use Refresh market history on Performance.", 14, MUTED)]
    else:
        def pick(name):
            app.inquiry_period = name
            app.render()

        body = price_chart(
            series, app.symbol, currency, p.average, app.book.trades,
            app.inquiry_period, max(320, (price_width if side_by_side else app.table_width - 38)), pick,
        )
    price = panel([text("Market price", 22, bold=True), *body])
    dividends = dividend_panel(app, p, dividend_width if side_by_side else None)
    if side_by_side:
        # A fixed height keeps the two cards level whatever each holds.
        out += pairs([price, dividends], True, weights=(3, 2), heights=(CARD_HEIGHT,))
    else:
        out += [price, dividends]
    exit_input = field(
        "Estimated total selling fee",
        app.inquiry_fees.get(currency, "2.00" if currency == "USD" else "0"),
        width=225,
        prefix=currency + " ",
    )
    result = text("", 22, NAVY, True)
    err = text("", 13, RED)

    def calculate(e=None):
        try:
            fee = fee_value(exit_input.value, "Estimated selling fee", currency)
            result.value = (
                f"{unit_price(break_even(p.basis, p.qty, fee, currency), currency)} per share"
            )
            app.inquiry_fees[currency], err.value = exit_input.value, ""
        except PortfolioError as exc:
            result.value, err.value = "—", str(exc)
        err.visible = bool(err.value)
        if e is not None:
            app.page.update()

    calculate()
    out += [
        panel(
            [
                text("Break-even selling price", 19, bold=True),
                text(
                    "Sell all open units in this stock. Enter the combined fixed fee for the sale or sales.",
                    14,
                    MUTED,
                ),
                ft.Row([exit_input, primary("Calculate break-even", calculate)], wrap=True),
                result,
                err,
                text(
                    f"Open cost plus selling fees, divided by units and rounded up to {minor_unit(currency)} {currency}.",
                    12,
                    MUTED,
                ),
            ]
        ),
    ]
    lots = sorted(
        (lot for queue in p.accounts.values() for lot in queue),
        key=lambda lot: (lot.purchase.day, lot.purchase.id),
    )
    size = 10
    app.lot_page = min(app.lot_page, max(0, (len(lots) - 1) // size))
    rows = [
        [
            f"#{lot.purchase.id}",
            display_date(lot.purchase.day),
            lot.purchase.broker,
            units(lot.qty),
            unit_price(lot.purchase.price, currency),
            dollars(lot.basis, currency=currency),
        ]
        for lot in lots[app.lot_page * size : (app.lot_page + 1) * size]
    ]
    if app.book.method == "AVCO":
        pool_rows = [
            [
                lot.purchase.broker,
                units(lot.qty),
                unit_price(lot.basis / lot.qty, currency),
                dollars(lot.basis, currency=currency),
            ]
            for lot in lots
        ]
        return out + [
            panel(
                [
                    text("Average-cost pools", 19, bold=True),
                    grid(
                        ["Brokerage", "Units held", "Average cost", "Remaining cost"],
                        pool_rows,
                        540,
                        app.table_width,
                        numeric=(1, 2, 3),
                    ),
                ]
            ),
            secondary(
                "Open recovery planner",
                lambda e: app.go("Recovery planner", p.symbol),
                ft.Icons.TUNE,
            ),
        ]
    content = [
        text("Open purchase lots", 19, bold=True),
        grid(
            ["Buy ID", "Date", "Brokerage", "Units left", "Buy price", "Remaining cost"],
            rows,
            660,
            app.table_width,
            numeric=(3, 4, 5),
        ),
    ]
    if len(lots) > size:
        content.append(
            pager(app.lot_page, len(lots), size, lambda i: app.change_page("lot_page", i))
        )
    out += [
        panel(content),
        secondary(
            "Open recovery planner", lambda e: app.go("Recovery planner", p.symbol), ft.Icons.TUNE
        ),
    ]
    return out


def planner(app):
    out = [
        heading(
            "Recovery planner",
            "Model additional purchases and a sale of the entire position. Your ledger stays separate.",
        )
    ]
    if not app.symbol:
        return out + [empty("No open holdings", "Record a buy to model a recovery scenario.")]
    p = app.book.positions[app.symbol]
    currency = p.currency
    q = holding_quote(app, p)
    suggestion = units(q.price) if q else ""
    target = field("Target selling price", suggestion, 185, prefix=currency + " ")
    exit_fee = field("Total selling fee", "0.00", 170, prefix=currency + " ")
    future_price = field("Further buy price", suggestion, 185, prefix=currency + " ")
    future_fee = field("Further buy fee", "0.00", 170, prefix=currency + " ")
    legs = []
    holder = ft.Column(spacing=9)
    result_cards = ft.Container()
    result_note = text("", 14, NAVY, True)
    result_box = ft.Column([result_cards, result_note], spacing=10, visible=False)
    result_items = []
    error = text("", 14, RED, True)
    error.visible = False

    def invalidate(e=None):
        result_box.visible = False
        error.visible = False
        if e:
            app.page.update()

    edited_prices = set()

    def edit_assumption(e):
        edited_prices.add(e.control.label)
        invalidate(e)

    target.on_change = future_price.on_change = edit_assumption
    exit_fee.on_change = future_fee.on_change = invalidate

    def apply_quote(quote):
        if not result_box.visible:
            for control in (target, future_price):
                if control.label not in edited_prices:
                    control.value = units(quote.price)

    holding, resize_holding = holding_context(app, p, on_quote=apply_quote)
    out.extend(holding)

    def repaint_legs():
        holder.controls.clear()
        for index, fields in enumerate(legs, start=1):
            btn = ft.IconButton(
                icon=ft.Icons.DELETE_OUTLINE,
                icon_color=MUTED,
                tooltip=f"Remove buy {index}",
                on_click=lambda e, group=fields: remove(group),
            )
            holder.controls.append(
                ft.Row([text(f"Buy {index}", 13, NAVY, True), *fields, btn], wrap=True)
            )

    def remove(fields):
        legs.remove(fields)
        repaint_legs()
        invalidate()
        app.page.update()

    def add(e=None):
        group = [
            field("Quantity", width=140, on_change=invalidate),
            field("Purchase price", width=170, prefix=currency + " ", on_change=invalidate),
            field("Buy fee", "0.00", 155, prefix=currency + " ", on_change=invalidate),
        ]
        legs.append(group)
        repaint_legs()
        invalidate()
        if e:
            app.page.update()

    add()

    def calculate(e):
        nonlocal result_items
        try:
            entries = []
            for qty, price, fee in legs:
                if (
                    not qty.value.strip()
                    and not price.value.strip()
                    and fee_value(fee.value, currency=currency) == 0
                ):
                    continue
                entries.append({"quantity": qty.value, "price": price.value, "fee": fee.value})
            res = scenario(
                p, entries, target.value, exit_fee.value, future_price.value, future_fee.value
            )
            extra = "Unreachable" if res.extra is None else f"{res.extra:,} shares"
            if res.extra is None:
                explanation = "The further buy price must be below the target to close this loss."
            elif res.extra == 0:
                explanation = (
                    "The planned position already covers its costs at the target selling price."
                )
            else:
                explanation = f"These extra shares require {dollars(res.further_outlay, currency=currency)} including the further buy fee."
            result_items = [
                Metric(
                    "Revised average cost",
                    unit_price(res.basis / res.qty, currency),
                    f"{units(res.qty)} units after planned buys",
                    ft.Icons.PAYMENTS_OUTLINED,
                ),
                Metric(
                    "P/L at target",
                    dollars(res.profit, True, currency),
                    f"Buy outlay {dollars(res.outlay, currency=currency)}",
                    ft.Icons.QUERY_STATS,
                    color=tone(res.profit),
                ),
                Metric(
                    "Further units needed",
                    extra,
                    "Beyond the planned buys",
                    ft.Icons.LAYERS_OUTLINED,
                ),
            ]
            result_cards.content = metric_cards(app.content_width, result_items)
            result_note.value = f"Planned break-even selling price {unit_price(res.exit_price, currency)}. {explanation}"
            result_box.visible, error.visible = True, False
        except PortfolioError as exc:
            error.value, error.visible, result_box.visible = str(exc), True, False
        app.page.update()

    purchases = panel(
        [
            text("Hypothetical purchases", 19, bold=True),
            holder,
            secondary("Add another buy", add, ft.Icons.ADD),
        ]
    )
    assumptions = panel(
        [
            text("Exit assumptions", 19, bold=True),
            ft.Row([target, exit_fee], wrap=True),
            text(
                "If the position still has a loss, how many more whole shares would close it at this buy price?",
                14,
                MUTED,
            ),
            ft.Row([future_price, future_fee], wrap=True),
            primary("Calculate scenario", calculate, ft.Icons.TUNE),
            error,
        ]
    )
    aligned = ft.Container()
    is_wide = None

    def layout():
        nonlocal is_wide
        wide = app.table_width >= 1000
        if wide == is_wide:
            return
        is_wide = wide
        purchases.expand = assumptions.expand = None
        aligned.content = ft.Column(pairs([purchases, assumptions], wide, weights=(7, 5)), spacing=12)

    def resize():
        resize_holding()
        layout()
        if result_box.visible:
            result_cards.content = metric_cards(app.content_width, result_items)

    layout()
    app.resize_planner = resize
    out.append(aligned)
    out.append(result_box)
    return out
