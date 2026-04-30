"""Configuration, constants, and shared utilities."""

import argparse
import requests
from dataclasses import dataclass, field
from pathlib import Path

# ─── Print flush override ────────────────────────────────────

_builtin_print = print


def print(*args, **kwargs):
    kwargs.setdefault("flush", True)
    _builtin_print(*args, **kwargs)


# ─── Currency map ────────────────────────────────────────────

CURRENCY_MAP = {
    "AR": {"symbol": "ARS$", "code": "ARS", "decimal": True,  "sep": ","},
    "AU": {"symbol": "A$",   "code": "AUD", "decimal": True,  "sep": ","},
    "BR": {"symbol": "R$",   "code": "BRL", "decimal": True,  "sep": ","},
    "CA": {"symbol": "C$",   "code": "CAD", "decimal": True,  "sep": ","},
    "CN": {"symbol": "¥",    "code": "CNY", "decimal": True,  "sep": ","},
    "EU": {"symbol": "€",    "code": "EUR", "decimal": True,  "sep": ","},
    "GB": {"symbol": "£",    "code": "GBP", "decimal": True,  "sep": ","},
    "ID": {"symbol": "Rp",   "code": "IDR", "decimal": False, "sep": "."},
    "IN": {"symbol": "₹",    "code": "INR", "decimal": True,  "sep": ","},
    "JP": {"symbol": "¥",    "code": "JPY", "decimal": False, "sep": ","},
    "KR": {"symbol": "₩",    "code": "KRW", "decimal": False, "sep": ","},
    "MX": {"symbol": "MX$",  "code": "MXN", "decimal": True,  "sep": ","},
    "NO": {"symbol": "kr",   "code": "NOK", "decimal": True,  "sep": ","},
    "NZ": {"symbol": "NZ$",  "code": "NZD", "decimal": True,  "sep": ","},
    "PH": {"symbol": "₱",    "code": "PHP", "decimal": True,  "sep": ","},
    "PL": {"symbol": "zł",   "code": "PLN", "decimal": True,  "sep": ","},
    "RU": {"symbol": "₽",    "code": "RUB", "decimal": True,  "sep": ","},
    "SA": {"symbol": "﷼",    "code": "SAR", "decimal": True,  "sep": ","},
    "SE": {"symbol": "kr",   "code": "SEK", "decimal": True,  "sep": ","},
    "SG": {"symbol": "S$",   "code": "SGD", "decimal": True,  "sep": ","},
    "TH": {"symbol": "฿",    "code": "THB", "decimal": True,  "sep": ","},
    "TR": {"symbol": "₺",    "code": "TRY", "decimal": True,  "sep": ","},
    "TW": {"symbol": "NT$",  "code": "TWD", "decimal": True,  "sep": ","},
    "UA": {"symbol": "₴",    "code": "UAH", "decimal": True,  "sep": ","},
    "US": {"symbol": "$",    "code": "USD", "decimal": True,  "sep": ","},
    "ZA": {"symbol": "R",    "code": "ZAR", "decimal": True,  "sep": ","},
}

DEFAULT_CURRENCY = {"symbol": "$", "code": "USD", "decimal": True, "sep": ","}

# ─── Steam URLs ────────────────────────────────────────────────

# Use the site root rather than /category/trainer/. The category archive is
# ordered by post published_time, so trainers that get re-released years later
# (e.g. Graveyard Keeper, Starfield, Wartales — originally posted 2018-2023,
# updated in 2026) get buried on old pages and miss the min_year cutoff.
# The homepage is ordered by modified_time, which matches "Recently Updated"
# and surfaces re-uploads at the top.
FLING_BASE_URL = "https://flingtrainer.com/page/{page}/"
FLING_FIRST_PAGE = "https://flingtrainer.com/"
STEAM_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
STEAM_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
STEAM_REVIEWS_URL = "https://store.steampowered.com/appreviews/{appid}"
STEAM_DECK_URL = "https://store.steampowered.com/saleaction/ajaxgetdeckappcompatibilityreport"

DECK_COMPAT_MAP = {
    0: "Unknown",
    1: "Unsupported",
    2: "Playable",
    3: "Verified",
}

# ─── Config ────────────────────────────────────────────────────


@dataclass
class Config:
    min_year: int = 2024
    country_code: str = "ID"
    request_delay: float = 1.5
    max_workers: int = 2
    max_retries: int = 3
    cache_price_ttl_hours: int = 24
    cache_full_ttl_days: int = 7
    output_dir: Path = None  # default: current directory
    no_cache: bool = False
    verbose: bool = False
    # derived:
    currency: dict = field(default_factory=dict)
    overrides: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.output_dir is None:
            self.output_dir = Path.cwd()
        self.currency = CURRENCY_MAP.get(self.country_code, DEFAULT_CURRENCY)
        self.cache_path = self.output_dir / "fling_steam_cache.json"
        self.overrides_path = self.output_dir / "fling_steam_overrides.json"
        self._load_overrides()

    def _load_overrides(self):
        if self.overrides_path.exists():
            try:
                import json
                with open(self.overrides_path, "r") as f:
                    self.overrides = json.load(f)
                if self.verbose:
                    print(f"    Loaded {len(self.overrides)} AppID overrides from {self.overrides_path}")
            except Exception as e:
                print(f"  ⚠ Warning: Failed to load overrides file: {e}")

    @property
    def currency_code(self) -> str:
        return self.currency.get("code", "USD")

    @property
    def currency_symbol(self) -> str:
        return self.currency.get("symbol", "$")

    @property
    def price_header(self) -> str:
        return f"Price ({self.currency_code})"

    @property
    def price_number_format(self) -> str:
        # `,` in Excel format codes is the thousand-separator placeholder —
        # Excel localizes it to `.` on ID locale, `,` on US locale, etc.
        if self.currency.get("decimal", True):
            return "#,##0.00"
        return "#,##0"


def build_session() -> requests.Session:
    """Create a requests session with browser-like headers."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session