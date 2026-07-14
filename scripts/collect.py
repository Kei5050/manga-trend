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
# Comics & Graphic Novels > Manga & Asian Comics のカテゴリID(eBay Browse APIで確認済み)
CATEGORY_ID = "33346"
# Comics & Graphic Novels > Comics > Comics & Graphic Novels(一般コミック向けカテゴリ)。
# BGSグレーディング品や単巻がManga & Asian Comicsではなくこちらに出品されるケースがあるため、
# Manga & Asian Comics(33346)に加えて検索対象に含める。
GENERAL_COMICS_CATEGORY_ID = "259104"
SEARCH_CATEGORY_IDS = [CATEGORY_ID, GENERAL_COMICS_CATEGORY_ID]
# Browse APIのsearchはcategory_ids単体では受け付けず、qが必須。カテゴリを絞り込むための検索語。
# "manga"のみだとタイトルに"manga"を含まない出品(BGSなどグレーディング表記が中心の出品や
# 巻数のみのタイトル)を取りこぼすため、複数クエリを実行して結果をマージする。
# "#"は単独クエリだとeBay側で「結果が大きすぎる」エラーになるため使えない
# (巻数としての"#"表記はparse_title_componentsの正規表現側で拾う)。
SEARCH_QUERIES = ["manga", "BGS", "vol"]
TOP_N_SINGLE = 50
TOP_N_SET = 50
# 単巻/セットの分類はaspect_filter(Unit of Sale)が機能しないため、
# 広めに取得した結果をタイトル解析(is_single_volume/is_multi_volume_set)で分類する
SEARCH_POOL_LIMIT_PER_QUERY = 200
REQUEST_INTERVAL_SEC = 1.0

_DASH_CHARS = re.compile(r"[‐-―−]")  # en/em dash等をASCIIハイフンに統一
_LOT_WORDS = re.compile(r"\b(lot|bundle|collection|complete\s*set)\b", re.IGNORECASE)
_VOLUME_PATTERNS = [
    re.compile(r"\b(?:box\s*set|set)\s*\.?\s*#?\s*(\d+\s*-\s*\d+|\d+)\b", re.IGNORECASE),
    re.compile(r"\b(?:vols?\.?|volumes?)\s*\.?\s*#?\s*(\d+\s*-\s*\d+|\d+)\b", re.IGNORECASE),
    re.compile(r"#\s*(\d+\s*-\s*\d+|\d+)\b"),
]
_NOISE_WORDS = re.compile(
    r"\b(manga|english|japanese|brand\s*new|complete|authentic|viz\s*media|viz|"
    r"us|new|w/bonus\s*poster|book|set|box|lot)\b",
    re.IGNORECASE,
)


def parse_title_components(raw_title: str) -> tuple:
    """eBayの出品タイトル文字列からシリーズ名・巻数/セット・言語を抽出する。

    出品者ごとに表記が揺れるため厳密な解析はできないが、
    「Vol./Volume/Set + 数字(範囲可)」のパターンを優先的に検出する。
    言語表記が見当たらない場合は英語(en)を既定値とする。
    """
    normalized_title = _DASH_CHARS.sub("-", raw_title)

    volume_or_set = ""
    for pattern in _VOLUME_PATTERNS:
        match = pattern.search(normalized_title)
        if match:
            is_set = pattern is _VOLUME_PATTERNS[0]
            num = re.sub(r"\s*-\s*", "-", match.group(1))
            volume_or_set = f"{'set' if is_set else 'vol'}{num}"
            break

    language = "jp" if re.search(r"\bjapanese\b", normalized_title, re.IGNORECASE) else "en"

    series_name = normalized_title
    for pattern in _VOLUME_PATTERNS:
        series_name = pattern.sub("", series_name)
    series_name = _NOISE_WORDS.sub("", series_name)
    series_name = re.sub(r"\s+", " ", series_name).strip(" -.,")

    return series_name, volume_or_set, language


def is_single_volume(raw_title: str, volume_or_set: str) -> bool:
    """巻数/セット表記と『lot』『bundle』等のキーワードから単巻出品かどうかを判定する。"""
    if not volume_or_set.startswith("vol"):
        return False
    if "-" in volume_or_set:
        return False
    return not _LOT_WORDS.search(raw_title)


