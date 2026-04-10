"""Cache management for FLiNG + Steam data."""

import json

from fling_checker.config import print


def load_cache(config) -> dict:
    """Load cache from JSON file. Keyed by trainer_url."""
    if config.no_cache or not config.cache_path.exists():
        return {}
    try:
        with open(config.cache_path) as f:
            data = json.load(f)
        print(f"  Loaded cache: {len(data)} entries from {config.cache_path.name}")
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def save_cache(cache: dict, config):
    """Save cache to JSON file."""
    with open(config.cache_path, "w") as f:
        json.dump(cache, f, indent=2, default=str)
    print(f"  Cache saved: {len(cache)} entries to {config.cache_path.name}")