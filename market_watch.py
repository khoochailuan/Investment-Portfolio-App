# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Workspace watchlists, verified market statistics and persistent crossing alerts."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
import os
import smtplib
import ssl
from time import monotonic, sleep
from urllib.parse import quote as urlquote
from uuid import uuid4

from market import UTC, SUBUNITS, Quote, quote_and_metadata, refresh_prices, yahoo_json
from portfolio import PortfolioError, number, unit_price, units
from storage import atomic_json, read_json

DIRECTIONS = ("At or below", "At or above")
AUTO_DIRECTION = "Automatic"
WATCH_INTERVALS = {10: "10 sec", 30: "30 sec", 60: "1 min", 120: "2 min", 180: "3 min"}
DEFAULT_WATCH_INTERVAL = 60
STAT_RETRY = timedelta(minutes=5)


def automatic_direction(price, target, previous=None):
    """Choose the crossing direction once, keeping it on edits to the same target."""
    if previous and previous.get("automatic_direction") and target == previous["target"]:
        return previous["direction"]
    return DIRECTIONS[1] if target is not None and Decimal(target) >= price else DIRECTIONS[0]


def target_distance(price, target):
    if target is None:
        return None
    level = Decimal(target)
    difference = price - level
    return difference, difference * 100 / level


def utcnow():
    return datetime.now(UTC)


def timestamp(d):
    return d.isoformat(timespec="seconds")


def numeric(raw):
    try:
        result = Decimal(str(raw))
        return result if result.is_finite() else None
    except (TypeError, ValueError, InvalidOperation):
        return None


DEMO_STATS = {
    "AAPL": dict(
        change="0.62", low="164.08", high="199.62", pe="30.2", pe_asof="2026-09-30",
        eps="6.08", revenue="391000000000", ebitda="134700000000", fund_ccy="USD",
        fund_asof="2026-09-30", div12="0.98", extra_checked="2026-11-16",
    ),
    "ME8U.SI": dict(
        change="-0.35", low="1.52", high="1.85", pe=None, pe_asof=None,
        eps="0.07", revenue="690000000", ebitda="520000000", fund_ccy="SGD",
        fund_asof="2026-09-30", div12="0.105", extra_checked="2026-11-16",
    ),
}


def demo_line(last, drift, wobble):
    """Deterministic 30-point sample series for the offline demo."""
    from math import sin

    first = Decimal(str(last)) / (1 + Decimal(str(drift)))
    return [
        str((first + (Decimal(str(last)) - first) * i / 29 + Decimal(str(sin(i * 0.45) * wobble))).quantize(Decimal("0.0001")))
        for i in range(30)
    ]


DEMO_STATS["AAPL"]["spark"] = demo_line("183.50", "0.045", 2.2)
DEMO_STATS["ME8U.SI"]["spark"] = demo_line("1.68", "-0.03", 0.025)


def trailing_pe(symbol, now):
    path = "/ws/fundamentals-timeseries/v1/finance/timeseries/" + urlquote(symbol, safe="")
    payload = yahoo_json(
        path,
        {
            "symbol": symbol,
            "type": "trailingPeRatio",
            "period1": int((now - timedelta(days=400)).timestamp()),
            "period2": int(now.timestamp()) + 1,
        },
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("timeseries"), dict):
        raise PortfolioError("P/E data is unavailable.")
    found = []
    for group in payload["timeseries"].get("result") or []:
        if (
            not isinstance(group, dict)
            or not isinstance(group.get("meta"), dict)
            or symbol not in (group["meta"].get("symbol") or [])
        ):
            continue
        for item in group.get("trailingPeRatio") or []:
            if not isinstance(item, dict):
                continue
            raw = item.get("reportedValue")
            value = numeric(raw.get("raw")) if isinstance(raw, dict) else None
            try:
                day = date.fromisoformat(item["asOfDate"])
            except (TypeError, KeyError, ValueError):
                continue
            if value is not None and day <= now.date():
                found.append((day, value))
    if found:
        d, v = max(found, key=lambda p: p[0])
        return str(v), str(d)
    return None, None


# Trailing-twelve-month fundamentals requested from Yahoo's fundamentals-timeseries feed.
FUNDAMENTALS = {
    "eps": "trailingDilutedEPS",
    "revenue": "trailingTotalRevenue",
    "ebitda": "trailingEBITDA",
}


