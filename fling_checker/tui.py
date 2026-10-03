"""Interactive Textual TUI: config -> run -> results."""

from __future__ import annotations

import threading
from pathlib import Path

from rich.text import Text

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    RichLog,
    Select,
)

from fling_checker.cache import load_cache, save_cache
from fling_checker.config import CURRENCY_MAP, Config, ensure_session
from fling_checker.excel import write_excel
from fling_checker.pipeline import PipelineResult, run_pipeline
from fling_checker.processor import _process_single_new_trainer
from fling_checker.reporter import Reporter

DECK_STYLES = {
    "Verified": "black on #C6EFCE",
    "Playable": "black on #FFEB9C",
    "Unsupported": "white on #E3344B",
}
SALE_STYLE = "black on #E2EFDA"
DECK_FILTER_OPTIONS = [
    ("All", "All"),
    ("Deck OK (Verified+Playable)", "Deck OK"),
    ("Verified", "Verified"),
    ("Playable", "Playable"),
    ("Unsupported", "Unsupported"),
    ("Unknown", "Unknown"),
]
DECK_OK = {"Verified", "Playable"}


# ─── Reporter bridging pipeline events to the UI thread ──────────


class PhaseChanged(Message):
    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name


class ProgressUpdated(Message):
    def __init__(self, desc: str, done: int, total: int | None) -> None:
        super().__init__()
        self.desc = desc
        self.done = done
        self.total = total


class LogLine(Message):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class ItemDone(Message):
    def __init__(self, result: dict) -> None:
        super().__init__()
        self.result = result


class PipelineFinished(Message):
    def __init__(self, result, error: str | None = None) -> None:
        super().__init__()
        self.result = result
        self.error = error


class TuiReporter(Reporter):
    def __init__(self, post, cancel_event: threading.Event) -> None:
        self._post = post
        self._cancel_event = cancel_event

    def phase(self, name: str) -> None:
        self._post(PhaseChanged(name))

    def progress(self, desc: str, done: int, total: int | None) -> None:
        self._post(ProgressUpdated(desc, done, total))

    def log(self, msg: str) -> None:
        self._post(LogLine(msg))

    def item(self, result: dict) -> None:
        self._post(ItemDone(result))

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()


# ─── Row formatting helpers (shared with tests) ──────────────────

RESULT_COLUMNS = [
    ("Game", "game_name"),
    ("Trainer Date", None),
    ("Opt", None),
    ("Steam App ID", "steam_appid"),
    ("Deck", "deck_compat"),
    ("Price", None),
    ("Disc%", "discount_pct"),
    ("Sale", None),
    ("Rating%", "positive_pct"),
    ("Reviews", "total_reviews"),
    ("Genres", "genres"),
]


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("-inf")


def sort_key_for_column(col: int):
    """Return a key function extracting the raw (sortable) value for a column."""
    if col == 1:
        return lambda r: (r.get("trainer_date") or "") + (r.get("trainer_date_str") or "")
    if col == 2:
        return lambda r: r.get("options_count", r.get("num_options", 0)) or 0
    if col == 3:
        return lambda r: r.get("steam_appid") if r.get("steam_appid") is not None else float("-inf")
    if col == 4:
        order = {"Verified": 0, "Playable": 1, "Unsupported": 2, "Unknown": 3}
        return lambda r: (order.get(r.get("deck_compat", "Unknown"), 3), r.get("game_name", ""))
    if col == 5:
        return lambda r: r.get("price_idr") if isinstance(r.get("price_idr"), (int, float)) else float("-inf")
    if col == 6:
        return lambda r: r.get("discount_pct", 0) or 0
    if col == 7:
        return lambda r: (1 if r.get("on_sale") else 0, r.get("discount_pct", 0) or 0)
    if col == 8:
        return lambda r: _num(r.get("positive_pct", 0))
    if col == 9:
        return lambda r: _num(r.get("total_reviews", 0))
    return lambda r: str(r.get(RESULT_COLUMNS[col][1] or "", "") or "").lower()


def sort_results(results: list[dict], col: int, reverse: bool) -> list[dict]:
    """Return a new list sorted by the given column index."""
    return sorted(results, key=sort_key_for_column(col), reverse=reverse)


def cached_rows(cache: dict) -> list[dict]:
    """Rows for browsing a saved cache without scanning, grouped by Deck status.

    Column 4 is the Deck column, so this mirrors the workbook's Verified-first
    ordering; entries missing ``deck_compat`` sort as Unknown.
    """
    return sort_results(list(cache.values()), 4, False)


