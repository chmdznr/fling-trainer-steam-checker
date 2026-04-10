"""FLiNG Trainer + Steam Deck Compatibility Checker package."""

from fling_checker.config import Config, CURRENCY_MAP, DEFAULT_CURRENCY
from fling_checker.config import (
    FLING_BASE_URL, FLING_FIRST_PAGE,
    STEAM_SEARCH_URL, STEAM_APPDETAILS_URL,
    STEAM_REVIEWS_URL, STEAM_DECK_URL,
    DECK_COMPAT_MAP, build_session,
)
from fling_checker.cache import load_cache, save_cache
from fling_checker.fling import scrape_fling_trainers
from fling_checker.steam import (
    steam_request, search_steam_appid,
    get_steam_app_details, get_steam_deck_compat,
    get_steam_reviews, extract_price_info,
)
from fling_checker.processor import (
    process_new_trainers, refresh_prices,
)
from fling_checker.excel import write_excel
from fling_checker.cli import parse_args

__all__ = [
    "Config", "CURRENCY_MAP", "DEFAULT_CURRENCY",
    "FLING_BASE_URL", "FLING_FIRST_PAGE",
    "STEAM_SEARCH_URL", "STEAM_APPDETAILS_URL",
    "STEAM_REVIEWS_URL", "STEAM_DECK_URL",
    "DECK_COMPAT_MAP", "build_session",
    "load_cache", "save_cache",
    "scrape_fling_trainers",
    "steam_request", "search_steam_appid",
    "get_steam_app_details", "get_steam_deck_compat",
    "get_steam_reviews", "extract_price_info",
    "process_new_trainers", "refresh_prices",
    "write_excel", "parse_args",
]