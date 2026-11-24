# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Portfolio insights, recent price moves and dividend dates on Overview."""

import flet as ft

from analytics import risk_report
from dividend_stats import build_insights, move_reasons, upcoming_events
from dividends import today_sg
from frost_theme import action, frost_dialog, panel, title, txt
from watch_views import HOLDINGS_NEWS, open_news
from widgets import card_rows

SECTION_PADDING = 27
PAYOUT = "Dividend payout"


def pill(label, colour, fill):
    return ft.Container(
        txt(label, 12, colour, 700),
        padding=ft.Padding.symmetric(horizontal=8, vertical=3),
        border_radius=7,
        bgcolor=fill,
    )


def section_padding(width):
    return SECTION_PADDING if width >= 750 else 14


def tile_room(width):
    return width - 2 * section_padding(width) - 2  # padding and panel border


def tile_section(s, label, width, tiles, columns, gap, *, cap=None):
    slot = (tile_room(width) - (columns - 1) * gap) / columns
    tile_width = min(slot, cap) if cap else slot
    for tile in tiles:
        tile.width = tile_width
    return panel(
        s,
        ft.Column(
            [
                title(label, 21, s),
                ft.Column(card_rows(tiles, columns, spacing=gap), spacing=gap),
            ],
            spacing=15,
        ),
        padding=section_padding(width),
    )


def insight_dialog(app, card, s):
    lines = [txt(line, 14, s.ink, 500) for line in card.detail]
    destination = "Dividends" if card.key == "income" else "Risk"
    app.page.show_dialog(
        frost_dialog(
            modal=True,
            scrollable=True,
            title=title(card.title, 20, s),
            content=ft.Container(ft.Column(lines, spacing=10, tight=True), width=420),
            actions=[
                action(s, f"Open {destination}", lambda e: (app.page.pop_dialog(), app.go(destination))),
                action(s, "Close", lambda e: app.page.pop_dialog()),
            ],
        )
    )


