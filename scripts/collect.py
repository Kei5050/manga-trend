"""eBay収集係。eBay Browse APIのみに触れ、DBに書き込む。分析は行わない。"""
import os
import re
import sys
import time
import unicodedata
from datetime import date, datetime
from pathlib import Path
from statistics import median

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import get_connection

load_dotenv()

EBAY_CLIENT_ID = os.environ.get("EBAY_CLIENT_ID")
EBAY_CLIENT_SECRET = os.environ.get("EBAY_CLIENT_SECRET")
EBAY_OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
# Comics & Graphic Novels > Manga のカテゴリID。実装時にeBay Category APIで要確認
CATEGORY_ID = "85010"
TOP_N = 30
REQUEST_INTERVAL_SEC = 1.0


def normalize_title_key(series_name: str, volume_or_set: str, language: str) -> str:
    """シリーズ名+巻数/セット+言語から title_key を生成する。

    例: normalize_title_key("One Piece", "Vol.1", "English") -> "one_piece_vol1_en"
    """
    lang_map = {
        "english": "en", "en": "en",
        "japanese": "jp", "jp": "jp", "japan": "jp",
    }
    lang_code = lang_map.get(language.strip().lower(), language.strip().lower()[:2])

    def slugify(text: str) -> str:
        text = unicodedata.normalize("NFKC", text).lower()
        text = re.sub(r"[^\w\s-]", "", text)
        text = re.sub(r"[\s_-]+", "_", text).strip("_")
        return text

    series_slug = slugify(series_name)
    vol_slug = slugify(volume_or_set)
    vol_slug = vol_slug.replace("volume_", "vol").replace("vol_", "vol")
    return f"{series_slug}_{vol_slug}_{lang_code}"


def get_access_token() -> str:
    """OAuth client credentials フローでアクセストークンを取得する。"""
    if not EBAY_CLIENT_ID or not EBAY_CLIENT_SECRET:
        raise RuntimeError("EBAY_CLIENT_ID / EBAY_CLIENT_SECRET が未設定です")
    resp = requests.post(
        EBAY_OAUTH_URL,
        auth=(EBAY_CLIENT_ID, EBAY_CLIENT_SECRET),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def fetch_listings(access_token: str, keyword: str, category_id: str = CATEGORY_ID, limit: int = 50) -> list:
    """指定タイトルのリスティングをeBay Browse APIから取得する。"""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    }
    params = {
        "q": keyword,
        "category_ids": category_id,
        "limit": str(limit),
    }
    resp = requests.get(EBAY_SEARCH_URL, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("itemSummaries", [])


def search_top_titles(access_token: str, category_id: str = CATEGORY_ID, top_n: int = TOP_N) -> list:
    """カテゴリを人気順で検索し、上位タイトルの生アイテムを取得する。"""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    }
    params = {
        "category_ids": category_id,
        "sort": "-watchCount",
        "limit": str(top_n),
    }
    resp = requests.get(EBAY_SEARCH_URL, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("itemSummaries", [])


def summarize_listings(items: list) -> dict:
    """1タイトル分のリスティング群から中央値・最安値・出品数を計算する。"""
    prices = [float(item["price"]["value"]) for item in items if item.get("price")]
    if not prices:
        return {"median_price": None, "min_price": None, "listing_count": 0, "single_listing_flag": False}
    return {
        "median_price": median(prices),
        "min_price": min(prices),
        "listing_count": len(prices),
        "single_listing_flag": len(prices) == 1,
    }


def upsert_title(conn, title_key: str, display_name: str, snap_date: str) -> None:
    conn.execute(
        """
        INSERT INTO titles (title_key, display_name, first_seen)
        VALUES (?, ?, ?)
        ON CONFLICT(title_key) DO NOTHING
        """,
        (title_key, display_name, snap_date),
    )


def upsert_snapshot(
    conn,
    title_key: str,
    snap_date: str,
    median_price,
    min_price,
    listing_count: int,
    rank_in_category,
) -> None:
    conn.execute(
        """
        INSERT INTO snapshots (title_key, snap_date, median_price, min_price, listing_count, rank_in_category)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(title_key, snap_date) DO UPDATE SET
          median_price = excluded.median_price,
          min_price = excluded.min_price,
          listing_count = excluded.listing_count,
          rank_in_category = excluded.rank_in_category
        """,
        (title_key, snap_date, median_price, min_price, listing_count, rank_in_category),
    )


def collect_top_titles(conn, access_token: str, snap_date: str) -> dict:
    """上位30タイトルを取得し、titlesに記録する。title_key -> rank の辞書を返す。"""
    items = search_top_titles(access_token)
    rank_map = {}
    for rank, item in enumerate(items[:TOP_N], start=1):
        title_key = normalize_title_key(item.get("title", ""), item.get("volume", ""), item.get("language", "en"))
        rank_map[title_key] = rank
        upsert_title(conn, title_key, item.get("title", title_key), snap_date)
    conn.commit()
    return rank_map


def collect_tracked_titles(conn, access_token: str, snap_date: str, rank_map: dict) -> None:
    """titlesテーブル全件について価格を取得し、snapshotsに記録する(圏外追跡継続)。"""
    cur = conn.execute("SELECT title_key, display_name FROM titles")
    tracked = cur.fetchall()
    for title_key, display_name in tracked:
        items = fetch_listings(access_token, display_name)
        stats = summarize_listings(items)
        upsert_snapshot(
            conn,
            title_key=title_key,
            snap_date=snap_date,
            median_price=stats["median_price"],
            min_price=stats["min_price"],
            listing_count=stats["listing_count"],
            rank_in_category=rank_map.get(title_key),
        )
        conn.commit()
        time.sleep(REQUEST_INTERVAL_SEC)


def main():
    snap_date = date.today().isoformat()
    conn = get_connection()
    access_token = get_access_token()
    rank_map = collect_top_titles(conn, access_token, snap_date)
    collect_tracked_titles(conn, access_token, snap_date, rank_map)
    conn.close()


if __name__ == "__main__":
    main()
