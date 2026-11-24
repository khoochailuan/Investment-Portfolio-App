# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Watchlist editor, market snapshots, threshold alerts and linked headlines."""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import flet as ft

from frost_theme import FROST, WHITE, frost_dialog, menu

from dividends import SGT
from market import Quote, SymbolChoice, fetch_named_quote, resolve_symbol, search_listings
from market_news import headlines
from market_watch import AUTO_DIRECTION, DIRECTIONS, WATCH_INTERVALS, MailSettings, automatic_direction, target_distance
from portfolio import PortfolioError, currency_prefix, number, percent, unit_price, units
from widgets import (
    BLUE,
    INK,
    RULE,
    TINT,
    POSITIVE,
    AMBER,
    range_bar,
    spark,
    MUTED,
    NAVY,
    RED,
    card_rows,
    choose,
    empty,
    field,
    grid,
    heading,
    pager,
    panel,
    primary,
    secondary,
    tag,
    text,
    tone,
)


def local_time(iso):
    return (
        datetime.fromisoformat(iso).astimezone(SGT).strftime("%d %b %Y, %H:%M SGT") if iso else "—"
    )


def watch_editor(app, old=None):
    store = app.store
    checked = Quote.from_record(old["quote"]) if old else None
    symbol = field(
        "Ticker or company name", checked.symbol if checked else "", 335, hint="e.g. ME8U or NVIDIA"
    )
    symbol.disabled = bool(old)
    info = text(
        checked.listing.label if checked else "Choose the listing and exchange you want to watch.",
        14,
        NAVY,
    )
    matches = ft.Column(spacing=1)
    level = field("Watch price (optional)", old["target"] if old and old["target"] else "", 210)
    direction = choose(
        "Alert direction",
        [(AUTO_DIRECTION, "Automatic from quote"), *((s, s) for s in DIRECTIONS)],
        old["direction"] if old and old["target"] and not old.get("automatic_direction") else AUTO_DIRECTION,
        width=225,
    )
    mail_configured = bool(MailSettings.from_env()) and not store.demo
    email = ft.Checkbox(
        label="Also email this alert",
        value=bool(old and old["email"]),
        disabled=not mail_configured,
    )
    rule = text("", 14, NAVY, True)
    interval = WATCH_INTERVALS[app.watch_state["refresh_seconds"]]
    delivery = text(
        f"Price alerts appear in the app while your workspace is open. Auto-refresh runs every {interval}.",
        13, MUTED,
    )
    err = text("", 13, RED)
    seq = 0
    saving = False

    def active():
        return app.store is store and app.editor is view

    def alert_preview(event=None):
        if not (level.value or "").strip():
            rule.value, rule.color = "Add a watch price to turn on an in-app alert.", MUTED
        elif not checked:
            rule.value, rule.color = "Choose a listing to preview the alert.", MUTED
        else:
            try:
                target = units(number(level.value, "Watch price", 8))
                when = (
                    automatic_direction(checked.price, target, old)
                    if direction.value == AUTO_DIRECTION else direction.value
                )
                rule.value = f"Alert {when.lower()} {unit_price(Decimal(target), checked.currency)}."
                rule.color = NAVY
            except PortfolioError as exc:
                rule.value, rule.color = str(exc), RED
        if event:
            err.value = ""
            app.page.update()

    def options(choices):
        def pick(code):
            async def handler(e):
                symbol.value = code
                await verify(code)

            return handler

        matches.controls = [
            ft.TextButton(str(label), on_click=pick(code))
            for code, label in list(choices.items())[:8]
        ]

    async def verify(code=None):
        nonlocal checked, seq
        seq += 1
        ticket = seq
        query = symbol.value.strip()
        checked = None
        matches.controls = []
        info.value = "Checking the listing…"
        err.value = ""
        alert_preview()
        app.page.update()
        try:
            q = await asyncio.to_thread(
                fetch_named_quote if code else resolve_symbol, code or query
            )
            if not active() or ticket != seq:
                return
            checked = q
            symbol.value = q.symbol
            info.value = f"{q.listing.label} · Latest price {unit_price(q.price, q.currency)}"
            level.label = f"Watch price ({q.currency}, optional)"
        except SymbolChoice as exc:
            if not active() or ticket != seq:
                return
            options(exc.choices)
            info.value = "Select the intended listing."
        except PortfolioError as exc:
            if not active() or ticket != seq:
                return
            info.value = str(exc)
        alert_preview()
        app.page.update()

    async def typing(e):
        nonlocal seq, checked
        seq += 1
        ticket = seq
        checked = None
        q = symbol.value.strip()
        matches.controls = []
        info.value = "Choose a listing from the results."
        alert_preview()
        app.page.update()
        if not q:
            return
        await asyncio.sleep(0.35)
        if not active() or ticket != seq:
            return
        try:
            choices = await asyncio.to_thread(search_listings, q, suggest=True)
            if not active() or ticket != seq:
                return
            options(choices)
            if not choices:
                info.value = "No suggestions. Check the full exchange ticker."
        except PortfolioError as exc:
            if not active() or ticket != seq:
                return
            info.value = str(exc)
        app.page.update()

    async def check(e):
        await verify()

    async def save(e):
        nonlocal saving
        if not active() or saving:
            return
        saving = True
        try:
            if not checked:
                await verify()
            if not active() or not checked:
                return
            saved = app.watch.set(
                checked, level.value, direction.value, email.value, old["version"] if old else None
            )
        except PortfolioError as exc:
            err.value = str(exc)
            app.page.update()
            return
        finally:
            saving = False
        app.load_watch()
        app.request_watch(force=True)
        app.go("Watchlist")
        if saved["target"] is None:
            app.notice("Watchlist entry saved.")
        else:
            target = unit_price(Decimal(saved["target"]), checked.currency)
            app.notice(f"In-app price alert saved. {saved['direction']} {target}.")

    if not old:
        symbol.on_change = typing
        symbol.on_submit = check
    else:
        level.label = f"Watch price ({checked.currency}, optional)"
    level.on_change = direction.on_select = alert_preview
    alert_preview()
    settings = [direction]
    if mail_configured or email.value:
        settings.append(email)
    view = [
        heading(
            "Edit watch" if old else "Add to watchlist",
            "Watch a listed stock, ETF or REIT in its trading currency.",
        ),
        panel(
            [
                symbol,
                matches,
                info,
                level,
                rule,
                delivery,
                ft.ExpansionTile(
                    title=text("Alert settings", 14, NAVY, True),
                    controls=[ft.Column(settings, spacing=6)],
                ),
                err,
                ft.Row(
                    [
                        primary("Save watch", save),
                        secondary("Cancel", lambda e: app.go("Watchlist")),
                    ],
                    wrap=True,
                ),
            ]
        ),
    ]
    app.open_editor(view, "Watchlist")