def cache_price_as_of(rows: list[dict]) -> str:
    """Newest price-refresh date among rows as YYYY-MM-DD, or "" if unknown."""
    dates = [str(r.get("_price_updated_at") or "")[:10] for r in rows]
    return max((d for d in dates if d), default="")


def trainer_date_display(r: dict) -> str:
    return r.get("trainer_date_str", "") or r.get("trainer_date", "")


def options_display(r: dict) -> str:
    return str(r.get("options_count", r.get("num_options", 0)) or 0)


def price_display(r: dict) -> str:
    val = r.get("price_idr")
    if isinstance(val, (int, float)):
        return f"{val:,.0f}"
    return str(r.get("price", "N/A"))


def sale_display(r: dict) -> str:
    return "YES" if r.get("on_sale") else "NO"


def deck_cell(r: dict):
    deck = r.get("deck_compat", "Unknown")
    style = DECK_STYLES.get(deck)
    if style:
        return Text(deck, style=style)
    return deck


def sale_cells(r: dict):
    on_sale = bool(r.get("on_sale")) or (r.get("discount_pct", 0) or 0) > 0
    disc = str(r.get("discount_pct", 0))
    sale = sale_display(r)
    if on_sale:
        return Text(disc, style=SALE_STYLE), Text(sale, style=SALE_STYLE)
    return disc, sale


def format_row(r: dict) -> tuple:
    disc, sale = sale_cells(r)
    return (
        r.get("game_name", ""),
        trainer_date_display(r),
        options_display(r),
        str(r.get("steam_appid") or ""),
        deck_cell(r),
        price_display(r),
        disc,
        sale,
        str(r.get("positive_pct", 0)),
        str(r.get("total_reviews", 0)),
        r.get("genres", "") or "",
    )


def filter_results(results: list[dict], deck: str, query: str, on_sale_only: bool) -> list[dict]:
    q = query.strip().lower()
    out = []
    for r in results:
        status = r.get("deck_compat", "Unknown")
        if deck == "Deck OK":
            if status not in DECK_OK:
                continue
        elif deck != "All" and status != deck:
            continue
        if on_sale_only and not r.get("on_sale"):
            continue
        if q and q not in f"{r.get('game_name', '')} {r.get('steam_name', '')}".lower():
            continue
        out.append(r)
    return out


DETAIL_FIELDS = [
    ("Game Name", "game_name"),
    ("Trainer Date", None),
    ("Options", None),
    ("Game Version", "game_version"),
    ("Last Updated", "last_updated"),
    ("Version Info", "version_info"),
    ("Steam Name", "steam_name"),
    ("Steam App ID", "steam_appid"),
    ("Deck Compatibility", "deck_compat"),
    ("Price", None),
    ("Original Price", "original_price_idr"),
    ("Discount %", "discount_pct"),
    ("On Sale", None),
    ("Rating", "review_desc"),
    ("Rating %", "positive_pct"),
    ("Total Reviews", "total_reviews"),
    ("Genres", "genres"),
    ("Trainer URL", "trainer_url"),
    ("Steam URL", "steam_url"),
]


def detail_lines(r: dict) -> list[str]:
    lines = []
    for label, key in DETAIL_FIELDS:
        if key is None:
            if label == "Trainer Date":
                value = trainer_date_display(r)
            elif label == "Options":
                value = options_display(r)
            elif label == "Price":
                value = price_display(r)
            elif label == "On Sale":
                value = sale_display(r)
            else:
                value = ""
        else:
            value = r.get(key)
            if value is None:
                value = "—"
        lines.append(f"[b]{label}:[/b] {value}")
    return lines


# ─── Screens ─────────────────────────────────────────────────────


class DetailModal(ModalScreen):
    def __init__(self, result: dict) -> None:
        super().__init__()
        self.result = result

    def compose(self) -> ComposeResult:
        with Vertical(id="detail-box"):
            yield Label(f"Detail — {self.result.get('game_name', '')}", id="detail-title")
            yield RichLog(id="detail-log", highlight=True, markup=True)
            with Horizontal(id="detail-actions"):
                yield Button("Close", id="detail-close", variant="primary")

    def on_mount(self) -> None:
        log = self.query_one("#detail-log", RichLog)
        for line in detail_lines(self.result):
            log.write(line)

    @on(Button.Pressed, "#detail-close")
    def close_modal(self) -> None:
        self.dismiss(None)


