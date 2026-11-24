# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Print or email the daily digest without opening the app (python main.py --digest)."""

from __future__ import annotations

from pathlib import Path

from analytics import build_digest, digest_text, risk_report
from dividends import today_sg
from market import refresh_prices
from market_news import headlines, read_feeds
from market_watch import MailSettings, Watchlist
from portfolio import PortfolioError
from storage import Store
from valuation import Overview, SalesReport


def run_digest(root: Path, *, demo=False, refresh=False, email=False) -> int:
    """Return a process exit code: 0 on success, 1 if the digest or email failed."""
    try:
        store = Store(root, "demo" if demo else "personal")
        book = store.snapshot().ledger
        if refresh and book.positions:
            fresh, errors = refresh_prices(tuple(book.positions))
            store.save_quotes(fresh.values())
            for symbol, message in sorted(errors.items()):
                print(f"Could not refresh {symbol}: {message}")
        quotes, rates = store.quotes(), store.fx_rates()
        histories, payments = store.histories(), store.payments()
        try:
            feeds = read_feeds(store)
        except PortfolioError:
            feeds = {}
        sectors = {k: v.sector for k, v in histories.items() if v.sector}
        sales = SalesReport(book, rates)
        view = Overview(book, quotes, rates, sales, sectors)
        if not view.rows:
            print("There are no holdings to summarise yet.")
            return 0
        today = today_sg()
        risk = risk_report(view, histories, today)
        titles = [h["title"] for h in headlines(feeds, book.positions)]
        state = Watchlist(store).snapshot() if not demo else dict(events=[])
        digest = build_digest(
            view, sales, histories, list(payments.rows.values()), state, titles, risk, rates, today
        )
    except (PortfolioError, OSError) as exc:
        print(f"Could not build the digest: {exc}")
        return 1
    body = digest_text(digest)
    print(body)
    if email:
        mail = MailSettings.from_env()
        if mail is None:
            print("\nEmail is not configured. See START_HERE.txt.")
            return 1
        try:
            mail.deliver(f"Folio Ledger digest · {today:%d %b %Y}", body)
            print("\nDigest emailed.")
        except Exception:
            print("\nThe email could not be sent. Check the SMTP settings.")
            return 1
    return 0
