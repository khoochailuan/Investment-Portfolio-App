# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Explore the Frost application using a temporary copy of the demo portfolio."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

import flet as ft
from interface import PortfolioApp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8560)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Choose a port from 1 to 65535.")
    with TemporaryDirectory(prefix="folio-frost-") as temp:

        def session(page):
            PortfolioApp(page, Path(temp), snapshot_demo=True)

        print(f"Folio Ledger demo: http://127.0.0.1:{args.port}", flush=True)
        ft.run(
            session,
            host="127.0.0.1",
            port=args.port,
            view=ft.AppView.WEB_BROWSER,
            no_cdn=True,
            assets_dir=str(Path(__file__).resolve().parent / "assets"),
        )


if __name__ == "__main__":
    main()