class OverridesModal(ModalScreen[bool]):
    """View/add/edit/delete AppID overrides; saves on confirm."""

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="overrides-box"):
            yield Label("AppID Overrides (key = game name or trainer slug)", id="overrides-title")
            yield DataTable(id="overrides-table", cursor_type="row")
            with Horizontal(id="overrides-form"):
                yield Input(placeholder="key, e.g. elden-ring", id="ov-key")
                yield Input(placeholder="appid, e.g. 1245620", id="ov-appid", restrict=r"[0-9]*")
                yield Button("Add/Update", id="ov-add", variant="success")
                yield Button("Delete", id="ov-delete", variant="error")
            with Horizontal(id="overrides-actions"):
                yield Button("Save & Close", id="ov-save", variant="primary")
                yield Button("Discard", id="ov-discard")
            yield Label("", id="ov-status")

    def on_mount(self) -> None:
        table = self.query_one("#overrides-table", DataTable)
        table.add_column("Key", width=40)
        table.add_column("AppID", width=12)
        self._reload_table()

    def _reload_table(self) -> None:
        table = self.query_one("#overrides-table", DataTable)
        table.clear()
        for key in sorted(self.config.overrides, key=str):
            table.add_row(str(key), str(self.config.overrides[key]), key=str(key))

    @on(Button.Pressed, "#ov-add")
    def add_override(self) -> None:
        key = self.query_one("#ov-key", Input).value.strip()
        appid = self.query_one("#ov-appid", Input).value.strip()
        status = self.query_one("#ov-status", Label)
        if not key or not appid:
            status.update("Key and AppID are both required.")
            return
        try:
            self.config.overrides[key] = int(appid)
        except ValueError:
            status.update("AppID must be a number.")
            return
        self._reload_table()
        status.update(f"Staged: {key} -> {appid} (not saved yet)")

    def _selected_override_key(self) -> str | None:
        table = self.query_one("#overrides-table", DataTable)
        if table.row_count == 0 or not table.is_valid_coordinate(table.cursor_coordinate):
            return None
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        except Exception:  # noqa: BLE001
            return None
        return str(row_key.value)

    @on(Button.Pressed, "#ov-delete")
    def delete_override(self) -> None:
        status = self.query_one("#ov-status", Label)
        key = self._selected_override_key()
        if key is None:
            status.update("Select a row first.")
            return
        self.config.overrides.pop(key, None)
        self._reload_table()
        status.update(f"Removed (staged): {key}")

    @on(Button.Pressed, "#ov-save")
    def save_and_close(self) -> None:
        try:
            self.config.save_overrides()
        except OSError as exc:
            self.query_one("#ov-status", Label).update(f"Save failed: {exc}")
            return
        self.config.reload_overrides()
        self.dismiss(True)

    @on(Button.Pressed, "#ov-discard")
    def discard_and_close(self) -> None:
        self.config.reload_overrides()
        self.dismiss(False)


