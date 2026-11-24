# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Read broker exports locally and produce editable drafts before any ledger write."""

import csv
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import Path

from market import PortfolioError, SymbolChoice, resolve_symbol
from portfolio import Trade, clean_name, minor_unit, units

LABELS = {
    "date": "Trade date",
    "symbol": "Symbol or company",
    "side": "Buy or sell",
    "quantity": "Quantity",
    "price": "Unit price",
    "amount": "Gross amount",
    "fee": "Trading fees",
    "broker": "Brokerage",
    "currency": "Currency",
    "note": "Note",
    "asset": "Asset class",
}
ALIASES = {
    "date": "trade date time|date/time|executed at|trade date|transaction date|execution date|date|filled time|order filled time|contract date",
    "symbol": "symbol|ticker|stock symbol|stock ticker|security code|stock code|counter code|product code|instrument|security|security name|stock name|counter name|company",
    "side": "side|action|buy/sell|direction|order side|trade type|transaction type|b/s|type",
    "quantity": "quantity|qty|units|shares|filled quantity|executed quantity|trade quantity|no of shares|quantity filled",
    "price": "price|unit price|trade price|t. price|execution price|avg price|average price|dealt price|filled price|average fill price|price per share",
    "amount": "gross amount|trade amount|gross consideration|consideration|amount|principal amount|gross value|trade value",
    "fee": "total fees|total charges|combined fees|trading fees|fees|fee|comm/fee|commission and fees|transaction fees|transaction fee|commission|commission fee|IB commission",
    "broker": "broker|brokerage|platform|broker name|brokerage name",
    "currency": "currency|ccy|trade currency|settlement currency|currency code",
    "note": "note|notes|memo|remarks|description notes|comment|comments",
    "asset": "asset class|asset category|instrument type|asset type|security type",
}


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", s.lower().strip())


OPTIONS = {
    name: tuple(dict.fromkeys(norm(label) for label in [*spec.split("|"), LABELS[name]]))
    for name, spec in ALIASES.items()
}
SIDES = {
    "BUY": "BUY",
    "B": "BUY",
    "BOT": "BUY",
    "BOUGHT": "BUY",
    "PURCHASE": "BUY",
    "SELL": "SELL",
    "S": "SELL",
    "SLD": "SELL",
    "SOLD": "SELL",
    "SALE": "SELL",
}


def guess(headers):
    columns = {norm(header): i for i, header in reversed(list(enumerate(headers)))}
    return {
        key: next(columns[alias] for alias in aliases if alias in columns)
        for key, aliases in OPTIONS.items()
        if any(alias in columns for alias in aliases)
    }


STOCK_CLASSES = {
    "stock",
    "stocks",
    "stk",
    "equity",
    "equities",
    "reit",
    "reits",
    "etf",
    "etfs",
    "listedreit",
    "equityandreit",
}
FEE_PARTS = {
    "commission",
    "commissionfee",
    "platformfee",
    "clearingfee",
    "tradingfee",
    "tradingaccessfee",
    "settlementfee",
    "regulatoryfee",
    "exchangefee",
    "gst",
    "gstamount",
    "stampduty",
    "secfee",
    "sgxclearingfee",
    "sgxtradingfee",
}


def fee_indices(headers):
    names = [norm(h) for h in headers]
    if any(
        name
        in {"totalfees", "totalcharges", "fees", "combinedfees", "tradingfees", "commissionandfees"}
        for name in names
    ):
        return []
    indices = [i for i, name in enumerate(names) if name in FEE_PARTS]
    return indices if len(indices) > 1 else []


def combined_fee(cells, indices):
    values = [cells[i].strip() for i in indices if i < len(cells) and cells[i].strip()]
    try:
        return units(sum((Decimal(decimal_text(v)) for v in values), Decimal(0)))
    except PortfolioError:
        return "Check fee components " + " + ".join(values)


@dataclass
class Source:
    headers: list
    rows: list
    digest: str
    broker: str = ""
    signed: bool = False
    info: str = ""
    overlapping_orders: set = field(default_factory=set)


def header_names(headers):
    return tuple(clean_name(name).casefold() for name in headers)


