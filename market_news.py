# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Dated ticker headlines with a per-workspace offline cache."""

from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from market import UTC, refresh_prices
from portfolio import PortfolioError, ticker
from storage import atomic_json, read_json

FRESH_FOR = timedelta(minutes=15)


def web_link(value):
    try:
        parts = urlsplit(value or "")
        return bool(
            parts.scheme in ("https", "http")
            and parts.hostname
            and not parts.username
            and not parts.password
        )
    except (TypeError, ValueError):
        return False


def feed_url(key):
    symbol = ticker(key)
    if symbol == "@BUSINESS":
        raise PortfolioError("News is available for tracked stock symbols.")
    region, language = ("SG", "en-SG") if symbol.endswith(".SI") else ("US", "en-US")
    return "https://feeds.finance.yahoo.com/rss/2.0/headline?" + urlencode(
        {"s": symbol, "region": region, "lang": language}
    ), "Yahoo Finance"


def feed_items(raw, publisher):
    try:
        root = ElementTree.fromstring(raw)
        if root.find("./channel") is None:
            raise ValueError
    except (ElementTree.ParseError, ValueError):
        raise PortfolioError("The publisher returned an unreadable news feed.") from None
    out = []
    for node in root.findall("./channel/item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        if not title or not web_link(link):
            continue
        try:
            dt = parsedate_to_datetime(node.findtext("pubDate") or "")
            if dt.tzinfo is None:
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        out.append(
            dict(
                title=title[:500],
                url=link,
                time=dt.astimezone(UTC).isoformat(timespec="seconds"),
                source=(node.findtext("source") or publisher).strip()[:100],
            )
        )
    return sorted(out, key=lambda item: item["time"], reverse=True)[:35]


def fetch_feed(key):
    url, name = feed_url(key)
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; FolioLedger/2.0)",
            "Accept": "application/rss+xml,application/xml",
        },
    )
    try:
        with urlopen(req, timeout=12) as f:
            raw = f.read(2_000_001)
    except (OSError, HTTPException) as exc:
        raise PortfolioError(f"{name} feed unavailable.") from exc
    if len(raw) > 2_000_000:
        raise PortfolioError("The news feed exceeded the response size limit.")
    return feed_items(raw, name)


def read_feeds(store):
    with store.guarded():
        path = store.directory / "market_news.json"
        feeds = read_json(path).get("feeds") if path.exists() else {}
        if not isinstance(feeds, dict):
            raise PortfolioError("Cannot read the saved news feeds.")
        # Older versions cached a general feed alongside ticker feeds.
        feeds.pop("@business", None)
        try:
            for entry in feeds.values():
                if not isinstance(entry, dict) or not isinstance(entry.get("articles", []), list):
                    raise ValueError
                for article in entry.get("articles", []):
                    if not all(
                        isinstance(article[k], str) for k in ("title", "url", "time", "source")
                    ):
                        raise ValueError
                    datetime.fromisoformat(article["time"])
        except (ValueError, KeyError, TypeError):
            raise PortfolioError("Cannot read the saved news stories.") from None
        return feeds


def refresh_news(store, keys, force=False, fetcher=fetch_feed, now=None):
    now = now or datetime.now(UTC)
    feeds = read_feeds(store)
    stamp = now.isoformat(timespec="seconds")
    due = []
    for key in keys:
        item = feeds.get(key, {})
        if (
            force
            or not item.get("checked")
            or item["checked"] < (now - FRESH_FOR).isoformat(timespec="seconds")
        ):
            due.append(key)
    if not due:
        return feeds
    results, errors = refresh_prices(due, fetcher)
    with store.guarded():
        feeds = read_feeds(store)
        for key in due:
            entry = feeds.get(key, {})
            if entry.get("checked", "") > stamp:
                continue
            if key in errors:
                feeds[key] = {**entry, "checked": stamp, "error": errors[key]}
            else:
                feeds[key] = dict(checked=stamp, asof=stamp, articles=results[key], error="")
        atomic_json(store.directory / "market_news.json", {"schema": 1, "feeds": feeds})
    return feeds


def headlines(feeds, keys):
    by_url = {}
    for key in keys:
        for h in feeds.get(key, {}).get("articles", []):
            if not web_link(h.get("url")):
                continue
            # RSS tracking query strings can differ when the same story covers two tickers.
            parts = urlsplit(h["url"])
            article_key = (
                parts.netloc.casefold(),
                parts.path.rstrip("/"),
                parts.query if parts.path in ("", "/") else "",
            )
            if article_key not in by_url:
                by_url[article_key] = {**h, "symbols": set()}
            by_url[article_key]["symbols"].add(key)
    return sorted(by_url.values(), key=lambda h: (h["time"], h["title"]), reverse=True)
