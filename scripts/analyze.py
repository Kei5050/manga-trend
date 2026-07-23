"""分析係。data/trend.db のみを読み、data/analysis_latest.json を出力する。APIには触らない。"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import get_connection

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "analysis_latest.json"
WEEK_TOLERANCE_DAYS = 2
MONTH_TOLERANCE_DAYS = 5
MIN_LISTING_COUNT = 2


def load_snapshots(conn) -> dict:
    """title_key -> [(snap_date, median_price, min_price, listing_count, rank), ...] (日付昇順)"""
    rows = conn.execute(
        """
        SELECT title_key, snap_date, median_price, min_price, listing_count, rank_in_category
        FROM snapshots
        ORDER BY snap_date ASC
        """
    ).fetchall()
    by_title = {}
    for title_key, snap_date, median_price, min_price, listing_count, rank in rows:
        by_title.setdefault(title_key, []).append(
            {
                "snap_date": snap_date,
                "median_price": median_price,
                "min_price": min_price,
                "listing_count": listing_count,
                "rank_in_category": rank,
            }
        )
    return by_title


def find_nearest_snapshot(history: list, target_date: datetime, tolerance_days: int):
    """target_dateに最も近いスナップショットを±tolerance_days以内から探す。無ければNone。"""
    best = None
    best_diff = None
    for snap in history:
        snap_date = datetime.fromisoformat(snap["snap_date"])
        diff = abs((snap_date - target_date).days)
        if diff <= tolerance_days and (best_diff is None or diff < best_diff):
            best = snap
            best_diff = diff
    return best


def compute_change(latest: dict, past: dict) -> dict:
    if not past or past["median_price"] in (None, 0) or latest["median_price"] is None:
        return None
    diff = latest["median_price"] - past["median_price"]
    pct = (diff / past["median_price"]) * 100
    return {"diff_amount": round(diff, 2), "diff_pct": round(pct, 2)}


def build_analysis(conn) -> dict:
    by_title = load_snapshots(conn)
    titles_meta = {
        row[0]: {"display_name": row[1], "kind": row[2]}
        for row in conn.execute("SELECT title_key, display_name, kind FROM titles").fetchall()
    }

    latest_date = max(
        (datetime.fromisoformat(s["snap_date"]) for hist in by_title.values() for s in hist),
        default=None,
    )
    weekly_ranking = []
    monthly_ranking = []
    new_entries = []
    weekly_low_confidence = []
    monthly_low_confidence = []

    for title_key, history in by_title.items():
        history_sorted = sorted(history, key=lambda s: s["snap_date"])
        latest = history_sorted[-1]
        if datetime.fromisoformat(latest["snap_date"]) != latest_date:
            continue

        meta = titles_meta.get(title_key, {"display_name": title_key, "kind": None})
        entry_base = {
            "title_key": title_key,
            "display_name": meta["display_name"],
            "kind": meta["kind"],
            "current_price": latest["median_price"],
            "listing_count": latest["listing_count"],
            "history": history_sorted,
        }

        if len(history_sorted) == 1:
            new_entries.append(entry_base)

        week_target = latest_date - timedelta(days=7)
        week_snap = find_nearest_snapshot(history_sorted[:-1], week_target, WEEK_TOLERANCE_DAYS)
        week_change = compute_change(latest, week_snap) if week_snap else None
        if week_change:
            item = dict(entry_base, **week_change)
            if latest["listing_count"] is not None and latest["listing_count"] < MIN_LISTING_COUNT:
                weekly_low_confidence.append(item)
            else:
                weekly_ranking.append(item)

        month_target = latest_date - timedelta(days=30)
        month_snap = find_nearest_snapshot(history_sorted[:-1], month_target, MONTH_TOLERANCE_DAYS)
        month_change = compute_change(latest, month_snap) if month_snap else None
        if month_change:
            item = dict(entry_base, **month_change)
            if latest["listing_count"] is not None and latest["listing_count"] < MIN_LISTING_COUNT:
                monthly_low_confidence.append(item)
            else:
                monthly_ranking.append(item)

    weekly_ranking.sort(key=lambda x: x["diff_pct"], reverse=True)
    monthly_ranking.sort(key=lambda x: x["diff_pct"], reverse=True)
    weekly_low_confidence.sort(key=lambda x: x["diff_pct"], reverse=True)
    monthly_low_confidence.sort(key=lambda x: x["diff_pct"], reverse=True)

    return {
        "generated_at": datetime.now().isoformat(),
        "latest_snap_date": latest_date.date().isoformat() if latest_date else None,
        "weekly_ranking": weekly_ranking,
        "monthly_ranking": monthly_ranking,
        "new_entries": new_entries,
        "weekly_low_confidence": weekly_low_confidence,
        "monthly_low_confidence": monthly_low_confidence,
        "notes": {},
    }


def main():
    conn = get_connection()
    analysis = build_analysis(conn)
    conn.close()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"analysis written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
