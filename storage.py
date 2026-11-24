# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Atomic JSON snapshots, advisory file locks and optimistic edit revisions."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from threading import RLock

from filelock import FileLock, Timeout

from dividends import Dividend, PaymentBook
from market import Quote
from performance import Series
from portfolio import Ledger, PortfolioError, StockSplit, Trade, canonical_names
from trade_import import ImportProfile
from valuation import FxRate

# Folder holding the demo_*.json seed files. Tests point this at a small fixed portfolio.
SEED_DIR = Path(__file__).resolve().parent / "assets"


def read_json(path: Path):
    try:
        with path.open(encoding="utf-8") as handle:
            obj = json.load(handle)
        if not isinstance(obj, dict) or obj.get("schema") != 1:
            raise PortfolioError("Unrecognised file format.")
        return obj
    except (OSError, UnicodeError, ValueError) as exc:
        raise PortfolioError(
            f"Cannot read {path.name}. Check the file or restore a backup. {exc}"
        ) from None


def atomic_json(path: Path, content: dict):
    """A temporary file on the same volume makes os.replace an atomic commit."""
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".folio-",
            suffix=".txt",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(content, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


@dataclass(frozen=True)
class Snapshot:
    revision: int
    next_id: int
    ledger: Ledger


class Store:
    def __init__(self, root: Path, workspace: str):
        if workspace not in ("demo", "personal"):
            raise PortfolioError("Unknown portfolio.")
        self.root = root
        self.directory = root / workspace
        self.ledger_file = self.directory / "transactions.json"
        self.quote_file = self.directory / "quotes.json"
        self.fx_file = self.directory / "fx_rates.json"
        self.payment_file = self.directory / "dividends.json"
        self.history_file = self.directory / "market_history.json"
        self.profile_file = self.directory / "import_profiles.json"
        self.history_stamp, self.history_cache = None, {}
        self.demo = workspace == "demo"
        root.mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(str(root / f"{workspace}_lock.txt"), timeout=5)
        self.mutex = RLock()
        self.cached = self.stamp = None
        with self.guarded():
            if not self.directory.exists():
                seed = SEED_DIR
                trades = (
                    read_json(seed / "demo_transactions.json")
                    if workspace == "demo"
                    else {"schema": 1, "revision": 0, "next_id": 1, "trades": []}
                )
                prices = (
                    read_json(seed / "demo_quotes.json")
                    if workspace == "demo"
                    else {"schema": 1, "quotes": []}
                )
                with tempfile.TemporaryDirectory(dir=root) as staging:
                    folder = Path(staging) / workspace
                    folder.mkdir()
                    atomic_json(folder / "transactions.json", trades)
                    atomic_json(folder / "quotes.json", prices)
                    if self.demo:
                        atomic_json(
                            folder / "dividends.json", read_json(seed / "demo_dividends.json")
                        )
                    os.replace(folder, self.directory)

    @contextmanager
    def guarded(self):
        try:
            with self.mutex, self.lock:
                yield
        except Timeout:
            raise PortfolioError("Another window is saving. Please try again.") from None
        except OSError as exc:
            raise PortfolioError(
                f"File operation failed. Check folder permissions and free space. {exc}"
            ) from None

    def _snapshot(self):
        try:
            stat = self.ledger_file.stat()
            stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
            if self.stamp != stamp or self.cached is None:
                raw = read_json(self.ledger_file)
                rev, nxt = raw["revision"], raw["next_id"]
                if (
                    type(rev) is not int
                    or rev < 0
                    or type(nxt) is not int
                    or not isinstance(raw["trades"], list)
                ):
                    raise PortfolioError("Invalid revision, next ID or transaction list.")
                ledger = Ledger(
                    (Trade.from_input(row["id"], row) for row in raw["trades"]),
                    raw.get("cost_method", "FIFO"),
                    (StockSplit.parse(event) for event in raw.get("splits", [])),
                )
                if nxt <= max(ledger.by_id, default=0):
                    raise PortfolioError("The next transaction ID must exceed all existing IDs.")
                self.cached, self.stamp = Snapshot(rev, nxt, ledger), stamp
            return self.cached
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise PortfolioError(f"Could not load {self.ledger_file.name}. {exc}") from None

    def snapshot(self):
        with self.guarded():
            return self._snapshot()

    @staticmethod
    def verified_trade(trade_id, data, quote):
        if not isinstance(quote, Quote) or not quote.listing or quote.source != "Yahoo Finance":
            raise PortfolioError("Verify the listing and its market quote before saving.")
        quote = Quote.from_record(quote.record())
        new = Trade.from_input(trade_id, data)
        if new.symbol != quote.symbol or new.currency != quote.currency:
            raise PortfolioError("The trade no longer matches the verified symbol and currency.")
        return new

    def commit(
        self,
        action: str,
        revision: int,
        data=None,
        trade_id=None,
        *,
        verified_quote=None,
        correct_all=False,
    ):
        with self.guarded():
            old = self._snapshot()
            if old.revision != revision:
                raise PortfolioError(
                    "The ledger changed in another window. Close this form, reload and try again."
                )
            records = dict(old.ledger.by_id)
            broker_names = canonical_names(t.broker for t in old.ledger.trades)
            nxt = old.next_id
            method = old.ledger.method
            splits = dict(old.ledger.splits_by_key)
            observed = {}
            if action in ("add", "edit"):
                if action == "edit" and trade_id not in records:
                    raise PortfolioError("The selected transaction is unavailable.")
                new = self.verified_trade(
                    nxt if action == "add" else trade_id, data, verified_quote
                )
                new = replace(new, broker=broker_names.setdefault(new.broker.casefold(), new.broker))
                if action == "edit":
                    new = replace(new, import_key=records[trade_id].import_key)
                observed[new.symbol] = verified_quote
                if correct_all and action == "edit":
                    previous = records[trade_id].symbol
                    if previous != new.symbol or records[trade_id].currency != new.currency:
                        for key, item in tuple(records.items()):
                            if item.symbol == previous:
                                # Revalidate precision and fees in the corrected currency, retaining all entered amounts.
                                record = dict(
                                    item.record(), symbol=new.symbol, currency=new.currency
                                )
                                records[key] = Trade.from_input(key, record)
                records[new.id] = new
                if action == "add":
                    nxt += 1
            elif action == "delete" and trade_id in records:
                del records[trade_id]
            elif action == "method":
                method = data
            elif action == "split":
                event = StockSplit.parse(data)
                if event.symbol not in old.ledger.currencies:
                    raise PortfolioError("Record a transaction for the listing first.")
                key = (event.symbol, event.day)
                if key in splits and key != trade_id:
                    raise PortfolioError("A stock split already exists for this listing and date.")
                if trade_id is not None:
                    del splits[trade_id]
                splits[key] = event
            elif action == "remove_split" and trade_id in splits:
                del splits[trade_id]
            elif action == "import":
                if not data:
                    raise PortfolioError("Select at least one reviewed trade.")
                imported = {t.import_key for t in records.values() if t.import_key}
                for row, quote in data:
                    new = self.verified_trade(nxt, row, quote)
                    if not new.import_key or new.import_key in imported:
                        raise PortfolioError(
                            "This statement row has already been imported. Review the file again."
                        )
                    imported.add(new.import_key)
                    new = replace(new, broker=broker_names.setdefault(new.broker.casefold(), new.broker))
                    records[nxt] = new
                    observed[new.symbol] = quote
                    nxt += 1
            else:
                raise PortfolioError("The selected transaction is unavailable.")
            # Replaying the entire candidate also validates all later historical sales.
            candidate = Ledger(records.values(), method, splits.values())
            if observed:
                # Persist the mark before the ledger. A failed write can leave an unused
                # quote, but cannot leave a saved trade without its verified mark.
                self.save_quotes(observed.values())
            atomic_json(
                self.ledger_file,
                {
                    "schema": 1,
                    "revision": old.revision + 1,
                    "next_id": nxt,
                    "cost_method": method,
                    "trades": [t.record() for t in candidate.trades],
                    "splits": [event.record() for event in candidate.splits],
                },
            )
            self.cached = Snapshot(old.revision + 1, nxt, candidate)
            stat = self.ledger_file.stat()
            self.stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
            return self.cached

    def rebalance_settings(self):
        """Saved target weights (percent per symbol) and the no-trade band."""
        with self.guarded():
            path = self.directory / "targets.json"
            if not path.exists():
                return {}, "2"
            data = read_json(path)
            targets, band = data.get("targets"), data.get("band", "2")
            if not isinstance(targets, dict) or not isinstance(band, str):
                raise PortfolioError("Cannot read rebalancing targets.")
            return targets, band

    def save_rebalance_settings(self, targets, band):
        with self.guarded():
            atomic_json(
                self.directory / "targets.json",
                {"schema": 1, "targets": {k: str(v) for k, v in targets.items()}, "band": str(band)},
            )

    def _import_profiles(self):
        if not self.profile_file.exists():
            return []
        records = read_json(self.profile_file).get("profiles")
        if not isinstance(records, list):
            raise PortfolioError("Cannot read the saved CSV mappings.")
        return [ImportProfile.from_record(record) for record in records]

    def import_profiles(self):
        with self.guarded():
            return self._import_profiles()

    def save_import_profile(self, profile):
        with self.guarded():
            profiles = [
                old for old in self._import_profiles()
                if not (old.broker.casefold() == profile.broker.casefold()
                        and old.remap(profile.headers, profile.signed) is not None)
            ]
            profiles.append(profile)
            atomic_json(self.profile_file, {"schema": 1, "profiles": [p.record() for p in profiles]})
            return profiles

    def _quotes(self):
        try:
            rows = read_json(self.quote_file)["quotes"]
            if not isinstance(rows, list):
                raise PortfolioError("Invalid quote list.")
            values = {}
            for row in rows:
                quote = Quote.from_record(row)
                if quote.symbol in values:
                    raise PortfolioError(f"Duplicate price for {quote.symbol}.")
                values[quote.symbol] = quote
            return values
        except (ValueError, KeyError, TypeError) as exc:
            raise PortfolioError(f"Could not load {self.quote_file.name}. {exc}") from None

    def quotes(self):
        with self.guarded():
            return self._quotes()

    def fx_rates(self):
        with self.guarded():
            path = self.fx_file
            if not path.exists():
                if not self.demo:
                    return {}
                path = SEED_DIR / "demo_fx.json"
            try:
                rows = read_json(path)["rates"]
                if not isinstance(rows, list):
                    raise ValueError
                rates = {}
                for row in rows:
                    fx = FxRate.from_record(row)
                    if fx.currency in rates:
                        raise ValueError
                    rates[fx.currency] = fx
                return rates
            except (KeyError, TypeError, ValueError) as exc:
                raise PortfolioError(
                    f"Could not load the SGD exchange rates from {path.name}. {exc}"
                ) from None

    def save_fx_rates(self, observed):
        with self.guarded():
            rates = self.fx_rates()
            before = rates.copy()
            for item in observed:
                fx = FxRate.from_record(item.record())
                if fx.currency not in rates or fx.asof >= rates[fx.currency].asof:
                    rates[fx.currency] = fx
            if rates != before:
                atomic_json(
                    self.fx_file, {"schema": 1, "rates": [rates[c].record() for c in sorted(rates)]}
                )
            return rates

    def _payments(self):
        if not self.payment_file.exists():
            return PaymentBook(0, 1, {})
        try:
            return PaymentBook.load(read_json(self.payment_file))
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise PortfolioError(f"Could not read dividends.json. {exc}") from None

    def payments(self):
        with self.guarded():
            return self._payments()

    def save_payment(self, revision, data=None, payment_id=None):
        with self.guarded():
            old = self._payments()
            if old.revision != revision:
                raise PortfolioError("Dividend records changed in another window. Reopen the form.")
            if payment_id is not None and payment_id not in old.rows:
                raise PortfolioError("The selected dividend is unavailable.")
            rows = dict(old.rows)
            nxt = old.next_id
            if data is None:
                if payment_id is None:
                    raise PortfolioError("Choose a dividend to remove.")
                del rows[payment_id]
            else:
                item = Dividend.parse(payment_id or nxt, data)
                journal = self._snapshot().ledger
                broker_names = canonical_names(
                    [*(t.broker for t in journal.trades), *(p.broker for p in rows.values())]
                )
                item = replace(item, broker=broker_names.get(item.broker.casefold(), item.broker))
                old_symbol = old.rows[payment_id].symbol if payment_id else None
                if (
                    item.symbol != old_symbol
                    and item.symbol not in journal.currencies
                ):
                    raise PortfolioError("Choose a symbol from this portfolio’s trade history.")
                for d in rows.values():
                    same_event = (
                        d.ex_day == item.ex_day if d.ex_day and item.ex_day else d.day == item.day
                    )
                    if (
                        d.id != item.id
                        and d.symbol == item.symbol
                        and same_event
                        and d.currency == item.currency
                        and (
                            not d.broker
                            or not item.broker
                            or d.broker.casefold() == item.broker.casefold()
                        )
                    ):
                        raise PortfolioError(
                            "This payout is already recorded. Edit the existing payment."
                        )
                rows[item.id] = item
                nxt += payment_id is None
            atomic_json(
                self.payment_file,
                dict(
                    schema=1,
                    revision=old.revision + 1,
                    next_id=nxt,
                    payments=[d.record() for d in rows.values()],
                ),
            )

    def _histories(self):
        path = self.history_file
        if not path.exists():
            if not self.demo:
                return {}
            path = SEED_DIR / "demo_history.json"
        stat = path.stat()
        stamp = (path, stat.st_mtime_ns, stat.st_size, stat.st_ino)
        if stamp != self.history_stamp:
            try:
                rows = read_json(path)["series"]
                parsed = [Series.parse(row) for row in rows]
                self.history_cache = {s.key: s for s in parsed}
                self.history_stamp = stamp
            except (KeyError, TypeError, ValueError, AttributeError, ArithmeticError) as exc:
                raise PortfolioError(f"Could not read daily market history. {exc}") from None
        return dict(self.history_cache)

    def histories(self):
        with self.guarded():
            return self._histories()

    def save_histories(self, updates):
        with self.guarded():
            old = self._histories()
            for h in updates:
                previous = old.get(h.key)
                if previous and previous.currency == h.currency:
                    if not h.sector:
                        h = replace(h, sector=previous.sector)
                    if previous.source != "Demo history":
                        recent, prior = (
                            (previous, h) if previous.checked > h.checked else (h, previous)
                        )
                        h = recent.include_earlier(prior) if prior.start < recent.start else recent
                old[h.key] = h
            if updates:
                atomic_json(
                    self.history_file, {"schema": 1, "series": [h.record() for h in old.values()]}
                )
            return old

    def save_quotes(self, quotes):
        with self.guarded():
            old = self._quotes()
            original = old.copy()
            accepted = set()
            for quote in quotes:
                previous = old.get(quote.symbol)
                if previous is not None and previous.currency != quote.currency:
                    raise PortfolioError(
                        f"The quote currency for {quote.symbol} changed from {previous.currency} to {quote.currency}. Review its trades."
                    )
                if (
                    previous
                    and previous.listing
                    and quote.listing
                    and quote.listing.name.upper() == quote.symbol
                ):
                    quote = replace(
                        quote, listing=replace(quote.listing, name=previous.listing.name)
                    )
                if previous is None or quote.asof >= previous.asof:
                    if previous and previous.listing and not quote.listing:
                        quote = replace(quote, listing=previous.listing)
                    old[quote.symbol] = quote
                    accepted.add(quote.symbol)
                elif quote.listing and (
                    not previous.listing or previous.listing.name.upper() == quote.symbol
                ):
                    # A newer manual price is still preferred, with the verified identity attached.
                    old[quote.symbol] = replace(previous, listing=quote.listing)
            if old != original:
                atomic_json(
                    self.quote_file, {"schema": 1, "quotes": [q.record() for q in old.values()]}
                )
            return accepted
