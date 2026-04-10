"""FLiNG Trainer web scraping."""

import re
from datetime import datetime

from bs4 import BeautifulSoup
from tqdm import tqdm

from fling_checker.config import print, FLING_BASE_URL, FLING_FIRST_PAGE


def scrape_fling_trainers(cache: dict, config) -> tuple[list[dict], list[dict]]:
    """
    Scrape FLiNG Trainer category pages for trainers from min_year onwards.
    Stops when it hits a trainer_url already in cache.

    Returns:
        (new_trainers, cached_results) — trainers not yet in cache, plus
        cached entries for trainers that still match.
    """
    cached_urls = set(cache.keys())
    new_trainers = []
    cached_results = []
    page = 1
    stop_scraping = False

    pbar = tqdm(desc="Scraping FLiNG", unit="page")

    while not stop_scraping:
        url = FLING_FIRST_PAGE if page == 1 else FLING_BASE_URL.format(page=page)
        pbar.set_postfix_str(f"page {page}")
        resp = config.session.get(url, timeout=15)
        if resp.status_code != 200:
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        articles = soup.select("article.post")

        if not articles:
            break

        for article in articles:
            title_tag = article.select_one("h2.entry-title a")
            if not title_tag:
                continue

            game_name = title_tag.text.strip()
            trainer_url = title_tag["href"]

            # Extract trainer date from URL or article
            date_tag = article.select_one("time.entry-date")
            trainer_date_str = date_tag["datetime"][:10] if date_tag else ""

            # Extract year from date
            try:
                trainer_year = int(trainer_date_str[:4])
            except (ValueError, IndexError):
                trainer_year = 0

            if trainer_year < config.min_year:
                stop_scraping = True
                continue

            # Extract version/update info
            version_tag = article.select_one(".entry-content p")
            version_info = ""
            if version_tag:
                version_info = version_tag.text.strip()[:100]

            # Count options from article
            options_tag = article.select_one(".entry-content")
            num_options = 0
            if options_tag:
                num_options = len(options_tag.select("li"))

            trainer_entry = {
                "game_name": game_name,
                "trainer_url": trainer_url,
                "trainer_date": trainer_date_str,
                "trainer_year": trainer_year,
                "num_options": num_options,
                "version_info": version_info,
            }

            if trainer_url in cached_urls:
                # Already cached — grab existing data
                cached_results.append(cache[trainer_url])
                stop_scraping = True  # incremental: stop at first cached entry
                continue

            new_trainers.append(trainer_entry)

        pbar.update(1)
        page += 1

    pbar.close()

    total = len(new_trainers) + len(cached_results)
    print(f"  Found {total} trainers ({len(new_trainers)} new, {len(cached_results)} cached)")
    return new_trainers, cached_results