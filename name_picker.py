# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Workspace lists for recurring income groups and brokerage names."""

import flet as ft

from portfolio import PortfolioError, canonical_names, clean_name
from widgets import ENTRY_TEXT_SIZE, MUTED, choose, field, text

CREATE = "create"


class NamePicker(ft.Column):
    def __init__(
        self, label, names, selected="", *, none_label, new_label, width=240, max_length=50
    ):
        self.names = canonical_names([*names, selected])
        self.by_key = {
            name.casefold(): name for name in sorted(self.names.values(), key=str.casefold)
        }
        self.new_label = new_label
        items = [
            ("", none_label),
            *((f"name:{key}", value) for key, value in self.by_key.items()),
            (CREATE, f"Create new {new_label}…"),
        ]
        self.choice = choose(
            label,
            items,
            "",
            self.changed,
            width,
            option_controls=[
                ft.DropdownOption(
                    key=key,
                    text=value,
                    content=text(
                        value, ENTRY_TEXT_SIZE, max_lines=3, overflow=ft.TextOverflow.ELLIPSIS
                    ),
                )
                for key, value in items
            ],
        )
        self.new_name = field(
            f"New {new_label} name",
            width=width,
            max_length=max_length,
        )
        self.full_name = text("", 12, MUTED, visible=False)
        super().__init__(
            [self.choice, self.new_name, self.full_name],
            width=width,
            spacing=6,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        self.select(selected)

    def select(self, name):
        normalized = clean_name(name).casefold()
        self.choice.value = f"name:{normalized}" if normalized in self.by_key else ""
        self.new_name.value = ""
        self.sync()

    def sync(self):
        self.new_name.visible = self.choice.value == CREATE
        current = self.by_key.get(self.choice.value.removeprefix("name:"), "")
        # Also show long names in a wrapping line, since a dropdown's selected value is single-line.
        self.full_name.value = current
        self.full_name.visible = len(current) > 21

    def changed(self, e):
        self.sync()
        self.update()

    @property
    def value(self):
        selected = self.choice.value or ""
        if selected != CREATE:
            return self.by_key[selected.removeprefix("name:")] if selected else ""
        name = clean_name(self.new_name.value or "")
        if not name:
            raise PortfolioError(f"Enter a new {self.new_label} name or choose an existing option.")
        if not name.isprintable():
            raise PortfolioError(f"Use printable characters in the {self.new_label} name.")
        return self.names.get(name.casefold(), name)


def brokerage_picker(
    app, selected="", *, label="Brokerage", none_label="Select a brokerage", extra=()
):
    names = [
        *(trade.broker for trade in app.book.trades),
        *(payment.broker for payment in app.payments.rows.values()),
        *extra,
    ]
    return NamePicker(label, names, selected, none_label=none_label, new_label="brokerage")
