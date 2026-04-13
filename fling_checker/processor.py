"""Orchestration: process new trainers and refresh cached prices."""

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from tqdm import tqdm

from fling_checker.config import print, Config
from fling_checker.steam import (
    _clean_game_name,
    search_steam_appid,
    get_steam_app_details,
    get_steam_deck_compat,
    get_steam_reviews,
    extract_price_info,
    extract_genres,
)
import time


def _process_single_new_trainer(trainer: dict, config: Config) -> dict:
    """Process a single new trainer: full Steam lookup (search + deck + details + reviews)."""
    game_name = trainer["game_name"]
    trainer_slug = trainer.get("trainer_slug")
    now = datetime.now().isoformat()

    steam_match = search_steam_appid(game_name, config, trainer_slug=trainer_slug)
    time.sleep(config.request_delay)

    if not steam_match:
        search_name = _clean_game_name(game_name)
        if config.verbose and search_name != game_name:
            tqdm.write(f"  ⚠ {game_name} (searched: {search_name}) — not found on Steam")
        else:
            tqdm.write(f"  ⚠ {game_name} — not found on Steam")
        return {
            **trainer,
            "steam_appid": None, "steam_name": None, "steam_url": None,
            "deck_compat": "Unknown",
            "price": "N/A", "price_idr": None, "original_price_idr": None,
            "discount_pct": 0, "on_sale": False,
            "total_reviews": 0, "positive_pct": 0, "review_desc": "Not Found",
            "genres": "",
            "last_fetched": now,
            "_price_updated_at": None,
            "_country_code": config.country_code,
        }

    appid = steam_match["appid"]
    steam_name = steam_match["name"]
    tqdm.write(f"  ✓ {game_name} → {steam_name} (ID: {appid})")

    deck_compat = get_steam_deck_compat(appid, config)
    time.sleep(config.request_delay)

    app_details = get_steam_app_details(appid, config)
    time.sleep(config.request_delay)
    price_info = extract_price_info(app_details, config) if app_details else {
        "price": "N/A", "price_idr": None, "original_price_idr": None,
        "discount_pct": 0, "on_sale": False,
    }

    reviews = get_steam_reviews(appid, config)
    time.sleep(config.request_delay)

    if price_info.get("on_sale"):
        tqdm.write(f"  💰 ON SALE: {game_name} — {price_info['price']} (-{price_info['discount_pct']}%)")

    # Extract genres from app_details
    genres = extract_genres(app_details) if app_details else ""

    return {
        **trainer,
        "steam_appid": appid, "steam_name": steam_name,
        "steam_url": f"https://store.steampowered.com/app/{appid}/",
        "deck_compat": deck_compat,
        **price_info, **reviews,
        "genres": genres,
        "last_fetched": now,
        "_price_updated_at": now if price_info.get("price_idr") is not None else None,
        "_country_code": config.country_code,
    }


def process_new_trainers(trainers: list[dict], config: Config) -> list[dict]:
    """For each NEW trainer, do full Steam lookup concurrently."""
    if not trainers:
        return []

    results = []
    total = len(trainers)

    with tqdm(total=total, desc="New games", unit="game") as pbar:
        with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
            futures = {
                executor.submit(_process_single_new_trainer, t, config): t
                for t in trainers
            }
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                pbar.update(1)

    return results


def _refresh_single_price(cached_entry: dict, config: Config) -> dict:
    """Refresh price for a single cached entry. Skips if price data is fresh (within TTL)
    AND the country code hasn't changed."""
    now = datetime.now()
    now_iso = now.isoformat()

    # Force refresh if country code changed since last fetch
    cached_country = cached_entry.get("_country_code")
    country_changed = cached_country != config.country_code

    # Check TTL
    price_updated = cached_entry.get("_price_updated_at")
    if price_updated and not country_changed:
        try:
            price_time = datetime.fromisoformat(price_updated)
            if (now - price_time) < __import__("datetime").timedelta(hours=config.cache_price_ttl_hours):
                # Price data is fresh and country hasn't changed — skip
                cached_entry["last_fetched"] = now_iso
                return cached_entry
        except (ValueError, TypeError):
            pass  # Invalid timestamp — refresh

    appid = cached_entry.get("steam_appid")
    if not appid:
        cached_entry["last_fetched"] = now_iso
        return cached_entry

    # Fetch fresh price
    app_details = get_steam_app_details(appid, config)
    if app_details:
        price_info = extract_price_info(app_details, config)
        cached_entry.update(price_info)
        cached_entry["_price_updated_at"] = now_iso
        cached_entry["_country_code"] = config.country_code

    cached_entry["last_fetched"] = now_iso
    return cached_entry


def refresh_prices(cached_results: list[dict], config: Config) -> list[dict]:
    """Refresh prices for cached entries that have Steam IDs."""
    has_appid = [r for r in cached_results if r.get("steam_appid")]
    no_appid = [r for r in cached_results if not r.get("steam_appid")]

    if not has_appid:
        return no_appid

    print(f"\n💲 Step 2b: Refreshing prices for {len(has_appid)} cached games...")
    refreshed = []
    total = len(has_appid)

    with tqdm(total=total, desc="Price refresh", unit="game") as pbar:
        with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
            futures = {
                executor.submit(_refresh_single_price, r, config): r
                for r in has_appid
            }
            for future in as_completed(futures):
                result = future.result()
                refreshed.append(result)
                pbar.update(1)

    return refreshed + no_appid