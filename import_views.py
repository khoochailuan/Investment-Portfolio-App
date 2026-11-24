# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Choose a local statement, map its fields, review rows and commit once."""

import asyncio
from dataclasses import replace
from pathlib import Path

import flet as ft

from frost_theme import frost_dialog
from import_reconciliation import (
    FEES,
    NET_CASH,
    TradeTotals,
    selected_trade_totals,
    statement_difference,
)
from name_picker import brokerage_picker

from portfolio import (
    DATE_HINT, ZERO, PortfolioError, currency_prefix, display_date, dollars, minor_unit, parse_day, units,
)
from trade_import import ImportProfile, LABELS, commit_rows, drafts, guess, read_source, review
from widgets import (
    MUTED,
    NAVY,
    POSITIVE,
    RED,
    choose,
    field,
    grid,
    heading,
    pager,
    panel,
    primary,
    secondary,
    text,
)


def total_text(value, currency=None):
    if currency is None:
        return units(value)
    places = -minor_unit(currency).as_tuple().exponent
    return f"{'−' if value < 0 else ''}{currency_prefix(currency)}{value.copy_abs():,.{places}f}"


class ImportEditor:
    def __init__(self, app):
        self.app, self.page, self.store = app, app.page, app.store
        self.revision, self.book = app.snap.revision, app.book
        self.body = ft.Column(spacing=15)
        self.status = text("", 14, NAVY, True)
        self.status.visible = False
        self.view = [
            heading(
                "Import trades", "Choose a CSV export or a searchable PDF brokerage statement."
            ),
            self.body,
        ]
        self.file_button = secondary("Choose CSV or PDF", None, ft.Icons.UPLOAD_FILE)
        self.file_button.action = ft.PickFiles(
            app.import_picker,
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["csv", "pdf"],
            with_data=True,
        )
        self.filename, self.source = "", None
        self.cache, self.rows = {}, []
        self.profiles = []
        self.reported = {}
        self.group_labels = {}
        self.page_index = 0
        self.busy = False

    def show(self):
        self.app.import_picker.on_result = self.picked
        self.start()
        self.app.open_editor(self.view, "Transactions")

    def active(self):
        return self.app.editor is self.view and self.app.store is self.store

    def cancel(self, e):
        self.app.go("Transactions")

    def start(self):
        self.body.controls = [
            panel(
                [
                    self.file_button,
                    text(
                        "Files are read locally. Ticker and company lookups go to Yahoo Finance.",
                        13,
                        MUTED,
                    ),
                    text("The ZIP includes a sample in assets/trades_template.csv.", 13, MUTED),
                    self.status,
                    secondary("Cancel", self.cancel),
                ]
            )
        ]

    def message(self, msg, error=False):
        self.status.value, self.status.visible, self.status.color = (
            msg,
            bool(msg),
            RED if error else NAVY,
        )
        self.page.update()

    async def picked(self, event):
        if not self.active() or self.busy or not event.files:
            return
        picked = event.files[0]
        self.busy = self.file_button.disabled = True
        self.message("Reading the file…")
        try:
            payload = picked.bytes
            if payload is None and picked.path:
                payload = await asyncio.to_thread(Path(picked.path).read_bytes)
            if not payload:
                raise PortfolioError("The file was empty or could not be read. Choose it again.")
            source = await asyncio.to_thread(read_source, picked.name, payload)
            if not self.active():
                return
            self.filename, self.source = picked.name, source
            self.cache, self.rows = {}, []
            self.reported, self.group_labels = {}, {}
            self.mapped = guess(source.headers)
            self.profiles, profile_error = [], ""
            if self.is_csv:
                try:
                    self.profiles = self.store.import_profiles()
                except PortfolioError as exc:
                    profile_error = str(exc)
            self.broker = brokerage_picker(
                self.app, source.broker, label="Default brokerage", none_label="No default",
                extra=("Syfe", "DBS Vickers", *(p.broker for p in self.profiles)),
            )
            self.order = choose(
                "Numeric date order",
                [("DMY", "Day / month / year"), ("MDY", "Month / day / year")],
                "DMY",
                width=232,
            )
            self.row_order = choose(
                "Same-day row order",
                [("old", "Oldest first"), ("new", "Newest first")],
                "old",
                width=175,
            )
            self.columns()
            if profile_error:
                self.message(profile_error, True)
            elif self.is_csv and source.broker:
                self.use_mapping()
        except (PortfolioError, OSError) as exc:
            if self.active():
                self.message(str(exc), True)
        finally:
            self.busy = self.file_button.disabled = False
            if self.active():
                self.page.update()

    @property
    def is_csv(self):
        return Path(self.filename).suffix.lower() == ".csv"

    def column_choices(self):
        return {key: int(field.value) for key, field in self.fields.items() if field.value != "-"}

    def mapping_broker(self):
        broker = self.broker.value
        if not broker:
            raise PortfolioError("Choose a brokerage for this CSV mapping.")
        return broker

    def use_mapping(self, event=None):
        if event and self.busy:
            return
        try:
            broker = self.mapping_broker()
            for profile in self.profiles:
                if profile.broker.casefold() != broker.casefold():
                    continue
                mapping = profile.remap(self.source.headers, self.source.signed)
                if mapping is not None:
                    for key, field in self.fields.items():
                        field.value = str(mapping[key]) if key in mapping else "-"
                    self.order.value, self.row_order.value = profile.date_order, profile.row_order
                    self.message(f"Loaded the saved mapping for {profile.broker}.")
                    return
            if event:
                self.message(f"No saved mapping for {broker} matches these CSV headers.", True)
        except PortfolioError as exc:
            self.message(str(exc), True)

    def save_mapping(self, event):
        if self.busy:
            return
        try:
            profile = ImportProfile.from_record(dict(
                broker=self.mapping_broker(),
                headers=self.source.headers,
                columns=self.column_choices(),
                date_order=self.order.value,
                row_order=self.row_order.value,
                signed=self.source.signed,
            ))
            self.profiles = self.store.save_import_profile(profile)
            self.use_mapping_button.disabled = False
            self.message(f"Saved this CSV mapping for {profile.broker}.")
        except PortfolioError as exc:
            self.message(str(exc), True)

    def columns(self, e=None):
        if self.busy and e:
            return
        self.status.visible = False
        self.fields = {}
        options = [("-", "Unassigned")] + [
            (str(i), f"{i + 1}. {name.strip() or 'Unnamed column'}")
            for i, name in enumerate(self.source.headers)
        ]
        for key, label in LABELS.items():
            self.fields[key] = choose(
                label, options, str(self.mapped[key]) if key in self.mapped else "-", width=230
            )
        mapping_actions = []
        if self.is_csv:
            self.use_mapping_button = secondary("Use saved mapping", self.use_mapping)
            self.use_mapping_button.disabled = not self.profiles
            mapping_actions.append(ft.Row(
                [self.use_mapping_button, secondary("Save CSV mapping", self.save_mapping)],
                wrap=True, run_spacing=8,
            ))
        self.body.controls = [
            panel(
                [
                    ft.Row([text(self.filename, 20, bold=True), self.file_button], wrap=True),
                    text(f"{len(self.source.rows)} source rows. {self.source.info}", 14, MUTED),
                    ft.Row([self.broker, self.order, self.row_order], wrap=True),
                    ft.ExpansionTile(
                        title=text("Column mapping", 16, NAVY, True),
                        subtitle=text(
                            "Check which source column supplies each trade field.", 13, MUTED
                        ),
                        expanded=not {"date", "symbol", "price"}.issubset(self.mapped),
                        controls=[
                            ft.Row(list(self.fields.values()), wrap=True, spacing=12, run_spacing=8)
                        ],
                    ),
                    *mapping_actions,
                    self.status,
                    ft.Row(
                        [
                            primary("Review trades", self.prepare, ft.Icons.FACT_CHECK_OUTLINED),
                            secondary("Cancel", self.cancel),
                        ],
                        wrap=True,
                    ),
                ]
            )
        ]
        self.page.update()

    async def prepare(self, e):
        if self.busy:
            return
        self.busy = self.file_button.disabled = True
        self.message("Checking trade fields and stock listings…")
        try:
            self.mapped = self.column_choices()
            candidates = drafts(self.source, self.mapped, self.broker.value)
            self.cache = {k: v for k, v in self.cache.items() if not isinstance(v, PortfolioError)}
            await asyncio.to_thread(
                review, candidates, self.source, self.order.value, self.book, self.cache
            )
            if self.active():
                self.rows, self.page_index = candidates, 0
                self.preview()
        except PortfolioError as exc:
            if self.active():
                self.message(str(exc), True)
        finally:
            self.busy = self.file_button.disabled = False
            if self.active():
                self.page.update()

    def preview(self):
        self.status.visible = False
        size = 8

        def change_page(value):
            self.page_index = value
            self.preview()

        def tick(event, d):
            d.selected = bool(event.control.value)
            self.update_count()
            self.page.update()

        lines = []
        for d in self.rows[self.page_index * size : (self.page_index + 1) * size]:
            tr = d.trade
            state = (
                "Already imported"
                if d.imported
                else d.error
                if d.error
                else "Possible duplicate"
                if d.duplicate
                else "Ready"
            )
            pick = ft.Checkbox(
                value=d.selected,
                disabled=tr is None or d.imported,
                label=f"Row {d.line}",
                on_change=lambda e, row=d: tick(e, row),
            )
            lines.append(
                [
                    pick,
                    display_date(tr.day) if tr else d.values.get("date", "—"),
                    tr.symbol if tr else d.values.get("symbol", "—"),
                    tr.side if tr else d.values.get("side", "—"),
                    units(tr.qty) if tr else d.values.get("quantity", "—"),
                    units(tr.price) if tr else d.values.get("price", "—"),
                    dollars(tr.fee, currency=tr.currency) if tr else d.values.get("fee", "—"),
                    tr.currency if tr else d.values.get("currency", "—"),
                    tr.broker if tr else d.values.get("broker", "—"),
                    ft.Container(text(state, 12, RED if d.error else MUTED), width=153),
                    (
                        ft.TextButton("Edit row", on_click=lambda e, row=d: self.edit(row))
                        if not d.imported
                        else "—"
                    ),
                ]
            )
        self.count = text("", 14, NAVY, True)
        self.reconciliation_note = text("", 13, MUTED)
        self.reconciliation = ft.Column(
            [
                ft.Row(
                    [
                        text("Statement reconciliation", 17, bold=True),
                        secondary("Compare statement totals", self.compare_statement, ft.Icons.FACT_CHECK_OUTLINED),
                    ],
                    wrap=True, run_spacing=6,
                ),
                self.reconciliation_note,
            ],
            spacing=4,
        )
        self.save_button = primary("Import selected trades", self.save, ft.Icons.CHECK)
        self.body.controls = [
            panel(
                [
                    text(self.filename, 20, bold=True),
                    self.count,
                    text(
                        "Check the execution prices, fees and trade count against your statement. Blank fees count as zero. Select a possible duplicate only if it is a separate execution.",
                        13,
                        MUTED,
                    ),
                    self.reconciliation,
                    grid(
                        [
                            "Include",
                            "Date",
                            "Symbol",
                            "Side",
                            "Quantity",
                            "Unit price",
                            "Fee",
                            "CCY",
                            "Brokerage",
                            "Review",
                            "",
                        ],
                        lines,
                        1060,
                        self.app.table_width,
                        numeric=(4, 5, 6),
                    ),
                    *(
                        [pager(self.page_index, len(self.rows), size, change_page)]
                        if len(self.rows) > size
                        else []
                    ),
                    self.status,
                    ft.Row(
                        [
                            self.save_button,
                            secondary("Back to columns", self.columns),
                            secondary("Cancel", self.cancel),
                        ],
                        wrap=True,
                    ),
                ]
            )
        ]
        self.update_count()
        self.page.update()

    def update_count(self):
        count = sum(r.selected for r in self.rows)
        self.count.value = (
            f"{count} of {len(self.rows)} selected · {sum(bool(r.error) for r in self.rows)} need correction · "
            f"{sum(r.duplicate for r in self.rows)} possible duplicates · {sum(r.imported for r in self.rows)} already imported"
        )
        self.save_button.disabled = not count
        self.totals = selected_trade_totals(self.rows)
        for key in self.reported:
            self.totals.setdefault(key, TradeTotals(self.group_labels[key], key[1]))
        self.reconciliation.visible = bool(self.totals)
        self.update_reconciliation_note()

    def difference(self, group, item, raw):
        currency = None if item[1] else group.currency
        actual = group.amounts.get(item, ZERO)
        return statement_difference(raw, actual, currency=currency, signed=item == NET_CASH)

    def update_reconciliation_note(self):
        matches = differences = invalid = 0
        for key, inputs in self.reported.items():
            for item, raw in inputs.items():
                try:
                    variance = self.difference(self.totals[key], item, raw)
                except PortfolioError:
                    invalid += 1
                    continue
                matches += variance == 0
                differences += variance is not None and variance != 0
        self.reconciliation_note.value = (
            f"{matches} matched · {differences} differ" + (f" · {invalid} invalid" if invalid else "")
            if self.reported else "Check the selected trades against your statement by brokerage and currency."
        )
        self.reconciliation_note.color = RED if differences or invalid else POSITIVE if matches else MUTED

    def compare_statement(self, event):
        if self.busy or not self.totals:
            return
        keys = sorted(self.totals)
        groups = {str(i): key for i, key in enumerate(keys)}
        width = max(195, min(590, int(self.page.width or 1200) - 104))
        body = ft.Column(spacing=11)
        scoped = choose(
            "Brokerage and currency",
            [(i, f"{self.totals[key].broker} · {key[1]}") for i, key in groups.items()],
            "0", width=min(245, width),
        )
        scoped.width = min(scoped.width, width)

        def show_group(event=None):
            key = groups[scoped.value]
            group = self.totals[key]
            old_inputs = self.reported.get(key, {})
            symbols = sorted(set(group.symbols) | {sym for _, sym in old_inputs if sym})

            def check_row(item, name, label):
                actual = group.amounts.get(item, ZERO)
                currency = None if item[1] else group.currency
                entry = field(label, self.reported.get(key, {}).get(item, ""), width=min(162, width))
                result = text("", 12, MUTED)

                def compare():
                    try:
                        diff = self.difference(group, item, entry.value or "")
                        if diff is None:
                            result.value, result.color = "—", MUTED
                        elif diff == 0:
                            result.value, result.color = "Matched", POSITIVE
                        else:
                            direction = "above" if diff > 0 else "below"
                            result.value = f"{total_text(diff.copy_abs(), currency)} {direction} statement"
                            result.color = RED
                    except PortfolioError as exc:
                        result.value, result.color = str(exc), RED

                def changed(event):
                    self.group_labels[key] = group.broker
                    inputs = self.reported.setdefault(key, {})
                    raw = (entry.value or "").strip()
                    if raw:
                        inputs[item] = raw
                    else:
                        inputs.pop(item, None)
                        if not inputs:
                            self.reported.pop(key)
                    compare()
                    self.update_reconciliation_note()
                    self.page.update()

                entry.on_change = changed
                compare()
                figures = ft.Column(
                    [text(name, 14, bold=True), text(f"Selected {total_text(actual, currency)}", 13, MUTED)],
                    spacing=2,
                    expand=width >= 495,
                )
                if width < 495:
                    return ft.Column([figures, ft.Row([entry, result], wrap=True, run_spacing=3)], spacing=1)
                return ft.Row(
                    [figures, entry, ft.Container(result, width=150)],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )

            def show_listing(event=None):
                symbol = listing.value
                share_rows.controls = [
                    check_row(("BUY", symbol), "Shares bought", "Statement bought"),
                    check_row(("SELL", symbol), "Shares sold", "Statement sold"),
                ]
                if event:
                    self.page.update()

            controls = [
                check_row(FEES, "Total fees", "Statement fees"),
                check_row(NET_CASH, "Net trade cash", "Statement net cash"),
            ]
            if symbols:
                share_rows = ft.Column(spacing=9)
                listing = choose(
                    "Listing for share totals", [(s, s) for s in symbols], symbols[0],
                    show_listing, width=min(215, width),
                )
                show_listing()
                controls += [listing, share_rows]
            body.controls = controls
            if event:
                self.page.update()

        scoped.on_select = show_group
        show_group()
        self.page.show_dialog(frost_dialog(
            title=text("Compare statement totals", 21, bold=True),
            modal=True, scrollable=True,
            content=ft.Container(ft.Column(
                [
                    text(
                        "Use statement totals for the checked executions in their trading currency. Net cash is negative when buy payments exceed sale proceeds, after fees.",
                        13, MUTED,
                    ),
                    scoped,
                    body,
                ],
                spacing=10, tight=True,
            ), width=width),
            actions=[primary("Done", lambda e: self.page.pop_dialog())],
        ))

    def edit(self, d):
        if self.busy:
            return
        fields = {
            key: field(label, d.values.get(key, ""), 225)
            for key, label in LABELS.items() if key != "broker"
        }
        fields["broker"] = brokerage_picker(
            self.app, d.trade.broker if d.trade else d.values.get("broker", ""),
            extra=[row.trade.broker for row in self.rows if row.trade],
        )
        fields["date"].hint_text = DATE_HINT
        if d.trade:
            fields["date"].value = display_date(d.trade.day)
        if self.source.signed:
            fields["fee"].hint_text = "IBKR commission debit, e.g. -1"
        feedback = text(d.error, 13, RED)
        check = primary("Apply changes", None)

        def assign(sym):
            def handler(e):
                fields["symbol"].value = sym
                self.page.update()

            return handler

        async def apply(e):
            if self.busy:
                return
            self.busy = check.disabled = True
            feedback.value = "Checking this row…"
            self.page.update()
            try:
                states = {r.key: (r.selected, r.duplicate, bool(r.error)) for r in self.rows}
                try:
                    values = {k: f.value.strip() for k, f in fields.items()}
                    values["date"] = parse_day(values["date"]).isoformat()
                except PortfolioError as exc:
                    feedback.value = str(exc)
                    return
                # Work on copies until the user applies a valid correction.
                trial = [replace(r, values=values if r is d else r.values) for r in self.rows]
                q = values.get("symbol", "").upper()
                if isinstance(self.cache.get(q), PortfolioError):
                    self.cache.pop(q)
                await asyncio.to_thread(
                    review, trial, self.source, self.order.value, self.book, self.cache
                )
                if not self.active() or not dlg.open:
                    return
                edited = next(r for r in trial if r.key == d.key)
                if edited.error:
                    feedback.value = edited.error
                    suggestions.controls = [
                        ft.TextButton(label, on_click=assign(code))
                        for code, label in edited.choices.items()
                    ]
                    return
                for row in trial:
                    was_selected, was_duplicate, was_error = states[row.key]
                    if (
                        row.key != d.key
                        and row.trade
                        and not was_error
                        and row.duplicate == was_duplicate
                    ):
                        row.selected = was_selected
                self.rows = trial
                self.page.pop_dialog()
                self.preview()
            finally:
                self.busy = check.disabled = False
                if self.active():
                    self.page.update()

        check.on_click = apply
        suggestions = ft.Column(
            [ft.TextButton(label, on_click=assign(code)) for code, label in d.choices.items()]
        )
        dlg = frost_dialog(
            title=text(f"Review source row {d.line}", 20, bold=True),
            scrollable=True,
            content=ft.Container(
                ft.Column(
                    [
                        ft.Row(list(fields.values()), wrap=True, spacing=12, run_spacing=10),
                        suggestions,
                        feedback,
                    ],
                    tight=True,
                ),
                width=515,
            ),
            actions=[check, secondary("Cancel", lambda e: self.page.pop_dialog())],
        )
        self.page.show_dialog(dlg)

    async def save(self, e):
        if self.busy:
            return
        chosen = commit_rows(self.rows, self.row_order.value == "new")
        if not chosen:
            return
        self.busy = self.save_button.disabled = True
        self.message("Saving the selected trades…")
        try:
            # The revision check and full replay happen under the same file lock.
            self.store.commit("import", self.revision, chosen)
            self.app.trade_page = 0
            self.app.go("Transactions")
            self.app.notice(f"Imported {len(chosen)} trade{'s' if len(chosen) != 1 else ''}.")
        except PortfolioError as exc:
            self.message(str(exc), True)
        finally:
            self.busy = False
            if self.active():
                self.update_count()
                self.page.update()
