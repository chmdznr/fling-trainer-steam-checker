import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fling_checker.config import Config
from fling_checker.cache import load_cache, save_cache
from fling_checker.fling import scrape_fling_trainers
from fling_checker.pipeline import run_pipeline
from fling_checker.processor import process_new_trainers, refresh_prices
from fling_checker.reporter import NullReporter, Reporter, say
from fling_checker.tui import (
    cache_price_as_of,
    cached_rows,
    detail_lines,
    filter_results,
    format_row,
    price_display,
    sale_display,
    sort_results,
)


class FakeReporter(Reporter):
    def __init__(self):
        self.phases = []
        self.progress_events = []
        self.logs = []
        self.items = []
        self._cancelled = False

    def phase(self, name):
        self.phases.append(name)

    def progress(self, desc, done, total):
        self.progress_events.append((desc, done, total))

    def log(self, msg):
        self.logs.append(msg)

    def item(self, result):
        self.items.append(result)

    def is_cancelled(self):
        return self._cancelled

    def cancel(self):
        self._cancelled = True


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


def sample_result(**kw):
    base = {
        "game_name": "Alpha Trainer",
        "trainer_url": "https://flingtrainer.com/trainer/alpha-trainer/",
        "trainer_date_str": "01 Jul 2026",
        "options_count": 5,
        "steam_appid": 111,
        "steam_name": "Alpha",
        "deck_compat": "Verified",
        "price_idr": 100000,
        "price": "Rp100.000",
        "discount_pct": 20,
        "on_sale": True,
        "positive_pct": 95,
        "total_reviews": 1000,
        "review_desc": "Overwhelmingly Positive",
        "genres": "Action",
        "last_updated": "",
        "game_version": "",
        "version_info": "",
        "steam_url": "https://store.steampowered.com/app/111/",
    }
    base.update(kw)
    return base


class ReporterTests(unittest.TestCase):
    def test_scrape_emits_progress_and_summary_via_reporter(self):
        config = Config(min_year=2024)
        config.session = FlingSession({
            1: trainer_article("New Game Trainer", "new-game"),
            2: "",
        })
        reporter = FakeReporter()

        new_trainers, _cached = scrape_fling_trainers({}, config, reporter=reporter)

        self.assertEqual(len(new_trainers), 1)
        self.assertTrue(any(d == "Scraping FLiNG" for d, _, _ in reporter.progress_events))
        self.assertTrue(any("Found 1 trainers" in m for m in reporter.logs))

    def test_scrape_stops_when_cancelled(self):
        config = Config(min_year=2024)
        config.session = FlingSession({
            1: trainer_article("New Game Trainer", "new-game"),
            2: trainer_article("Other Game Trainer", "other-game"),
        })
        reporter = FakeReporter()
        reporter.cancel()

        new_trainers, _cached = scrape_fling_trainers({}, config, reporter=reporter)

        self.assertEqual(new_trainers, [])

    def test_process_new_trainers_reports_items_and_progress(self):
        config = Config(max_workers=1, request_delay=0)
        trainer = {
            "game_name": "Some Game Trainer",
            "trainer_url": "https://flingtrainer.com/trainer/some-game-trainer/",
            "trainer_slug": "some-game",
        }
        fake_result = {**trainer, "steam_appid": 42, "steam_name": "Some Game"}
        reporter = FakeReporter()

        with patch("fling_checker.processor._process_single_new_trainer", return_value=fake_result):
            results = process_new_trainers([trainer], config, reporter=reporter)

        self.assertEqual(results, [fake_result])
        self.assertEqual(reporter.items, [fake_result])
        self.assertIn(("New games", 1, 1), reporter.progress_events)

    def test_refresh_prices_reports_items_and_progress(self):
        config = Config(max_workers=1, request_delay=0)
        cached = {
            "game_name": "Cached Game",
            "trainer_url": "https://flingtrainer.com/trainer/cached-game-trainer/",
            "steam_appid": 123,
            "_price_updated_at": None,
        }
        reporter = FakeReporter()

        with patch(
            "fling_checker.processor.get_steam_app_details",
            return_value={"is_free": True},
        ):
            results = refresh_prices([cached], config, reporter=reporter)

        self.assertEqual(len(results), 1)
        self.assertEqual(len(reporter.items), 1)
        self.assertIn(("Price refresh", 1, 1), reporter.progress_events)

    def test_say_routes_to_log_for_custom_reporter(self):
        reporter = FakeReporter()
        say(reporter, "hello")
        self.assertEqual(reporter.logs, ["hello"])
        null_reporter = NullReporter()
        say(null_reporter, "quiet")
        self.assertEqual(null_reporter.logs if hasattr(null_reporter, "logs") else [], [])

    def test_say_prints_for_none(self):
        with patch("fling_checker.reporter._print") as mock_print:
            say(None, "world")
        self.assertEqual(mock_print.call_count, 1)

    def test_overrides_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config(output_dir=Path(tmp))
            config.overrides["some-slug"] = 12345
            config.save_overrides()

            config2 = Config(output_dir=Path(tmp))
            self.assertEqual(config2.overrides.get("some-slug"), 12345)

            config2.overrides.pop("some-slug")
            config2.save_overrides()
            config3 = Config(output_dir=Path(tmp))
            self.assertNotIn("some-slug", config3.overrides)

    def test_run_pipeline_merges_by_trainer_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config(max_workers=1, request_delay=0, output_dir=Path(tmp))
            new_row = sample_result()
            refreshed_row = sample_result(
                game_name="Old Game",
                trainer_url="https://flingtrainer.com/trainer/old-trainer/",
                steam_name="Old Game",
            )
            seed_cache = {
                refreshed_row["trainer_url"]: dict(refreshed_row, price="stale"),
            }

            with patch("fling_checker.pipeline.load_cache", return_value=dict(seed_cache)), \
                 patch("fling_checker.pipeline.scrape_fling_trainers", return_value=([{"game_name": "x"}], [])), \
                 patch("fling_checker.pipeline.process_new_trainers", return_value=[new_row]), \
                 patch("fling_checker.pipeline.refresh_prices", return_value=[refreshed_row]), \
                 patch("fling_checker.pipeline.save_cache") as mock_save, \
                 patch("fling_checker.pipeline.write_excel") as mock_excel:
                result = run_pipeline(config, reporter=FakeReporter())

            self.assertEqual(len(result.all_results), 2)
            self.assertEqual(result.new_results, [new_row])
            self.assertEqual(result.refreshed, [refreshed_row])
            mock_save.assert_called_once()
            mock_excel.assert_called_once()