def trailing_fundamentals(symbol, now):
    """Latest reported TTM EPS, revenue and EBITDA with the reporting currency and date.

    Returns {key: (value, as-of date)} plus a "currency" entry. Missing figures are simply
    absent, e.g. ETFs report no EBITDA. Cost: one HTTP call for all three series.
    """
    path = "/ws/fundamentals-timeseries/v1/finance/timeseries/" + urlquote(symbol, safe="")
    payload = yahoo_json(
        path,
        {
            "symbol": symbol,
            "type": ",".join(FUNDAMENTALS.values()),
            "period1": int((now - timedelta(days=800)).timestamp()),
            "period2": int(now.timestamp()) + 1,
        },
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("timeseries"), dict):
        raise PortfolioError("Fundamental data is unavailable.")
    found, currency = {}, None
    for group in payload["timeseries"].get("result") or []:
        if (
            not isinstance(group, dict)
            or not isinstance(group.get("meta"), dict)
            or symbol not in (group["meta"].get("symbol") or [])
        ):
            continue
        for key, series in FUNDAMENTALS.items():
            for item in group.get(series) or []:
                if not isinstance(item, dict):
                    continue
                raw = item.get("reportedValue")
                value = numeric(raw.get("raw")) if isinstance(raw, dict) else None
                try:
                    day = date.fromisoformat(item["asOfDate"])
                except (TypeError, KeyError, ValueError):
                    continue
                if value is not None and day <= now.date() and (
                    key not in found or day > found[key][1]
                ):
                    found[key] = (value, day)
                    if isinstance(item.get("currencyCode"), str):
                        currency = item["currencyCode"]
    return {**{k: (str(v), str(d)) for k, (v, d) in found.items()}, "currency": currency}


def trailing_chart(symbol, now, divisor=1):
    """One chart call giving the 12-month dividend per share and the last 30 closes.

    The yield is derived at display time (dividends / latest price), so it moves with the quote
    between daily refreshes. Cost: one HTTP call, then O(d) over d daily bars.
    """
    payload = yahoo_json(
        "/v8/finance/chart/" + urlquote(symbol, safe=""),
        {"range": "1y", "interval": "1d", "events": "div"},
    )
    try:
        item = payload["chart"]["result"][0]
        events = (item.get("events") or {}).get("dividends") or {}
        since = int((now - timedelta(days=365)).timestamp())
        total = sum(
            (numeric(e.get("amount")) or Decimal(0))
            for e in events.values()
            if isinstance(e, dict) and isinstance(e.get("date"), int) and e["date"] >= since
        )
        try:
            closes = item["indicators"]["quote"][0]["close"]
            spark = [str(numeric(c) / divisor) for c in closes if numeric(c) is not None][-30:]
        except (KeyError, IndexError, TypeError):
            spark = []
    except (KeyError, IndexError, TypeError, AttributeError):
        raise PortfolioError("Dividend history is unavailable.") from None
    return dict(div12=str(total / divisor), spark=spark)


def trailing_dividends(symbol, now, divisor=1):
    return trailing_chart(symbol, now, divisor)["div12"]


def stats_due(stats, key, now):
    if stats.get(f"{key}_checked") == str(now.date()):
        return False
    try:
        attempted = datetime.fromisoformat(stats.get(f"{key}_attempted") or "")
        return now - attempted >= STAT_RETRY
    except (TypeError, ValueError):
        return True