class ConfigScreen(Screen):
    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="config-box"):
            yield Label("FLiNG + Steam Deck Checker", id="config-title")
            yield Label("Country")
            yield Select(
                [(code, code) for code in sorted(CURRENCY_MAP)],
                value=self.config.country_code,
                id="cfg-country",
                allow_blank=False,
            )
            yield Label("Min trainer year")
            yield Input(str(self.config.min_year), id="cfg-year")
            yield Label("Workers")
            yield Input(str(self.config.max_workers), id="cfg-workers")
            yield Label("Delay (s)")
            yield Input(str(self.config.request_delay), id="cfg-delay")
            yield Label("Retries")
            yield Input(str(self.config.max_retries), id="cfg-retries")
            yield Label("Price TTL (hours)")
            yield Input(str(self.config.cache_price_ttl_hours), id="cfg-ttl")
            yield Label("Output dir (empty = current dir)")
            yield Input(str(self.config.output_dir), id="cfg-outdir")
            with Horizontal(id="config-checks"):
                yield Checkbox("Ignore cache (--no-cache)", value=self.config.no_cache, id="cfg-nocache")
                yield Checkbox("Verbose", value=self.config.verbose, id="cfg-verbose")
            yield Label("", id="cfg-error")
            with Horizontal(id="config-actions"):
                yield Button("Start Scan", id="cfg-start", variant="primary")
                yield Button("Browse Cache", id="cfg-cache")
                yield Button("Quit", id="cfg-quit")
        yield Footer()

    def _read_config(self) -> Config | None:
        err = self.query_one("#cfg-error", Label)

        def _int(widget_id: str, label: str) -> int | None:
            try:
                return int(self.query_one(widget_id, Input).value.strip())
            except ValueError:
                err.update(f"{label} must be an integer.")
                return None

        def _float(widget_id: str, label: str) -> float | None:
            try:
                return float(self.query_one(widget_id, Input).value.strip())
            except ValueError:
                err.update(f"{label} must be a number.")
                return None

        year = _int("#cfg-year", "Year")
        workers = _int("#cfg-workers", "Workers")
        retries = _int("#cfg-retries", "Retries")
        ttl = _int("#cfg-ttl", "TTL")
        delay = _float("#cfg-delay", "Delay")
        if None in (year, workers, retries, ttl, delay):
            return None
        if workers <= 0 or retries <= 0:
            err.update("Workers and retries must be greater than 0.")
            return None

        country = self.query_one("#cfg-country", Select).value
        if country not in CURRENCY_MAP:
            err.update(f"Unknown country code: {country}")
            return None
        outdir_raw = self.query_one("#cfg-outdir", Input).value.strip()
        self.config.min_year = year
        self.config.country_code = country
        self.config.max_workers = workers
        self.config.request_delay = delay
        self.config.max_retries = retries
        self.config.cache_price_ttl_hours = ttl
        self.config.no_cache = self.query_one("#cfg-nocache", Checkbox).value
        self.config.verbose = self.query_one("#cfg-verbose", Checkbox).value
        if outdir_raw:
            self.config.output_dir = Path(outdir_raw)
            self.config.output_dir.mkdir(parents=True, exist_ok=True)
            self.config.cache_path = self.config.output_dir / "fling_steam_cache.json"
            self.config.overrides_path = self.config.output_dir / "fling_steam_overrides.json"
            self.config.reload_overrides()
        self.config.currency = CURRENCY_MAP[country]
        return self.config

    @on(Button.Pressed, "#cfg-start")
    def start_scan(self) -> None:
        config = self._read_config()
        if config is None:
            return
        self.app.push_screen(RunScreen(config))

    @on(Button.Pressed, "#cfg-cache")
    def browse_cache(self) -> None:
        """Open the results table from the saved cache, without any network call."""
        config = self._read_config()
        if config is None:
            return
        if config.no_cache:
            self.query_one("#cfg-error", Label).update(
                "Ignore cache is enabled — uncheck it to browse cached results."
            )
            return
        rows = cached_rows(load_cache(config))
        if not rows:
            self.query_one("#cfg-error", Label).update(f"No cached results in {config.cache_path}.")
            return
        self.app.push_screen(
            ResultsScreen(config, PipelineResult(all_results=rows), offline=True)
        )

    @on(Button.Pressed, "#cfg-quit")
    def quit_app(self) -> None:
        self.app.exit()


