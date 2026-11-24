# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Flet screens for portfolio risk, rebalancing, the daily digest and the AI copilot."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import flet as ft

import copilot
from analytics import (
    GROUP_LIMIT,
    SINGLE_POSITION_LIMIT,
    build_digest,
    digest_text,
    parse_percent,
    plan_rebalance,
    revised_allocation,
    risk_report,
)
from charts import doughnut
from dashboard import sgd
from dividends import today_sg
from frost_theme import FROST, menu
from insights_views import refresh_history
from market_news import headlines
from market_watch import MailSettings
from portfolio import PortfolioError, ZERO, percent, units
from widgets import (
    AMBER,
    INK,
    MUTED,
    NAVY,
    POSITIVE,
    RED,
    WHITE,
    bar_row,
    empty,
    field,
    grid,
    heading,
    pairs,
    Metric,
    metric_cards,
    panel,
    primary,
    secondary,
    tag,
    text,
)


def not_ready(app, view, title, subtitle, actions=()):
    """Shared early exit when there is nothing to analyse yet."""
    out = [heading(title, subtitle, list(actions))]
    if not view.rows:
        out.append(empty("No holdings yet", "Add a transaction first. This page analyses what you hold today."))
    elif view.value is None:
        out.append(
            empty(
                "Prices or exchange rates are missing",
                "Use Refresh prices & FX on the Overview, then return here.",
            )
        )
    else:
        return None
    return out


# --------------------------------------------------------------------------- risk


def pct(value, digits=1):
    return "—" if value is None else f"{value:.{digits}f}%"


