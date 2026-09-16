"""Shared pipeline orchestration used by both CLI and TUI."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from fling_checker.cache import load_cache, save_cache
from fling_checker.config import ensure_session
from fling_checker.excel import write_excel
from fling_checker.fling import scrape_fling_trainers
from fling_checker.processor import process_new_trainers, refresh_prices
from fling_checker.reporter import CliReporter, Reporter, say


@dataclass
class PipelineResult:
    all_results: list[dict] = field(default_factory=list)
    new_results: list[dict] = field(default_factory=list)
    refreshed: list[dict] = field(default_factory=list)
    output_path: Path | None = None


def run_pipeline(config, reporter: Reporter | None = None) -> PipelineResult:
    """Run scrape -> Steam lookup -> price refresh -> cache -> Excel."""
    import datetime

    ensure_session(config)
    own_reporter = False
    if reporter is None:
        reporter = CliReporter()
        own_reporter = True
    try:
        reporter.phase("load_cache")
        say(reporter, "\n📦 Loading cache...")
        cache = load_cache(config)

        reporter.phase("scrape")
        say(reporter, f"\n📋 Step 1: Scraping FLiNG Trainer (year >= {config.min_year})...")
        new_trainers, _cached_results = scrape_fling_trainers(cache, config, reporter=reporter)

        new_results: list[dict] = []
        if new_trainers and not reporter.is_cancelled():
            reporter.phase("lookup")
            say(reporter, f"\n🎮 Step 2a: Looking up {len(new_trainers)} NEW games on Steam...")
            new_results = process_new_trainers(new_trainers, config, reporter=reporter)
        else:
            say(reporter, "\n🎮 Step 2a: No new games to look up!")

        refreshed: list[dict] = []
        cached_with_ids = [r for r in cache.values() if r.get("steam_appid")]
        if cached_with_ids and not reporter.is_cancelled():
            refreshed = refresh_prices(cached_with_ids, config, reporter=reporter)
        else:
            say(reporter, "\n💲 Step 2b: No cached entries to refresh.")

        all_results = list(cache.values())
        for r in new_results + refreshed:
            all_results = [item for item in all_results if item["trainer_url"] != r["trainer_url"]]
            all_results.append(r)

        if all_results and not reporter.is_cancelled():
            say(reporter, "\n💾 Updating cache...")
            for r in all_results:
                cache[r["trainer_url"]] = r
            save_cache(cache, config)
        else:
            say(reporter, "\n💾 No new data to update cache.")

        reporter.phase("excel")
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        output_path = config.output_dir / f"fling_steam_deck_{timestamp}.xlsx"
        say(reporter, "\n📊 Step 3: Writing Excel...")
        write_excel(all_results, output_path, config)
        reporter.phase("done")

        return PipelineResult(
            all_results=all_results,
            new_results=new_results,
            refreshed=refreshed,
            output_path=output_path,
        )
    finally:
        if own_reporter:
            reporter.close()
