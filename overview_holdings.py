# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Native-currency positions, allocation and contributors for the Overview."""

import flet as ft

from frost_theme import frost_dialog

from charts import doughnut
from analytics import sparkline_values
from dashboard import allocation_detail, holdings_grid, ordered_holdings
from dividends import today_sg
from widgets import spark
from portfolio import dollars, percent, units, unit_price
from frost_theme import WHITE, action, menu, panel, rule, title, txt

GROUPS = ("Sector", "Holding", "Market", "Currency", "Brokerage")
GROUP_NOUNS = {
    "Sector": ("sector", "sectors"),
    "Holding": ("holding", "holdings"),
    "Market": ("market", "markets"),
    "Currency": ("currency", "currencies"),
    "Brokerage": ("brokerage", "brokerages"),
}
SORTS = ("Market value", "Unrealised P/L", "Return (%)", "Symbol", "Exchange")


def pair(first, second, s, *, right=False, ink=None):
    return ft.Column(
        [txt(first, 14, ink or s.ink, 600), txt(second, 12, ink or s.muted, 500)],
        spacing=0,
        tight=True,
        horizontal_alignment=ft.CrossAxisAlignment.END if right else ft.CrossAxisAlignment.START,
    )


def stock_name(r):
    return r.quote.listing.name if r.quote and r.quote.listing else "Listing details unavailable"


def shares(quantity):
    whole, point, fraction = units(quantity).partition(".")
    return f"{int(whole):,}{point}{fraction}"


def stock_id(r, s):
    symbol = txt(r.position.symbol, 15, s.accent, 700)
    return ft.Column(
        [
            symbol,
            txt(
                stock_name(r),
                12,
                s.muted,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
            ),
        ],
        spacing=1,
        tight=True,
    )


def detail_dialog(app, name, controls, s, *, width=510, extra_action=None):
    fit = min(width, max(190, int(app.page.width or 900) - 95))
    app.page.show_dialog(
        frost_dialog(
            modal=True,
            scrollable=True,
            title=title(name, 24, s),
            content=ft.Container(ft.Column(controls, spacing=11, tight=True), width=fit),
            actions=[
                *([extra_action] if extra_action else []),
                action(s, "Close", lambda e: app.page.pop_dialog()),
            ],
        )
    )


def position_detail(app, r, view, s):
    p = r.position
    currency = p.currency
    q = r.quote

    def go(e):
        app.page.pop_dialog()
        app.go("Stock inquiry", p.symbol)

    metrics = [
        ("Shares", shares(p.qty)),
        ("Average cost per share", unit_price(p.average, currency)),
        ("Last price", unit_price(q.price, currency) if q else "Awaiting price"),
        ("Open cost basis", dollars(p.basis, currency=currency)),
        ("Market value", dollars(r.local.value, currency=currency)),
        ("Unrealised P/L", dollars(r.local.profit, True, currency)),
        ("Return on open cost", percent(r.return_pct)),
        (
            "Portfolio weight",
            f"{view.weight(r):.2f}%" if view.weight(r) is not None else "—",
        ),
    ]
    rows = [
        ft.Row(
            [
                txt(k, 14, s.muted, expand=True),
                txt(
                    v,
                    15,
                    s.sign(r.local.profit)
                    if k in ("Unrealised P/L", "Return on open cost")
                    else s.ink,
                    600,
                ),
            ],
            spacing=10,
        )
        for k, v in metrics
    ]
    detail_dialog(
        app,
        f"{p.symbol} · {currency}",
        [
            txt(stock_name(r), 18, s.ink, 600),
            txt(
                f"{r.market}  ·  {view.sectors.get(p.symbol, 'Unclassified')}",
                13,
                s.muted,
            ),
            rule(s.line),
            *rows,
            txt(f"{q.source} · {q.asof:%d %b %Y}" if q else "Quote pending", 12, s.muted),
        ],
        s,
        extra_action=action(s, "Stock inquiry", go, primary=True),
    )