def watch_snapshot(symbol, previous, now):
    quote, meta = quote_and_metadata(symbol)
    _, divisor = SUBUNITS.get(quote.listing.price_unit, (quote.currency, 1))

    def stat(key, scale=1):
        v = numeric(meta.get(key))
        return str(v / scale) if v is not None else None

    stats = {
        "change": stat("regularMarketChangePercent"),
        "low": stat("fiftyTwoWeekLow", divisor),
        "high": stat("fiftyTwoWeekHigh", divisor),
        "pe": previous.get("pe"),
        "pe_asof": previous.get("pe_asof"),
        "pe_checked": previous.get("pe_checked"),
        "pe_attempted": previous.get("pe_attempted"),
        **{k: previous.get(k) for k in ("eps", "revenue", "ebitda", "fund_ccy", "fund_asof")},
        "div12": previous.get("div12"),
        "spark": previous.get("spark"),
        "extra_checked": previous.get("extra_checked"),
        "extra_attempted": previous.get("extra_attempted"),
    }
    if stats_due(stats, "pe", now):
        stats["pe_attempted"] = timestamp(now)
        try:
            stats["pe"], stats["pe_asof"] = trailing_pe(symbol, now)
            stats["pe_checked"] = str(now.date())
        except PortfolioError:
            pass  # Quote-based alerts can still be evaluated if fundamentals are unavailable.
    if stats_due(stats, "extra", now):
        # Reuse successful statistics for the day. Failed requests retry on a slower timer.
        stats["extra_attempted"] = timestamp(now)
        done = True
        try:
            found = trailing_fundamentals(symbol, now)
            for key in FUNDAMENTALS:
                stats[key] = found[key][0] if key in found else None
            stats["fund_ccy"] = found["currency"]
            stats["fund_asof"] = max((found[k][1] for k in FUNDAMENTALS if k in found), default=None)
        except PortfolioError:
            done = False
        try:
            chart = trailing_chart(symbol, now, divisor)
            stats["div12"], stats["spark"] = chart["div12"], chart["spark"]
        except PortfolioError:
            done = False
        if done:
            stats["extra_checked"] = str(now.date())
    return quote, stats


def new_item(quote, target=None, direction=DIRECTIONS[0], email=False):
    if not quote.listing or quote.listing.symbol != quote.symbol:
        raise PortfolioError("Choose a verified stock listing.")
    return dict(
        quote=quote.record(),
        target=target,
        direction=direction,
        email=email,
        version=uuid4().hex,
        last_tick=None,
        met=False,
        stats={},
    )


