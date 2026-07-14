"""レポート係。data/analysis_latest.json のみを読み、docs/index.html を生成する。"""
import json
import sys
from pathlib import Path

INPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "analysis_latest.json"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "index.html"

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>eBay漫画トレンドレポート</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
  :root {
    color-scheme: dark;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: #121212;
    color: #e8e8e8;
    font-family: -apple-system, BlinkMacSystemFont, "Hiragino Kaku Gothic ProN", "Segoe UI", sans-serif;
  }
  header {
    padding: 16px;
    background: #1a1a1a;
    border-bottom: 1px solid #2a2a2a;
    position: sticky;
    top: 0;
    z-index: 10;
  }
  header h1 { margin: 0 0 4px; font-size: 18px; }
  header .generated { font-size: 12px; color: #888; }
  .tabs {
    display: flex;
    background: #1a1a1a;
    border-bottom: 1px solid #2a2a2a;
    position: sticky;
    top: 58px;
    z-index: 10;
  }
  .tab {
    flex: 1;
    padding: 12px 0;
    text-align: center;
    font-size: 14px;
    color: #888;
    cursor: pointer;
    border-bottom: 2px solid transparent;
  }
  .tab.active {
    color: #fff;
    border-bottom-color: #4da3ff;
  }
  .panel { display: none; padding: 12px; }
  .panel.active { display: block; }
  .card {
    background: #1e1e1e;
    border-radius: 10px;
    padding: 14px;
    margin-bottom: 10px;
  }
  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    cursor: pointer;
  }
  .card-title { font-size: 15px; font-weight: 600; }
  .card-sub { font-size: 12px; color: #999; margin-top: 4px; }
  .price { font-size: 16px; font-weight: 700; }
  .change-up { color: #ff6b6b; }
  .change-down { color: #4dabf7; }
  .chart-wrap { display: none; margin-top: 12px; height: 180px; }
  .chart-wrap.open { display: block; }
  .empty { text-align: center; color: #666; padding: 40px 0; font-size: 14px; }
  .note {
    font-size: 12px;
    color: #999;
    background: #1e1e1e;
    border-radius: 8px;
    padding: 10px;
    margin: 0 12px 12px;
  }
</style>
</head>
<body>
<header>
  <h1>eBay漫画トレンド</h1>
  <div class="generated">生成日時: __GENERATED_AT__</div>
</header>
<div class="note">
  データ蓄積開始から1週間後に前週比、1ヶ月後に前月比が有効になります。
  出品数が少ないタイトルは参考値として下部に分離しています。
</div>
<div class="tabs">
  <div class="tab active" data-panel="weekly">前週比</div>
  <div class="tab" data-panel="monthly">前月比</div>
  <div class="tab" data-panel="new">NEW</div>
</div>

<div class="panel active" id="panel-weekly"></div>
<div class="panel" id="panel-monthly"></div>
<div class="panel" id="panel-new"></div>

<script>
const ANALYSIS = __ANALYSIS_JSON__;

function fmtPrice(v) {
  return v === null || v === undefined ? "-" : "$" + v.toFixed(2);
}

function renderCard(item, kind) {
  const hasChange = kind === "weekly" || kind === "monthly";
  const changeClass = hasChange ? (item.diff_pct >= 0 ? "change-up" : "change-down") : "";
  const changeText = hasChange
    ? `${item.diff_pct >= 0 ? "+" : ""}${item.diff_pct}% (${item.diff_amount >= 0 ? "+" : ""}$${item.diff_amount.toFixed(2)})`
    : "新規ランクイン";
  const note = ANALYSIS.notes && ANALYSIS.notes[item.title_key];

  const wrap = document.createElement("div");
  wrap.className = "card";

  const header = document.createElement("div");
  header.className = "card-header";

  const left = document.createElement("div");
  const title = document.createElement("div");
  title.className = "card-title";
  title.textContent = item.display_name;
  const sub = document.createElement("div");
  sub.className = "card-sub";
  sub.textContent = `出品数: ${item.listing_count ?? "-"}件`;
  left.appendChild(title);
  left.appendChild(sub);
  if (note) {
    const noteEl = document.createElement("div");
    noteEl.className = "card-sub";
    noteEl.style.marginTop = "6px";
    noteEl.style.color = "#e0b34d";
    noteEl.textContent = note;
    left.appendChild(noteEl);
  }

  const right = document.createElement("div");
  right.style.textAlign = "right";
  const priceEl = document.createElement("div");
  priceEl.className = "price";
  priceEl.textContent = fmtPrice(item.current_price);
  const changeEl = document.createElement("div");
  changeEl.className = changeClass;
  changeEl.textContent = changeText;
  right.appendChild(priceEl);
  right.appendChild(changeEl);

  header.appendChild(left);
  header.appendChild(right);

  const chartWrap = document.createElement("div");
  chartWrap.className = "chart-wrap";
  const canvas = document.createElement("canvas");
  chartWrap.appendChild(canvas);

  wrap.appendChild(header);
  wrap.appendChild(chartWrap);

  let chart = null;
  header.addEventListener("click", () => {
    chartWrap.classList.toggle("open");
    if (chartWrap.classList.contains("open") && !chart) {
      chart = new Chart(canvas, {
        type: "line",
        data: {
          labels: item.history.map(h => h.snap_date),
          datasets: [{
            label: "中央値価格",
            data: item.history.map(h => h.median_price),
            borderColor: "#4da3ff",
            backgroundColor: "rgba(77,163,255,0.15)",
            tension: 0.2,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            x: { ticks: { color: "#999" }, grid: { color: "#2a2a2a" } },
            y: { ticks: { color: "#999" }, grid: { color: "#2a2a2a" } },
          },
          plugins: { legend: { labels: { color: "#ccc" } } },
        },
      });
    }
  });

  return wrap;
}

function renderPanel(panelId, items, kind, lowConfidenceItems) {
  const panel = document.getElementById(panelId);
  panel.innerHTML = "";
  const hasMain = items && items.length > 0;
  const hasLow = lowConfidenceItems && lowConfidenceItems.length > 0;
  if (!hasMain && !hasLow) {
    panel.innerHTML = '<div class="empty">データがありません</div>';
    return;
  }
  if (hasMain) {
    items.forEach(item => panel.appendChild(renderCard(item, kind)));
  }
  if (hasLow) {
    const heading = document.createElement("div");
    heading.className = "card-sub";
    heading.style.margin = "16px 0 8px";
    heading.textContent = "出品数が少なく参考値のタイトル";
    panel.appendChild(heading);
    lowConfidenceItems.forEach(item => panel.appendChild(renderCard(item, kind)));
  }
}

renderPanel("panel-weekly", ANALYSIS.weekly_ranking, "weekly", ANALYSIS.weekly_low_confidence);
renderPanel("panel-monthly", ANALYSIS.monthly_ranking, "monthly", ANALYSIS.monthly_low_confidence);
renderPanel("panel-new", ANALYSIS.new_entries, "new");

document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById("panel-" + tab.dataset.panel).classList.add("active");
  });
});
</script>
</body>
</html>
"""


def render_html(analysis: dict) -> str:
    generated_at = analysis.get("generated_at", "")
    generated_at_safe = generated_at.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html = HTML_TEMPLATE.replace("__GENERATED_AT__", generated_at_safe)
    # <script>タグ内に埋め込むため、</script>によるタグ終端注入を防ぐ
    analysis_json = json.dumps(analysis, ensure_ascii=False).replace("<", "\\u003c")
    html = html.replace("__ANALYSIS_JSON__", analysis_json)
    return html


def main():
    if not INPUT_PATH.exists():
        print(f"入力ファイルが見つかりません: {INPUT_PATH}", file=sys.stderr)
        sys.exit(1)
    analysis = json.loads(INPUT_PATH.read_text(encoding="utf-8"))
    html = render_html(analysis)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"report written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
