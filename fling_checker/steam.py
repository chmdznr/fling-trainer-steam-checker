"""Steam Store API functions."""

import json
import re
import time
import requests
from tqdm import tqdm

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


def _clean_game_name(game_name: str) -> str:
    """Strip FLiNG-specific suffixes from game name for Steam search.

    FLiNG appends ' Trainer' (and sometimes version info like ' v1.2+')
    to game names. These must be removed before searching Steam.
    """
    # Remove " Trainer" suffix (FLiNG naming convention)
    name = game_name.removesuffix(" Trainer")
    # Remove trailing version info like " v1.0" or " v1.0-v1.2+"
    name = re.sub(r"\s+v[\d.]+\S*$", "", name)
    return name.strip()


def search_steam_appid(game_name: str, config: Config, trainer_slug: str = None) -> dict | None:
    """Search Steam Store for a game by name using multiple strategies."""
    search_name = _clean_game_name(game_name)
    
    # Strategy 0: Manual Overrides from JSON file
    # Check by full search_name or trainer_slug
    if search_name in config.overrides:
        appid = config.overrides[search_name]
        if config.verbose:
            tqdm.write(f"    [Override] Found '{search_name}' in overrides -> AppID {appid}")
        return {"appid": int(appid), "name": search_name}
    
    if trainer_slug and trainer_slug in config.overrides:
        appid = config.overrides[trainer_slug]
        if config.verbose:
            tqdm.write(f"    [Override] Found slug '{trainer_slug}' in overrides -> AppID {appid}")
        return {"appid": int(appid), "name": search_name}

    def normalize(n: str) -> str:
        # Lowercase and strip special chars
        n = re.sub(r"[^\w\s]", "", n.lower())
        # Replace Roman Numerals with digits for comparison
        n = n.replace(" ii", " 2").replace(" iii", " 3").replace(" iv", " 4")
        return " ".join(n.split())

    norm_target = normalize(search_name)
    
    # Define search terms to try (Full Name, Slug if available)
    search_terms = [search_name]
    if trainer_slug:
        # 'long-yin-li-zhi-zhuan' -> 'long yin li zhi zhuan'
        search_terms.append(trainer_slug.replace("-", " "))

    # Strategy 1: Steam Suggest API (Very robust)
    suggest_url = "https://store.steampowered.com/search/suggest"
    for term in search_terms:
        suggest_params = {"term": term, "f": "games", "cc": config.country_code, "l": "english"}
        resp = steam_request(suggest_url, params=suggest_params, config=config)
        time.sleep(0.5) # Polite delay
        if resp and resp.text:
            matches = re.findall(r'data-ds-appid="(\d+)".*?<div class="match_name">([^<]+)</div>', resp.text, re.DOTALL)
            for appid, s_name in matches:
                if normalize(s_name) == norm_target or norm_target in normalize(s_name) or normalize(s_name) in norm_target:
                    return {"appid": int(appid), "name": s_name}

    # Strategy 2: Official StoreSearch API (Fallback)
    search_strategies = []
    for term in search_terms:
        search_strategies.append({"term": term, "cc": config.country_code})
        words = term.split()
        if len(words) > 2:
            search_strategies.append({"term": " ".join(words[:2]), "cc": config.country_code})
        search_strategies.append({"term": term, "cc": "US"})
    
    for strategy in search_strategies:
        params = {**strategy, "l": "english"}
        resp = steam_request(STEAM_SEARCH_URL, params=params, config=config)
        time.sleep(0.5) # Polite delay
        if not resp:
            continue
        try:
            data = resp.json()
            items = data.get("items", [])
            for item in items:
                if normalize(item["name"]) == norm_target or norm_target in normalize(item["name"]):
                    return {"appid": item["id"], "name": item["name"]}
        except:
            continue

    # Strategy 3: Fuzzy Word Fallback (Try searching longest words individually)
    words = sorted(search_name.split(), key=len, reverse=True)
    for word in words[:2]:
        if len(word) < 4: continue
        params = {"term": word, "cc": config.country_code, "l": "english"}
        resp = steam_request(STEAM_SEARCH_URL, params=params, config=config)
        time.sleep(0.5) # Polite delay
        if resp:
            try:
                data = resp.json()
                for item in data.get("items", []):
                    s_name_norm = normalize(item["name"])
                    if norm_target in s_name_norm or s_name_norm in norm_target:
                        return {"appid": item["id"], "name": item["name"]}
            except: continue

    return None


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
    except json.JSONDecodeError:
        return "Unknown"

    # Steam Deck API returns resolved_category at top level of "results"
    # Note: If no data, "results" might be an empty list [] instead of a dict
    results = data.get("results")
    if isinstance(results, dict):
        resolved_category = results.get("resolved_category", 0)
    else:
        resolved_category = 0
        
    return DECK_COMPAT_MAP.get(resolved_category, "Unknown")


def get_steam_reviews(appid: int, config: Config) -> dict:
    """Get review summary for an app."""
    url = STEAM_REVIEWS_URL.format(appid=appid)
    params = {
        "json": 1,
        "language": "all",
        "purchase_type": "all",
        "num_per_page": 0,
        "review_type": "all",
    }
    resp = steam_request(url, params=params, config=config)
    if resp is None:
        return {"total_reviews": 0, "positive_pct": 0, "review_desc": "Not Found"}

    try:
        data = resp.json()
    except json.JSONDecodeError:
        return {"total_reviews": 0, "positive_pct": 0, "review_desc": "Not Found"}

    summary = data.get("query_summary", {})
    total = summary.get("total_reviews", 0)
    positive = summary.get("total_positive", 0)
    pct = round(positive / total * 100, 1) if total > 0 else 0
    desc = summary.get("review_score_desc", "No Reviews")
    return {
        "total_reviews": total,
        "positive_pct": pct,
        "review_desc": desc,
    }


def extract_price_info(app_data: dict, config: Config) -> dict:
    """Extract price and discount info from app details."""
    if app_data.get("is_free"):
        return {
            "price": "Free", "price_idr": 0, "original_price_idr": 0,
            "discount_pct": 0, "on_sale": False,
        }

    price_overview = app_data.get("price_overview")
    if not price_overview:
        # Fallback to package_groups if price_overview is missing (common for pre-orders)
        package_groups = app_data.get("package_groups", [])
        if package_groups:
            subs = package_groups[0].get("subs", [])
            if subs:
                first_sub = subs[0]
                final_cents = first_sub.get("price_in_cents_with_discount", 0)
                discount = first_sub.get("percent_savings", 0)
                # Calculate initial if discount > 0
                if discount > 0:
                    initial_cents = int(final_cents / (1 - discount / 100))
                else:
                    initial_cents = final_cents
                
                price_overview = {
                    "final": final_cents,
                    "initial": initial_cents,
                    "discount_percent": discount
                }

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

    # Steam always provides prices in the smallest currency unit (cents).
    # Divide by 100 for all currencies to get the base value.
    final_val = final_cents / 100
    initial_val = initial_cents / 100

    if currency["decimal"]:
        price_str = f"{symbol}{final_val:,.2f}"
    else:
        price_str = f"{symbol}{final_val:,.0f}"

    return {
        "price": price_str,
        "price_idr": final_val,
        "original_price_idr": initial_val,
        "discount_pct": price_overview.get("discount_percent", 0),
        "on_sale": price_overview.get("discount_percent", 0) > 0,
    }