class Watchlist:
    def __init__(self, store):
        self.store = store
        self.path = store.directory / "watchlist.json"

    def _read(self):
        if self.path.exists():
            state = read_json(self.path)
            if not isinstance(state.get("items"), dict) or not isinstance(
                state.get("events"), list
            ):
                raise PortfolioError("Cannot read the saved watchlist.")
            interval = state.get("refresh_seconds")
            if type(interval) is not int or interval not in WATCH_INTERVALS:
                state["refresh_seconds"] = DEFAULT_WATCH_INTERVAL
            try:
                for symbol, item in state["items"].items():
                    q = Quote.from_record(item["quote"])
                    if (
                        q.symbol != symbol
                        or not q.listing
                        or item["direction"] not in DIRECTIONS
                        or not item["version"]
                    ):
                        raise ValueError
                    if (
                        not isinstance(item.get("stats"), dict)
                        or not isinstance(item.get("met"), bool)
                        or not isinstance(item.get("email"), bool)
                        or not isinstance(item.get("automatic_direction", False), bool)
                    ):
                        raise ValueError
                    for key in ("pe", "change", "low", "high", "eps", "revenue", "ebitda", "div12"):
                        val = item["stats"].get(key)
                        if val is not None and numeric(val) is None:
                            raise ValueError
                    line = item["stats"].get("spark")
                    if line is not None and (
                        not isinstance(line, list) or any(numeric(v) is None for v in line)
                    ):
                        raise ValueError
                    if item["last_tick"] is not None:
                        datetime.fromisoformat(item["last_tick"])
                    if item["target"] is not None:
                        number(item["target"], "Watch price", 8)
                for event in state["events"]:
                    if not all(
                        key in event
                        for key in ("id", "symbol", "message", "when", "read", "delivery")
                    ):
                        raise ValueError
                    datetime.fromisoformat(event["when"])
                if not isinstance(state.get("errors"), dict):
                    raise ValueError
            except (AttributeError, KeyError, TypeError, ValueError):
                raise PortfolioError("The saved watchlist contains an invalid entry.") from None
            return state
        items = {}
        if self.store.demo:
            quotes = self.store.quotes()
            items = {
                s: new_item(quotes[s])
                for s in ("AAPL", "ME8U.SI")
                if s in quotes and quotes[s].listing
            }
            # Illustrative demo fundamentals so every watchlist column can be rehearsed offline.
            for s, sample in DEMO_STATS.items():
                if s in items:
                    items[s]["stats"] = dict(sample)
        data = dict(
            schema=1, items=items, events=[], last_check=None, errors={},
            refresh_seconds=DEFAULT_WATCH_INTERVAL,
        )
        if items:
            atomic_json(self.path, data)
        return data

    def snapshot(self):
        with self.store.guarded():
            return self._read()

    def set_refresh_interval(self, seconds):
        if type(seconds) is not int or seconds not in WATCH_INTERVALS:
            raise PortfolioError("Choose a refresh interval from 10 seconds to 3 minutes.")
        with self.store.guarded():
            data = self._read()
            data["refresh_seconds"] = seconds
            atomic_json(self.path, data)
            return data

    def set(self, quote, target="", direction=DIRECTIONS[0], email=False, version=None):
        value = "" if target is None else str(target).strip()
        target = units(number(value, "Watch price", 8)) if value else None
        automatic = direction == AUTO_DIRECTION
        if not automatic and direction not in DIRECTIONS:
            raise PortfolioError("Choose automatic, at or below or at or above.")
        with self.store.guarded():
            data = self._read()
            old = data["items"].get(quote.symbol)
            if (old and old["version"] != version) or (version and not old):
                raise PortfolioError(
                    "This watchlist entry changed or already exists. Reopen it to edit."
                )
            reference = Quote.from_record(old["quote"]) if old else quote
            if reference.currency != quote.currency:
                raise PortfolioError("The listing currency has changed. Review the watch price.")
            if automatic:
                direction = automatic_direction(reference.price, target, old)
            item = new_item(
                quote, target, direction, bool(email and target and not self.store.demo)
            )
            item["automatic_direction"] = automatic
            if old:
                item["quote"], item["stats"] = old["quote"], old["stats"]
                if (target, direction) == (old["target"], old["direction"]):
                    item["last_tick"], item["met"] = old["last_tick"], old["met"]
            data["items"][quote.symbol] = item
            atomic_json(self.path, data)
            return item

    def remove(self, symbol, version):
        with self.store.guarded():
            data = self._read()
            if data["items"].get(symbol, {}).get("version") != version:
                raise PortfolioError("This entry changed. Reopen it before removing.")
            del data["items"][symbol]
            data.get("errors", {}).pop(symbol, None)
            atomic_json(self.path, data)

    def mark_read(self, ids):
        with self.store.guarded():
            data = self._read()
            for item in data["events"]:
                if item["id"] in ids:
                    item["read"] = True
            atomic_json(self.path, data)

    def observe(self, samples, errors, versions, now):
        with self.store.guarded():
            data = self._read()
            new = []
            for symbol, version in versions.items():
                item = data["items"].get(symbol)
                if not item or item["version"] != version:
                    continue
                if item.get("polled_at", "") > timestamp(now):
                    continue
                item["polled_at"] = timestamp(now)
                if symbol in errors:
                    data["errors"][symbol] = errors[symbol]
                    continue
                if symbol not in samples:
                    continue
                q, stats = samples[symbol]
                old = Quote.from_record(item["quote"])
                if (
                    q.symbol != symbol
                    or q.source != "Yahoo Finance"
                    or not q.listing
                    or q.currency != old.currency
                ):
                    data["errors"][symbol] = "Listing or currency could not be verified."
                    continue
                if old.source == "Yahoo Finance" and q.asof < old.asof:
                    continue
                item["quote"], item["stats"] = q.record(), stats
                if now - q.asof > timedelta(days=4) or q.asof > now + timedelta(minutes=5):
                    data["errors"][symbol] = (
                        "Quote is too old or outside the clock window for alerts."
                    )
                    continue
                data["errors"].pop(symbol, None)
                stamp = timestamp(q.asof)
                if item["target"] is None or (item["last_tick"] and stamp <= item["last_tick"]):
                    continue
                level = Decimal(item["target"])
                met = q.price <= level if item["direction"] == DIRECTIONS[0] else q.price >= level
                if met and not item["met"]:
                    message = (
                        f"{symbol} reached {unit_price(q.price, q.currency)} · "
                        f"target {item['direction'].lower()} {unit_price(level, q.currency)}."
                    )
                    event = dict(
                        id=uuid4().hex,
                        symbol=symbol,
                        message=message,
                        when=stamp,
                        read=False,
                        delivery="Pending" if item["email"] else "In app",
                    )
                    new.append(event)
                    data["events"].append(event)
                item["last_tick"], item["met"] = stamp, met
            data["last_check"] = max(timestamp(now), data.get("last_check") or "")
            atomic_json(self.path, data)
            return new

    def deliver(self, send, now):
        # Claim each pending message under the same interprocess lock as the event.
        with self.store.guarded():
            state = self._read()
            candidates = [
                e["id"]
                for e in state["events"]
                if e["delivery"] in ("Pending", "Retry", "Sending")
                and (not e.get("retry_at") or e["retry_at"] <= timestamp(now))
            ]
        for key in candidates:
            claim = uuid4().hex
            with self.store.guarded():
                state = self._read()
                event = next(e for e in state["events"] if e["id"] == key)
                if event["delivery"] not in ("Pending", "Retry", "Sending") or event.get(
                    "retry_at", ""
                ) > timestamp(now):
                    continue
                event.update(
                    delivery="Sending", claim=claim, retry_at=timestamp(now + timedelta(minutes=10))
                )
                atomic_json(self.path, state)
            try:
                send(dict(event))
                status = "Sent"
            except (OSError, ValueError, smtplib.SMTPException, PortfolioError):
                status = "Retry"
            with self.store.guarded():
                state = self._read()
                event = next(e for e in state["events"] if e["id"] == key)
                if event.get("claim") == claim:
                    event["delivery"] = status
                    event.pop("claim", None)
                    atomic_json(self.path, state)