def remove_watch(app, symbol, item):
    store = app.store
    message = text(
        f"Remove {symbol} and its active price target? Past alerts stay in the alert history.", 14
    )

    def remove(e):
        if app.store is not store:
            return
        try:
            app.watch.remove(symbol, item["version"])
        except PortfolioError as exc:
            message.value = str(exc)
            dialog.update()
            return
        app.page.pop_dialog()
        app.go("Watchlist")

    dialog = frost_dialog(
        title=text("Remove watch", 20, bold=True),
        content=message,
        actions=[
            secondary("Cancel", lambda e: app.page.pop_dialog()),
            primary("Remove watch", remove),
        ],
    )
    app.page.show_dialog(dialog)


SORTS = (
    "Symbol",
    "Day change",
    "12M div. yield",
    "Trailing P/E",
    "Near 52-week low",
    "Closest to watch price",
)


def dec(value):
    return Decimal(value) if value is not None else None


def range_position(price, low, high):
    """0 at the 52-week low, 1 at the high; None when the range is unknown or flat."""
    if low is None or high is None or high <= low:
        return None
    return float((price - low) / (high - low))


def watch_sort_key(mode, symbol, item):
    """Sort key: (missing, value, symbol) so rows without the figure always come last."""
    q, stats = Quote.from_record(item["quote"]), item["stats"]
    price = q.price
    paid, pe = dec(stats.get("div12")), dec(stats.get("pe"))
    if mode == "Day change":
        raw = dec(stats.get("change"))
        value = -raw if raw is not None else None
    elif mode == "12M div. yield":
        value = -(paid / price) if paid is not None and price > 0 else None
    elif mode == "Trailing P/E":
        value = pe if pe is not None and pe > 0 else None
    elif mode == "Near 52-week low":
        pos = range_position(price, dec(stats.get("low")), dec(stats.get("high")))
        value = pos
    elif mode == "Closest to watch price":
        gap = target_distance(price, item["target"])
        value = abs(gap[1]) if gap is not None else None
    else:
        value = symbol
    return (value is None, value if value is not None else 0, symbol)


