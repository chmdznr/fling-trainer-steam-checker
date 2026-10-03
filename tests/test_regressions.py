import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

from fling_checker.cache import save_cache
from fling_checker.cli import parse_args
from fling_checker.config import Config
from fling_checker.fling import scrape_fling_trainers
from fling_checker.processor import process_new_trainers, refresh_prices
from fling_checker.steam import (
    cached_game_image,
    get_steam_app_details,
    load_game_image,
    steam_image_url,
)


class BadJsonResponse:
    text = "not-json"

    def raise_for_status(self):
        return None

    def json(self):
        raise ValueError("bad json")


class BadJsonSession:
    def get(self, *args, **kwargs):
        return BadJsonResponse()


class HtmlResponse:
    status_code = 200

    def __init__(self, text):
        self.text = text


class FlingSession:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url, timeout=15):
        if url == "https://flingtrainer.com/":
            return HtmlResponse(self.pages[1])
        page_num = int(url.rstrip("/").split("/")[-1])
        return HtmlResponse(self.pages.get(page_num, ""))


def trainer_article(name, slug, year="2026"):
    return f"""
    <article class="post">
      <h2 class="post-title">
        <a href="https://flingtrainer.com/trainer/{slug}-trainer/">{name}</a>
      </h2>
      <div class="post-details-day">01</div>
      <div class="post-details-month">Jul</div>
      <div class="post-details-year">{year}</div>
      <div class="entry">5 Options · Game Version: 1.0 · Last Updated: 2026.07.01</div>
    </article>
    """


class RegressionTests(unittest.TestCase):
    def test_app_details_bad_json_returns_none(self):
        config = Config()
        config.session = BadJsonSession()

        self.assertIsNone(get_steam_app_details(123, config))

    def test_save_cache_creates_output_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "missing" / "nested"
            config = Config(output_dir=output_dir)

            save_cache({"trainer-url": {"trainer_url": "trainer-url"}}, config)

            cache_path = output_dir / "fling_steam_cache.json"
            self.assertTrue(cache_path.exists())
            self.assertEqual(json.loads(cache_path.read_text())["trainer-url"]["trainer_url"], "trainer-url")

    def test_parse_args_rejects_non_positive_workers_and_retries(self):
        for flag in ("--workers", "--retries"):
            with self.subTest(flag=flag):
                with patch.object(sys, "argv", ["prog", flag, "0"]):
                    with self.assertRaises(SystemExit):
                        parse_args()

    def test_scrape_continues_past_cached_entries(self):
        cached_url = "https://flingtrainer.com/trainer/cached-game-trainer/"
        config = Config(min_year=2024)
        config.session = FlingSession({
            1: trainer_article("Cached Game Trainer", "cached-game"),
            2: trainer_article("New Game Trainer", "new-game"),
            3: "",
        })

        new_trainers, cached_results = scrape_fling_trainers(
            {cached_url: {"trainer_url": cached_url, "game_name": "Cached Game Trainer"}},
            config,
        )

        self.assertEqual([t["game_name"] for t in new_trainers], ["New Game Trainer"])
        self.assertEqual([r["game_name"] for r in cached_results], ["Cached Game Trainer"])

    def test_process_new_trainers_keeps_failed_items(self):
        config = Config(max_workers=1, request_delay=0)
        trainer = {
            "game_name": "Broken Game Trainer",
            "trainer_url": "https://flingtrainer.com/trainer/broken-game-trainer/",
            "trainer_slug": "broken-game",
        }

        with patch("fling_checker.processor.search_steam_appid", side_effect=ValueError("bad response")):
            results = process_new_trainers([trainer], config)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["game_name"], "Broken Game Trainer")
        self.assertIsNone(results[0]["steam_appid"])
        self.assertEqual(results[0]["review_desc"], "Error")

    def test_refresh_prices_keeps_failed_cached_items(self):
        config = Config(max_workers=1, request_delay=0)
        cached = {
            "game_name": "Cached Broken Game",
            "trainer_url": "https://flingtrainer.com/trainer/cached-broken-game-trainer/",
            "steam_appid": 123,
        }

        with patch("fling_checker.processor.get_steam_app_details", side_effect=ValueError("bad response")):
            results = refresh_prices([cached], config)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["game_name"], "Cached Broken Game")
        self.assertEqual(results[0]["steam_appid"], 123)


class ImageResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None


class ImageSession:
    """Serves a canned banner payload and counts how often it was called."""

    def __init__(self, content=b"\xff\xd8fakejpeg"):
        self.content = content
        self.calls = 0
        self.urls = []

    def get(self, url, params=None, timeout=10):
        self.calls += 1
        self.urls.append(url)
        return ImageResponse(self.content)


class ExplodingSession:
    def get(self, *args, **kwargs):
        raise AssertionError("network must not be used when the banner is already cached")


class NotFoundSession:
    def get(self, url, params=None, timeout=10):
        return NotFoundResponse()


class NotFoundResponse:
    content = b""

    def raise_for_status(self):
        raise requests.HTTPError(response=SimpleNamespace(status_code=404))


class SteamImageTests(unittest.TestCase):
    def test_image_url_is_derived_from_appid(self):
        self.assertEqual(
            steam_image_url(1446780),
            "https://cdn.cloudflare.steamstatic.com/steam/apps/1446780/header.jpg",
        )
        self.assertIsNone(steam_image_url(None))
        self.assertIsNone(steam_image_url(0))

    def test_cached_banner_is_read_without_any_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config(output_dir=Path(tmp))
            config.images_dir.mkdir(parents=True, exist_ok=True)
            (config.images_dir / "123.jpg").write_bytes(b"jpeg-bytes")
            config.session = ExplodingSession()

            self.assertEqual(cached_game_image(123, config).read_bytes(), b"jpeg-bytes")
            self.assertEqual(load_game_image(123, config).read_bytes(), b"jpeg-bytes")
            self.assertIsNone(cached_game_image(None, config))

    def test_zero_byte_cache_file_is_treated_as_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config(output_dir=Path(tmp))
            config.images_dir.mkdir(parents=True, exist_ok=True)
            (config.images_dir / "123.jpg").write_bytes(b"")
            session = ImageSession()
            config.session = session

            self.assertIsNone(cached_game_image(123, config))
            self.assertEqual(load_game_image(123, config).read_bytes(), session.content)
            self.assertEqual(session.calls, 1)

    def test_banner_downloads_once_then_serves_from_disk(self):
        payload = b"\xff\xd8realjpeg"
        with tempfile.TemporaryDirectory() as tmp:
            config = Config(output_dir=Path(tmp))
            config.session = ImageSession(payload)

            path = load_game_image(456, config)

            self.assertIsNotNone(path)
            self.assertEqual(path.read_bytes(), payload)
            self.assertEqual(path.parent, config.images_dir)
            config.session = ExplodingSession()
            self.assertEqual(load_game_image(456, config).read_bytes(), payload)

    def test_failed_banner_request_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config(output_dir=Path(tmp))
            config.session = NotFoundSession()

            self.assertIsNone(load_game_image(789, config))
            self.assertIsNone(load_game_image(None, config))


if __name__ == "__main__":
    unittest.main()
