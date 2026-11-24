"""Serve a separate, disposable Folio Ledger demo for each browser session."""

from pathlib import Path
from tempfile import TemporaryDirectory

import flet as ft

from interface import PortfolioApp


def session(page: ft.Page) -> None:
    workspace = TemporaryDirectory(prefix="folio-demo-")
    page.on_close = lambda _: workspace.cleanup()
    PortfolioApp(page, Path(workspace.name), snapshot_demo=True)


app = ft.run(
    session,
    assets_dir=str(Path(__file__).resolve().with_name("assets")),
    export_asgi_app=True,
    no_cdn=True,
)