class RunScreen(Screen):
    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.cancel_event = threading.Event()
        self.totals = {"scrape": 0, "new": 0, "refresh": 0}
        self.finished = False
        self._results_pushed = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="run-box"):
            yield Label("Running pipeline…", id="run-phase")
            yield Label("Scrape pages: 0", id="run-scrape-label")
            yield ProgressBar(total=None, id="run-scrape-bar")
            yield Label("New games: 0/0", id="run-new-label")
            yield ProgressBar(total=100, id="run-new-bar")
            yield Label("Price refresh: 0/0", id="run-refresh-label")
            yield ProgressBar(total=100, id="run-refresh-bar")
            yield RichLog(id="run-log", highlight=True, markup=False)
            with Horizontal(id="run-actions"):
                yield Button("Cancel", id="run-cancel", variant="warning")
        yield Footer()

    def on_mount(self) -> None:
        self.config.cancel_event = self.cancel_event
        ensure_session(self.config)
        reporter = TuiReporter(self._post_from_thread, self.cancel_event)
        self.reporter = reporter
        self.run_worker(self._run_pipeline(reporter), thread=True, exclusive=True)

    def _post_from_thread(self, message: Message) -> None:
        self.app.call_from_thread(self.post_message, message)

    def _run_pipeline(self, reporter: Reporter):
        def _work():
            try:
                result = run_pipeline(self.config, reporter=reporter)
                self._post_from_thread(PipelineFinished(result))
            except Exception as exc:  # noqa: BLE001
                self._post_from_thread(PipelineFinished(None, error=str(exc)))

        return _work

    def _set_bar(self, bar_id: str, label_id: str, label: str, done: int, total: int | None) -> None:
        bar = self.query_one(bar_id, ProgressBar)
        self.query_one(label_id, Label).update(label)
        if total:
            bar.update(total=total, progress=done)
        else:
            bar.update(total=None)
            bar.advance(1)

    def on_phase_changed(self, event: PhaseChanged) -> None:
        names = {
            "load_cache": "Loading cache…",
            "scrape": "Step 1: Scraping FLiNG Trainer…",
            "lookup": "Step 2a: Looking up new games on Steam…",
            "refresh": "Step 2b: Refreshing cached prices…",
            "excel": "Step 3: Writing Excel…",
            "done": "Done!",
        }
        self.query_one("#run-phase", Label).update(names.get(event.name, event.name))

    def on_progress_updated(self, event: ProgressUpdated) -> None:
        if event.desc == "Scraping FLiNG":
            self.totals["scrape"] = event.done
            self._set_bar("#run-scrape-bar", "#run-scrape-label", f"Scrape pages: {event.done}", event.done, None)
        elif event.desc == "New games":
            self._set_bar(
                "#run-new-bar", "#run-new-label",
                f"New games: {event.done}/{event.total}", event.done, event.total,
            )
        elif event.desc == "Price refresh":
            self._set_bar(
                "#run-refresh-bar", "#run-refresh-label",
                f"Price refresh: {event.done}/{event.total}", event.done, event.total,
            )

    def on_log_line(self, event: LogLine) -> None:
        self.query_one("#run-log", RichLog).write(event.text)

    def on_item_done(self, event: ItemDone) -> None:
        pass

    def on_pipeline_finished(self, event: PipelineFinished) -> None:
        self.finished = True
        if event.error:
            self.query_one("#run-phase", Label).update(f"Failed: {event.error}")
            self.query_one("#run-cancel", Button).label = "Back"
            return
        self.app.push_screen(ResultsScreen(self.config, event.result))
        self._results_pushed = True

    def on_screen_resume(self) -> None:
        if getattr(self, "_results_pushed", False):
            self.app.pop_screen()

    @on(Button.Pressed, "#run-cancel")
    def cancel_or_back(self) -> None:
        if self.finished:
            self.app.pop_screen()
            return
        self.cancel_event.set()
        self.query_one("#run-phase", Label).update("Cancelling after current items…")
        self.query_one("#run-cancel", Button).disabled = True


