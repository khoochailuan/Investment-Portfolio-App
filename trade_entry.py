# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Stock autocomplete, dated execution-price suggestions and reviewed trade entry."""

import asyncio

import flet as ft

from dividends import today_sg
from market import PortfolioError, SymbolChoice, resolve_symbol, search_listings
from performance import trade_reference
from name_picker import brokerage_picker
from portfolio import (
    DATE_HINT,
    display_date,
    dollars,
    number,
    quantity_from_amount,
    trade_day,
    unit_price,
    units,
)
from trade_calendar import trade_calendar
from widgets import (
    ENTRY_HEIGHT,
    ENTRY_MARGIN,
    MUTED,
    NAVY,
    RED,
    choose,
    field,
    heading,
    panel,
    primary,
    secondary,
    text,
    when,
)


class TradeEditor:
    def __init__(self, app, trade=None):
        self.app, self.page, self.store = app, app.page, app.store
        self.trade, self.revision = trade, app.snap.revision
        self.checked = self.validation = self.price_task = None
        self.generation = self.price_generation = 0
        self.search_active = False
        self.manual_price = trade is not None
        self.committing = False
        self.stock = field(
            "Stock symbol",
            trade.symbol if trade else "",
            320,
            hint="Search by ticker or company name",
            prefix_icon=ft.Icons.SEARCH,
            on_change=self.search_changed,
            on_focus=self.focus_search,
            on_blur=self.leave_search,
            on_submit=self.submit_symbol,
        )
        self.choices = ft.Column(spacing=0, visible=False)
        self.status = text("", 14, NAVY, True)
        self.status.visible = False
        self.day = field(
            "Transaction date",
            display_date(trade.day if trade else today_sg()),
            182,
            hint=DATE_HINT,
            on_change=self.date_changed,
        )
        self.side = choose(
            "Action", [("BUY", "Buy"), ("SELL", "Sell")], trade.side if trade else "BUY", width=135
        )
        self.broker = brokerage_picker(app, trade.broker if trade else "")
        self.mode = choose(
            "Enter trade by",
            [("qty", "Quantity"), ("amount", "Amount")],
            "amount" if trade and trade.amount is not None else "qty",
            self.change_mode,
            175,
        )
        prefix = trade.currency + " " if trade else ""
        self.qty = field(
            "Quantity", units(trade.qty) if trade else "", 170, on_change=self.figure_changed
        )
        self.amount = field(
            "Trade amount",
            units(trade.amount) if trade and trade.amount is not None else "",
            185,
            prefix=prefix,
            hint="Before fees",
            on_change=self.figure_changed,
        )
        self.price = field(
            "Unit price",
            units(trade.price) if trade else "",
            185,
            prefix=prefix,
            on_change=self.price_edited,
        )
        self.fee = field(
            "Transaction fee", str(trade.fee) if trade else "", 165, prefix=prefix, hint="0.00"
        )
        self.ref = text("Choose a listing and date to look up its price.", 12, MUTED)
        self.reference = None
        self.restore = ft.TextButton("Use date price", on_click=self.use_reference, visible=False)
        self.calculated = text("", 14, NAVY, True)
        self.note = field(
            "Note (optional)",
            trade.note if trade else "",
            multiline=True,
            min_lines=1,
            max_lines=4,
            max_length=1000,
            hint="Your own reference",
        )
        self.note.col = {"xs": 12, "md": 8}
        self.correct = ft.Checkbox(
            label="Apply this listing to all matching trades", value=False, visible=False
        )
        self.confirm_currency = ft.Checkbox(value=False, visible=False)
        self.err = text("", 14, RED, True)
        self.err.visible = False
        self.save_button = primary("Save transaction", self.save, ft.Icons.CHECK)
        title = f"Edit transaction #{trade.id}" if trade else "Add transaction"
        form = panel(
            [
                self.stock,
                self.choices,
                self.status,
                ft.Row(
                    [
                        self.side,
                        self.day,
                        ft.IconButton(
                            icon=ft.Icon(
                                ft.Icons.CALENDAR_MONTH,
                                semantics_label="Choose trade date",
                            ),
                            tooltip="Choose trade date",
                            on_click=self.calendar,
                            height=ENTRY_HEIGHT,
                            margin=ft.Margin.only(top=ENTRY_MARGIN),
                        ),
                        self.broker,
                    ],
                    wrap=True,
                    run_spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
                ft.Row(
                    [self.mode, self.qty, self.amount, self.price, self.fee],
                    wrap=True,
                    run_spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
                ft.Row([self.ref, self.restore], wrap=True),
                self.calculated,
                ft.ResponsiveRow([self.note]),
                self.correct,
                self.confirm_currency,
                self.err,
                ft.Row(
                    [self.save_button, secondary("Cancel", lambda e: app.go(app.tab))],
                    wrap=True,
                ),
            ]
        )
        form.width = 1020
        self.view = [
            heading(
                title,
                "Record a purchase or sale in the listing currency.",
                [secondary("Import trades", app.import_form, ft.Icons.UPLOAD_FILE)],
            ),
            ft.Container(form, alignment=ft.Alignment.TOP_CENTER),
        ]
        self.update_totals()

    def show(self):
        self.app.open_editor(self.view, "Transactions")
        if self.trade:
            self.page.run_task(self.validate)

    def active(self):
        return self.app.editor is self.view and self.app.store is self.store

    def currency(self):
        return (
            self.checked.currency if self.checked else self.trade.currency if self.trade else None
        )

    def update_totals(self):
        by_amount = self.mode.value == "amount"
        self.qty.visible, self.amount.visible = not by_amount, by_amount
        self.calculated.visible = by_amount or bool(self.qty.value and self.price.value)
        try:
            currency = self.currency()
            if not currency:
                raise PortfolioError("Select a listing to calculate the trade.")
            if by_amount:
                if not self.amount.value or not self.price.value:
                    raise PortfolioError(
                        "Enter the gross trade amount and unit price to calculate shares. Fees are entered separately."
                    )
                qty = quantity_from_amount(self.amount.value, self.price.value, currency)
                self.calculated.value = f"Calculated quantity · {units(qty)} shares"
            else:
                gross = number(self.qty.value, "Quantity") * number(
                    self.price.value, "Unit price", 8
                )
                self.calculated.value = (
                    f"Trade amount before fees · {dollars(gross, currency=currency)}"
                )
        except PortfolioError as exc:
            self.calculated.value = str(exc)

    def change_mode(self, e):
        self.update_totals()
        self.page.update()

    def figure_changed(self, e):
        self.update_totals()
        self.page.update()

    def price_edited(self, e):
        self.manual_price = bool(self.price.value.strip())
        self.restore.visible = self.manual_price and self.reference is not None
        self.figure_changed(e)

    def use_reference(self, e):
        if self.reference is not None:
            self.price.value, self.manual_price = units(self.reference), False
            self.restore.visible = False
            self.figure_changed(e)

    def reset_listing(self):
        if self.checked and not (self.trade and self.manual_price):
            self.price.value = ""
            self.manual_price = False
        self.generation += 1
        self.price_generation += 1
        self.checked = self.validation = self.price_task = self.reference = None
        self.status.visible = self.choices.visible = self.restore.visible = False
        self.err.visible = self.correct.visible = self.correct.value = False
        self.confirm_currency.visible = self.confirm_currency.value = False
        self.price.prefix = self.amount.prefix = self.fee.prefix = ""
        self.ref.value = "Choose a listing and date to look up its price."
        self.update_totals()

    async def focus_search(self, e):
        self.search_active = True

    async def leave_search(self, e):
        self.search_active = False
        epoch = self.generation
        # A click or keyboard focus on a suggestion stays inside the search list.
        await asyncio.sleep(0.12)
        if (
            self.active()
            and not self.search_active
            and self.generation == epoch
            and not self.validation
            and not self.err.visible
        ):
            self.choices.visible = False
            if not self.checked:
                self.status.visible = False
            self.page.update()

    def show_choices(self, choices):
        def pick(symbol):
            async def select(e):
                await self.select_symbol(symbol)

            return select

        self.choices.controls = [
            ft.TextButton(label, on_focus=self.focus_search, on_click=pick(code))
            for code, label in list(choices.items())[:10]
        ]
        self.choices.visible = bool(self.choices.controls)

    async def search_changed(self, e):
        self.search_active = True
        self.reset_listing()
        epoch = self.generation
        query = self.stock.value.strip()
        self.page.update()
        if not query or len(query) > 96:
            return
        await asyncio.sleep(0.28)
        if (
            not self.active()
            or not self.search_active
            or self.generation != epoch
            or self.validation
            or self.checked
        ):
            return
        cached = {
            q.symbol: q.listing.label
            for q in self.app.prices.values()
            if q.listing
            and (
                query.casefold() in q.symbol.casefold()
                or query.casefold() in q.listing.name.casefold()
            )
        }
        self.show_choices(cached)
        self.status.value, self.status.visible = "Searching listings…", True
        self.page.update()
        try:
            hits = await asyncio.to_thread(search_listings, query.upper(), suggest=True)
            results = {**hits, **{k: v for k, v in cached.items() if k not in hits}}
            message = (
                "" if results else "No matches. Try the exchange ticker or another company name."
            )
        except PortfolioError as exc:
            results, message = cached, str(exc)
        if (
            self.active()
            and self.search_active
            and self.generation == epoch
            and not self.validation
            and not self.checked
        ):
            self.show_choices(results)
            self.status.value, self.status.visible = message, bool(message)
            self.page.update()

    async def select_symbol(self, code):
        self.reset_listing()
        self.stock.value = code
        await self.validate()

    async def submit_symbol(self, e):
        await self.validate()

    async def validate(self):
        if self.checked and self.stock.value.strip().upper() == self.checked.symbol:
            return self.checked
        if self.checked:
            self.reset_listing()
        raw, epoch = self.stock.value.strip(), self.generation
        if self.validation:
            return await self.validation
        if not raw:
            self.fail("Enter a ticker or company name.")
            return None

        async def lookup():
            self.choices.visible = self.err.visible = False
            self.status.value, self.status.visible = "Checking the listing…", True
            self.page.update()
            try:
                quote = await asyncio.to_thread(resolve_symbol, raw)
                if not self.active() or self.generation != epoch:
                    return None
                self.checked, self.stock.value = quote, quote.symbol
                self.fee.prefix = self.price.prefix = self.amount.prefix = quote.currency + " "
                self.status.value = f"{quote.listing.label} · Latest {unit_price(quote.price, quote.currency)} as of {when(quote)}"
                if quote.listing.price_unit != quote.currency:
                    self.status.value += f". Enter prices in {quote.currency} (feed unit {quote.listing.price_unit})."
                if self.trade and (self.trade.symbol, self.trade.currency) != (
                    quote.symbol,
                    quote.currency,
                ):
                    count = sum(t.symbol == self.trade.symbol for t in self.app.book.trades)
                    self.correct.label = f"Correct all {count} trades for {self.trade.symbol} to {quote.symbol} ({quote.currency}). Keep the recorded numbers."
                    self.correct.visible = count > 1
                self.update_totals()
                self.start_price()
                return quote
            except PortfolioError as exc:
                if self.active() and self.generation == epoch:
                    self.status.visible = False
                    self.fail(str(exc))
                    if isinstance(exc, SymbolChoice):
                        self.show_choices(exc.choices)
                return None
            finally:
                if self.active() and self.generation == epoch:
                    self.validation = None
                    self.page.update()

        self.validation = asyncio.create_task(lookup())
        return await self.validation

    def start_price(self):
        self.price_generation += 1
        self.reference = None
        self.restore.visible = False
        self.price_task = None
        try:
            selected = trade_day(self.day.value)
        except PortfolioError as exc:
            self.ref.value = str(exc)
            return
        if not self.checked:
            self.ref.value = "Choose a listing to look up the price on this date."
        else:
            self.ref.value = "Looking up the price for the selected date…"
            self.price_task = asyncio.create_task(
                self.load_date_price(self.checked, selected, self.price_generation)
            )

    async def load_date_price(self, quote, selected, epoch):
        try:
            value, source = await asyncio.to_thread(
                trade_reference, quote, selected, self.app.histories.get(quote.symbol), today_sg()
            )
            message = f"{source} · {display_date(selected)} · {unit_price(value, quote.currency)}. You can enter your executed price."
        except PortfolioError as exc:
            value, message = None, f"{exc} You can enter your broker’s execution price."
        if self.active() and self.price_generation == epoch and self.checked is quote:
            self.reference, self.ref.value = value, message
            if value is not None and not self.manual_price:
                self.price.value = units(value)
            self.restore.visible = value is not None and self.manual_price
            self.update_totals()
            self.page.update()

    async def date_changed(self, e):
        self.price_generation += 1
        epoch = self.price_generation
        self.price.value = ""
        self.manual_price = self.restore.visible = False
        self.reference = self.price_task = None
        self.ref.value = "Checking the transaction date…"
        self.update_totals()
        self.page.update()
        await asyncio.sleep(0.25)
        if self.active() and epoch == self.price_generation:
            self.start_price()
            self.page.update()

    def calendar(self, e):
        today = today_sg()
        try:
            selected = trade_day(self.day.value)
        except PortfolioError:
            selected = today

        async def apply(day):
            if not self.active():
                return
            updated = display_date(day)
            if self.day.value != updated:
                self.day.value = updated
                await self.date_changed(None)

        trade_calendar(self.page, selected, today, apply)

    def fail(self, message):
        self.err.value, self.err.visible = message, True
        self.page.update()

    async def save(self, e):
        if self.committing:
            return
        self.committing = self.save_button.disabled = True
        self.page.update()
        try:
            reviewed = self.checked is not None
            quote = await self.validate()
            if not quote or not self.active():
                return
            if not self.price.value and self.price_task:
                await self.price_task
            if not self.active() or self.checked is not quote:
                return
            changed = (
                self.trade and self.trade.currency != quote.currency and not self.correct.value
            )
            if (
                changed
                or (not reviewed and quote.listing.price_unit != quote.currency)
                or self.confirm_currency.visible
            ) and not self.confirm_currency.value:
                self.confirm_currency.label = (
                    f"My entered unit price and fee are in {quote.currency}."
                )
                self.confirm_currency.visible = True
                raise PortfolioError(
                    "Confirm the currency on your trade confirmation before saving."
                )
            row = dict(
                symbol=quote.symbol,
                currency=quote.currency,
                date=self.day.value,
                side=self.side.value,
                price=self.price.value,
                broker=self.broker.value,
                fee=self.fee.value,
                note=self.note.value,
            )
            if self.mode.value == "amount":
                row["amount"] = self.amount.value
            else:
                row["quantity"] = self.qty.value
            self.store.commit(
                "edit" if self.trade else "add",
                self.revision,
                row,
                self.trade.id if self.trade else None,
                verified_quote=quote,
                correct_all=self.correct.visible and self.correct.value,
            )
            self.app.symbol, self.app.trade_page = quote.symbol, 0
            self.app.go(self.app.tab)
            self.app.notice("Transaction updated." if self.trade else "Transaction added.")
        except PortfolioError as exc:
            if self.active():
                self.fail(str(exc))
        finally:
            self.committing = self.save_button.disabled = False
            if self.active():
                self.page.update()