@dataclass(frozen=True)
class ImportProfile:
    broker: str
    headers: tuple[str, ...]
    columns: dict[str, int]
    date_order: str
    row_order: str
    signed: bool

    @classmethod
    def from_record(cls, record):
        try:
            broker = clean_name(record["broker"])
            raw_headers, columns = record["headers"], record["columns"]
            date_order, row_order, signed = record["date_order"], record["row_order"], record["signed"]
            if (
                not broker or len(broker) > 50 or not broker.isprintable()
                or not isinstance(raw_headers, (list, tuple))
                or not all(isinstance(header, str) for header in raw_headers)
                or date_order not in ("DMY", "MDY") or row_order not in ("old", "new")
                or type(signed) is not bool
                or not isinstance(columns, dict)
                or any(key not in LABELS or type(i) is not int or not 0 <= i < len(raw_headers)
                       for key, i in columns.items())
            ):
                raise ValueError
        except (AttributeError, KeyError, TypeError, ValueError):
            raise PortfolioError("A saved mapping needs a valid brokerage and CSV columns.") from None
        validate_mapping(columns, signed)
        if len(columns.keys() & {"quantity", "price", "amount"}) < 2:
            raise PortfolioError("Map at least two of quantity, unit price and gross amount.")
        return cls(broker, header_names(raw_headers), columns.copy(), date_order, row_order, signed)

    def record(self):
        return dict(
            broker=self.broker, headers=self.headers, columns=self.columns,
            date_order=self.date_order, row_order=self.row_order, signed=self.signed,
        )

    def remap(self, headers, signed):
        if self.signed != signed:
            return None
        current = header_names(headers)
        if current == self.headers:
            return self.columns.copy()
        # A repeated label cannot identify a specific column after a reorder.
        indices = {name: i for i, name in enumerate(current)}
        if len(indices) == len(current) == len(self.headers) and set(indices) == set(self.headers):
            return {key: indices[self.headers[i]] for key, i in self.columns.items()}
        return None


def likely_broker(content):
    for term, name in [
        (r"\bDBS\s+Vickers\b", "DBS Vickers"),
        (r"\bSyfe\b", "Syfe"),
        (r"\bInteractive Brokers\b", "IBKR"),
    ]:
        if re.search(term, content, re.I):
            return name
    return ""