def table_row(app, view, r, s, width):
    header = r is None
    price = width >= 775
    trend = width >= 900
    columns = [
        ("Holding", 22, False),
        ("Market", 8, False),
        ("Shares", 7, True),
        *([("Last price", 11, True)] if price else []),
        *([("30 days", 10, True)] if trend else []),
        ("Open cost", 14, True),
        ("Market value", 14, True),
        ("Open P/L", 16, True),
    ]
    if header:
        cells = [txt(c, 12, s.muted, 600) for c, _, _ in columns]
    else:
        p = r.position
        cur = p.currency
        q = r.quote
        cost, value, profit = (
            dollars(p.basis, currency=cur),
            dollars(r.local.value, currency=cur),
            dollars(r.local.profit, True, cur),
        )
        line = sparkline_values(app.histories.get(p.symbol), today_sg())
        cells = [
            stock_id(r, s),
            pair(r.market, cur, s),
            txt(shares(p.qty), 14, s.ink, 500),
            *([txt(unit_price(q.price if q else None, cur), 14, s.ink, 500)] if price else []),
            *([spark(line) if line else txt("—", 14, s.muted, 500)] if trend else []),
            txt(cost, 14, s.ink, 500),
            txt(value, 14, s.ink, 600),
            pair(profit, percent(r.return_pct), s, right=True, ink=s.sign(r.local.profit)),
        ]
    parts = [
        ft.Container(
            c,
            expand=weight,
            alignment=ft.Alignment.CENTER_RIGHT if numeric else ft.Alignment.CENTER_LEFT,
        )
        for c, (_, weight, numeric) in zip(cells, columns)
    ]
    return ft.Container(
        ft.Row(parts, spacing=8),
        height=33 if header else 57,
        width=width,
        padding=ft.Padding.symmetric(horizontal=13),
        border=ft.Border(bottom=ft.BorderSide(1, s.line)),
        bgcolor=s.wash if header else ft.Colors.TRANSPARENT,
        on_click=None if header else lambda e: position_detail(app, r, view, s),
        ink=not header,
    )


def mobile_row(app, view, r, s):
    p = r.position
    cur = p.currency
    measures = [
        ("Open cost", dollars(p.basis, currency=cur)),
        ("Market value", dollars(r.local.value, currency=cur)),
        ("Shares", shares(p.qty)),
    ]
    top = ft.Row(
        [
            ft.Container(stock_id(r, s), expand=True),
            pair(
                dollars(r.local.profit, True, cur),
                percent(r.return_pct),
                s,
                right=True,
                ink=s.sign(r.local.profit),
            ),
        ],
        spacing=8,
    )
    return ft.Container(
        ft.Column(
            [
                top,
                txt(f"{r.market} · {cur}", 12, s.muted),
                ft.Row(
                    [
                        ft.Column([txt(k, 11, s.muted), txt(v, 14, s.ink, 600)], spacing=0)
                        for k, v in measures
                    ],
                    spacing=16,
                    wrap=True,
                ),
            ],
            spacing=5,
        ),
        padding=12,
        border=ft.Border(bottom=ft.BorderSide(1, s.line)),
        ink=True,
        on_click=lambda e: position_detail(app, r, view, s),
    )


def holdings(app, view, s, width):
    rows = ordered_holdings(view, app.holdings_order)
    sort = menu(
        s,
        "Order holdings",
        app.holdings_order,
        SORTS,
        lambda v: app.change_page("holdings_order", v),
        prefix="Sort · ",
    )
    head = ft.Row(
        [title("Current holdings", 25, s), sort],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        wrap=width < 390,
        run_spacing=3,
    )
    bar = ft.Container(
        ft.Column(
            [
                head,
                txt(
                    "Cost, value and open P/L in each holding’s trading currency.",
                    13,
                    s.muted,
                ),
            ],
            spacing=2,
        ),
        padding=ft.Padding.only(left=16, right=16, top=12, bottom=10),
    )
    table = (
        [
            table_row(app, view, None, s, width - 2),
            *[table_row(app, view, r, s, width - 2) for r in rows],
        ]
        if width >= 674
        else [mobile_row(app, view, r, s) for r in rows]
    )

    def detailed(e):
        detail_dialog(app, "Holdings · all columns", [holdings_grid(app, view)], s, width=1120)

    foot = ft.Row(
        [
            action(
                s,
                "View all columns",
                detailed,
                icon=ft.Icons.TABLE_CHART_OUTLINED,
                subtle=True,
            )
        ],
        alignment=ft.MainAxisAlignment.END,
    )
    return panel(s, ft.Column([bar, *table, foot], spacing=0), width=width)


