# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Manage dated split adjustments in the transaction ledger."""

from datetime import date

import flet as ft

from frost_theme import frost_dialog
from portfolio import DATE_HINT, PortfolioError, display_date
from widgets import MUTED, RED, choose, field, panel, primary, secondary, text

SANDISK_FIRST_SESSION = date(2026, 11, 20)


def edit_split(app, item=None):
    book, store, revision = app.book, app.store, app.snap.revision
    symbols = sorted(book.currencies)
    if not symbols:
        app.notice("Add the original purchase before recording a stock split.")
        return
    default = item.symbol if item else "SNDK" if "SNDK" in symbols else symbols[0]
    symbol = choose("Stock symbol", [(s, s) for s in symbols], default, width=160)
    day = field(
        "First split-adjusted trading date",
        display_date(item.day) if item else "",
        244,
        hint=DATE_HINT,
    )
    new = field("New shares", str(item.new) if item else "", 114)
    old = field("Old shares", str(item.old) if item else "1", 114)
    error = text("", 13, RED)

    def suggest(e=None):
        if not item:
            sandisk = symbol.value == "SNDK"
            day.value = display_date(SANDISK_FIRST_SESSION) if sandisk else ""
            new.value, old.value = ("8" if sandisk else ""), "1"
        if e:
            dialog.update()

    def save(e):
        if app.store is not store:
            return
        try:
            store.commit(
                "split",
                revision,
                dict(symbol=symbol.value, date=day.value, new=new.value, old=old.value),
                (item.symbol, item.day) if item else None,
            )
        except PortfolioError as exc:
            error.value = str(exc)
            dialog.update()
            return
        app.page.pop_dialog()
        app.go("Transactions")
        app.notice("Stock split saved. Holdings and sale costs have been recalculated.")

    symbol.on_select = suggest
    suggest()
    dialog = frost_dialog(
        title=text("Edit stock split" if item else "Record stock split", 22, bold=True),
        modal=True,
        scrollable=True,
        content=ft.Container(
            ft.Column(
                [
                    text(
                        "Enter trades as executed. The split changes the share count at every brokerage before any trades on this date and preserves total cost.",
                        14,
                        MUTED,
                    ),
                    ft.Row([symbol, day], wrap=True, run_spacing=8),
                    ft.Row([new, old], wrap=True),
                    error,
                ],
                tight=True,
                spacing=12,
            ),
            width=min(490, max(210, (app.page.width or 700) - 99)),
        ),
        actions=[
            secondary("Cancel", lambda e: app.page.pop_dialog()),
            primary("Save split", save),
        ],
    )
    app.page.show_dialog(dialog)


def confirm_remove(app, split):
    store, revision = app.store, app.snap.revision
    error = text("", 13, RED)

    def remove(e):
        if app.store is not store:
            return
        try:
            store.commit("remove_split", revision, trade_id=(split.symbol, split.day))
        except PortfolioError as exc:
            error.value = str(exc)
            dlg.update()
            return
        app.page.pop_dialog()
        app.go("Transactions")

    dlg = frost_dialog(
        title=text(f"Remove {split.symbol} stock split?", 20, bold=True),
        content=ft.Column(
            [text("Share counts and realised sale costs will be recalculated."), error], tight=True
        ),
        modal=True,
        actions=[
            secondary("Cancel", lambda e: app.page.pop_dialog()),
            primary("Remove split", remove),
        ],
    )
    app.page.show_dialog(dlg)


def split_panel(app):
    return panel(
        [
            text("Stock splits", 19, bold=True),
            *[
                ft.Row(
                    [
                        text(
                            f"{event.symbol}  ·  {event.new}-for-{event.old}  ·  {display_date(event.day)}",
                            14,
                            bold=True,
                        ),
                        ft.TextButton(
                            "Edit split", on_click=lambda e, split=event: edit_split(app, split)
                        ),
                        ft.TextButton(
                            "Remove split",
                            on_click=lambda e, split=event: confirm_remove(app, split),
                        ),
                    ],
                    wrap=True,
                )
                for event in reversed(app.book.splits)
            ],
        ]
    )
