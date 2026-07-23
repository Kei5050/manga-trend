"""DBスキーマ定義と接続ヘルパー。collect.py / analyze.py 共通で使用する。"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "trend.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS titles (
  title_key    TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  first_seen   TEXT NOT NULL,
  kind         TEXT              -- 'single'(単巻) / 'set'(セット) / NULL(未分類)
);

CREATE TABLE IF NOT EXISTS snapshots (
  title_key        TEXT NOT NULL REFERENCES titles(title_key),
  snap_date        TEXT NOT NULL,
  median_price     REAL,
  min_price        REAL,
  listing_count    INTEGER,
  rank_in_category INTEGER,
  PRIMARY KEY (title_key, snap_date)
);
"""


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    # kind列導入前に作成されたDBへのマイグレーション
    cols = {row[1] for row in conn.execute("PRAGMA table_info(titles)")}
    if "kind" not in cols:
        conn.execute("ALTER TABLE titles ADD COLUMN kind TEXT")
        conn.commit()
    return conn
