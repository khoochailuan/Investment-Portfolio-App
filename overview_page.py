# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""The combined live portfolio Overview in the shared Frost frame."""

import flet as ft

from widgets import equal_height_row

from dashboard import fx_notices, rates_panel
from frost_shell import dimensions
from home_extras import extras
from frost_theme import FROST, action, menu, panel, title, txt
from overview_holdings import allocation, contributors, detail_dialog, holdings
from overview_metrics import metrics
from valuation import Overview, SGD


def dates(app, view):
    stamps = sorted({r.quote.asof.date() for r in view.rows if r.quote})
    exchange_days = sorted(
        {app.rates[c].asof.date() for c in view.currencies if c != SGD and c in app.rates}
    )
    parts = []
    if stamps:
        prefix = "Quotes" if len(stamps) == 1 else "Latest quote"
        parts.append(f"{prefix} {stamps[-1]:%d %b %Y}")
    if exchange_days:
        prefix = "FX" if len(exchange_days) == 1 else "Oldest FX"
        parts.append(f"{prefix} {exchange_days[0]:%d %b %Y}")
    parts.append("Totals in SGD")
    return "   ·   ".join(parts)


def heading(app, view, s, width):
    name = title("Portfolio overview", 37 if width < 600 else 46, s)
    intro = ft.Column([name, txt(dates(app, view), 13, s.muted, 500)], spacing=0, tight=True)
    basis = menu(
        s,
        "Cost basis method",
        app.book.method,
        ("FIFO", "AVCO"),
        app.set_cost_method,
        prefix="Cost basis · ",
    )
    controls = []
    controls.append(basis)
    if not (app.snapshot_demo and app.workspace == "demo"):
        refresh = action(
            s,
            "Refreshing…" if app.refreshing else "Refresh prices & FX",
            app.refresh,
            icon=ft.Icons.SYNC,
        )
        refresh.disabled = app.refreshing or not (view.rows or view.currencies - {SGD})
        controls.append(refresh)
    controls.append(action(s, "Add transaction", app.trade_form, primary=True, icon=ft.Icons.ADD))
    actions = ft.Row(controls, spacing=8, wrap=True, run_spacing=6, tight=True)
    if width < 930:
        return ft.Column([intro, actions], spacing=9)
    return ft.Row([intro, ft.Container(expand=True), actions], spacing=12)


def fx_footer(app, view, s, width):
    foreign = sorted(view.currencies - {SGD})
    label = "Amounts reported in SGD"
    if len(foreign) == 1 and foreign[0] in app.rates:
        r = app.rates[foreign[0]]
        label = f"1 {r.currency} = {format(r.rate, 'f')} SGD   ·   {r.asof:%d %b %Y}"
    elif foreign:
        label = (
            f"{len(foreign)} foreign "
            + ("currency" if len(foreign) == 1 else "currencies")
            + " in portfolio"
        )
    line = ft.Row(
        [
            ft.Icon(ft.Icons.CURRENCY_EXCHANGE, size=15, color=s.muted),
            txt(label, 12, s.muted, 500),
        ],
        spacing=7,
        tight=True,
    )
    items = [line]
    if app.snapshot_demo and app.workspace == "demo":
        items.append(txt("Demo prices and exchange rates", 12, s.muted))
    if foreign:

        def details(e):
            body = [
                txt(
                    "Costs and values use the same latest available SGD exchange rate for each currency.",
                    14,
                    s.ink,
                ),
                *rates_panel(app, view.currencies),
            ]
            detail_dialog(app, "Exchange rates", body, s, width=720)

        items.append(action(s, "Exchange rates", details, subtle=True))
    return ft.Row(
        items,
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        spacing=10,
        run_spacing=3,
        wrap=width < 650,
    )


def overview(app):
    s = FROST
    _, _, _, width = dimensions(app.page.width)
    sectors = {k: v.sector for k, v in app.histories.items() if v.sector}
    view = Overview(app.book, app.prices, app.rates, app.sales_report(), sectors)
    app.autoload(view)
    summary = [heading(app, view, s, width), metrics(app, view, s, width)]
    body = []
    if app.refresh_report:
        body.append(txt(app.refresh_report, 14, s.accent, 600))
    if view.missing_quotes:
        body.append(
            txt(
                "Awaiting stock prices for "
                + ", ".join(view.missing_quotes)
                + ". Refresh prices or check the symbol in Transactions.",
                14,
                s.negative,
                600,
            )
        )
    body += fx_notices(app, view.currencies)
    if view.rows:
        body += extras(app, view, s, width)
        if width >= 1030:
            right = max(285, round(width * 0.248))
            gap = 16
            body.append(
                equal_height_row(
                    [
                        holdings(app, view, s, width - right - gap),
                        allocation(app, view, s, right),
                    ],
                    spacing=gap,
                )
            )
        else:
            body.extend([holdings(app, view, s, width), allocation(app, view, s, width)])
        if view.gain is not None:
            body.append(contributors(app, view, s, width))
    elif not app.book.trades:
        from tools_views import checklist

        body.append(checklist(app, view))
    else:
        body.append(
            panel(
                s,
                ft.Column(
                    [
                        title("No open holdings", 23, s),
                        txt(
                            "Record a purchase to see its cost, value and open P/L. Completed sales appear in realised P/L.",
                            14,
                            s.muted,
                        ),
                    ],
                    spacing=6,
                ),
                padding=19,
                width=width,
            )
        )
    body.append(fx_footer(app, view, s, width))
    return summary, body
