# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Income mix, payout calendar, period returns and holding comparisons."""

from calendar import month_abbr
from datetime import timedelta

import flet as ft

from frost_theme import FROST

from analysis_metrics import (
    BENCHMARKS,
    GROUPINGS,
    PAYOUT_SOURCES,
    RANGES,
    benchmark_return,
    future_payments,
    group_label,
    holding_returns,
    monthly_returns,
    range_start,
    yield_groups,
)
from charts import columns_chart, legend, ring_png, stock_profit_chart
from dashboard import sgd
from dividend_stats import (
    ALL_TIME,
    INTERVALS,
    TRAILING,
    YEAR_TO_DATE,
    growth_by_holding,
    monthly_by_year,
    received_buckets,
    totals_by_group,
)
from portfolio import ZERO, dollars, percent
from valuation import complete_sum
from widgets import (
    ALLOCATION_COLOURS,
    AMBER,
    BLUE,
    MUTED,
    NAVY,
    choose,
    grid,
    panel,
    text,
)


def setting(app, title, attr, options, width=175):
    def change(e):
        setattr(app, attr, e.control.value)
        app.render()

    return choose(title, [(value, value) for value in options], getattr(app, attr), change, width)


def title_row(title, controls):
    return ft.Row(
        [text(title, 20, bold=True), ft.Row(controls, wrap=True, spacing=9)],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        wrap=True,
        run_spacing=10,
    )


def details(app, title, cols, rows, width=None):
    return ft.ExpansionTile(
        title=text(title, 14, bold=True),
        controls=[grid(cols, rows, max(300, len(cols) * 100), width or app.table_width)],
    )


