# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""A date-only calendar that sends calendar days without time zones."""

from calendar import Calendar, month_abbr
from datetime import date

import flet as ft

from frost_theme import frost_dialog
from widgets import choose, primary, secondary, text


def trade_calendar(page, selected, today, apply):
    earliest = date(1900, 1, 1)
    selected = max(selected, earliest)
    month, year = selected.month, selected.year
    month_choice = choose(
        "Month", [(str(n), month_abbr[n]) for n in range(1, 13)], str(month), width=125
    )
    year_choice = choose(
        "Year", [(str(n), str(n)) for n in range(today.year, 1899, -1)], str(year), width=100
    )
    days = ft.Column(spacing=3)
    calendar = Calendar()

    def select(day):
        async def picked(e):
            page.pop_dialog()
            await apply(day)

        return picked

    def draw(e=None):
        year, month = int(year_choice.value), int(month_choice.value)
        days.controls = [
            ft.Row(
                [
                    ft.Container(text(name, 11, bold=True), width=32, alignment=ft.Alignment.CENTER)
                    for name in ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
                ],
                spacing=3,
            )
        ]
        for week in calendar.monthdatescalendar(year, month):
            cells = []
            for day in week:
                if day.month != month:
                    cells.append(ft.Container(width=32, height=34))
                    continue
                cell = ft.TextButton(
                    str(day.day),
                    tooltip=day.strftime("%d-%m-%Y"),
                    width=32,
                    height=34,
                    disabled=not earliest <= day <= today,
                    on_click=select(day),
                    style=ft.ButtonStyle(
                        padding=0,
                        bgcolor=ft.Colors.PRIMARY_CONTAINER if day == selected else None,
                        shape=ft.RoundedRectangleBorder(radius=8),
                    ),
                )
                cells.append(cell)
            days.controls.append(ft.Row(cells, spacing=3))
        if e:
            dialog.update()

    month_choice.on_select = year_choice.on_select = draw
    dialog = frost_dialog(
        title=text("Choose transaction date", 20, bold=True),
        content=ft.Column([ft.Row([month_choice, year_choice], spacing=8), days], tight=True),
        modal=True,
        scrollable=True,
        actions=[
            secondary("Cancel", lambda e: page.pop_dialog()),
            primary("Today", select(today)),
        ],
    )
    draw()
    page.show_dialog(dialog)