def risk_view(app):
    view = app.overview_view()
    early = not_ready(
        app, view, "Portfolio risk", "Concentration, volatility and drawdown for what you hold today."
    )
    if early:
        return early
    report = risk_report(view, app.histories, today_sg())
    n = len(report.holdings)
    top = max(report.holdings, key=lambda h: h.weight or ZERO)
    vol, fall = report.portfolio_volatility, report.portfolio_drawdown
    out = [
        heading(
            "Portfolio risk",
            "Concentration, volatility and drawdown for what you hold today.",
            [refresh_history(app, True)],
        ),
        metric_cards(
            app.content_width,
            [
                Metric(
                    "Largest holding", pct(report.top1), f"{top.symbol} · share of value",
                    ft.Icons.PIE_CHART_OUTLINE,
                    color=RED if report.top1 > SINGLE_POSITION_LIMIT else INK,
                ),
                Metric("Top three holdings", pct(report.top3), "Combined share of value", ft.Icons.BAR_CHART),
                Metric(
                    "Effective holdings", f"{report.effective_holdings:.1f}",
                    f"Equal-weight equivalent · {n} held",
                    ft.Icons.LAYERS_OUTLINED,
                ),
                Metric(
                    "Volatility (annual)", pct(vol),
                    f"Worst 1-year fall {pct(abs(fall)) if fall is not None else '—'}"
                    if vol is not None else "Needs daily price history",
                    ft.Icons.STACKED_LINE_CHART,
                ),
            ],
            header=True,
        ),
    ]
    if app.history_busy:
        out.append(text("Downloading daily price history…", 13, MUTED))
    elif report.without_history:
        out.append(text("No usable history yet for " + ", ".join(report.without_history) + ".", 13, AMBER))

    # Flags
    rows = []
    for f in report.flags:
        colour = RED if f.level == "High" else AMBER
        rows.append(
            ft.Row(
                [
                    ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color=colour, size=22),
                    ft.Column([text(f.title, 14, INK, True), text(f.detail, 12, MUTED)], spacing=1, expand=True),
                    tag(f.level),
                ],
                spacing=12,
            )
        )
    if not rows:
        rows = [
            ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE, color=POSITIVE), text("No flags at the current thresholds.", 14, INK, True)], spacing=10)
        ]
    out.append(panel([text("What to review", 22, INK, True), *rows], pad=19))

    # Exposure
    def set_group(value):
        app.change_page("risk_group", value)

    items = dict(report.exposures)[app.risk_group]
    biggest = max((s for _, s in items), default=Decimal(1))
    bars = [
        bar_row(label, share, max(biggest, Decimal(1)), pct(share),
                AMBER if share > GROUP_LIMIT and label not in ("Unclassified", "ETF") else NAVY)
        for label, share in items
    ]
    out.append(
        panel(
            [
                ft.Row(
                    [
                        text("Where the money sits", 22, INK, True),
                        menu(FROST, "Group exposure", app.risk_group,
                             ("Sector", "Market", "Currency", "Holding"), set_group, prefix="By · "),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN, wrap=True,
                ),
                text("Share of SGD market value. Amber means above the 40% group limit.", 12, MUTED),
                *bars,
            ]
        )
    )

    # Per holding
    lines = []
    for h in sorted(report.holdings, key=lambda h: -(h.weight or ZERO)):
        lines.append(
            [
                text(h.symbol, 14, INK, True),
                pct(h.weight),
                text(pct(h.volatility), 14, AMBER if (h.volatility or 0) >= 40 else INK),
                pct(h.max_drawdown and abs(h.max_drawdown)),
                text(pct(h.current_drawdown and abs(h.current_drawdown)), 14,
                     RED if (h.current_drawdown or 0) <= -20 else INK),
            ]
        )
    out.append(
        panel(
            [
                text("Holding by holding", 22, INK, True),
                grid(["Holding", "Weight", "Volatility (1y)", "Worst fall (1y)", "Below 1y high"],
                     lines, 640, app.table_width, numeric=(1, 2, 3, 4)),
                text(
                    "Volatility is the annualised standard deviation of daily moves. Worst fall is the "
                    "largest peak-to-trough drop in the last year. Portfolio volatility replays today's "
                    "weights over the same days in each stock's own currency, so it ignores exchange-rate "
                    "moves. Past behaviour does not predict the future.",
                    12, MUTED,
                ),
            ]
        )
    )
    return out


# --------------------------------------------------------------------------- rebalance

DEPOSIT, WITHDRAW = "Deposit / Rebalance", "Withdrawal"
ACTIONS = (DEPOSIT, WITHDRAW)
REVISED_GROUPS = ("Holding", "Sector", "Market", "Currency")
REVISED_VIEWS = ("After trades", "Current")


def revised_panel(app, view, plan):
    """Doughnut of the allocation after the planned trades, switchable like the Overview one."""
    after = app.rb_view == REVISED_VIEWS[0]
    shown = revised_allocation(view, plan, app.rb_group, after)
    before = {x.label: x.value for x in revised_allocation(view, plan, app.rb_group, False)}
    total = sum((x.value for x in shown), ZERO)

    def pick(attr):
        def change(value):
            setattr(app, attr, value)
            app.render()

        return change

    heading = ft.Row(
        [
            text("Revised allocation", 22, INK, True),
            ft.Row(
                [
                    menu(FROST, "Group allocation", app.rb_group, REVISED_GROUPS, pick("rb_group")),
                    menu(FROST, "Before or after trades", app.rb_view, REVISED_VIEWS, pick("rb_view")),
                ],
                wrap=True,
                spacing=9,
            ),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        wrap=True,
        run_spacing=8,
    )
    if total <= 0:
        return panel([heading, text("Nothing to show: no holding has a value after these trades.", 14, MUTED)])
    size = 200
    colours = FROST.colours(len(shown))
    chart = doughnut(shown, total, None, colours, WHITE, lambda g: None, size=size)
    noun = {"Holding": "holding", "Sector": "sector", "Market": "market", "Currency": "currency"}[app.rb_group]
    n = sum(1 for x in shown if x.label != "Cash")  # unspent cash is a slice, not a holding
    centre = ft.TransparentPointer(
        ft.Column(
            [
                text(str(n), 32, INK, True),
                text(noun if n == 1 else noun[:-1] + "ies" if noun.endswith("y") else noun + "s", 12, MUTED),
            ],
            spacing=0,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
    )
    legend = []
    for i, x in enumerate(shown):
        weight = x.value * 100 / total
        was = before.get(x.label)
        note = (
            f"from {was * 100 / view.value:.1f}%"
            if after and was is not None and x.label != "Cash"
            else ""
        )
        legend.append(
            ft.Row(
                [
                    ft.Container(bgcolor=colours[i], width=10, height=10, border_radius=2),
                    text(x.label, 13, INK, expand=True),
                    text(note, 11, MUTED),
                    ft.Container(text(f"{weight:.1f}%", 13, INK, True), width=52, alignment=ft.Alignment.CENTER_RIGHT),
                ],
                spacing=9,
            )
        )
    return panel(
        [
            heading,
            text(
                "Share of SGD market value at the latest cached prices"
                + (", after the suggested trades and any unspent cash." if after else ", before any trades."),
                13,
                MUTED,
            ),
            ft.Row(
                [
                    ft.Stack([chart, centre], width=size, height=size, alignment=ft.Alignment.CENTER),
                    ft.Container(ft.Column(legend, spacing=9), width=340),
                ],
                wrap=True,
                spacing=26,
                run_spacing=14,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        ]
    )


def stash_rebalance(app, fields, cash, band, minimum):
    """Keep what was typed so a re-render does not lose it."""
    app.rb_targets = {s: f.value.strip() for s, f in fields.items() if f.value.strip()}
    app.rb_cash, app.rb_band = cash.value.strip() or "0", band.value.strip() or "2"
    app.rb_min = minimum.value.strip() or "0"


def rebalance_view(app):
    view = app.overview_view()
    early = not_ready(app, view, "Rebalance", "Set target weights and see the trades that restore them.")
    if early:
        return early
    rows = sorted(view.rows, key=lambda r: -(r.value or ZERO))
    fields = {
        r.position.symbol: field("%", app.rb_targets.get(r.position.symbol, ""), 100, text_size=14, hint="e.g. 20")
        for r in rows
    }
    withdrawing = app.rb_action == WITHDRAW
    cash = field(
        "Cash to withdraw (SGD)" if withdrawing else "New cash to invest (SGD)",
        app.rb_cash, 220, text_size=14,
    )
    band = field("Ignore drift under (points)", app.rb_band, 220, text_size=14)
    minimum = field("Minimum per trade (SGD)", app.rb_min, 220, text_size=14)

    def collect():
        stash_rebalance(app, fields, cash, band, minimum)
        targets = {s: parse_percent(v, f"Target for {s}") for s, v in app.rb_targets.items()}
        try:
            extra = Decimal(app.rb_cash)
            threshold = Decimal(app.rb_band)
            floor = Decimal(app.rb_min)
        except Exception:
            raise PortfolioError("Cash, the drift band and the minimum trade must be numbers.") from None
        if not all(v.is_finite() and v >= 0 for v in (extra, threshold, floor)):
            raise PortfolioError("Cash, the drift band and the minimum trade cannot be negative.")
        return targets, extra, threshold, floor

    def calculate(e):
        try:
            targets, extra, threshold, floor = collect()
            sells = app.rb_sales or withdrawing
            app.rb_mode = "Full rebalance" if sells else "Buy only"
            if withdrawing and not targets:
                # With no targets a withdrawal is met by selling every holding in proportion.
                targets = {r.position.symbol: view.weight(r) for r in rows}
            app.rb_plan = plan_rebalance(
                view, targets, ZERO if withdrawing else extra, app.rb_mode, threshold,
                fractional=app.rb_fractional, min_trade=floor,
                withdraw=extra if withdrawing else ZERO,
            )
            app.rb_error = ""
        except PortfolioError as exc:
            app.rb_plan, app.rb_error = None, str(exc)
        app.render()

    def save(e):
        try:
            targets, _, threshold, _ = collect()
            app.store.save_rebalance_settings(targets, threshold)
        except (PortfolioError, OSError) as exc:
            app.notice(str(exc))
            return
        app.notice("Targets saved.")

    def fill(values):
        for s, f in fields.items():
            f.value = values.get(s, "")
        stash_rebalance(app, fields, cash, band, minimum)
        app.page.update()

    def equal(e):
        fill({s: f"{Decimal(100) / len(fields):.1f}" for s in fields})

    def current(e):
        fill({r.position.symbol: f"{view.weight(r):.1f}" for r in rows})

    def set_action(value):
        stash_rebalance(app, fields, cash, band, minimum)
        app.rb_plan = None
        app.change_page("rb_action", value)

    def toggle(name):
        def handler(e):
            stash_rebalance(app, fields, cash, band, minimum)
            app.rb_plan = None
            setattr(app, name, e.control.value)
            app.render()

        return handler

    plan = app.rb_plan
    out = [
        heading(
            "Rebalance",
            "Set target weights and see the trades that restore them.",
            [menu(FROST, "Rebalance action", app.rb_action, ACTIONS, set_action),
             secondary("Save targets", save, ft.Icons.SAVE_OUTLINED),
             primary("Calculate plan", calculate, ft.Icons.CALCULATE_OUTLINED)],
        ),
        metric_cards(
            app.content_width,
            [
                Metric("Portfolio value", sgd(view.value), f"{len(rows)} holdings", ft.Icons.ACCOUNT_BALANCE_WALLET_OUTLINED),
                Metric("Cash to withdraw" if withdrawing else "New cash",
                       sgd(Decimal(app.rb_cash)) if app.rb_cash.replace(".", "", 1).isdigit() else "—",
                       "Raised by selling first" if withdrawing else "Added before sizing trades",
                       ft.Icons.PAYMENTS_OUTLINED),
                Metric("Trades suggested", str(sum(1 for l in plan.lines if l.shares) if plan else "—"),
                       ("Fractional shares" if app.rb_fractional else "Whole shares only") if plan else "After calculating a plan",
                       ft.Icons.SWAP_HORIZ),
                Metric("Shortfall" if plan and plan.cash_left < 0 else "Cash left",
                       sgd(abs(plan.cash_left)) if plan else "—",
                       "Sales fall short of the withdrawal" if plan and plan.cash_left < 0 else
                       f"{plan.reserve:.1f}% of target left uninvested" if plan else "After the suggested trades",
                       ft.Icons.SAVINGS_OUTLINED),
            ],
            header=True,
        ),
    ]
    if app.rb_error:
        out.append(text(app.rb_error, 14, RED, True))
    entries = [
        ft.Row(
            [
                ft.Container(ft.Column([text(r.position.symbol, 14, INK, True),
                                        text(r.quote.listing.name if r.quote and r.quote.listing else "", 11, MUTED, max_lines=1)],
                                       spacing=0), expand=True),
                ft.Container(text(f"{view.weight(r):.1f}%", 14, INK), width=70, alignment=ft.Alignment.CENTER_RIGHT),
                fields[r.position.symbol],
            ],
            spacing=12,
        )
        for r in rows
    ]
    out.append(
        panel(
            [
                ft.Row([text("Target weights", 22, INK, True)], wrap=True),
                text("Leave a box empty to keep that holding as it is. Targets may add to less than 100%: the rest stays in cash.", 12, MUTED),
                ft.Row([secondary("Equal weights", equal), secondary("Use current weights", current)], wrap=True),
                ft.Container(
                    ft.Row([ft.Container(text("Holding", 12, MUTED, True), expand=True),
                            ft.Container(text("Now", 12, MUTED, True), width=70, alignment=ft.Alignment.CENTER_RIGHT),
                            ft.Container(text("Target %", 12, MUTED, True), width=100)], spacing=12),
                    width=560,
                ),
                ft.Container(ft.Column(entries, spacing=8), width=560),
                ft.Container(height=4),
                ft.Row([cash, band, minimum], wrap=True, spacing=12),
                ft.Row(
                    [
                        ft.Switch(label="Allow sales", value=app.rb_sales or withdrawing,
                                  disabled=withdrawing, on_change=toggle("rb_sales")),
                        ft.Switch(label="Fractional shares", value=app.rb_fractional,
                                  on_change=toggle("rb_fractional")),
                    ],
                    wrap=True, spacing=24,
                ),
                text("Without Allow sales the plan only buys with new cash. Fractional shares use six "
                     "decimals. Trades under the minimum are skipped. A withdrawal with no targets sells "
                     "every holding in proportion.", 12, MUTED),
            ]
        )
    )
    if plan:
        wide = app.table_width >= 1000
        lines = []
        for l in plan.lines:
            if l.shares > 0:
                action = text(f"Buy {units(l.shares)}", 14, NAVY, True)
            elif l.shares < 0:
                action = text(f"Sell {units(abs(l.shares))}", 14, AMBER, True)
            else:
                action = text("Hold", 14, MUTED)
            drift = "—" if l.drift is None else f"{abs(l.drift):.1f} pts {'over' if l.drift > 0 else 'under' if l.drift < 0 else ''}".strip()
            row = [text(l.symbol, 14, INK, True), f"{l.weight:.1f}%",
                   "—" if l.target is None else f"{l.target:.1f}%"]
            if not wide:
                row.append(drift)
            row += [action, sgd(abs(l.amount)) if l.shares else "—", f"{l.after:.1f}%"]
            lines.append(row)
        heads = ["Holding", "Now", "Target"] + ([] if wide else ["Drift"]) + ["Action", "Value (SGD)", "After"]
        numeric = tuple(i for i, h in enumerate(heads) if h in ("Now", "Target", "Drift", "Value (SGD)", "After"))
        trades = panel(
            [
                text("Suggested trades", 22, INK, True),
                grid(heads, lines, 520 if wide else 760,
                     (app.table_width - 16) * 3 // 5 - 38 if wide else app.table_width,
                     numeric=numeric),
                text(
                    "Shares at the latest cached prices, rounded toward zero so nothing is "
                    "overspent or oversold. Brokerage fees, tax and price moves are not included. "
                    "Without Allow sales the plan never sells and spends at most your new cash. "
                    "Information only, not advice.",
                    12, MUTED,
                ),
            ]
        )
        out += pairs([revised_panel(app, view, plan), trades], wide, weights=(2, 3))
    return out


# --------------------------------------------------------------------------- digest


def make_digest(app):
    view = app.overview_view()
    risk = risk_report(view, app.histories, today_sg())
    titles = [h["title"] for h in headlines(app.news_feeds, app.book.positions)]
    return view, build_digest(
        view, app.sales_report(), app.histories, list(app.payments.rows.values()),
        app.watch_state, titles, risk, app.rates, today_sg(),
    )


def digest_view(app):
    view = app.overview_view()
    early = not_ready(app, view, "Daily digest", "What changed and what needs attention today.")
    if early:
        return early
    _, d = make_digest(app)
    body = digest_text(d)
    mail = MailSettings.from_env() if not app.store.demo else None

    def save(e):
        try:
            path = app.store.directory / f"digest_{d.as_of.isoformat()}.txt"
            path.write_text(body, encoding="utf-8")
            app.notice(f"Saved {path.name} in your data folder.")
        except OSError as exc:
            app.notice(f"Could not save the digest: {exc}")

    async def send(e):
        try:
            await asyncio.to_thread(mail.deliver, f"Folio Ledger digest · {d.as_of:%d %b %Y}", body)
            app.notice("Digest emailed.")
        except Exception:
            app.notice("The email could not be sent. Check the SMTP settings in START_HERE.txt.")

    actions = [secondary("Refresh prices & FX", app.refresh, ft.Icons.SYNC), secondary("Save as text", save, ft.Icons.DOWNLOAD)]
    actions.append(primary("Email digest", send, ft.Icons.EMAIL_OUTLINED) if mail else primary("Refresh history", lambda e: (app.request_history(force=True), app.render()), ft.Icons.SYNC))
    change = "—" if d.day_change is None else sgd(d.day_change, True)
    out = [
        heading("Daily digest", "What changed and what needs attention today.", actions),
        metric_cards(
            app.content_width,
            [
                Metric("Portfolio value", sgd(d.value), f"As of {d.as_of:%d %b %Y}", ft.Icons.ACCOUNT_BALANCE_WALLET_OUTLINED),
                Metric("Daily change", change, "Since previous close", ft.Icons.SHOW_CHART,
                       percentage=None if d.day_change_pct is None else percent(Decimal(str(round(d.day_change_pct, 2)))),
                       color=FROST.sign(d.day_change) if d.day_change is not None else INK),
                Metric("Realised this month", sgd(d.realised_month, True), "From completed sales", ft.Icons.TASK_ALT,
                       color=FROST.sign(d.realised_month)),
                Metric("Dividends this month", sgd(d.dividends_month), "Received, after tax", ft.Icons.PAID_OUTLINED),
            ],
            header=True,
        ),
    ]
    if d.stale:
        out.append(text("Prices may be out of date for " + ", ".join(d.stale) + ". Use Refresh prices & FX.", 13, AMBER, True))

    def section(title, rows, none):
        return panel([text(title, 20, INK, True), *(rows or [text(none, 13, MUTED)])])

    movers = [
        ft.Row([text(m.symbol, 14, INK, True, expand=True),
                text(percent(Decimal(str(round(m.pct, 2)))), 14, FROST.sign(Decimal(str(m.pct))), True),
                text(sgd(m.change, True), 13, MUTED)], spacing=12)
        for m in d.movers
    ]
    due = [ft.Row([text(s, 14, INK, True, expand=True), text(f"{day:%d %b}", 13, MUTED), text(sgd(v), 14, INK)], spacing=12)
           for s, day, v in d.upcoming]
    alerts = [text(a, 13, INK) for a in d.alerts]
    flags = [ft.Row([ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color=AMBER, size=18), text(f.title, 13, INK, expand=True)], spacing=8) for f in d.flags]
    news = [text("• " + h, 13, INK) for h in d.headlines]
    out += pairs(
        [
            section("Biggest movers", movers, "Download daily history to see movers."),
            section("Dividends due in 30 days", due, "No expected payments recorded."),
            section("Unread price alerts", alerts, "No unread alerts."),
            section("Risk watch", flags, "No risk flags."),
        ],
        app.table_width >= 730,
    )
    out += [
        section("Headlines", news, "Open Market news to load headlines."),
        text("Built from cached prices and daily history, so it is only as fresh as the last refresh. "
             "Run python main.py --digest to print or email it without opening the app.", 12, MUTED),
    ]
    return out


# --------------------------------------------------------------------------- copilot


def copilot_figures(value):
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            label = key.replace("_", " ").capitalize().replace("sgd", "SGD")
            separator = "\n" if isinstance(item, (dict, list)) else "  "
            lines.append(f"{label}{separator}{copilot_figures(item)}")
        return "\n".join(lines)
    if isinstance(value, list):
        return "\n\n".join(copilot_figures(item) for item in value) or "None"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value) if value is not None else "—"


def copilot_view(app):
    cfg = copilot.LLMConfig.from_env()
    mode = f"Model · {cfg.label}" if cfg else "Built-in answers (no model set)"
    box = field(None, hint="Ask about your portfolio…", expand=True, autofocus=True)
    caption = (
        "The configured provider receives the question and selected portfolio figures. "
        "The app uses its computed answer if the call fails or the reply introduces an unrecognised multi-digit figure."
        if cfg else
        "Answers use your saved portfolio data. See START_HERE.txt to connect a language model."
    )

    def send(question):
        question = (question or "").strip()
        if question and not app.copilot_busy:
            app.page.run_task(app.ask_copilot, question)

    box.on_submit = lambda e: send(box.value)
    messages = []
    for m in app.copilot_log:
        mine = m["role"] == "user"
        parts = [text(m["text"], 14, WHITE if mine else INK, selectable=True)]
        if not mine:
            source = m["source"] + (f" · {m['note']}" if m.get("note") else "")
            parts.append(text(source, 11, MUTED))
            if m.get("facts"):
                parts.append(
                    ft.ExpansionTile(
                        title=text("Figures used", 12, NAVY, True),
                        controls=[text(copilot_figures(m["facts"]), 12, MUTED, selectable=True)],
                        tile_padding=ft.Padding.all(0), dense=True,
                    )
                )
        messages.append(
            ft.Container(
                ft.Container(
                    ft.Column(parts, spacing=4, tight=True),
                    padding=ft.Padding.symmetric(horizontal=14, vertical=10),
                    border_radius=14,
                    bgcolor=NAVY if mine else FROST.input,
                    border=None if mine else ft.Border.all(1, FROST.line),
                    width=560,
                ),
                alignment=ft.Alignment.CENTER_RIGHT if mine else ft.Alignment.CENTER_LEFT,
            )
        )
    if app.copilot_busy:
        messages.append(ft.Row([ft.ProgressRing(width=16, height=16, stroke_width=2), text("Working it out…", 13, MUTED)], spacing=8))
    if not messages:
        messages = [text("Ask a question, or tap a suggestion below.", 14, MUTED)]
    chips = ft.Row(
        [ft.TextButton(q, on_click=lambda e, q=q: send(q), style=ft.ButtonStyle(bgcolor="#D9FFFFFF", side=ft.BorderSide(1, FROST.line), shape=ft.RoundedRectangleBorder(radius=18))) for q in copilot.SUGGESTIONS],
        wrap=True, spacing=6, run_spacing=6,
    )

    def clear(e):
        app.copilot_log = []
        app.render()

    return [
        heading(
            "AI Copilot",
            "Ask about returns, holdings or a break-even scenario.",
            [tag(mode), secondary("Clear chat", clear, ft.Icons.DELETE_OUTLINE)],
        ),
        panel(
            [
                chips,
                ft.Column(messages, spacing=10, scroll=ft.ScrollMode.AUTO, height=360 if len(messages) > 1 else None, auto_scroll=True),
                ft.Row([box, primary("Send", lambda e: send(box.value), ft.Icons.SEND)], spacing=8),
                text(caption, 12, MUTED),
            ]
        ),
    ]


# --------------------------------------------------------------------------- first-run checklist


def checklist(app, view):
    """Shown on an empty portfolio. Adding holdings is the only step: prices, exchange rates and
    market history are fetched for them automatically."""
    card = panel(
        [
            text("Get started", 26, INK, True),
            text("Add your holdings to build a working dashboard", 15, MUTED),
            ft.Row(
                [
                    primary("Add transaction", app.trade_form, ft.Icons.ADD),
                    secondary("Import trades", app.import_form),
                ],
                spacing=10,
                wrap=True,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
        ],
        pad=32,
    )
    column = card.content
    column.alignment = ft.MainAxisAlignment.CENTER  # top to bottom
    column.horizontal_alignment = ft.CrossAxisAlignment.CENTER  # left to right
    column.spacing = 14
    return card
