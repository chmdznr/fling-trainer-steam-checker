"""Command-line interface and main entry point."""

import argparse
from pathlib import Path

from fling_checker.config import Config, CURRENCY_MAP, ensure_session, print
from fling_checker.pipeline import run_pipeline


def _positive_int(value: str) -> int:
    """Parse a CLI integer that must be greater than zero."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


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
    parser.add_argument("--workers", type=_positive_int, default=4,
                        help="Number of concurrent threads (default: 4)")
    parser.add_argument("--delay", type=float, default=1.5,
                        help="Seconds between Steam API requests per game (default: 1.5)")
    parser.add_argument("--retries", type=_positive_int, default=3,
                        help="Max retry attempts for Steam API errors (default: 3)")
    parser.add_argument("--ttl-hours", type=int, default=24,
                        help="Skip price refresh if cached within N hours (default: 24)")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Output directory for Excel and cache (default: current dir)")
    parser.add_argument("--no-cache", action="store_true",
                        help="Ignore cache, fetch all data fresh")
    parser.add_argument("--verbose", action="store_true",
                        help="Show verbose/debug output")
    parser.add_argument("--tui", action="store_true",
                        help="Launch the interactive Textual TUI instead of CLI mode")

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
    config.session = ensure_session(config)

    if config.country_code not in CURRENCY_MAP:
        print(f"⚠ Warning: Country code '{config.country_code}' not in CURRENCY_MAP.")
        print(f"  Prices will use Steam's raw format. Supported: {', '.join(sorted(CURRENCY_MAP.keys()))}")

    return config, args.tui


def main():
    config, use_tui = parse_args()

    if use_tui:
        from fling_checker.tui import run_tui
        run_tui(config)
        return

    print("=" * 60)
    print("FLiNG Trainer + Steam Deck Compatibility Checker")
    print("=" * 60)
    print(f"  Region: {config.country_code} ({config.currency_code})")
    print(f"  Workers: {config.max_workers} | Delay: {config.request_delay}s")
    print(f"  Min year: {config.min_year} | Cache TTL: {config.cache_price_ttl_hours}h")

    result = run_pipeline(config)

    # Summary
    deck_ok = [r for r in result.all_results if r.get("deck_compat") in ("Verified", "Playable")]
    on_sale = [r for r in result.all_results if r.get("on_sale") and r.get("deck_compat") in ("Verified", "Playable")]

    print(f"\n{'=' * 60}")
    print(f"  Done! {len(result.all_results)} games processed")
    print(f"  New (fetched):         {len(result.new_results)}")
    print(f"  Cached (refreshed):    {len(result.refreshed)}")
    print(f"  Deck Compatible:       {len(deck_ok)} (Verified + Playable)")
    print(f"  On Sale (Deck OK):     {len(on_sale)}")
    print(f"{'=' * 60}")
