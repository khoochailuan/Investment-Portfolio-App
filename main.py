# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Run Folio Ledger locally. The browser and optional desktop view share one Python UI."""

import argparse
from pathlib import Path

import flet as ft

from interface import PortfolioApp


def main():
    parser = argparse.ArgumentParser(description="Folio Ledger personal stock portfolio")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "data",
        help="Local folder for saved portfolios",
    )
    parser.add_argument("--port", type=int, default=8550, help="Local browser port (default 8550)")
    parser.add_argument(
        "--desktop", action="store_true", help="Open the optional Flet desktop client"
    )
    monitor_flags = parser.add_mutually_exclusive_group()
    monitor_flags.add_argument(
        "--monitor",
        action="store_true",
        help="Check personal watch prices at the saved refresh interval without a window",
    )
    monitor_flags.add_argument(
        "--check-alerts", action="store_true", help="Check personal watch prices once and exit"
    )
    parser.add_argument(
        "--digest", action="store_true", help="Print the daily digest and exit (no window)"
    )
    parser.add_argument("--email", action="store_true", help="With --digest, also email it")
    parser.add_argument(
        "--refresh", action="store_true", help="With --digest, fetch fresh prices first"
    )
    parser.add_argument("--demo", action="store_true", help="With --digest, use the demo portfolio")
    args = parser.parse_args()
    if args.digest:
        from daily_digest import run_digest

        raise SystemExit(
            run_digest(args.data_dir.resolve(), demo=args.demo, refresh=args.refresh, email=args.email)
        )
    if args.monitor or args.check_alerts:
        from market_watch import monitor

        try:
            raise SystemExit(monitor(args.data_dir.resolve(), once=args.check_alerts))
        except KeyboardInterrupt:
            return
    if not 1 <= args.port <= 65535:
        parser.error("Choose a port between 1 and 65535.")

    def session(page: ft.Page):
        PortfolioApp(page, args.data_dir.resolve())

    if not args.desktop:
        print(
            f"Folio Ledger is opening at http://127.0.0.1:{args.port}. Press Ctrl+C to stop.",
            flush=True,
        )
    ft.run(
        session,
        no_cdn=True,
        host="127.0.0.1",
        port=args.port,
        view=ft.AppView.FLET_APP if args.desktop else ft.AppView.WEB_BROWSER,
        assets_dir=str(Path(__file__).resolve().parent / "assets"),
    )


if __name__ == "__main__":
    main()