def is_multi_volume_set(raw_title: str, volume_or_set: str) -> bool:
    """複数巻セット/ボックスセット/まとめ売りの出品かどうかを判定する。"""
    if volume_or_set.startswith("set"):
        return True
    if volume_or_set.startswith("vol") and "-" in volume_or_set:
        return True
    return bool(_LOT_WORDS.search(raw_title))


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


def fetch_listings(access_token: str, keyword: str, category_id: str = None, limit: int = 50) -> list:
    """指定タイトルのリスティングをeBay Browse APIから取得する。

    タイトルの初出カテゴリ(Manga & Asian Comics / 一般Comics)を問わず
    同一出品を追跡できるよう、既定ではカテゴリ指定なしで検索する。
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    }
    params = {
        "q": keyword,
        "limit": str(limit),
    }
    if category_id:
        params["category_ids"] = category_id
    resp = requests.get(EBAY_SEARCH_URL, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("itemSummaries", [])


def search_category_pool(
    access_token: str,
    category_ids: list = SEARCH_CATEGORY_IDS,
    queries: list = SEARCH_QUERIES,
    limit_per_query: int = SEARCH_POOL_LIMIT_PER_QUERY,
) -> list:
    """複数カテゴリ×複数クエリでBest Match順(sort省略時の既定)に検索し、結果をマージして返す。

    Browse APIはcategory_idsを1つしか受け付けないため、カテゴリごとに個別リクエストする。
    "manga"のみのクエリだとタイトルに"manga"を含まない出品(BGSグレーディング表記中心の
    出品など)を取りこぼすため、複数クエリの結果をitemId基準で重複排除しながら結合する。
    Browse APIにwatchCount等の人気順ソートは存在しないため、
    関連度に基づくBest Matchを人気順の代替として採用する。
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    }
    seen_item_ids = set()
    merged = []
    for category_id in category_ids:
        for query in queries:
            params = {
                "q": query,
                "category_ids": category_id,
                "limit": str(limit_per_query),
            }
            resp = requests.get(EBAY_SEARCH_URL, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            for item in resp.json().get("itemSummaries", []):
                item_id = item.get("itemId")
                if item_id in seen_item_ids:
                    continue
                seen_item_ids.add(item_id)
                merged.append(item)
    return merged


def split_top_titles(items: list, top_n_single: int = TOP_N_SINGLE, top_n_set: int = TOP_N_SET) -> tuple:
    """検索結果を単巻/セットに分類し、Best Match順を保ったまま上位N件ずつに絞る。

    eBay Browse APIのaspect_filter(Unit of Sale)が実際には機能しないため、
    タイトル文字列の解析(is_single_volume/is_multi_volume_set)で判定する。
    """
    singles = []
    sets_ = []
    for item in items:
        raw_title = item.get("title", "")
        _, volume_or_set, _ = parse_title_components(raw_title)
        if is_single_volume(raw_title, volume_or_set) and len(singles) < top_n_single:
            singles.append(item)
        elif is_multi_volume_set(raw_title, volume_or_set) and len(sets_) < top_n_set:
            sets_.append(item)
    return singles, sets_


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
    """単巻・セットそれぞれ上位N件を取得し、titlesに記録する。title_key -> rank の辞書を返す。

    単巻とセットは別のランキング(共にBest Match順で1位から採番)として扱う。
    """
    pool = search_category_pool(access_token)
    singles, sets_ = split_top_titles(pool)

    rank_map = {}
    for rank, item in enumerate(singles + sets_, start=1):
        raw_title = item.get("title", "")
        series_name, volume_or_set, language = parse_title_components(raw_title)
        title_key = normalize_title_key(series_name, volume_or_set, language)
        # 同一カテゴリ内の順位は単巻/セット双方で1位から振り直しているため、
        # ここでは単巻50件を1〜50、セット50件を51〜100として通し番号にする
        rank_map[title_key] = rank
        upsert_title(conn, title_key, raw_title or title_key, snap_date)
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