@dataclass(frozen=True)
class MailSettings:
    host: str
    port: int
    sender: str
    recipient: str
    mode: str
    user: str
    password: str

    @classmethod
    def from_env(cls):
        env = os.environ
        host, sender, to = [env.get("FOLIO_SMTP_" + k, "").strip() for k in ("HOST", "FROM", "TO")]
        if not all((host, sender, to)):
            return None
        mode = env.get("FOLIO_SMTP_MODE", "starttls").lower()
        if mode not in ("ssl", "starttls"):
            return None
        try:
            port = int(env.get("FOLIO_SMTP_PORT", "465" if mode == "ssl" else "587"))
        except ValueError:
            return None
        if not 1 <= port <= 65535 or any("\n" in s or "\r" in s for s in (to, sender)):
            return None
        return cls(
            host,
            port,
            sender,
            to,
            mode,
            env.get("FOLIO_SMTP_USER", ""),
            env.get("FOLIO_SMTP_PASSWORD", ""),
        )

    def send(self, event):
        self.deliver(
            f"Folio Ledger price alert · {event['symbol']}",
            event["message"] + "\nExchange quote time (UTC): " + event["when"],
            f"<folio-{event['id']}@folio-ledger.local>",
        )

    def deliver(self, subject, body, message_id=None):
        """Send one plain-text email. Used by price alerts and the daily digest."""
        message = EmailMessage()
        message["From"], message["To"] = self.sender, self.recipient
        message["Subject"] = subject
        if message_id:
            message["Message-ID"] = message_id
        message.set_content(body)
        context = ssl.create_default_context()
        client = (
            smtplib.SMTP_SSL(self.host, self.port, timeout=15, context=context)
            if self.mode == "ssl"
            else smtplib.SMTP(self.host, self.port, timeout=15)
        )
        with client:
            if self.mode == "starttls":
                client.starttls(context=context)
            if self.user:
                client.login(self.user, self.password)
            client.send_message(message)


def check_watches(watch, fetch=watch_snapshot, now=None, mail=None):
    now = now or utcnow()
    data = watch.snapshot()
    items = data["items"]
    events = []
    if items:
        fresh, errors = refresh_prices(items, lambda s: fetch(s, items[s]["stats"], now))
        events = watch.observe(fresh, errors, {s: i["version"] for s, i in items.items()}, now)
    if mail and not watch.store.demo:
        watch.deliver(mail.send, now)
    return events


def monitor(root, once=False):
    from storage import Store

    watch = Watchlist(Store(root, "personal"))
    interval = DEFAULT_WATCH_INTERVAL
    while True:
        started = monotonic()
        try:
            events = check_watches(watch, mail=MailSettings.from_env())
            state = watch.snapshot()
            interval = state["refresh_seconds"]
            print(
                f"{timestamp(utcnow())} · {len(state['items'])} listings · {len(events)} new alerts",
                flush=True,
            )
            for symbol, error in state["errors"].items():
                print(f"{symbol} · {error}", flush=True)
        except (OSError, PortfolioError) as exc:
            print(str(exc), flush=True)
            if once:
                return 1
        if once:
            return 0
        sleep(max(0, interval - (monotonic() - started)))