def insight_cards(app, view, s, width):
    today = today_sg()
    risk = risk_report(view, app.histories, today)
    cards = [
        card for card in build_insights(
            view, risk, app.histories, list(app.payments.rows.values()), app.rates, today
        )
        if card.key not in app.dismissed_insights
    ]
    if not cards:
        return []

    def dismiss(key):
        app.dismissed_insights.add(key)
        app.render()

    tiles = [
        ft.Container(
            ft.Column(
                [
                    ft.Row(
                        [
                            pill(card.level, "#7A4B00", "#FFF1D6"),
                            ft.Container(expand=True),
                            ft.IconButton(
                                ft.Icons.CLOSE,
                                icon_size=16,
                                icon_color=s.muted,
                                tooltip="Hide until the app restarts",
                                on_click=lambda e, key=card.key: dismiss(key),
                            ),
                        ],
                        height=30,
                    ),
                    txt(card.title, 15, s.ink, 700),
                    txt(card.summary, 13, s.muted, 500),
                    action(s, "See details", lambda e, item=card: insight_dialog(app, item, s), subtle=True),
                ],
                spacing=6,
                tight=True,
            ),
            padding=ft.Padding.only(left=13, right=8, top=8, bottom=7),
            border_radius=14,
            border=ft.Border.all(1, "#E9B44C"),
            bgcolor="#F7F5EF",
        )
        for card in cards
    ]
    columns = max(1, min(4, int((tile_room(width) + 14) // 278)))
    return [tile_section(s, "Portfolio insights", width, tiles, columns, 14, cap=370)]


def moving_panel(app, view, s, width):
    reasons = move_reasons(view, app.histories, app.rates, app.news_feeds)[:6]
    if not reasons:
        return []
    missing = [move.symbol for move in reasons if move.headline is None]
    if missing:
        app.request_mover_news(missing)

    tiles = []
    for move in reasons:
        up = move.pct > 0
        colour, arrow = (s.positive, "↑") if up else (s.negative, "↓")
        fill = "#D9F0EB" if up else "#F7E1E7"
        if move.headline:
            headline = move.headline
            source = move.source or "Ticker feed"
        else:
            headline = (
                "Looking for headlines…" if move.symbol in app.news_busy
                else "No recent headline in the ticker feed."
            )
            source = "Open ticker news"
        tiles.append(
            ft.Container(
                ft.Column(
                    [
                        ft.Row(
                            [
                                txt(move.symbol, 14, s.ink, 700),
                                pill(f"{arrow} {abs(move.pct):.2f}%", colour, fill),
                                ft.Container(expand=True),
                                ft.Icon(ft.Icons.ARROW_OUTWARD, size=16, color=s.accent),
                            ],
                            spacing=8,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        txt(headline, 14, s.ink, 500, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                        txt(source, 12, s.muted, 500, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ],
                    spacing=6,
                    tight=True,
                ),
                height=138,
                alignment=ft.Alignment.CENTER_LEFT,
                padding=ft.Padding.symmetric(horizontal=18, vertical=15),
                border_radius=14,
                border=ft.Border.only(left=ft.BorderSide(3, colour)),
                bgcolor="#ECF4F8",
                ink=True,
                tooltip=f"Open news for {move.symbol}",
                on_click=lambda e, symbol=move.symbol: open_news(app, HOLDINGS_NEWS, symbol),
            )
        )
    columns = max(1, min(3, int((tile_room(width) + 20) // 326)))
    return [tile_section(s, "Latest moves and headlines", width, tiles, columns, 20, cap=380)]


def events_panel(app, s, width):
    today = today_sg()
    events = upcoming_events(app.book, app.payments.rows.values(), app.histories, today)
    if not events:
        return []
    tiles = []
    for event in events[:7]:
        colour = s.positive if event.kind == PAYOUT else "#986014"
        quote = app.prices.get(event.symbol)
        name = quote.listing.name if quote and quote.listing else ""
        identities = [txt(event.symbol, 14, s.ink, 700, no_wrap=True)]
        if name and name != event.symbol:
            identities.append(
                txt(
                    name, 13, s.ink, expand=True, max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS, tooltip=name,
                )
            )
        tiles.append(
            ft.Container(
                ft.Row(
                    [
                        ft.Column(
                            [
                                txt(str(event.day.day), 17, s.ink, 700),
                                txt(f"{event.day:%b}", 13, s.muted, 500),
                                txt("Past" if event.day < today else f"{event.day:%a}", 11, s.muted),
                            ],
                            spacing=0,
                            width=31,
                            tight=True,
                        ),
                        ft.Column(
                            [
                                txt(event.kind, 12, colour, 700),
                                ft.Row(identities, spacing=5),
                                txt(
                                    event.detail, 12, s.muted, 500, max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS, tooltip=event.detail,
                                ),
                            ],
                            spacing=3,
                            tight=True,
                            expand=True,
                        ),
                    ],
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                height=92,
                alignment=ft.Alignment.CENTER_LEFT,
                padding=ft.Padding.symmetric(horizontal=15, vertical=10),
                border_radius=13,
                bgcolor="#ECF4F8",
                border=ft.Border.only(left=ft.BorderSide(2, colour)),
            )
        )
    tiles.append(
        ft.Container(
            ft.Row(
                [
                    txt("See all", 14, s.accent, 700),
                    ft.Icon(ft.Icons.ARROW_FORWARD, size=16, color=s.accent),
                ],
                spacing=7,
                tight=True,
            ),
            height=92,
            alignment=ft.Alignment.CENTER,
            border_radius=13,
            border=ft.Border.all(1, s.line),
            bgcolor="#66F6FBFF",
            ink=True,
            tooltip="See all dividend events",
            on_click=lambda e: app.go("Dividends"),
        )
    )
    room = tile_room(width)
    columns = 4 if room >= 995 else 2 if room >= 480 else 1
    return [tile_section(s, "Recent and upcoming events", width, tiles, columns, 14)]


def extras(app, view, s, width):
    return [
        *insight_cards(app, view, s, width),
        *moving_panel(app, view, s, width),
        *events_panel(app, s, width),
    ]
