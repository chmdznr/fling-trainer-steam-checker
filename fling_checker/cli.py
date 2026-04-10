"""Command-line interface and main entry point."""

import argparse
from pathlib import Path

from fling_checker.config import Config, CURRENCY_MAP, build_session, print
from fling_checker.cache import load_cache, save_cache
from fling_checker.fling import scrape_fling_trainers
from fling_checker.processor import process_new_trainers, refresh_prices
from fling_checker.excel import write_excel


def parse_args() -> Config:
    """Parse command-line arguments and return a Config object."""
    parser = argparse.ArgumentParser(
        description="FLiNG Trainer + Steam Deck Compatibility Checker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  %(prog)s                          Default: ID/IDR, trainers from 2024+
  %(prog)s --country US             Use USD pricing (United States)
  %(prog)s --country JP             Use JPY pricing (Japan)
  %(prog)s --year 2023             Include older trainers
  %(prog)s --no-cache              Fresh run, ignore existing cache
  %(prog)s --workers 2             Use 2 concurrent threads

Supported country codes for pricing:
  AR, AU, BR, CA, CN, EU, GB, ID, IN, JP, KR, MX, NO, NZ,
  PH, PL, RU, SA, SE, SG, TH, TR, TW, UA, US, ZA
""",
    )
    parser.add_argument("--year", type=int, default=2024,
                        help="Minimum trainer year (default: 2024)")
    parser.add_argument("--country", type=str, default="ID",
                        help="Steam store country code for pricing (default: ID)")
    parser.add_argument("--workers", type=int, default=4,
                        help="Number of concurrent threads (default: 4)")
    parser.add_argument("--delay", type=float, default=1.5,
                        help="Seconds between Steam API requests per game (default: 1.5)")
    parser.add_argument("--retries", type=int, default=3,
                        help="Max retry attempts for Steam API errors (default: 3)")
    parser.add_argument("--ttl-hours", type=int, default=24,
                        help="Skip price refresh if cached within N hours (default: 24)")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Output directory for Excel and cache (default: current dir)")
    parser.add_argument("--no-cache", action="store_true",
                        help="Ignore cache, fetch all data fresh")
    parser.add_argument("--verbose", action="store_true",
                        help="Show verbose/debug output")

    args = parser.parse_args()

    config = Config(
        min_year=args.year,
        country_code=args.country.upper(),
        request_delay=args.delay,
        max_workers=args.workers,
        max_retries=args.retries,
        cache_price_ttl_hours=args.ttl_hours,
        no_cache=args.no_cache,
        verbose=args.verbose,
        output_dir=Path(args.output_dir) if args.output_dir else None,
    )

    # Attach session
    config.session = build_session()

    if config.country_code not in CURRENCY_MAP:
        print(f"⚠ Warning: Country code '{config.country_code}' not in CURRENCY_MAP.")
        print(f"  Prices will use Steam's raw format. Supported: {', '.join(sorted(CURRENCY_MAP.keys()))}")

    return config


def main():
    config = parse_args()

    print("=" * 60)
    print("FLiNG Trainer + Steam Deck Compatibility Checker")
    print("=" * 60)
    print(f"  Region: {config.country_code} ({config.currency_code})")
    print(f"  Workers: {config.max_workers} | Delay: {config.request_delay}s")
    print(f"  Min year: {config.min_year} | Cache TTL: {config.cache_price_ttl_hours}h")

    # Load cache
    print(f"\n📦 Loading cache...")
    cache = load_cache(config)

    # Step 1: Scrape FLiNG (incremental — stops at cached entries)
    print(f"\n📋 Step 1: Scraping FLiNG Trainer (year >= {config.min_year})...")
    new_trainers, cached_results = scrape_fling_trainers(cache, config)

    # Step 2a: Full Steam lookup for NEW trainers only
    new_results = []
    if new_trainers:
        print(f"\n🎮 Step 2a: Looking up {len(new_trainers)} NEW games on Steam...")
        new_results = process_new_trainers(new_trainers, config)
    else:
        print(f"\n🎮 Step 2a: No new games to look up!")

    # Step 2b: Refresh prices for cached entries
    refreshed_cached = []
    if cached_results:
        refreshed_cached = refresh_prices(cached_results, config)
    else:
        print(f"\n💲 Step 2b: No cached entries to refresh.")

    # Merge results
    all_results = new_results + refreshed_cached

    # Update cache
    print(f"\n💾 Updating cache...")
    for r in all_results:
        cache[r["trainer_url"]] = r
    save_cache(cache, config)

    # Step 3: Write Excel
    timestamp = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M")
    output_path = config.output_dir / f"fling_steam_deck_{timestamp}.xlsx"
    print(f"\n📊 Step 3: Writing Excel...")
    write_excel(all_results, output_path, config)

    # Summary
    deck_ok = [r for r in all_results if r.get("deck_compat") in ("Verified", "Playable")]
    on_sale = [r for r in all_results if r.get("on_sale") and r.get("deck_compat") in ("Verified", "Playable")]

    print(f"\n{'=' * 60}")
    print(f"  Done! {len(all_results)} games processed")
    print(f"  New (fetched):         {len(new_results)}")
    print(f"  Cached (refreshed):    {len(refreshed_cached)}")
    print(f"  Deck Compatible:       {len(deck_ok)} (Verified + Playable)")
    print(f"  On Sale (Deck OK):     {len(on_sale)}")
    print(f"{'=' * 60}")