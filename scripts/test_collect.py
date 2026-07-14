"""collect.pyのユニットテスト。normalize_title_keyとモックレスポンスでのDB書き込みを検証する。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collect import (
    collect_tracked_titles,
    collect_top_titles,
    normalize_title_key,
    parse_title_components,
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
            {"title": "One Piece", "volume": "Vol.1", "language": "en"},
            {"title": "Naruto", "volume": "Vol.1", "language": "en"},
        ]
        mock_listing_items = [
            {"price": {"value": "9.99"}},
            {"price": {"value": "12.50"}},
        ]

        class FakeAccessToken:
            pass

        import collect as collect_module

        original_search = collect_module.search_top_titles
        original_fetch = collect_module.fetch_listings
        collect_module.search_top_titles = lambda token, category_id=collect_module.CATEGORY_ID, top_n=collect_module.TOP_N: mock_top_items
        collect_module.fetch_listings = lambda token, keyword, category_id=collect_module.CATEGORY_ID, limit=50: mock_listing_items
        try:
            rank_map = collect_top_titles(self.conn, FakeAccessToken(), "2026-07-12")
            collect_tracked_titles(self.conn, FakeAccessToken(), "2026-07-12", rank_map)
        finally:
            collect_module.search_top_titles = original_search
            collect_module.fetch_listings = original_fetch

        titles = self.conn.execute("SELECT title_key FROM titles").fetchall()
        self.assertEqual(len(titles), 2)

        snapshots = self.conn.execute(
            "SELECT title_key, median_price, rank_in_category FROM snapshots"
        ).fetchall()
        self.assertEqual(len(snapshots), 2)
        for title_key, median_price, rank in snapshots:
            self.assertAlmostEqual(median_price, 11.245)
            self.assertIn(rank, (1, 2))


if __name__ == "__main__":
    unittest.main()
