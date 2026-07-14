"""collect.pyのユニットテスト。normalize_title_keyとモックレスポンスでのDB書き込みを検証する。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collect import (
    collect_tracked_titles,
    collect_top_titles,
    is_multi_volume_set,
    is_single_volume,
    normalize_title_key,
    parse_title_components,
    search_category_pool,
    split_top_titles,
    summarize_listings,
    upsert_title,
)
from db import get_connection


class TestNormalizeTitleKey(unittest.TestCase):
    def test_basic_volume(self):
        self.assertEqual(normalize_title_key("One Piece", "Vol.1", "English"), "one_piece_vol1_en")

    def test_set_range(self):
        self.assertEqual(
            normalize_title_key("Chainsaw Man", "Set 1-11", "English"),
            "chainsaw_man_set_1_11_en",
        )

    def test_japanese_language_code(self):
        self.assertEqual(normalize_title_key("Naruto", "Vol.5", "Japanese"), "naruto_vol5_jp")

    def test_case_and_whitespace_insensitive(self):
        self.assertEqual(
            normalize_title_key("  Attack on Titan ", "vol.3", "en"),
            normalize_title_key("Attack on Titan", "Vol.3", "English"),
        )


class TestParseTitleComponents(unittest.TestCase):
    def test_box_set_range(self):
        series, vol, lang = parse_title_components(
            "Demon Slayer Complete Box Set Manga Volumes 1-23 English VIZ w/Bonus Poster"
        )
        self.assertEqual(vol, "vol1-23")
        self.assertEqual(lang, "en")
        self.assertNotIn("manga", series.lower())

    def test_set_range_adjacent_number(self):
        series, vol, lang = parse_title_components("Chainsaw Man Box Set 1-11 Manga English")
        self.assertEqual(vol, "set1-11")
        self.assertEqual(lang, "en")

    def test_single_volume(self):
        series, vol, lang = parse_title_components("One Piece Vol.1 Manga English")
        self.assertEqual(vol, "vol1")
        self.assertEqual(lang, "en")

    def test_japanese_language_detected(self):
        _, _, lang = parse_title_components("Naruto Vol.5 Japanese Edition")
        self.assertEqual(lang, "jp")

    def test_no_volume_found(self):
        series, vol, lang = parse_title_components("Chainsaw Man Manga English")
        self.assertEqual(vol, "")


class TestSingleVsSetClassification(unittest.TestCase):
    def test_single_volume_detected(self):
        title = "One Piece, Vol. 112"
        _, vol, _ = parse_title_components(title)
        self.assertTrue(is_single_volume(title, vol))
        self.assertFalse(is_multi_volume_set(title, vol))

    def test_en_dash_range_treated_as_set(self):
        title = "Demon Slayer Manga Box Set Kimetsu no Yaiba (Volumes 1–23)"
        _, vol, _ = parse_title_components(title)
        self.assertFalse(is_single_volume(title, vol))
        self.assertTrue(is_multi_volume_set(title, vol))

    def test_box_set_detected(self):
        title = "Chainsaw Man Box Set 1 (Vol. 1-11) Manga"
        _, vol, _ = parse_title_components(title)
        self.assertTrue(is_multi_volume_set(title, vol))
        self.assertFalse(is_single_volume(title, vol))

    def test_lot_keyword_treated_as_set_even_without_range(self):
        title = "Manga Lot Assorted Vol.3"
        _, vol, _ = parse_title_components(title)
        self.assertTrue(is_multi_volume_set(title, vol))
        self.assertFalse(is_single_volume(title, vol))

    def test_split_top_titles_respects_limits_and_order(self):
        items = (
            [{"title": f"Series {i} Vol.{i} Manga"} for i in range(3)]
            + [{"title": f"Series {i} Box Set 1-{i+10} Manga"} for i in range(3)]
        )
        singles, sets_ = split_top_titles(items, top_n_single=2, top_n_set=2)
        self.assertEqual(len(singles), 2)
        self.assertEqual(len(sets_), 2)
        self.assertEqual(singles[0]["title"], "Series 0 Vol.0 Manga")
        self.assertEqual(sets_[0]["title"], "Series 0 Box Set 1-10 Manga")


class TestSearchCategoryPoolMerging(unittest.TestCase):
    def test_merges_multiple_queries_and_dedupes_by_item_id(self):
        import collect as collect_module

        responses = {
            "manga": [{"itemId": "1", "title": "One Piece Vol.1 Manga"}, {"itemId": "2", "title": "Naruto Vol.1 Manga"}],
            "BGS": [{"itemId": "2", "title": "Naruto Vol.1 Manga"}, {"itemId": "3", "title": "BGS 9.8 Dragon Ball #1"}],
            "vol": [{"itemId": "4", "title": "Bleach Vol.1"}],
        }

        class FakeResponse:
            def __init__(self, items):
                self._items = items

            def raise_for_status(self):
                pass

            def json(self):
                return {"itemSummaries": self._items}

        def fake_get(url, headers=None, params=None, timeout=None):
            return FakeResponse(responses[params["q"]])

        original_get = collect_module.requests.get
        collect_module.requests.get = fake_get
        try:
            merged = search_category_pool(
                "fake-token", queries=["manga", "BGS", "vol"]
            )
        finally:
            collect_module.requests.get = original_get

        self.assertEqual([item["itemId"] for item in merged], ["1", "2", "3", "4"])


class TestSummarizeListings(unittest.TestCase):
    def test_median_and_min(self):
        items = [
            {"price": {"value": "10.00"}},
            {"price": {"value": "20.00"}},
            {"price": {"value": "30.00"}},
        ]
        stats = summarize_listings(items)
        self.assertEqual(stats["median_price"], 20.00)
        self.assertEqual(stats["min_price"], 10.00)
        self.assertEqual(stats["listing_count"], 3)
        self.assertFalse(stats["single_listing_flag"])

    def test_single_listing_flag(self):
        items = [{"price": {"value": "15.00"}}]
        stats = summarize_listings(items)
        self.assertTrue(stats["single_listing_flag"])

    def test_empty_listings(self):
        stats = summarize_listings([])
        self.assertIsNone(stats["median_price"])
        self.assertEqual(stats["listing_count"], 0)


class TestMockCollectionFlow(unittest.TestCase):
    """モックレスポンスを使ってDB書き込みまでの流れを検証する(実APIは呼ばない)。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test_trend.db"
        self.conn = get_connection(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_upsert_title_idempotent(self):
        upsert_title(self.conn, "one_piece_vol1_en", "One Piece Vol.1", "2026-07-12")
        upsert_title(self.conn, "one_piece_vol1_en", "One Piece Vol.1", "2026-07-13")
        self.conn.commit()
        rows = self.conn.execute("SELECT COUNT(*) FROM titles").fetchone()
        self.assertEqual(rows[0], 1)

    def test_mock_top_titles_and_tracked_snapshot(self):
        mock_top_items = [
            {"title": "One Piece Vol.1 Manga English"},
            {"title": "Naruto Vol.1 Manga English"},
            {"title": "Bleach Box Set 1-20 Manga English"},
        ]
        mock_listing_items = [
            {"price": {"value": "9.99"}},
            {"price": {"value": "12.50"}},
        ]

        class FakeAccessToken:
            pass

        import collect as collect_module

        original_search = collect_module.search_category_pool
        original_fetch = collect_module.fetch_listings
        collect_module.search_category_pool = (
            lambda token, category_id=collect_module.CATEGORY_ID, queries=collect_module.SEARCH_QUERIES, limit_per_query=collect_module.SEARCH_POOL_LIMIT_PER_QUERY: mock_top_items
        )
        collect_module.fetch_listings = lambda token, keyword, category_id=collect_module.CATEGORY_ID, limit=50: mock_listing_items
        try:
            rank_map = collect_top_titles(self.conn, FakeAccessToken(), "2026-07-12")
            collect_tracked_titles(self.conn, FakeAccessToken(), "2026-07-12", rank_map)
        finally:
            collect_module.search_category_pool = original_search
            collect_module.fetch_listings = original_fetch

        titles = self.conn.execute("SELECT title_key FROM titles").fetchall()
        self.assertEqual(len(titles), 3)

        snapshots = self.conn.execute(
            "SELECT title_key, median_price, rank_in_category FROM snapshots"
        ).fetchall()
        self.assertEqual(len(snapshots), 3)
        for title_key, median_price, rank in snapshots:
            self.assertAlmostEqual(median_price, 11.245)
            self.assertIn(rank, (1, 2, 3))


if __name__ == "__main__":
    unittest.main()
