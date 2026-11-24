# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""The shared responsive frame for every portfolio view and editor."""

import flet as ft

from frost_glass import backdrop, frost_header
from frost_theme import FROST, WHITE, menu, page_gradient, rule, txt

QUICK_SECTIONS = ("Overview", "Transactions", "Dividends", "Performance", "Copilot")


def dimensions(viewport):
    viewport = float(viewport or 1340)
    outer = min(1496, max(240, viewport - 8))
    gutter = 12 if viewport < 500 else 23 if viewport < 1000 else 36
    return viewport, outer, gutter, outer - 2 * gutter


def brand():
    return ft.Row(
        [
            ft.Container(
                ft.Icon(ft.Icons.FILTER_NONE_ROUNDED, size=22, color=WHITE),
                width=35,
                height=35,
                bgcolor=FROST.accent,
                alignment=ft.Alignment.CENTER,
                border_radius=9,
            ),
            txt("Folio Ledger", 22, FROST.ink, 700, tracking=-0.7),
        ],
        spacing=9,
        tight=True,
    )


def navigation(app, tabs, width):
    names = {"Demo portfolio": "demo", "My portfolio": "personal"}
    active = "Demo portfolio" if app.workspace == "demo" else "My portfolio"
    workspace = menu(FROST, "Workspace", active, names, lambda value: app.select(names[value]))
    current = app.editor_section if app.editor is not None else app.tab
    quick = width >= 1120
    sections = menu(
        FROST,
        "Sections",
        current,
        tabs,
        app.go,
        icon=ft.Icons.MENU,
        show_value=not quick or current not in QUICK_SECTIONS,
    )
    if width < 555:
        return ft.Column(
            [
                ft.Row(
                    [brand(), workspace],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    wrap=width < 307,
                    run_spacing=4,
                ),
                sections,
            ],
            spacing=5,
        )
    links = []
    if quick:
        for name in QUICK_SECTIONS:
            selected = current == name
            links.append(
                ft.TextButton(
                    txt(
                        name,
                        13,
                        FROST.accent if selected else FROST.muted,
                        700 if selected else 500,
                    ),
                    height=40,
                    on_click=lambda e, tab=name: app.go(tab),
                    style=ft.ButtonStyle(
                        bgcolor=FROST.wash if selected else None,
                        shape=ft.RoundedRectangleBorder(radius=9),
                    ),
                )
            )
    return ft.Row(
        [
            brand(),
            ft.Container(width=1, height=22, bgcolor=FROST.line),
            *links,
            sections,
            ft.Container(expand=True),
            workspace,
        ],
        spacing=9,
    )


class FrostShell:
    """Keep an editor's mounted fields and the planner's draft when the window resizes."""

    def __init__(self, app, tabs, intro, body, *, overview=False):
        self.app, self.tabs = app, tabs
        self.nav = ft.Container(padding=ft.Padding.symmetric(vertical=11))
        self.intro = ft.Column(
            intro, spacing=13, horizontal_alignment=ft.CrossAxisAlignment.STRETCH
        )
        self.upper = ft.Container(
            ft.Column(
                [
                    self.nav,
                    rule(FROST.line),
                    ft.Container(self.intro, padding=ft.Padding.only(top=16, bottom=17)),
                ],
                spacing=0,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        )
        self.stage = ft.Container(self.upper, alignment=ft.Alignment.CENTER)
        self.lower = ft.Container(
            ft.Column(body, spacing=15, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        )
        viewport, _, _, _ = dimensions(app.page.width)
        self.compact = not overview and len(intro) == 1
        self.header = frost_header(self.stage, viewport, compact=self.compact)
        self.content = ft.Column(
            [self.header, self.lower],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=0,
        )
        self.viewport_floor = ft.Container(ignore_interactions=True)
        self.frame = ft.Stack(
            [
                ft.Container(
                    left=0,
                    top=0,
                    right=0,
                    bottom=0,
                    gradient=page_gradient(),
                    ignore_interactions=True,
                ),
                self.viewport_floor,
                self.content,
            ],
            alignment=ft.Alignment.TOP_CENTER,
        )
        self.resize(viewport)

    def resize_height(self):
        self.viewport_floor.height = max(0, (self.app.page.height or 0) - 2)

    def resize(self, page_width):
        viewport, outer, gutter, width = dimensions(page_width)
        self.nav.content = navigation(self.app, self.tabs, width)
        self.stage.width = self.content.width = self.frame.width = viewport
        self.resize_height()
        self.upper.width = self.lower.width = outer
        self.upper.padding = ft.Padding.symmetric(horizontal=gutter)
        self.lower.padding = ft.Padding.only(left=gutter, right=gutter, bottom=24)
        self.intro.spacing = self.lower.content.spacing = 16 if width > 820 else 12
        if self.header.width != viewport:
            self.header.width = viewport
            self.header.controls[0] = backdrop(viewport, compact=self.compact)


def page_parts(controls):
    # Each view explicitly marks its last header filter or KPI. Do not move filters past their figures.
    stop = next((i + 1 for i, c in enumerate(controls) if c.data == "page-summary"), 1)
    # The header sits directly on the frost backdrop, exactly as on Overview.
    return controls[:stop], controls[stop:]
