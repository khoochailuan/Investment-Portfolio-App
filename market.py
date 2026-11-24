# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Resolve worldwide Yahoo Finance stock listings and their exchange-timestamped quotes."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from http.client import HTTPException
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import quote as urlquote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from portfolio import PortfolioError, currency_code, number, ticker, units

UTC = timezone.utc
ASSET_TYPES = {"EQUITY", "ETF", "REIT"}
# Price units are case-sensitive: GBP is pounds; GBp is pence.
SUBUNITS = {
    "GBp": ("GBP", 100),
    "GBX": ("GBP", 100),
    "ZAc": ("ZAR", 100),
    "ZAC": ("ZAR", 100),
    "ILA": ("ILS", 100),
    "ILa": ("ILS", 100),
    "KWF": ("KWD", 1000),
}


class NoListing(PortfolioError):
    """The provider has no listing at this exact symbol."""


class SymbolChoice(PortfolioError):
    def __init__(self, message, choices):
        super().__init__(message)
        self.choices = choices


@dataclass(frozen=True)
class Listing:
    symbol: str
    name: str
    exchange: str
    kind: str
    currency: str
    price_unit: str

    @classmethod
    def from_record(cls, data):
        symbol = ticker(data["symbol"])
        name, exchange = data["name"], data["exchange"]
        kind, currency, unit = data["kind"], currency_code(data["currency"]), data["price_unit"]
        if (
            kind not in ASSET_TYPES
            or not isinstance(name, str)
            or not name.strip()
            or not isinstance(exchange, str)
            or not exchange.strip()
            or not isinstance(unit, str)
            or SUBUNITS.get(unit, (unit, 1))[0] != currency
        ):
            raise PortfolioError(
                "The instrument type, exchange, or quote currency could not be verified."
            )
        return cls(symbol, name, exchange, kind, currency, unit)

    @property
    def label(self):
        return f"{self.symbol} · {self.name} · {exchange_label(self.exchange)} · {self.currency}"


def exchange_label(name):
    return "Singapore Exchange (SGX)" if name == "SES" else name


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: Decimal
    asof: datetime
    source: str
    currency: str = "USD"
    listing: Listing | None = None
    session_day: date | None = None

    def record(self):
        row = dict(
            symbol=self.symbol,
            price=units(self.price),
            asof=self.asof.isoformat(timespec="seconds"),
            source=self.source,
            currency=self.currency,
        )
        if self.listing:
            row["listing"] = asdict(self.listing)
        if self.session_day:
            row["session_day"] = self.session_day.isoformat()
        return row

    @classmethod
    def from_record(cls, row):
        try:
            when = datetime.fromisoformat(row["asof"].replace("Z", "+00:00"))
            if when.tzinfo is None or when > datetime.now(UTC) + timedelta(minutes=5):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise PortfolioError("A quote needs a valid timestamp and time zone.") from None
        source = row["source"]
        if not isinstance(source, str) or not source.strip() or len(source) > 50:
            raise PortfolioError("A quote is missing a valid source.")
        symbol, currency = ticker(row["symbol"]), currency_code(row.get("currency", "USD"))
        listing = Listing.from_record(row["listing"]) if "listing" in row else None
        if listing and (listing.currency != currency or listing.symbol != symbol):
            raise PortfolioError("The price and its listing have different symbols or currencies.")
        return cls(
            symbol,
            number(row["price"], "Market price", 10),
            when.astimezone(UTC),
            source.strip(),
            currency,
            listing,
            date.fromisoformat(row["session_day"]) if row.get("session_day") else None,
        )


def exchange_zone(meta):
    try:
        return ZoneInfo(meta["exchangeTimezoneName"])
    except (KeyError, TypeError, ZoneInfoNotFoundError, ValueError):
        return timezone(timedelta(seconds=int(meta.get("gmtoffset") or 0)))


def yahoo_json(path, params):
    url = "https://query1.finance.yahoo.com" + path + "?" + urlencode(params)
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; FolioLedger/2.0)",
            "Accept": "application/json",
        },
    )
    for attempt in range(2):
        try:
            with urlopen(req, timeout=12) as response:
                return json.load(response, parse_float=Decimal)
        except HTTPError as exc:
            if exc.code == 404:
                raise NoListing("Yahoo Finance has no listing at this exact symbol.") from None
            if exc.code == 429:
                raise PortfolioError(
                    "Yahoo Finance is limiting requests. Please try again later."
                ) from None
            if attempt or exc.code not in (500, 502, 503, 504):
                raise PortfolioError(
                    f"Yahoo Finance returned HTTP {exc.code}. Please try again."
                ) from None
        except (URLError, TimeoutError, OSError, HTTPException, ValueError):
            if attempt:
                raise PortfolioError(
                    "Yahoo Finance is unavailable. Please try again when connected."
                ) from None
        sleep(0.4)