class ResultsScreen(Screen):
    BINDINGS = [
        ("r", "retry_row", "Retry row"),
        ("o", "edit_overrides", "Overrides"),
        ("e", "export_excel", "Export Excel"),
        ("f", "export_filtered", "Export filtered"),
        ("d", "show_detail", "Detail"),
        ("q", "back", "Back"),
    ]
    AUTO_FOCUS = "#results-table"

    def __init__(self, config: Config, result, offline: bool = False) -> None:
        super().__init__()
        self.config = config
        self.result = result
        self.offline = offline
        self.all_results: list[dict] = list(result.all_results)
        self.shown: list[dict] = list(self.all_results)
        self.prices_as_of = cache_price_as_of(self.all_results) if offline else ""
        self.deck_filter = "All"
        self.search_text = ""
        self.on_sale_only = False
        self.retrying = False
        self.sort_col: int | None = None
        self.sort_reverse = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="results-box"):
            yield Label("", id="results-stats")
            with Horizontal(id="results-filters"):
                yield Select(
                    DECK_FILTER_OPTIONS,
                    value="All",
                    id="flt-deck",
                    allow_blank=False,
                )
                yield Input(placeholder="Search game…", id="flt-search")
                yield Checkbox("On sale only", value=False, id="flt-sale")
                yield Button("Export Excel", id="res-export", variant="primary")
                yield Button("Export Filtered", id="res-export-filtered")
                yield Button("Overrides", id="res-overrides")
            yield DataTable(
                id="results-table",
                cursor_type="row",
                zebra_stripes=True,
                cursor_foreground_priority="css",
                cursor_background_priority="css",
            )
            yield Label("", id="results-status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#results-table", DataTable)
        headers = [c[0] for c in RESULT_COLUMNS]
        headers[5] = self.config.price_header
        for i, h in enumerate(headers):
            table.add_column(h, key=f"c{i}")
        self._refresh_stats()
        self._reload_table()

    def _refresh_stats(self) -> None:
        total = len(self.all_results)
        verified = sum(1 for r in self.all_results if r.get("deck_compat") == "Verified")
        playable = sum(1 for r in self.all_results if r.get("deck_compat") == "Playable")
        on_sale = sum(1 for r in self.all_results if r.get("on_sale") and r.get("deck_compat") in ("Verified", "Playable"))
        if self.offline:
            source = f"Source: cache, prices as of {self.prices_as_of or 'unknown'}"
        else:
            source = f"New: {len(self.result.new_results)}"
        self.query_one("#results-stats", Label).update(
            f"{source} | Total: {total} | Verified: {verified} | Playable: {playable} | "
            f"On Sale (Deck OK): {on_sale} | Showing: {len(self.shown)}"
        )

    def _reload_table(self) -> None:
        self.shown = filter_results(self.all_results, self.deck_filter, self.search_text, self.on_sale_only)
        if self.sort_col is not None:
            self.shown = sort_results(self.shown, self.sort_col, self.sort_reverse)
        table = self.query_one("#results-table", DataTable)
        table.clear()
        for r in self.shown:
            table.add_row(*format_row(r), key=r.get("trainer_url", ""))
        self._update_sort_headers()
        self._refresh_stats()

    def _update_sort_headers(self) -> None:
        table = self.query_one("#results-table", DataTable)
        headers = [c[0] for c in RESULT_COLUMNS]
        headers[5] = self.config.price_header
        for i, h in enumerate(headers):
            if self.sort_col == i:
                arrow = " ▼" if self.sort_reverse else " ▲"
                label = f"{h}{arrow}"
            else:
                label = h
            table.columns[f"c{i}"].label = label
        table.refresh()

    def _selected_result(self) -> dict | None:
        table = self.query_one("#results-table", DataTable)
        if not self.shown or not table.is_valid_coordinate(table.cursor_coordinate):
            return None
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        except Exception:  # noqa: BLE001
            return None
        url = str(row_key.value)
        for r in self.shown:
            if r.get("trainer_url") == url:
                return r
        return None

    @on(Select.Changed, "#flt-deck")
    def deck_changed(self, event: Select.Changed) -> None:
        self.deck_filter = str(event.value)
        self._reload_table()

    @on(Input.Changed, "#flt-search")
    def search_changed(self, event: Input.Changed) -> None:
        self.search_text = event.value
        self._reload_table()

    @on(Checkbox.Changed, "#flt-sale")
    def sale_changed(self, event: Checkbox.Changed) -> None:
        self.on_sale_only = event.value
        self._reload_table()

    @on(DataTable.HeaderSelected, "#results-table")
    def header_clicked(self, event: DataTable.HeaderSelected) -> None:
        col = int(str(event.column_key.value)[1:])
        if self.sort_col == col:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_col = col
            self.sort_reverse = False
        self._reload_table()
        status = "desc" if self.sort_reverse else "asc"
        self.query_one("#results-status", Label).update(
            f"Sorted by {RESULT_COLUMNS[col][0]} ({status}). Click header again to toggle."
        )

    @on(DataTable.RowSelected, "#results-table")
    def row_opened(self, event: DataTable.RowSelected) -> None:
        url = str(event.row_key.value)
        for r in self.shown:
            if r.get("trainer_url") == url:
                self.app.push_screen(DetailModal(r))
                return

    def action_show_detail(self) -> None:
        result = self._selected_result()
        if result is not None:
            self.app.push_screen(DetailModal(result))

    def action_retry_row(self) -> None:
        result = self._selected_result()
        if result is None or self.retrying:
            return
        self.retrying = True
        self.query_one("#results-status", Label).update(f"Retrying: {result.get('game_name', '')}…")
        self.run_worker(self._retry_work(result), thread=True, exclusive=True)

    def _retry_work(self, trainer: dict):
        def _work():
            try:
                from fling_checker.reporter import NullReporter

                updated = _process_single_new_trainer(dict(trainer), self.config, reporter=NullReporter())
                self.app.call_from_thread(self._apply_retry, trainer, updated, None)
            except Exception as exc:  # noqa: BLE001
                self.app.call_from_thread(self._apply_retry, trainer, None, str(exc))

        return _work

    def _apply_retry(self, old: dict, updated: dict | None, error: str | None) -> None:
        self.retrying = False
        status = self.query_one("#results-status", Label)
        if error or updated is None:
            status.update(f"Retry failed: {error or 'unknown error'}")
            return
        url = old.get("trainer_url")
        for i, r in enumerate(self.all_results):
            if r.get("trainer_url") == url:
                self.all_results[i] = updated
                break
        else:
            self.all_results.append(updated)
        try:
            cache = {r.get("trainer_url"): r for r in self.all_results if r.get("trainer_url")}
            save_cache(cache, self.config)
        except OSError as exc:
            status.update(f"Retried but cache save failed: {exc}")
            return
        self._reload_table()
        status.update(f"Retried: {updated.get('game_name', '')} -> {updated.get('steam_name') or updated.get('review_desc', '')}")

    def action_edit_overrides(self) -> None:
        self.app.push_screen(OverridesModal(self.config), self._overrides_closed)

    def _overrides_closed(self, saved: bool) -> None:
        if saved:
            self.query_one("#results-status", Label).update(
                f"Overrides saved ({len(self.config.overrides)} entries). Press r on a failed row to retry."
            )

    def action_export_excel(self) -> None:
        self._export_rows(list(self.all_results), "Excel", filtered=False)

    def action_export_filtered(self) -> None:
        self._export_rows(
            list(self.shown),
            f"Filtered Excel ({len(self.shown)} rows)",
            filtered=True,
        )

    def _export_rows(self, rows: list[dict], label: str, filtered: bool) -> None:
        import datetime

        if not rows:
            self.query_one("#results-status", Label).update(
                f"{label}: nothing to export (filter is empty)."
            )
            return
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        suffix = "_filtered" if filtered else ""
        path = self.config.output_dir / f"fling_steam_deck_{timestamp}{suffix}.xlsx"
        try:
            write_excel(rows, path, self.config)
        except OSError as exc:
            self.query_one("#results-status", Label).update(f"Export failed: {exc}")
            return
        self.query_one("#results-status", Label).update(f"{label} saved to: {path}")

    @on(Button.Pressed, "#res-export")
    def export_button(self) -> None:
        self.action_export_excel()

    @on(Button.Pressed, "#res-export-filtered")
    def export_filtered_button(self) -> None:
        self.action_export_filtered()

    @on(Button.Pressed, "#res-overrides")
    def overrides_button(self) -> None:
        self.action_edit_overrides()

    def action_back(self) -> None:
        self.app.pop_screen()


