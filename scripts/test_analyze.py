"""analyze.pyのユニットテスト。日付をずらしたダミースナップショットで検証する。"""
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze import build_analysis
from db import get_connection


def d(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


class TestBuildAnalysis(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "test_trend.db"
        self.conn = get_connection(db_path)

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def _insert_title(self, title_key, display_name, first_seen):
        self.conn.execute(
            "INSERT INTO titles (title_key, display_name, first_seen) VALUES (?, ?, ?)",
            (title_key, display_name, first_seen),
        )

    def _insert_snapshot(self, title_key, snap_date, median_price, listing_count=5, rank=1):
        self.conn.execute(
            """
            INSERT INTO snapshots (title_key, snap_date, median_price, min_price, listing_count, rank_in_category)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (title_key, snap_date, median_price, median_price, listing_count, rank),
        )

    def test_weekly_and_monthly_change(self):
        self._insert_title("one_piece_vol1_en", "One Piece Vol.1", d(35))
        self._insert_snapshot("one_piece_vol1_en", d(35), 10.0)
        self._insert_snapshot("one_piece_vol1_en", d(7), 15.0)
        self._insert_snapshot("one_piece_vol1_en", d(0), 20.0)
        self.conn.commit()

        analysis = build_analysis(self.conn)

        self.assertEqual(len(analysis["weekly_ranking"]), 1)
        weekly = analysis["weekly_ranking"][0]
        self.assertAlmostEqual(weekly["diff_pct"], 33.33, places=1)

        self.assertEqual(len(analysis["monthly_ranking"]), 1)
        monthly = analysis["monthly_ranking"][0]
        self.assertAlmostEqual(monthly["diff_pct"], 100.0, places=1)

    def test_new_entry_excluded_from_ranking(self):
        self._insert_title("new_title_vol1_en", "New Title Vol.1", d(0))
        self._insert_snapshot("new_title_vol1_en", d(0), 12.0)
        self.conn.commit()

        analysis = build_analysis(self.conn)

        self.assertEqual(len(analysis["new_entries"]), 1)
        self.assertEqual(analysis["new_entries"][0]["title_key"], "new_title_vol1_en")
        self.assertEqual(len(analysis["weekly_ranking"]), 0)
        self.assertEqual(len(analysis["monthly_ranking"]), 0)

    def test_no_comparison_target_excluded_not_errored(self):
        self._insert_title("orphan_vol1_en", "Orphan Vol.1", d(3))
        self._insert_snapshot("orphan_vol1_en", d(3), 8.0)
        self._insert_snapshot("orphan_vol1_en", d(0), 9.0)
        self.conn.commit()

        analysis = build_analysis(self.conn)

        self.assertEqual(len(analysis["weekly_ranking"]), 0)
        self.assertEqual(len(analysis["monthly_ranking"]), 0)

    def test_low_listing_count_separated(self):
        self._insert_title("rare_vol1_en", "Rare Vol.1", d(7))
        self._insert_snapshot("rare_vol1_en", d(7), 10.0, listing_count=1)
        self._insert_snapshot("rare_vol1_en", d(0), 20.0, listing_count=1)
        self.conn.commit()

        analysis = build_analysis(self.conn)

        self.assertEqual(len(analysis["weekly_ranking"]), 0)
        self.assertEqual(len(analysis["weekly_low_confidence"]), 1)


if __name__ == "__main__":
    unittest.main()