def allocation(app, view, s, width):
    def change(value):
        app.allocation_group = value
        if value == "Sector":
            app.request_history()
        app.render()

    groups = view.allocation_groups(app.allocation_group)  # every group, none folded into Other
    colours = s.colours(len(groups))
    select = menu(s, "Group allocation", app.allocation_group, GROUPS, change)
    heading = ft.Row(
        [title("Allocation", 24, s), select],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        wrap=width < 280,
        run_spacing=2,
    )
    parts = [heading, txt("Share of SGD market value", 13, s.muted)]
    if not groups:
        parts.append(
            txt(
                "Weights appear when share prices and SGD exchange rates are available.",
                14,
                s.muted,
            )
        )
        return panel(s, ft.Column(parts, spacing=9), padding=16, width=width)

    def open_group(group):
        allocation_detail(app, group, view.value, view.basis)

    size = 177 if width > 280 else 154
    chart = doughnut(groups, view.value, view.basis, colours, WHITE, open_group, size=size)
    centre = ft.TransparentPointer(
        ft.Column(
            [
                txt(len(groups), 32, s.ink, 600, font=s.display),
                txt(GROUP_NOUNS[app.allocation_group][len(groups) != 1], 12, s.muted),
            ],
            spacing=0,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
    )
    parts += [
        ft.Container(
            ft.Stack([chart, centre], width=size, height=size, alignment=ft.Alignment.CENTER),
            alignment=ft.Alignment.CENTER,
        ),
        rule(s.line),
    ]
    legend = []
    for i, g in enumerate(groups):
        weight = g.value / view.value
        marker = ft.Container(
            bgcolor=colours[i],
            width=10,
            height=10,
            border_radius=2,
        )
        legend.append(
            ft.Container(
                ft.Row(
                    [
                        marker,
                        txt(g.label, 13, s.ink, 500, expand=True),
                        txt(f"{weight:.1%}", 13, s.ink, 600),
                    ],
                    spacing=9,
                ),
                padding=ft.Padding.symmetric(horizontal=4, vertical=7),
                height=44 if (app.page.width or 1200) < 900 else 36,
                border_radius=5,
                ink=True,
                tooltip=f"Inspect {g.label}",
                on_click=lambda e, grp=g: open_group(grp),
            )
        )
    parts.append(ft.Column(legend, spacing=0))
    if any(g.label == "Unclassified" for g in groups):
        parts.append(txt("Unclassified holdings have no verified sector.", 12, s.muted))
    return panel(s, ft.Column(parts, spacing=7), padding=16, width=width)


def contributors(app, view, s, width):
    known = [r for r in view.rows if r.gain is not None]
    best = max((r for r in known if r.gain > 0), key=lambda r: r.gain, default=None)
    worst = min((r for r in known if r.gain < 0), key=lambda r: r.gain, default=None)
    segments = []
    for label, r, icon, empty in [
        ("Largest open gain", best, ft.Icons.TRENDING_UP, "No open gains"),
        ("Largest open loss", worst, ft.Icons.TRENDING_DOWN, "No open losses"),
    ]:
        tone = s.sign(r.gain) if r else s.muted
        desc = ft.Column(
            [
                txt(label, 12, s.muted, 600),
                txt(r.position.symbol if r else empty, 14, s.ink, 700),
            ],
            spacing=1,
            expand=True,
        )
        price = (
            pair(
                dollars(r.local.profit, True, r.position.currency),
                percent(r.return_pct),
                s,
                right=True,
                ink=tone,
            )
            if r
            else ft.Container()
        )
        segments.append(
            ft.Container(
                ft.Row([ft.Icon(icon, size=19, color=tone), desc, price], spacing=9),
                padding=ft.Padding.symmetric(horizontal=15, vertical=9),
                expand=width >= 600,
                width=None if width >= 600 else width - 2,
                ink=bool(r),
                on_click=(lambda e, row=r: position_detail(app, row, view, s)) if r else None,
            )
        )
    body = (
        ft.Row(
            [
                segments[0],
                ft.Container(width=1, height=34, bgcolor=s.line),
                segments[1],
            ],
            spacing=0,
        )
        if width >= 600
        else ft.Column([segments[0], rule(s.line), segments[1]], spacing=0)
    )
    return panel(s, body, width=width)