class FlingTuiApp(App):
    TITLE = "FLiNG + Steam Deck Checker"
    CSS = """
    #config-box, #run-box {
        width: 72;
        max-width: 90;
        height: 1fr;
        min-height: 20;
        margin: 1 2;
    }
    #config-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #config-actions, #run-actions, #config-checks, #overrides-form, #overrides-actions, #detail-actions {
        height: auto;
        min-height: 3;
        margin-top: 1;
    }
    #results-filters {
        layout: grid;
        grid-size: 3;
        grid-columns: 1fr 1fr 1fr;
        grid-rows: auto auto;
        height: auto;
        min-height: 6;
        margin-top: 1;
        margin-bottom: 1;
    }
    #results-filters Input, #results-filters Select {
        width: 100%;
    }
    #results-filters Checkbox {
        width: 100%;
    }
    #results-filters Button {
        width: 100%;
    }
    #cfg-error {
        color: red;
    }
    #run-log {
        height: 1fr;
        min-height: 8;
        border: solid green;
    }
    #run-scrape-bar, #run-new-bar, #run-refresh-bar {
        height: 1;
        margin-bottom: 1;
    }
    #results-box {
        margin: 0 1;
    }
    #results-table {
        height: 1fr;
    }
    #results-stats {
        text-style: bold;
    }
    #detail-box, #overrides-box {
        width: 80;
        max-width: 100;
        height: auto;
        max-height: 90%;
        margin: 2 4;
        padding: 1 2;
        border: solid green;
        background: $surface;
    }
    #detail-log {
        height: 20;
    }
    #ov-status {
        color: yellow;
    }
    """

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config

    def on_mount(self) -> None:
        self.push_screen(ConfigScreen(self.config))


def run_tui(config: Config) -> None:
    app = FlingTuiApp(config)
    app.run()