def compact_amount(value, currency):
    """Short money figure for large company totals, e.g. US$391.0B. None shows a dash."""
    if value is None:
        return "—"
    size = abs(value)
    for limit, suffix in ((Decimal("1e12"), "T"), (Decimal("1e9"), "B"), (Decimal("1e6"), "M")):
        if size >= limit:
            body = f"{size / limit:,.1f}{suffix}"
            break
    else:
        body = f"{size:,.0f}"
    return ("−" if value < 0 else "") + (currency_prefix(currency) if currency else "") + body


def watch_distance_text(quote, target):
    gap = target_distance(quote.price, target)
    if gap is None:
        return "—", ""
    difference, relative = gap
    amount = unit_price(abs(difference), quote.currency)
    if not difference:
        return amount, "0.00% · At target"
    percentage = "<0.01%" if abs(relative) < Decimal(".01") else f"{abs(relative):,.2f}%"
    return amount, f"{percentage} {'above target' if difference > 0 else 'below target'}"


def watchlist_view(app):
    watch = app.watch
    intervals = {label: seconds for seconds, label in WATCH_INTERVALS.items()}
    current_interval = None
    alerts_key = ft.ScrollKey("watch-price-alerts")
    schedule = ft.Container()
    body = ft.Column(spacing=15, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
    price_scroll = ft.Row(scroll=ft.ScrollMode.ALWAYS, spacing=0)
    price_panel = panel([])

    async def show_alerts(event):
        await app.page.scroll_to(scroll_key=alerts_key, duration=240)

    alerts = secondary("Price alerts", show_alerts, ft.Icons.NOTIFICATIONS_OUTLINED)
    refresh = secondary(
        "Check prices now",
        lambda e: app.request_watch(force=True),
        ft.Icons.REFRESH,
    )

    def set_sort(value):
        app.watch_page = 0
        app.change_page("watch_sort", value)

    def change_refresh(value):
        app.page.run_task(app.set_watch_refresh, watch, intervals[value])

    def repaint(*, content=True):
        nonlocal current_interval
        state = app.watch_state
        refresh.content = "Checking prices…" if app.watch_busy else "Check prices now"
        refresh.disabled = app.watch_busy or not state["items"]
        if not content:
            return
        interval = WATCH_INTERVALS[state["refresh_seconds"]]
        if interval != current_interval:
            schedule.content = menu(
                FROST, "Refresh interval", interval, intervals, change_refresh,
                prefix="Auto-refresh · ", icon=ft.Icons.SCHEDULE,
            )
            current_interval = interval
        unread = sum(not event["read"] for event in state["events"])
        alerts.visible = bool(unread)
        alerts.content = f"Alerts · {unread} unread"
        body.controls = watchlist_body(app, price_panel, price_scroll, alerts_key)

    repaint()
    app.update_watch_panel = repaint
    return [
        heading(
            "Watchlist",
            "Follow prices, valuations and targets across exchanges.",
            [
                menu(FROST, "Sort watchlist", app.watch_sort, SORTS, set_sort, prefix="Sort · "),
                schedule,
                secondary(
                    "Watchlist news", lambda e: open_news(app, WATCHLIST_NEWS), ft.Icons.NEWSPAPER
                ),
                alerts,
                refresh,
                primary("Add to watchlist", lambda e: watch_editor(app), ft.Icons.ADD),
            ],
        ),
        body,
    ]


def watchlist_body(app, price_panel, price_scroll, alerts_key):
    state = app.watch_state
    out = []
    if app.watch_error:
        out.append(text(app.watch_error, 14, RED))
    lines = []
    items = sorted(state["items"].items(), key=lambda kv: watch_sort_key(app.watch_sort, *kv))
    size = 8
    index = min(app.watch_page, max(0, (len(items) - 1) // size))
    for symbol, item in items[index * size : (index + 1) * size]:
        q = Quote.from_record(item["quote"])
        stats = item["stats"]
        change = Decimal(stats["change"]) if stats.get("change") is not None else None
        low, high = (
            Decimal(stats[k]) if stats.get(k) is not None else None for k in ("low", "high")
        )
        target = unit_price(Decimal(item["target"]), q.currency) if item["target"] else "—"
        pe = Decimal(stats["pe"]) if stats.get("pe") is not None else None
        pe_text = f"{pe:.2f}×" if pe is not None and pe > 0 else "N/M" if pe is not None else "—"

        def stat(key):
            raw = stats.get(key)
            return Decimal(raw) if raw is not None else None

        # Financials are reported in the company's own currency, which can differ from the listing's.
        fund_ccy = stats.get("fund_ccy") or q.currency
        eps, revenue, ebitda, paid = stat("eps"), stat("revenue"), stat("ebitda"), stat("div12")
        yield_text = (
            f"{paid * 100 / q.price:.2f}%" if paid is not None and q.price > 0 else "—"
        )
        eps_text = (
            ("−" if eps < 0 else "") + unit_price(abs(eps), fund_ccy) if eps is not None else "—"
        )
        fund_date = stats.get("fund_asof") or ""
        # Colour cues: a yield of 4% or more is green; a P/E above 35 is amber, 15 or below green.
        yield_pct = paid * 100 / q.price if paid is not None and q.price > 0 else None
        yield_colour = POSITIVE if yield_pct is not None and yield_pct >= 4 else INK
        pe_colour = (
            AMBER if pe is not None and pe > 35 else POSITIVE if pe is not None and 0 < pe <= 15 else INK
        )
        position = range_position(q.price, low, high)
        trend = [float(x) for x in (stats.get("spark") or [])]
        range_cell = (
            ft.Column(
                [text(f"{unit_price(low, q.currency)} – {unit_price(high, q.currency)}", 12, MUTED), range_bar(position)],
                spacing=3, tight=True,
            )
            if position is not None
            else text("—", 14, MUTED)
        )

        def pair(a, b, colour=INK):
            return ft.Column(
                [
                    text(a, 14, colour, True),
                    text(b, 11, MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ],
                spacing=1,
                tight=True,
            )

        actions = ft.Row(
            [
                ft.IconButton(
                    ft.Icon(ft.Icons.EDIT_OUTLINED, semantics_label=f"Edit watch {symbol}"),
                    tooltip=f"Edit watch {symbol}",
                    on_click=lambda e, i=item: watch_editor(app, i),
                ),
                ft.IconButton(
                    ft.Icon(ft.Icons.NEWSPAPER, semantics_label=f"News for {symbol}"),
                    tooltip=f"News for {symbol}",
                    on_click=lambda e, s=symbol: open_news(app, WATCHLIST_NEWS, s),
                ),
                ft.IconButton(
                    ft.Icon(ft.Icons.DELETE_OUTLINE, semantics_label=f"Remove watch {symbol}"),
                    tooltip=f"Remove watch {symbol}",
                    on_click=lambda e, s=symbol, i=item: remove_watch(app, s, i),
                ),
            ],
            spacing=0,
            tight=True,
        )
        lines.append(
            [
                pair(symbol, q.listing.name),
                pair(unit_price(q.price, q.currency), q.currency),
                pair(target, item["direction"] if item["target"] else "No target"),
                pair(*watch_distance_text(q, item["target"])),
                text(percent(change), 14, tone(change), True),
                spark(trend) if trend else text("—", 14, MUTED),
                pair(yield_text, "Trailing 12 months" if paid is not None else "", yield_colour),
                pair(pe_text, stats.get("pe_asof") or "", pe_colour),
                pair(eps_text, f"TTM · {fund_date}" if eps is not None else ""),
                pair(compact_amount(revenue, fund_ccy), f"TTM · {fund_date}" if revenue is not None else ""),
                pair(compact_amount(ebitda, fund_ccy), f"TTM · {fund_date}" if ebitda is not None else ""),
                range_cell,
                pair(local_time(q.asof.isoformat()), q.source),
                actions,
            ]
        )
    if lines:
        # Keep the mounted scroll row when new prices replace its rows.
        price_scroll.controls = grid(
            [
                "Listing", "Latest price", "Watch price", "Distance to target", "Day change",
                "30 days", "12M div. yield", "Trailing P/E", "EPS", "Revenue", "EBITDA",
                "52-week range", "Quote time", "Actions",
            ],
            lines, 1800, app.table_width,
        ).controls
        price_panel.content.controls = [
            price_scroll,
            *(
                [pager(index, len(items), size, lambda i: app.change_page("watch_page", i))]
                if len(items) > size else []
            ),
            text(
                "Distance is the gap between the latest quote and the watch price, shown in the listing's currency and as a percentage of the watch price. Dividend yield uses the past 12 months of dividends per share and the latest price. EPS, revenue and EBITDA use trailing figures in the company's reporting currency. The 52-week bar shows where the price sits between its low and high.",
                12, MUTED,
            ),
        ]
        out.append(price_panel)
    else:
        out.append(
            empty(
                "Your watchlist is empty",
                "Add any verified listing. You can track it before buying shares.",
            )
        )
    for symbol, error in sorted(state.get("errors", {}).items()):
        out.append(text(f"{symbol} · {error}", 13, RED))

    events = sorted(state["events"], key=lambda v: v["when"], reverse=True)
    unread = {e["id"] for e in events if not e["read"]}

    def mark(e):
        try:
            app.watch.mark_read(unread)
        except PortfolioError as exc:
            app.notice(str(exc))
            return
        app.go("Watchlist")

    alert_controls = [
        ft.Row(
            [
                text("Price alerts", 20, bold=True),
                tag(f"{len(unread)} unread"),
                *([secondary("Mark all read", mark)] if unread else []),
            ],
            wrap=True,
        ),
        text(
            f"Checks every {WATCH_INTERVALS[state['refresh_seconds']]} while this workspace is open. "
            f"Last check · {local_time(state.get('last_check'))}",
            13,
            MUTED,
        ),
    ]
    if events:
        page = min(app.alert_page, (len(events) - 1) // size)
        for e in events[page * size : (page + 1) * size]:
            alert_controls.append(
                ft.Column(
                    [
                        text(e["message"], 14, NAVY, not e["read"]),
                        text(f"{local_time(e['when'])}  ·  {e['delivery']}", 12, MUTED),
                    ],
                    spacing=1,
                )
            )
        if len(events) > size:
            alert_controls.append(
                pager(page, len(events), size, lambda i: app.change_page("alert_page", i))
            )
    else:
        alert_controls.append(
            text("An alert appears here when a checked price reaches your target.", 14, MUTED)
        )
    alerts = panel(alert_controls)
    alerts.key = alerts_key
    return out + [alerts]


HOLDINGS_NEWS = "My holdings"
WATCHLIST_NEWS = "Watchlist"


def open_news(app, scope, symbol=None):
    app.news_scope, app.news_symbol, app.news_page = scope, symbol, 0
    app.go("Market news")


def news_symbols(app):
    if app.news_scope == WATCHLIST_NEWS:
        return sorted(app.watch_state["items"])
    return sorted(app.book.positions)


def news_keys(app):
    symbols = news_symbols(app)
    if app.news_symbol is not None:
        return [app.news_symbol] if app.news_symbol in symbols else []
    return symbols


def ago(iso, now=None):
    """Short relative time such as "12m ago" or "3h ago". Falls back to the date after a week."""
    try:
        when = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    seconds = ((now or datetime.now(timezone.utc)) - when).total_seconds()
    if seconds < 90:
        return "just now"
    for limit, unit, label in ((3600, 60, "m"), (86400, 3600, "h"), (7 * 86400, 86400, "d")):
        if seconds < limit:
            return f"{int(seconds // unit)}{label} ago"
    return when.astimezone(SGT).strftime("%d %b")


def day_label(iso, today):
    try:
        day = datetime.fromisoformat(iso).astimezone(SGT).date()
    except (TypeError, ValueError):
        return "Earlier"
    if day == today:
        return "Today"
    if (today - day).days == 1:
        return "Yesterday"
    return day.strftime("%A, %d %b") if (today - day).days < 7 else "Earlier"


def news_view(app):
    symbols = news_symbols(app)
    keys = news_keys(app)
    busy = bool(set(keys) & app.news_busy)
    refresh = secondary(
        "Loading headlines…" if busy else "Refresh headlines",
        lambda e: app.request_news(True),
        ft.Icons.REFRESH,
    )
    refresh.disabled = busy or not keys
    stories = headlines(app.news_feeds, keys)
    out = [
        heading(
            "Market news",
            "Latest stories from the ticker feeds for your holdings and watchlist.",
            [refresh],
        )
    ]

    def chip(label, value, icon=None):
        picked = app.news_scope == value
        return ft.TextButton(
            label,
            icon=icon,
            on_click=lambda e: open_news(app, value),
            style=ft.ButtonStyle(
                color=WHITE if picked else NAVY,
                bgcolor=NAVY if picked else "#D9FFFFFF",
                side=ft.BorderSide(1, RULE),
                shape=ft.RoundedRectangleBorder(radius=18),
                padding=ft.Padding.symmetric(horizontal=14),
            ),
        )

    filters = [
        chip(HOLDINGS_NEWS, HOLDINGS_NEWS, ft.Icons.ACCOUNT_BALANCE_WALLET_OUTLINED),
        chip(WATCHLIST_NEWS, WATCHLIST_NEWS, ft.Icons.VISIBILITY_OUTLINED),
    ]
    if len(symbols) > 1:
        label = "All holdings" if app.news_scope == HOLDINGS_NEWS else "All watchlist stocks"
        filters.append(choose(
            "Stock", [("", label), *((symbol, symbol) for symbol in symbols)],
            app.news_symbol or "",
            lambda e: open_news(app, app.news_scope, e.control.value or None),
            width=185,
        ))
    out.append(ft.Row(filters, wrap=True, spacing=8, run_spacing=6))
    if app.news_error:
        out.append(text(app.news_error, 13, RED))
    failures = [k for k in keys if app.news_feeds.get(k, {}).get("error")]
    if failures:
        out.append(
            text(
                "Feed unavailable for "
                + ", ".join(failures)
                + ". Showing saved headlines where available.",
                13,
                RED,
            )
        )
    if not stories:
        msg = (
            "Loading the selected feeds…"
            if busy
            else "No headlines are available for this selection."
        )
        if not keys:
            msg = (
                "Add a stock to your watchlist to see its news." if app.news_scope == WATCHLIST_NEWS
                else "Add a transaction to see news for a holding."
            )
        return out + [empty("No headlines to show", msg)]

    now = datetime.now(timezone.utc)
    today = now.astimezone(SGT).date()
    owned, watched = set(app.book.positions), set(app.watch_state["items"])
    out.append(
        text(
            f"{len(stories)} stories · latest {ago(stories[0]['time'], now)}",
            13,
            MUTED,
            True,
        )
    )

    def pills(story):
        row = []
        for s in sorted(story["symbols"])[:4]:
            held = s in owned
            row.append(
                ft.Container(
                    text(s, 11, WHITE if held else NAVY, True),
                    bgcolor=NAVY if held else TINT,
                    border_radius=10,
                    padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                    tooltip="You hold this" if held else "On your watchlist" if s in watched else None,
                )
            )
        return row

    def card(story, hero=False):
        from_holdings = bool(story["symbols"] & owned)
        accent = NAVY if from_holdings else BLUE
        body_colour = WHITE if hero else INK
        muted_colour = "#D6EAF5" if hero else MUTED
        meta = ft.Row(
            [
                ft.Container(width=7, height=7, border_radius=4, bgcolor=muted_colour if hero else accent),
                text(story["source"], 12, muted_colour, True, expand=True),
                text(ago(story["time"], now), 12, muted_colour),
            ],
            spacing=6,
        )
        footer = ft.Row(
            [
                ft.Row(pills(story), spacing=5, run_spacing=4, wrap=True, expand=True),
                ft.Icon(ft.Icons.ARROW_OUTWARD, size=15, color=muted_colour if hero else accent),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        return ft.Container(
            ft.Column(
                [meta, text(story["title"], 21 if hero else 15, body_colour, True), footer],
                spacing=7,
                tight=True,
            ),
            url=story["url"],
            tooltip="Open article in browser",
            ink=True,
            padding=ft.Padding.all(16 if hero else 12),
            border_radius=14,
            gradient=ft.LinearGradient(
                begin=ft.Alignment.TOP_LEFT, end=ft.Alignment.BOTTOM_RIGHT, colors=["#1B4D6B", "#2A78A6"]
            ) if hero else None,
            bgcolor=None if hero else "#ECF4FA",
            border=None if hero else ft.Border.all(1, RULE),
        )

    size = 12
    index = min(app.news_page, (len(stories) - 1) // size)
    page_stories = stories[index * size : (index + 1) * size]
    if index == 0:
        out.append(card(page_stories[0], hero=True))
        page_stories = page_stories[1:]
    groups = {}
    for s in page_stories:
        groups.setdefault(day_label(s["time"], today), []).append(s)
    columns = 3 if app.table_width >= 1060 else 2 if app.table_width >= 640 else 1
    for label, items in groups.items():
        out.append(text(label, 14, MUTED, True))
        tiles = [card(story) for story in items]
        for tile in tiles:
            tile.expand = 1
        out.extend(card_rows(tiles, columns))
    if len(stories) > size:
        out.append(pager(index, len(stories), size, lambda i: app.change_page("news_page", i)))
    return out