def csv_source(data):
    codec = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    try:
        value = data.decode(codec)
    except UnicodeError:
        value = data.decode("cp1252")
    if "\x00" in value:
        raise PortfolioError("This file is not a text CSV. Export a UTF-8 CSV from your broker.")
    try:
        dialect = csv.Sniffer().sniff(value[:10000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [
        (i, row)
        for i, row in enumerate(csv.reader(StringIO(value, newline=""), dialect=dialect), 1)
        if any(x.strip() for x in row)
    ]
    if not rows:
        raise PortfolioError("The CSV is empty.")
    ib = next((r for _, r in rows if [norm(x) for x in r[:2]] == ["trades", "header"]), None)
    if ib is not None:
        indices = {norm(col): i for i, col in enumerate(ib)}
        cat = indices.get("assetcategory")
        cols = []
        for i, r in rows:
            if (
                len(r) < 3
                or r[:2] != ["Trades", "Data"]
                or r[2].strip().lower() not in {"order", "trade", "execution", "exchtrade"}
            ):
                continue
            if cat is not None and (len(r) <= cat or norm(r[cat]) not in STOCK_CLASSES):
                continue
            cols.append((i, r))
        if not cols:
            raise PortfolioError(
                "The IBKR Trades section contains no supported stock or ETF executions."
            )
        # Only discard an aggregate when the detailed fills reconcile to its share count.
        # Ambiguous orders stay available for an explicit choice in the review table.
        exclude, overlaps = set(), set()
        if {"currency", "symbol", "datetime", "quantity"}.issubset(indices):
            groups = defaultdict(list)
            for line, row in cols:
                try:
                    qty = Decimal(decimal_text(row[indices["quantity"]]))
                    stamp = row[indices["datetime"]].split(",")[0].split(" ")[0]
                    order_id = row[indices["orderid"]] if "orderid" in indices else ""
                    key = (
                        row[indices["currency"]],
                        row[indices["symbol"]],
                        stamp,
                        qty < 0,
                        order_id,
                    )
                    groups[key].append((line, row[2].strip().lower() == "order", qty))
                except (ArithmeticError, IndexError, PortfolioError):
                    continue  # The ordinary row checks will report malformed executions.
            for group in groups.values():
                orders = [(ix, qty) for ix, order, qty in group if order]
                fills = [qty for ix, order, qty in group if not order]
                if orders and fills:
                    target = exclude if sum(q for _, q in orders) == sum(fills) else overlaps
                    target.update(ix for ix, _ in orders)
            cols = [(ix, row) for ix, row in cols if ix not in exclude]
        return Source(
            ib,
            cols,
            "",
            "IBKR",
            True,
            "IBKR stock trades. Reconciled order summaries are skipped. Check any overlapping orders marked as possible duplicates.",
            overlaps,
        )
    head = next(
        (ix for ix, (_, r) in enumerate(rows[:45]) if len(guess(r)) >= 3 and "date" in guess(r)), 0
    )
    headers = rows[head][1]
    body = rows[head + 1 :]
    components = fee_indices(headers)
    if components:
        body = [
            (i, [*(row + [""] * len(headers))[: len(headers)], combined_fee(row, components)])
            for i, row in body
        ]
        headers = [*headers, "Combined fees"]
    return Source(
        headers,
        body,
        "",
        likely_broker("\n".join(" ".join(r) for _, r in rows[:head])),
        info="Map the columns from your export. Amount means gross trade value before fees.",
    )


def pdf_source(data):
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise PortfolioError(
                "The PDF is password protected. Upload an unlocked copy or a CSV export."
            )
        pages = [p.extract_text(extraction_mode="layout") or "" for p in reader.pages]
    except PortfolioError:
        raise
    except (PyPdfError, ValueError, KeyError, TypeError, IndexError, OSError) as exc:
        raise PortfolioError(
            "This PDF could not be read. Use an unlocked text PDF or a CSV export."
        ) from exc
    if not any(page.strip() for page in pages):
        raise PortfolioError(
            "This PDF is a scan. Use your broker’s CSV or a searchable PDF with a text layer."
        )
    content = "\n".join(pages)
    broker = likely_broker(content[:8000])
    parsed = []
    labels = {}
    skipped = 0

    def add(record):
        if record:
            parsed.append(record)

    for page in pages:
        columns = None
        contract = {}
        for line in page.splitlines():
            if not line.strip():
                continue
            tokens = list(re.finditer(r"\S(?:.*?\S)?(?=\s{2,}|$)", line.rstrip()))
            cells = [t.group().strip() for t in tokens]
            mapping = guess(cells)
            if (
                "symbol" in mapping
                and "date" in mapping
                and ("quantity" in mapping or "amount" in mapping)
                and ("price" in mapping or "amount" in mapping)
            ):
                columns = (mapping, [t.start() for t in tokens], len(cells), fee_indices(cells))
                continue
            if columns:
                mapping, starts, n, fee_parts = columns
                if len(cells) == n:
                    values = cells
                else:
                    values = [line[a:b].strip() for a, b in zip(starts, starts[1:] + [len(line)])]
                rec = {key: values[i] for key, i in mapping.items()}
                if fee_parts:
                    rec["fee"] = combined_fee(values, fee_parts)
                d = rec.get("date", "")
                if re.search(
                    r"\d{1,4}[-/. ](?:\d{1,2}|[A-Za-z]{3,9})[-/. ,]+\d{2,4}", d
                ) or re.search(r"[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}", d):
                    add(rec)
                    continue
                if any(re.fullmatch(r"[-+()\d.,]+", v) for v in cells) and re.search(
                    r"\b(?:BUY|SELL|BOT|SLD)\b", line, re.I
                ):
                    add(rec)  # Show malformed trade rows for correction.
                    continue
            # Some contract notes print labelled fields instead of a table.
            for pair in re.finditer(
                r"(?:^|\s{2,})([A-Za-z][A-Za-z /().]{1,35}?)\s*:\s*(.*?)(?=\s{2,}[A-Za-z][A-Za-z /().]{1,35}?\s*:|$)",
                line.strip(),
            ):
                field_name = next(
                    (k for k, values in OPTIONS.items() if norm(pair[1]) in values), None
                )
                if not field_name:
                    continue
                if field_name == "date" and "date" in contract:
                    if "symbol" in contract:
                        add(contract)
                    contract = {}
                val = pair[2].strip()
                if norm(pair[1]) == "brokerage" and re.fullmatch(r"[0-9,.]+", val):
                    field_name = "fee"
                contract[field_name] = val
            if (
                re.search(r"\b(?:BUY|SELL|BOUGHT|SOLD)\b", line, re.I)
                and ":" not in line
                and not mapping
                and re.search(r"\d", line)
            ):
                skipped += 1
        if "symbol" in contract and "date" in contract:
            add(contract)
    if not parsed:
        raise PortfolioError(
            "No trade table or labelled contract note was recognised. Export the transaction CSV from your broker. Holdings summaries contain no execution details."
        )
    for row in parsed:
        labels.update(dict.fromkeys(row))
    headers = list(labels)
    return Source(
        [LABELS[k] for k in headers],
        [(i, [r.get(k, "") for k in headers]) for i, r in enumerate(parsed, 1)],
        "",
        broker,
        False,
        "Extracted text from the PDF. Compare the rows, fees and trade count with your statement."
        + (
            f" {skipped} additional line(s) may contain trades and need checking against the original."
            if skipped
            else ""
        ),
    )


def read_source(filename, data):
    if not data:
        raise PortfolioError("The selected file is empty.")
    ext = Path(filename).suffix.lower()
    if ext not in (".csv", ".pdf"):
        raise PortfolioError("Choose a CSV export or a PDF statement.")
    try:
        src = pdf_source(data) if ext == ".pdf" else csv_source(data)
    except (csv.Error, UnicodeError) as exc:
        raise PortfolioError(
            "Could not read the text or columns in this file. Try a UTF-8 CSV export."
        ) from exc
    if not src.rows:
        raise PortfolioError("The file contains a header but no trade rows.")
    src.digest = sha256(data).hexdigest()
    return src


def date_string(value, order):
    raw = value.strip()
    # IBKR uses an ISO trade date followed by a comma and execution time.
    trimmed = re.sub(r"(?:T|,?\s+)\d{1,2}:\d{2}.*$", "", raw)
    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y%m%d",
        "%d %b %Y",
        "%d %B %Y",
        "%d-%b-%Y",
        "%b %d, %Y",
        "%B %d, %Y",
    ]
    parts = ("%d", "%m") if order == "DMY" else ("%m", "%d")
    for sep in ("/", "-", "."):
        formats.append(sep.join([*parts, "%Y"]))
    for fmt in formats:
        try:
            return datetime.strptime(trimmed, fmt).date().isoformat()
        except ValueError:
            pass
    raise PortfolioError(f"Check the trade date “{value}” and date order.")


def decimal_text(value):
    v = value.strip().replace("−", "-")
    if not v:
        return ""
    if v.startswith("(") and v.endswith(")"):
        v = "-" + v[1:-1].strip()
    if "," in v:
        if not re.fullmatch(r"[+-]?\d{1,3}(,\d{3})+(\.\d+)?", v):
            raise PortfolioError(f"Check the number “{value}”. Use a decimal point.")
        v = v.replace(",", "")
    if len(v) > 40 or not re.fullmatch(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)", v):
        raise PortfolioError(f"Check the number or currency unit “{value}”.")
    return units(Decimal(v))


def signature(t):
    return (t.day, t.symbol, t.side, t.qty, t.price, t.fee, t.currency, t.broker.casefold())


@dataclass
class Draft:
    line: int
    values: dict
    key: str
    trade: Trade | None = None
    quote: object = None
    error: str = ""
    choices: dict = field(default_factory=dict)
    duplicate: bool = False
    imported: bool = False
    selected: bool = False


def validate_mapping(mapping, signed):
    if "date" not in mapping or "symbol" not in mapping:
        raise PortfolioError("Assign the trade date and symbol columns.")
    if "side" not in mapping and not signed:
        raise PortfolioError("Assign the column containing Buy or Sell.")
    if len(set(mapping.values())) != len(mapping):
        raise PortfolioError("Assign each source column to just one field.")


def drafts(source, mapping, broker):
    validate_mapping(mapping, source.signed)
    result = []
    for line, cells in source.rows:
        values = {k: (cells[ix].strip() if ix < len(cells) else "") for k, ix in mapping.items()}
        values["broker"] = values.get("broker") or broker.strip()
        result.append(Draft(line, values, f"{source.digest}:{line}"))
    return result


def reviewed_trade(draft, quote, source, order):
    r = draft.values
    if r.get("asset") and norm(r["asset"]) not in STOCK_CLASSES:
        raise PortfolioError(
            f"Unsupported asset class {r['asset']}. Only stocks, listed REITs and ETFs can be imported."
        )
    if r.get("currency", "").strip() and r["currency"].upper() != quote.currency:
        raise PortfolioError(
            f"The statement currency {r['currency']} differs from the listing’s {quote.currency}. Check the exchange and price units."
        )
    qty = decimal_text(r.get("quantity", ""))
    side = r.get("side", "").strip().upper()
    if source.signed and not side and qty:
        side = "SELL" if Decimal(qty) < 0 else "BUY"
    if side not in SIDES:
        raise PortfolioError("Set the action to Buy or Sell.")
    side = SIDES[side]
    if qty and Decimal(qty) < 0:
        if side != "SELL":
            raise PortfolioError("A negative quantity conflicts with a buy.")
        qty = units(abs(Decimal(qty)))

    def money(key):
        value = r.get(key, "").strip()
        for prefix in ("US$", "S$", "HK$", "A$", "C$"):
            if value.startswith(prefix):
                code = {"US$": "USD", "S$": "SGD", "HK$": "HKD", "A$": "AUD", "C$": "CAD"}[prefix]
                if code != quote.currency:
                    raise PortfolioError(
                        f"{prefix} conflicts with the listing currency {quote.currency}."
                    )
                value = value[len(prefix) :].strip()
                break
        if value.startswith(quote.currency + " "):
            value = value[4:].lstrip()
        return decimal_text(value.removeprefix("$"))

    price, amount, fee = money("price"), money("amount"), money("fee")
    if amount:
        amount = units(abs(Decimal(amount)))
    if source.signed and fee:
        fee = units(-Decimal(fee))
    if not price and amount and qty and Decimal(qty) > 0:
        price = units((Decimal(amount) / Decimal(qty)).quantize(Decimal(".00000001")))
    if not price:
        raise PortfolioError("Add the execution price, or provide quantity and gross amount.")
    row = dict(
        symbol=quote.symbol,
        currency=quote.currency,
        side=side,
        date=date_string(r.get("date", ""), order),
        quantity=qty,
        price=price,
        fee=fee,
        broker=r["broker"],
        note=r.get("note", ""),
        import_key=draft.key,
    )
    if not qty:
        if not amount:
            raise PortfolioError("Add quantity or a gross trade amount before fees.")
        row["amount"] = amount
    tr = Trade.from_input(draft.line, row)
    if qty and amount and abs(tr.gross - Decimal(amount)) > minor_unit(quote.currency):
        raise PortfolioError(
            "Quantity × unit price differs from the gross amount. Check the columns and fees."
        )
    return tr


def review(rows, source, order, book, cache, lookup=resolve_symbol):
    imported = {t.import_key for t in book.trades if t.import_key}
    queries = {d.values.get("symbol", "").strip().upper() for d in rows if d.key not in imported}

    def one(query):
        try:
            return query, lookup(query)
        except PortfolioError as exc:
            return query, exc

    with ThreadPoolExecutor(max_workers=4) as pool:
        cache.update(pool.map(one, sorted(q for q in queries if q and q not in cache)))
    seen = {signature(t) for t in book.trades}
    for d in rows:
        d.trade, d.quote, d.error, d.choices, d.selected, d.duplicate = (
            None,
            None,
            "",
            {},
            False,
            False,
        )
        d.imported = d.key in imported
        if d.imported:
            continue
        query = d.values.get("symbol", "").strip().upper()
        q = cache.get(query)
        try:
            if not q:
                raise PortfolioError("Enter a stock ticker.")
            if isinstance(q, PortfolioError):
                if isinstance(q, SymbolChoice):
                    d.choices = q.choices
                raise q
            d.quote = q
            d.trade = reviewed_trade(d, q, source, order)
            sig = signature(d.trade)
            d.duplicate = sig in seen or d.line in source.overlapping_orders
            seen.add(sig)
            d.selected = not d.duplicate
        except PortfolioError as exc:
            d.error = str(exc)
        except ArithmeticError:
            d.error = "Check the precision and size of the amounts on this row."
    return rows


def commit_rows(rows, newest_first=False):
    """Within a date, prefer explicit execution times or the chosen statement row order."""
    groups = defaultdict(list)
    for row in rows:
        if row.selected and row.trade:
            groups[row.trade.day].append(row)
    result = []
    for day in sorted(groups):
        group = groups[day]

        def clock(r):
            m = re.search(r"(?:T|,?\s+)(\d{1,2}):(\d{2})(?::(\d{2}))?", r.values.get("date", ""))
            return tuple(int(x or 0) for x in m.groups()) if m else None

        if all(clock(row) is not None for row in group):
            group = sorted(group, key=clock)
        elif newest_first:
            group = list(reversed(group))
        result.extend((r.trade.record(), r.quote) for r in group)
    return result