def yield_panels(app, records, today):
    def calc(by):
        return yield_groups(
            app.book,
            records,
            app.histories,
            app.prices,
            app.rates,
            today,
            by,
            app.payout_source,
        )

    groups, total, events = calc(app.yield_group)
    by_yield = app.yield_metric == "Yield (%)"

    def fmt(v):
        return "—" if v is None else f"{v:,.2f}%"

    def breakdown(g):
        return [
            g.label,
            sgd(g.payout),
            fmt(g.percentage(g.value)),
            fmt(g.percentage(g.basis)),
            f"{g.covered}/{g.holdings}",
        ]

    header = title_row(
        "Dividend yield and annual payout",
        [
            setting(app, "Payout source", "payout_source", PAYOUT_SOURCES, 225),
            setting(app, "Show measure", "yield_metric", ("Yield (%)", "Annual payout (SGD)"), 195),
            setting(app, "Group yield by", "yield_group", GROUPINGS),
        ],
    )
    description = {
        PAYOUT_SOURCES[
            0
        ]: "Eligible net receipts from the last 12 months are rescaled to shares held today.",
        PAYOUT_SOURCES[
            1
        ]: "Net cash actually received in the last 12 months for securities still held.",
        PAYOUT_SOURCES[
            2
        ]: "Recorded expected payments in the coming 12 months for securities still held.",
    }[app.payout_source]
    guide = text(
        f"{description} {events} records cover {total.covered} of {total.holdings} open holdings. "
        "Currency groups use the trading currency. "
        "Yield divides that income by the full group’s market value. Yield on cost uses open cost.",
        13,
        MUTED,
    )
    amounts = [(g.label, g.percentage(g.value) if by_yield else g.payout) for g in groups]

    amounts.sort(key=lambda row: (row[1] is None, -(row[1] or ZERO), row[0]))

    def page_to(i):
        app.change_page("yield_page", i)

    yield_card = panel(
        [
            header,
            ft.Row(
                [
                    ft.Column(
                        [
                            text("Annual net payout · SGD", 12, MUTED),
                            text(sgd(total.payout), 24, NAVY, True),
                        ],
                        spacing=2,
                    ),
                    ft.Column(
                        [
                            text("Portfolio yield", 12, MUTED),
                            text(fmt(total.percentage(total.value)), 24, NAVY, True),
                        ],
                        spacing=2,
                    ),
                    ft.Column(
                        [
                            text("Yield on cost", 12, MUTED),
                            text(fmt(total.percentage(total.basis)), 24, NAVY, True),
                        ],
                        spacing=2,
                    ),
                ],
                wrap=True,
                spacing=32,
            ),
            guide,
            *(
                [legend([("Yield at market value", BLUE), ("Yield on cost · marker", AMBER)])]
                if by_yield and amounts
                else []
            ),
            stock_profit_chart(
                amounts,
                "SGD",
                app.table_width - 8,
                app.yield_page,
                page_to,
                as_percent=by_yield,
                signed=False,
                cost_yields={g.label: g.percentage(g.basis) for g in groups} if by_yield else None,
                missing="Unavailable",
            )
            if amounts
            else text("Record a purchase to see holding yields.", 14, MUTED),
            details(
                app,
                "Yield and payout details",
                ["Group", "Net annual payout", "Yield", "Yield on cost", "Covered"],
                [breakdown(g) for g in groups],
            ),
        ]
    )
    mix = groups if app.income_mix == app.yield_group else calc(app.income_mix)[0]
    mix_head = title_row(
        "Passive income diversification",
        [setting(app, "Group income mix by", "income_mix", GROUPINGS)],
    )
    if total.payout is None or total.payout <= 0:
        mix_body = [
            text(
                "Add a positive payout for this source and its exchange rate to see the income mix.",
                14,
                MUTED,
            )
        ]
    else:
        # Every group is drawn and listed. Nothing is folded into an "Other" slice.
        parts = sorted(
            ((g.label, g.payout) for g in mix if g.payout is not None and g.payout > 0),
            key=lambda part: (-part[1], part[0]),
        )
        palette = FROST.colours(len(parts))
        data, lines = [], []
        for (label, amount), colour in zip(parts, palette):
            share = amount / total.payout
            data.append((float(share), 0, colour))
            lines.append(
                ft.Row(
                    [
                        ft.Container(width=9, height=9, bgcolor=colour, border_radius=5),
                        text(label, 13, expand=True),
                        text(f"{share:.1%}", 13, MUTED, bold=True),
                    ],
                    spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )
        wide = app.table_width >= 700
        size = min(364 if wide else 324, app.table_width - 4)
        ring = ft.Stack(
            [
                ft.Image(
                    src=ring_png(tuple(data), FROST.wash),
                    width=size,
                    height=size,
                    semantics_label="Doughnut showing each holding or group’s share of annual net dividends.",
                ),
                ft.Column(
                    [
                        text(sgd(total.payout), 28 if size >= 280 else 22, NAVY, True),
                        text("Annual net income · SGD", 12, MUTED),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    tight=True, spacing=3,
                ),
            ],
            width=size,
            height=size,
            alignment=ft.Alignment.CENTER,
        )
        legend_box = ft.Container(
            ft.Column(
                [
                    text("Largest shares" if len(lines) > 6 else "Share of net income", 13, bold=True),
                    ft.Column(lines[:6], spacing=8, tight=True),
                ],
                spacing=10, tight=True,
            ),
            width=250,
            padding=14,
            bgcolor="#88D7E9F5",
            border_radius=12,
            border=ft.Border.all(1, FROST.line),
        )
        graphic = ft.Row(
            [ring, legend_box], spacing=32, alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ) if wide else ft.Column(
            [ring, legend_box], spacing=12, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
        mix_body = [
            ft.Container(graphic, alignment=ft.Alignment.CENTER),
            details(
                app,
                f"All income shares ({len(parts)})",
                ["Group", "Net annual payout", "Income share"],
                [
                    [
                        g.label,
                        sgd(g.payout),
                        f"{g.payout / total.payout:.1%}" if g.payout is not None else "—",
                    ]
                    for g in mix
                ],
            ),
        ]
    return [
        yield_card,
        panel(
            [
                mix_head,
                text(
                    f"{app.payout_source} · Share of annual net income from open holdings, in SGD.",
                    13,
                    MUTED,
                ),
                *mix_body,
            ]
        ),
    ]


def income_total(label, value, note, *, col=12):
    return ft.Column(
        [text(label, 13, MUTED, True), text(sgd(value), 25, bold=True), text(note, 12, MUTED)],
        spacing=3,
        col=col,
    )


def records_charts(app, records, today, width=None):
    """Dividends received over time and by holding (or another group), as in the reference app."""
    width = width or app.table_width - 8
    years = sorted({today.year, *(d.day.year for d in records)}, reverse=True)
    periods = (TRAILING, YEAR_TO_DATE, ALL_TIME, *map(str, years))
    if app.income_period not in periods:
        app.income_period = TRAILING
    labels, values, received_rows = received_buckets(
        records, app.rates, app.income_period, app.income_interval, today
    )
    total = complete_sum(values)
    figures = [[label, sgd(v)] for label, v in zip(labels, values) if v is None or v != ZERO]
    received = panel(
        [
            text("Dividends received over time", 20, bold=True),
            ft.Row(
                [
                    setting(app, "Period", "income_period", periods, 190),
                    setting(app, "History interval", "income_interval", INTERVALS, 145),
                ],
                spacing=9, run_spacing=8, wrap=True,
            ),
            income_total("Total received", total, "Net dividends in the selected period"),
            *columns_chart(
                labels,
                [("Received net · SGD", values, BLUE)],
                width,
                "Net dividends received by period.",
                annotate=True,
            ),
            text(
                "Net dividends received, in SGD. Select a bar for the exact figure. "
                "Scheduled payments are on the Payment calendar below.",
                12,
                MUTED,
            ),
            details(app, "Dividends received figures", ["Period", "Net amount · SGD"], figures, width + 8)
            if figures
            else text("No dividends received in this period.", 13, MUTED),
        ]
    )
    groups = totals_by_group(
        received_rows,
        app.rates,
        "Received",
        lambda p: group_label(
            p.symbol, app.record_group, p.currency, app.prices, app.histories
        ),
    )
    by_group = panel(
        [
            text("Dividends received by group", 20, bold=True),
            setting(app, "Group by", "record_group", GROUPINGS, 175),
            income_total(
                "Total received", complete_sum(v for _, v in groups),
                f"Net dividends · {app.income_period}",
            ),
            *columns_chart(
                [g for g, _ in groups],
                [("Received net · SGD", [v for _, v in groups], BLUE)],
                width,
                "Net dividends received by group.",
                annotate=True,
            ),
            text(
                "Uses the same period as the chart over time. Currency groups use the payment currency.",
                12,
                MUTED,
            ),
            details(
                app,
                "Dividends received by group figures",
                ["Group", "Net amount · SGD"],
                [[g, sgd(v)] for g, v in groups],
                width + 8,
            ),
        ]
    )
    return [received, by_group]


def calendar_panel(app, records, today, edit, width=None):
    width = width or app.table_width - 8
    end, bins, amounts = future_payments(records, app.rates, today)
    dates = list(bins)
    total = complete_sum(amounts)
    controls = [
        title_row(
            "Payment calendar",
            [text(f"{today:%d %b %Y} – {end - timedelta(days=1):%d %b %Y}", 13, MUTED)],
        ),
        ft.ResponsiveRow(
            [
                income_total(
                    "Expected in the next 12 months",
                    total,
                    "Net scheduled payments",
                    col={"xs": 12, "sm": 6},
                ),
                income_total(
                    "Monthly average",
                    total / 12 if total is not None else None,
                    "Next 12 months ÷ 12",
                    col={"xs": 12, "sm": 6},
                ),
            ]
        ),
        *columns_chart(
            [m.strftime("%b\n%y") for m in dates],
            [("Expected net · SGD", amounts, BLUE)],
            width,
            "Expected net dividends for each calendar month in the next 12 months.",
            annotate=True,
        ),
        text(
            "Each bar sums dated expected payments. The first and last months cover only dates inside the displayed window.",
            12,
            MUTED,
        ),
        details(
            app,
            "Monthly payment amounts",
            ["Calendar month", "Payments", "Expected net · SGD"],
            [[m.strftime("%B %Y"), str(len(bins[m])), sgd(a)] for m, a in zip(dates, amounts)],
            width + 8,
        ),
    ]
    scheduled = sorted((p for p in records if p.status == "Expected"), key=lambda p: (p.day, p.id))
    if scheduled:
        controls.append(
            ft.ExpansionTile(
                title=text(f"Manage scheduled payments ({len(scheduled)})", 14, bold=True),
                controls=[
                    ft.Row(
                        [
                            text(f"{p.day:%d %b %Y}  ·  {p.symbol}", 14, bold=True),
                            text(dollars(p.net, currency=p.currency), 14),
                            ft.TextButton(
                                "Confirm receipt" if p.day <= today else "Edit payment",
                                on_click=lambda e, p=p: edit(app, p, confirm=p.day <= today),
                            ),
                        ],
                        wrap=True,
                    )
                    for p in scheduled
                ],
            )
        )
    return panel(controls)


def returns_panels(app, today):
    chart_width = app.table_width - 8

    def benchmark_changed(e):
        app.benchmark = e.control.value
        app.request_history()
        app.render()

    cutoff = range_start(app.dynamics_period, today)
    rows = monthly_returns(app.perf_rows, cutoff)
    series = [("Portfolio return", tuple(r[1] for r in rows), BLUE)]
    comparison = app.benchmark in BENCHMARKS
    if comparison:
        series.append(
            (
                app.benchmark,
                tuple(benchmark_return(app.histories, app.benchmark, r[2], r[3]) for r in rows),
                AMBER,
            )
        )
    monthly = panel(
        [
            title_row(
                "Dynamics of portfolio returns",
                [
                    setting(
                        app,
                        "Return history",
                        "dynamics_period",
                        ("3 months", "6 months", "1 year", "YTD", "Since inception"),
                    ),
                    choose(
                        "Compare with",
                        [(k, k) for k in ("No benchmark", *BENCHMARKS)],
                        app.benchmark,
                        benchmark_changed,
                        220,
                    ),
                ],
            ),
            text(
                "Each bar is the SGD return for that month or partial month. Purchases, sales and net dividends are weighted by date.",
                13,
                MUTED,
            ),
            *columns_chart(
                [r[0].strftime("%b\n%y") for r in rows],
                series,
                chart_width,
                "Monthly portfolio returns in percent.",
                mode="paired" if comparison else "signed",
                percentage=True,
                annotate=not comparison,
            ),
            *(
                [
                    text(
                        "Benchmark bars show the ETF’s SGD price return over the same observed dates. They exclude distributions and fees.",
                        12,
                        MUTED,
                    )
                ]
                if comparison
                else []
            ),
            details(
                app,
                "Monthly return figures",
                ["Month", *(n for n, _, _ in series)],
                [
                    [r[0].strftime("%b %Y"), *(percent(v[i]) for _, v, _ in series)]
                    for i, r in enumerate(rows)
                ],
            ),
        ]
    )
    holding = holding_returns(app.per_symbol_series, app.histories, app.holding_period, today)
    return [
        monthly,
        panel(
            [
                title_row(
                    "Holdings performance",
                    [setting(app, "Holding return period", "holding_period", RANGES, 195)],
                ),
                text(
                    "Return in SGD on each currently held security, including its trades, fees, dividends and currency movements. One day uses the latest two trading closes.",
                    13,
                    MUTED,
                ),
                stock_profit_chart(
                    holding,
                    "SGD",
                    chart_width,
                    app.holding_return_page,
                    lambda i: app.change_page("holding_return_page", i),
                    as_percent=True,
                    missing="Unavailable",
                )
                if holding
                else text("No open holdings.", 14, MUTED),
                details(
                    app,
                    "Holding return figures",
                    ["Holding", "Period return"],
                    [[s, percent(v)] for s, v in holding],
                ),
            ]
        ),
    ]


def growth_panels(app, records, today, width=None):
    """Average annual dividend growth per holding and a year-by-year monthly comparison."""
    width = width or app.table_width - 8
    growth = growth_by_holding(app.book, app.histories, today)
    if growth:
        spans = sorted({span for _, _, span in growth})
        note = (
            "Compound annual growth of dividends per share, using complete calendar years in the "
            f"saved history ({spans[0]}"
            + (f" to {spans[-1]}" if spans[-1] != spans[0] else "")
            + " years). Holdings with a shorter record are left out."
        )
        growth_body = columns_chart(
            [s for s, _, _ in growth],
            [("Average annual growth", [g for _, g, _ in growth], ALLOCATION_COLOURS[0])],
            width,
            "Average annual dividend growth by holding.",
            mode="signed",
            percentage=True,
            annotate=True,
        )
    else:
        note = (
            "Needs at least two complete calendar years of dividend history for a holding. "
            "Use Refresh market history to load more."
        )
        growth_body = [text("No holding has enough dividend history yet.", 14, MUTED)]
    growth_figures = [[sym, f"{pct:,.2f}%", f"{span} years"] for sym, pct, span in growth]
    out = [
        panel(
            [
                title_row("Average annual growth", []),
                text(note, 13, MUTED),
                *growth_body,
                details(
                    app,
                    "Average annual growth figures",
                    ["Holding", "Growth a year", "Years used"],
                    growth_figures,
                    width + 8,
                )
                if growth_figures
                else text("Open holdings with two or more complete years of dividends appear here.", 12, MUTED),
            ]
        )
    ]

    by_year, incomplete = monthly_by_year(records, app.rates)
    years = sorted(by_year)[-3:]
    if years:
        series = [(str(y), by_year[y], ALLOCATION_COLOURS[i]) for i, y in enumerate(years)]
        labels = [month_abbr[m] for m in range(1, 13)]
        caveat = (
            f" Some {', '.join(map(str, sorted(incomplete & set(years))))} amounts are missing an exchange rate and show as zero."
            if incomplete & set(years)
            else ""
        )
        out.append(
            panel(
                [
                    title_row("Dividend growth", []),
                    text(
                        "Net dividends received in SGD, month by month, for each of the last three years."
                        + caveat,
                        13,
                        MUTED,
                    ),
                    *columns_chart(
                        labels,
                        series,
                        width,
                        "Net dividends received by month and year.",
                        mode="grouped",
                    ),
                    details(
                        app,
                        "Dividend growth figures",
                        ["Month", *(str(y) for y in years)],
                        [
                            [labels[m], *(sgd(by_year[y][m]) for y in years)]
                            for m in range(12)
                        ],
                        width + 8,
                    ),
                ]
            )
        )
    return out
