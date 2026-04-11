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
        # Try multiple selectors to be more robust against site changes
        articles = soup.select("article.post") or soup.select("article") or soup.find_all("div", class_="post")

        if not articles:
            if config.verbose:
                print(f"    [Debug] No articles found on page {page}. HTML snippet: {resp.text[:500]}...")
            # If page 1 is empty, we might be blocked or the site changed significantly
            if page == 1:
                print(f"  ⚠ Warning: No trainers found on the first page. The site structure may have changed.")
            break

        for article in articles:
            title_tag = article.select_one("h2.post-title a") or article.select_one("h2.entry-title a")
            if not title_tag:
                continue

            game_name = title_tag.text.strip()
            trainer_url = title_tag["href"]

            # Extract trainer date — new site uses div-based date, old site used <time>
            day_tag = article.select_one(".post-details-day")
            month_tag = article.select_one(".post-details-month")
            year_tag = article.select_one(".post-details-year")
            if day_tag and month_tag and year_tag:
                month_map = {
                    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
                    "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
                    "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
                }
                month_str = month_map.get(month_tag.text.strip(), "01")
                trainer_date_str = f"{year_tag.text.strip()}-{month_str}-{day_tag.text.strip().zfill(2)}"
            else:
                date_tag = article.select_one("time.entry-date") or article.select_one("time")
                trainer_date_str = date_tag["datetime"][:10] if date_tag and date_tag.get("datetime") else ""

            # Extract year from date
            try:
                trainer_year = int(trainer_date_str[:4])
            except (ValueError, IndexError):
                trainer_year = 0

            if trainer_year < config.min_year:
                # We've hit the year boundary.
                # Mark for stopping, but finish the current page to be thorough.
                stop_scraping = True
                continue

            # Extract version/update info from listing page excerpt
            version_info = ""
            entry_div = article.select_one(".entry") or article.select_one(".entry-content")
            if entry_div:
                full_text = entry_div.get_text(strip=True)
                # Remove "Continue reading…" suffix if present
                full_text = re.sub(r"\s*Continue reading.*$", "", full_text)
                version_info = full_text[:100]

            # Parse individual fields from version_info
            options_count = None
            game_version = ""
            last_updated = ""
            if version_info:
                opt_match = re.match(r"(\d+)\s+Options", version_info)
                if opt_match:
                    options_count = int(opt_match.group(1))

                version_match = re.search(r"Game Version:\s*(.+?)(?:\s*·|$)", version_info)
                if version_match:
                    game_version = version_match.group(1).strip()

                updated_match = re.search(r"Last Updated:\s*([\d.]+)", version_info)
                if updated_match:
                    last_updated = updated_match.group(1).strip()

            # Also derive a human-readable date string
            trainer_date_display = ""
            if day_tag and month_tag and year_tag:
                trainer_date_display = f"{day_tag.text.strip()} {month_tag.text.strip()} {year_tag.text.strip()}"

            # Extract slug from URL (e.g., https://flingtrainer.com/trainer/game-name-trainer/ -> game-name)
            trainer_slug = trainer_url.strip("/").split("/")[-1].removesuffix("-trainer")

            trainer_entry = {
                "game_name": game_name,
                "trainer_url": trainer_url,
                "trainer_slug": trainer_slug,
                "trainer_date": trainer_date_str,
                "trainer_date_str": trainer_date_display,
                "trainer_year": trainer_year,
                "options_count": options_count,
                "num_options": options_count or 0,
                "game_version": game_version,
                "last_updated": last_updated,
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