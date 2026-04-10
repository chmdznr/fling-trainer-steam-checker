"""Steam Store API functions."""

import time
import requests

from fling_checker.config import (
    print, Config,
    STEAM_SEARCH_URL, STEAM_APPDETAILS_URL,
    STEAM_REVIEWS_URL, STEAM_DECK_URL,
    DECK_COMPAT_MAP, DEFAULT_CURRENCY,
)


def steam_request(url: str, params: dict, config: Config, timeout: int = 10) -> requests.Response | None:
    """Make a Steam API request with exponential backoff retry.

    Retries on transient errors (ConnectionError, Timeout, 5xx).
    Returns None on permanent failure (4xx) or exhausted retries.
    """
    for attempt in range(config.max_retries):
        try:
            resp = config.session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout) as e:
            wait = 2 ** (attempt + 1)
            if config.verbose:
                print(f"    Retry {attempt + 1}/{config.max_retries} — {type(e).__name__}, waiting {wait}s...")
            time.sleep(wait)
        except requests.HTTPError as e:
            status_code = e.response.status_code if e.response else 0
            if 400 <= status_code < 500:
                # Client error — don't retry
                if config.verbose:
                    print(f"    HTTP {status_code} — not retrying")
                return None
            wait = 2 ** (attempt + 1)
            if config.verbose:
                print(f"    Retry {attempt + 1}/{config.max_retries} — HTTP {status_code}, waiting {wait}s...")
            time.sleep(wait)
    return None


def search_steam_appid(game_name: str, config: Config) -> dict | None:
    """Search Steam Store for a game by name. Returns first match or None."""
    params = {"term": game_name, "cc": config.country_code, "l": "english"}
    resp = steam_request(STEAM_SEARCH_URL, params=params, config=config)
    if resp is None:
        return None

    data = resp.json()
    items = data.get("items", [])
    if not items:
        return None

    # Try exact match first
    for item in items:
        if item["name"].lower() == game_name.lower():
            return item

    # Fallback to first result
    return items[0]


def get_steam_app_details(appid: int, config: Config) -> dict | None:
    """Get app details from Steam Store API."""
    params = {"appids": appid, "cc": config.country_code, "l": "english"}
    resp = steam_request(STEAM_APPDETAILS_URL, params=params, config=config)
    if resp is None:
        return None

    data = resp.json()
    app_data = data.get(str(appid), {})
    if not app_data.get("success"):
        return None
    return app_data.get("data")


def get_steam_deck_compat(appid: int, config: Config) -> str:
    """Check Steam Deck compatibility for an app."""
    params = {"nAppID": appid}
    resp = steam_request(STEAM_DECK_URL, params=params, config=config)
    if resp is None:
        return "Unknown"

    try:
        data = resp.json()
        results = data.get("results", {}).get("app", {}).get("compat", [])
        if results:
            category = results[0].get("category", 0)
            return DECK_COMPAT_MAP.get(category, "Unknown")
    except (KeyError, IndexError, ValueError):
        pass

    return "Unknown"


def get_steam_reviews(appid: int, config: Config) -> dict:
    """Get review summary for an app."""
    url = STEAM_REVIEWS_URL.format(appid=appid)
    params = {
        "json": 1,
        "num_per_page": 0,
        "purchase_type": "all",
        "language": "english",
    }
    resp = steam_request(url, params=params, config=config)
    if resp is None:
        return {"total_reviews": 0, "positive_pct": 0, "review_desc": "Not Found"}

    try:
        data = resp.json()
        summary = data.get("query_summary", {})
        total = summary.get("total_reviews", 0)
        positive = summary.get("total_positive", 0)
        pct = round(positive / total * 100, 1) if total > 0 else 0
        desc = summary.get("review_score_desc", "Not Found")
        return {
            "total_reviews": total,
            "positive_pct": pct,
            "review_desc": desc,
        }
    except (KeyError, ValueError):
        return {"total_reviews": 0, "positive_pct": 0, "review_desc": "Not Found"}


def extract_price_info(app_data: dict, config: Config) -> dict:
    """Extract price and discount info from app details."""
    if app_data.get("is_free"):
        return {
            "price": "Free", "price_idr": 0, "original_price_idr": 0,
            "discount_pct": 0, "on_sale": False,
        }

    price_overview = app_data.get("price_overview")
    if not price_overview:
        # Free or not available for purchase in this region
        purchase_type = app_data.get("type", "")
        if purchase_type == "Free":
            return {
                "price": "Free", "price_idr": 0, "original_price_idr": 0,
                "discount_pct": 0, "on_sale": False,
            }
        return {
            "price": "N/A", "price_idr": None, "original_price_idr": None,
            "discount_pct": 0, "on_sale": False,
        }

    # Format price with currency symbol
    currency = config.currency or DEFAULT_CURRENCY
    symbol = currency["symbol"]
    final_cents = price_overview.get("final", 0)
    initial_cents = price_overview.get("initial", 0)

    if currency["decimal"]:
        price_str = f"{symbol}{final_cents / 100:,.2f}"
    else:
        price_str = f"{symbol}{final_cents:,.0f}"

    return {
        "price": price_str,
        "price_idr": price_overview.get("final", 0) / 100,
        "original_price_idr": price_overview.get("initial", 0) / 100,
        "discount_pct": price_overview.get("discount_percent", 0),
        "on_sale": price_overview.get("discount_percent", 0) > 0,
    }