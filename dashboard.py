# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""The all-market holdings dashboard, reported in Singapore dollars."""

from datetime import timedelta, timezone

import flet as ft

from frost_theme import frost_dialog

from portfolio import ZERO, dollars, percent, unit_price, units
from valuation import SGD
from widgets import (
    MUTED,
    RED,
    grid,
    pager,
    secondary,
    text,
    tone,
)

SGT = timezone(timedelta(hours=8))


def sgd(value, signed=False):
    return dollars(value, signed, SGD)


def ordered_holdings(view, order):
    if order == "Exchange":
        return sorted(view.rows, key=lambda r: (r.market.casefold(), r.position.symbol))
    if order == "Symbol":
        return list(view.rows)
    attr = {"Market value": "value", "Unrealised P/L": "gain", "Return (%)": "return_pct"}[order]
    return sorted(
        view.rows,
        key=lambda r: (getattr(r, attr) is None, -(getattr(r, attr) or ZERO), r.position.symbol),
    )


def holdings_grid(app, view):
    def inspect(symbol):
        app.page.pop_dialog()
        app.go("Stock inquiry", symbol)

    rows = ordered_holdings(view, app.holdings_order)
    table = []
    for row in rows:
        p, q, weight = row.position, row.quote, view.weight(row)
        market = ft.Column(
            [text(row.market, 13, bold=True), text(p.currency, 12, MUTED)],
            spacing=1,
            alignment=ft.MainAxisAlignment.CENTER,
        )
        price = ft.Column(
            [
                text(unit_price(q.price, p.currency) if q else "Awaiting price", 14, bold=True),
                text(f"{q.source} · {q.asof:%d %b}" if q else "No quote", 10, MUTED),
            ],
            spacing=1,
            alignment=ft.MainAxisAlignment.CENTER,
        )
        table.append(
            [
                ft.TextButton(p.symbol, on_click=lambda e, sym=p.symbol: inspect(sym)),
                market,
                units(p.qty),
                unit_price(p.average, p.currency),
                price,
                dollars(p.basis, currency=p.currency),
                dollars(row.local.value, currency=p.currency),
                text(dollars(row.local.profit, True, p.currency), 14, tone(row.local.profit), True),
                text(percent(row.return_pct), 13, tone(row.return_pct)),
                "—" if weight is None else f"{weight:.1f}%",
            ]
        )
    return grid(
        [
            "Symbol",
            "Market / CCY",
            "Units",
            "Avg cost",
            "Market price",
            "Open cost",
            "Market value",
            "Open P/L",
            "Return",
            "Weight",
        ],
        table,
        1010,
        app.table_width,
        numeric=(2, 3, 5, 6, 7, 8, 9),
    )


def allocation_detail(app, group, portfolio_value, portfolio_cost=None):
    entries = sorted(
        group.entries, key=lambda entry: (-entry.value, entry.holding.position.symbol, entry.label)
    )
    multiple_labels = len({entry.label for entry in entries}) > 1
    size = 10
    body = ft.Column(tight=True, spacing=12)
    dialog = frost_dialog(
        modal=True,
        title=text(f"{group.label} allocation", 21, bold=True),
        content=ft.Container(body, width=min(710, app.table_width)),
        scrollable=True,
        actions=[secondary("Close", lambda e: app.page.pop_dialog())],
    )

    def inspect(symbol):
        app.page.pop_dialog()
        app.go("Stock inquiry", symbol)

    def draw(index):
        rows = []
        for entry in entries[index * size : (index + 1) * size]:
            h = entry.holding
            symbol = h.position.symbol
            rows.append(
                [
                    ft.TextButton(symbol, on_click=lambda e, s=symbol: inspect(s)),
                    *([entry.label] if multiple_labels else []),
                    h.market,
                    h.position.currency,
                    units(entry.qty),
                    sgd(entry.value),
                    f"{entry.value / group.value:.1%}" if group.value else "—",
                    f"{entry.value / portfolio_value:.1%}",
                ]
            )
        headers = [
            "Symbol",
            *(["Group"] if multiple_labels else []),
            "Market",
            "CCY",
            "Units",
            "Value · SGD",
            "Of group",
            "Of portfolio",
        ]
        n = len(headers)
        body.controls = [
            text(
                f"{sgd(group.value)} · {group.value / portfolio_value:.1%} of portfolio market value",
                16,
                bold=True,
            ),
            text(
                f"Open cost {sgd(group.basis)}  ·  Open P/L {sgd(group.value - group.basis, True) if group.basis is not None else '—'}",
                14,
            ),
            text(
                f"Return on cost {percent((group.value - group.basis) * 100 / group.basis) if group.basis else '—'}  ·  Allocation at cost {f'{group.basis / portfolio_cost:.1%}' if group.basis is not None and portfolio_cost else '—'}",
                13,
                MUTED,
            ),
            grid(headers, rows, 650, 690, numeric=(n - 4, n - 3, n - 2, n - 1)),
        ]
        if len(entries) > size:

            def turn(page_index):
                draw(page_index)
                dialog.update()

            body.controls.append(pager(index, len(entries), size, turn))

    draw(0)
    app.page.show_dialog(dialog)


def fx_notices(app, currencies):
    notices = []
    missing = sorted(currencies - {SGD} - app.rates.keys())
    if missing:
        notices.append(
            text(
                "Awaiting SGD rates for "
                + ", ".join(missing)
                + ". Affected totals appear when a rate is available.",
                14,
                MUTED,
                True,
            )
        )
    if app.fx_cache_error and currencies - {SGD}:
        notices.append(
            text(
                "Local FX cache unavailable. Verified rates can still be used in this session. "
                + app.fx_cache_error,
                13,
                RED,
            )
        )
    errors = {c: msg for c, msg in app.fx_errors.items() if c in currencies}
    if errors:
        messages = [f"{c} · {msg}" for c, msg in sorted(errors.items())]
        notices.append(
            text(
                "FX refresh incomplete. Saved rates are used where available. "
                + " ".join(messages),
                13,
                RED,
            )
        )
    return notices


def rates_panel(app, currencies):
    foreign = sorted(currencies - {SGD})
    if not foreign:
        return []
    used = [app.rates[c] for c in foreign if c in app.rates]

    def local_time(stamp):
        return stamp.astimezone(SGT).strftime("%d %b %Y, %H:%M SGT")

    if app.fx_busy:
        summary = "Checking the latest exchange rates…"
    elif used:
        summary = f"Oldest rate in use · {local_time(min(f.asof for f in used))}"
    else:
        summary = "Waiting for the first available exchange rate."
    entries = []
    for code in foreign:
        fx = app.rates.get(code)
        if fx:
            price = f"{fx.rate:.12f}".rstrip("0").rstrip(".")
            source = "Yahoo Finance · " + " / ".join(fx.pairs)
            entries.append([f"1 {code}", "S$" + price, local_time(fx.asof), source])
        else:
            entries.append([f"1 {code}", "Awaiting rate", "—", "Yahoo Finance"])
    tile = ft.ExpansionTile(
        title=text("Exchange rates to SGD", 16, bold=True),
        subtitle=text(summary, 12, MUTED),
        expanded=app.rates_expanded,
        on_change=lambda e: setattr(app, "rates_expanded", e.data),
        controls=[
            grid(
                ["From", "SGD equivalent", "FX quote time", "Source"], entries, 620, app.table_width
            )
        ],
        maintain_state=False,
    )
    return [tile]