def quote_and_metadata(symbol: str):
    """Verify the listed instrument and return its quote with the source market statistics."""
    symbol = ticker(symbol)
    payload = yahoo_json(
        "/v8/finance/chart/" + urlquote(symbol, safe=""), {"range": "5d", "interval": "1d"}
    )
    try:
        chart = payload["chart"]
        if not chart.get("result"):
            raise NoListing(f"Yahoo Finance has no chart for {symbol}.")
        meta = chart["result"][0]["meta"]
        if ticker(meta.get("symbol")) != symbol:
            raise NoListing(f"The response did not match {symbol}.")
        if meta.get("instrumentType") not in ASSET_TYPES:
            raise PortfolioError(
                f"Yahoo Finance classifies {symbol} as {meta.get('instrumentType') or 'an unknown instrument type'}. Choose a stock, listed REIT or ETF."
            )
        raw_unit = meta["currency"]
        currency, divisor = SUBUNITS.get(raw_unit, (raw_unit, 1))
        listing = Listing.from_record(
            dict(
                symbol=symbol,
                name=meta.get("longName") or meta.get("shortName") or symbol,
                exchange=meta.get("fullExchangeName") or meta.get("exchangeName"),
                currency=currency,
                price_unit=raw_unit,
                kind=meta["instrumentType"],
            )
        )
        if meta["regularMarketTime"] <= 0:
            raise ValueError
        timestamp = datetime.fromtimestamp(meta["regularMarketTime"], UTC)
        price = Decimal(str(meta["regularMarketPrice"])) / divisor
        observed = Quote.from_record(
            {
                "symbol": symbol,
                "price": format(price, "f"),
                "asof": timestamp.isoformat(),
                "session_day": timestamp.astimezone(exchange_zone(meta)).date().isoformat()
                if meta.get("exchangeTimezoneName") or meta.get("gmtoffset") is not None
                else None,
                "source": "Yahoo Finance",
                "currency": currency,
                "listing": asdict(listing),
            }
        )
        return observed, meta
    except PortfolioError:
        raise
    except (
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        ArithmeticError,
        OverflowError,
        OSError,
        AttributeError,
    ):
        raise PortfolioError(
            f"Yahoo Finance could not verify the exchange, currency, price and time for {symbol}."
        ) from None


def fetch_quote(symbol: str) -> Quote:
    return quote_and_metadata(symbol)[0]


def search_listings(query, *, names_only=False, suggest=False):
    result = yahoo_json(
        "/v1/finance/search",
        {
            "q": query,
            "quotesCount": 32,
            "newsCount": 0,
            "enableFuzzyQuery": "true" if suggest else "false",
        },
    )
    choices = {}
    if not isinstance(result, dict) or not isinstance(result.get("quotes"), list):
        raise PortfolioError("Yahoo Finance returned an incomplete search result. Please retry.")
    for item in result["quotes"]:
        if (
            not isinstance(item, dict)
            or item.get("quoteType") not in ASSET_TYPES
            or not item.get("exchange")
        ):
            continue
        try:
            symbol = ticker(item.get("symbol"))
        except PortfolioError:
            continue
        name = item.get("longname") or item.get("shortname") or symbol
        exchange = exchange_label(item.get("exchDisp") or item["exchange"])
        choices[symbol] = name if names_only else f"{symbol} · {name} · {exchange}"
    return choices


def fetch_named_quote(symbol):
    quote = fetch_quote(symbol)
    if quote.listing.name.upper() != quote.symbol:
        return quote
    # Some exchanges supply the company name only through the listing search.
    try:
        name = search_listings(symbol, names_only=True).get(quote.symbol)
    except PortfolioError:
        name = None
    if isinstance(name, str) and name.strip():
        return replace(quote, listing=replace(quote.listing, name=name.strip()))
    return quote


def resolve_symbol(value) -> Quote:
    """Exact Yahoo symbols win; a unique exact local code can resolve to its exchange suffix."""
    query = str(value or "").strip()
    if not query or len(query) > 96 or not query.isprintable():
        raise PortfolioError("Enter a stock ticker or company name to check.")
    query = query.upper()
    if not any(c.isspace() for c in query):
        try:
            return fetch_quote(query)
        except NoListing:
            # Preserve share-class shorthand without rewriting real suffixes such as .L.
            if "." in query and len(query.rsplit(".", 1)[1]) == 1:
                try:
                    return fetch_quote(query.replace(".", "-"))
                except NoListing:
                    pass
    choices = search_listings(query)
    if query in choices:
        return fetch_quote(query)
    matching = {s: name for s, name in choices.items() if s.rpartition(".")[0] == query}
    if len(matching) == 1:
        return fetch_quote(next(iter(matching)))
    if matching:
        raise SymbolChoice(
            f"{query} matches multiple listings. Select the exchange on your confirmation.",
            matching,
        )
    if choices:
        raise SymbolChoice(
            f"“{query}” is not an exact stock symbol. Select the intended listing.", choices
        )
    raise NoListing(
        f"No stock, listed REIT or ETF matched “{query}”. Check the ticker and its exchange suffix."
    )


def refresh_prices(symbols, fetcher=fetch_quote):
    fresh, errors = {}, {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = {pool.submit(fetcher, s): s for s in sorted(set(symbols))}
        for task in as_completed(pending):
            symbol = pending[task]
            try:
                fresh[symbol] = task.result()
            except PortfolioError as exc:
                errors[symbol] = str(exc)
    return fresh, errors