class TuiHelperTests(unittest.TestCase):
    def test_format_row_handles_failed_result(self):
        row = format_row(sample_result(steam_appid=None, steam_name=None, steam_url=None,
                                       deck_compat="Unknown", price_idr=None, price="N/A",
                                       review_desc="Not Found"))
        self.assertEqual(row[3], "")
        self.assertEqual(row[4], "Unknown")
        self.assertEqual(row[5], "N/A")

    def test_format_row_marks_sale(self):
        row = format_row(sample_result())
        self.assertEqual(str(row[6]), "20")
        self.assertEqual(str(row[7]), "YES")
        row2 = format_row(sample_result(on_sale=False, discount_pct=0))
        self.assertEqual(str(row2[7]), "NO")

    def test_price_display_none_safe(self):
        self.assertEqual(price_display(sample_result(price_idr=None, price="N/A")), "N/A")
        self.assertEqual(price_display(sample_result(price_idr=None, price="Free")), "Free")
        self.assertEqual(sale_display(sample_result(on_sale=True)), "YES")
        self.assertEqual(sale_display(sample_result(on_sale=False)), "NO")

    def test_filter_results(self):
        rows = [
            sample_result(),
            sample_result(game_name="Beta Trainer",
                          trainer_url="https://flingtrainer.com/trainer/beta-trainer/",
                          deck_compat="Playable", on_sale=False, steam_name="Beta"),
            sample_result(game_name="Gamma Trainer",
                          trainer_url="https://flingtrainer.com/trainer/gamma-trainer/",
                          deck_compat="Unknown", on_sale=False, steam_name="Gamma"),
        ]
        self.assertEqual(len(filter_results(rows, "All", "", False)), 3)
        self.assertEqual(len(filter_results(rows, "Verified", "", False)), 1)
        self.assertEqual(len(filter_results(rows, "Deck OK", "", False)), 2)
        self.assertEqual(len(filter_results(rows, "Deck OK", "", True)), 1)
        self.assertEqual(len(filter_results(rows, "All", "beta", False)), 1)
        self.assertEqual(len(filter_results(rows, "All", "", True)), 1)
        self.assertEqual(len(filter_results(rows, "Unknown", "alpha", False)), 0)

    def test_detail_lines_none_safe(self):
        lines = detail_lines(sample_result(steam_appid=None, steam_name=None, steam_url=None,
                                           review_desc="Error"))
        joined = "\n".join(lines)
        self.assertIn("Alpha Trainer", joined)
        self.assertIn("—", joined)

    def test_sort_results_numeric_columns(self):
        rows = [
            sample_result(game_name="Cheap", trainer_url="https://x/cheap",
                          price_idr=1000, price="1", discount_pct=5, positive_pct=80, total_reviews=10),
            sample_result(game_name="Pricey", trainer_url="https://x/pricey",
                          price_idr=9000, price="9", discount_pct=50, positive_pct=90, total_reviews=5),
            sample_result(game_name="NoPrice", trainer_url="https://x/noprice",
                          price_idr=None, price="N/A", discount_pct=0, positive_pct=70, total_reviews=99),
        ]
        by_price = [r["game_name"] for r in sort_results(rows, 5, False)]
        self.assertEqual(by_price[-1], "Pricey")
        by_price_desc = [r["game_name"] for r in sort_results(rows, 5, True)]
        self.assertEqual(by_price_desc[0], "Pricey")
        by_disc = [r["game_name"] for r in sort_results(rows, 6, True)]
        self.assertEqual(by_disc[0], "Pricey")
        by_rating = [r["game_name"] for r in sort_results(rows, 8, False)]
        self.assertEqual(by_rating[0], "NoPrice")
        by_reviews = [r["game_name"] for r in sort_results(rows, 9, True)]
        self.assertEqual(by_reviews[0], "NoPrice")
        by_name = [r["game_name"] for r in sort_results(rows, 0, False)]
        self.assertEqual(by_name, ["Cheap", "NoPrice", "Pricey"])

    def test_sort_results_deck_order(self):
        rows = [
            sample_result(game_name="U", trainer_url="https://x/u", deck_compat="Unknown"),
            sample_result(game_name="P", trainer_url="https://x/p", deck_compat="Playable"),
            sample_result(game_name="V", trainer_url="https://x/v", deck_compat="Verified"),
        ]
        ordered = [r["game_name"] for r in sort_results(rows, 4, False)]
        self.assertEqual(ordered, ["V", "P", "U"])

    def test_cached_rows_groups_by_deck_and_tolerates_missing_fields(self):
        cache = {
            "https://x/u": {"trainer_url": "https://x/u", "game_name": "U"},
            "https://x/p": sample_result(game_name="P", trainer_url="https://x/p", deck_compat="Playable"),
            "https://x/v": sample_result(game_name="V", trainer_url="https://x/v", deck_compat="Verified"),
        }
        self.assertEqual([r["game_name"] for r in cached_rows(cache)], ["V", "P", "U"])
        self.assertEqual(cached_rows({}), [])

    def test_cache_price_as_of_uses_newest_date(self):
        rows = [
            sample_result(_price_updated_at="2026-09-16T16:50:01"),
            sample_result(trainer_url="https://x/b", _price_updated_at="2026-10-03T18:49:13"),
            sample_result(trainer_url="https://x/c", _price_updated_at=None),
            sample_result(trainer_url="https://x/d"),
        ]
        self.assertEqual(cache_price_as_of(rows), "2026-10-03")
        self.assertEqual(cache_price_as_of([]), "")
        self.assertEqual(cache_price_as_of([sample_result(trainer_url="https://x/c", _price_updated_at=None)]), "")

    def test_cached_rows_from_saved_cache_without_scraping(self):
        """Browsing the cache reads only the JSON file — no session is ever used."""
        with tempfile.TemporaryDirectory() as tmp:
            config = Config(output_dir=Path(tmp))
            config.session = None
            save_cache(
                {
                    "https://x/u": sample_result(game_name="U", trainer_url="https://x/u", deck_compat="Unknown"),
                    "https://x/v": sample_result(game_name="V", trainer_url="https://x/v", deck_compat="Verified"),
                },
                config,
            )
            with patch("fling_checker.fling.scrape_fling_trainers") as scrape:
                rows = cached_rows(load_cache(config))
            scrape.assert_not_called()
            self.assertEqual([r["game_name"] for r in rows], ["V", "U"])


if __name__ == "__main__":
    unittest.main()
