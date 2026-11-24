# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""The four consolidated valuation cards on Overview."""

import flet as ft

from dashboard import sgd
from portfolio import percent
from widgets import Metric, metric_cards


def metrics(app, view, s, width):
    count = len(view.rows)
    return metric_cards(
        width,
        [
            Metric(
                "Market value",
                sgd(view.value),
                f"{count} open holding" + ("s" if count != 1 else ""),
                ft.Icons.ACCOUNT_BALANCE_WALLET_OUTLINED,
            ),
            Metric(
                "Open cost basis",
                sgd(view.basis),
                f"{app.book.method} · incl. buy fees",
                ft.Icons.LAYERS_OUTLINED,
            ),
            Metric(
                "Unrealised P/L",
                sgd(view.gain, True),
                "on open cost",
                ft.Icons.SHOW_CHART,
                percentage=percent(view.return_pct),
                color=s.sign(view.gain),
            ),
            Metric(
                "Realised P/L",
                sgd(view.realised, True),
                "on sold cost",
                ft.Icons.TASK_ALT,
                percentage=percent(view.realised_return_pct),
                color=s.sign(view.realised),
                on_click=lambda e: app.go("Realised P/L"),
                tooltip="Open realised P/L report",
            ),
        ],
    )
